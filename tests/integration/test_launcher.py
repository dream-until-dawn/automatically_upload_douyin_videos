"""浏览器启动与关闭的集成测试。

两个关注点：

  1. **启动失败要有确定的错误码**（8），而不是让异常裸奔到顶层变成 88；
  2. **清理必须在任何路径上都执行** —— 包括抛异常和被取消的路径。

第 2 点尤其重要：清理漏做会留下占用用户数据目录的浏览器进程，
下一次任务会因目录被占用而直接启动失败，且失败原因看起来毫不相干。

## 为什么这些测试需要本机 Chrome

其余集成测试都用 Playwright 自带的 Chromium，唯独这里不行。
实测结论（三轮对照实验，见 docs/probe-results.md 的「工程约束」一节）：

    把 `playwright.chromium.executable_path` 取到的路径再显式传回
    `launch_persistent_context(executable_path=...)`，会报 `spawn UNKNOWN`；
    不传该参数则正常。与 headless 与否无关。

而本模块的 `launch_context` 必然会显式传入路径——这正是它的职责
（复用本机 Chrome 里的登录态）。传本机 Chrome 的路径是成功的，
也就是生产实际走的路径。因此这里改用本机 Chrome，缺失时跳过。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from douyin_publisher.browser.launcher import (
    browser_session,
    close_context,
    launch_context,
)
from douyin_publisher.config.models import TaskConfig
from douyin_publisher.core.errors import ErrorCode, PublishError


def make_config(exec_path: str) -> TaskConfig:
    return TaskConfig(
        exec_path=exec_path,
        user_data_dir="unused",
        task_id="t",
        douyin_id="d",
        video_path="unused",
        cart_url="https://example.com/1",
        headless=True,
    )


@pytest.fixture
def user_data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "profile"
    directory.mkdir()
    return directory


# 本机 Chrome 的常见安装位置
_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    str(Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"),
)


def find_local_chrome() -> str | None:
    """探测本机 Chrome，找不到返回 None。"""
    for candidate in _CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


LOCAL_CHROME = find_local_chrome()

# 真正需要启动浏览器的用例：没有本机 Chrome 就跳过，而不是失败。
# 跳过比失败更诚实——缺少运行条件不等于代码有问题。
requires_chrome = pytest.mark.skipif(
    LOCAL_CHROME is None, reason="需要本机安装的 Google Chrome"
)


async def chromium_path() -> str:
    """本机 Chrome 的可执行文件路径。"""
    assert LOCAL_CHROME is not None
    return LOCAL_CHROME


# ======================================================================
# 反向：启动失败
# ======================================================================


async def test_可执行文件不是浏览器时报8(tmp_path: Path, user_data_dir: Path) -> None:
    """路径存在但不是浏览器，启动必然失败，要落到 8 而非兜底的 88。"""
    fake = tmp_path / "not_a_browser.exe"
    fake.write_bytes(b"this is not an executable")

    async with async_playwright() as playwright:
        with pytest.raises(PublishError) as exc_info:
            await launch_context(playwright, make_config(str(fake)), user_data_dir)

    assert exc_info.value.code is ErrorCode.BROWSER_LAUNCH_FAILED


async def test_可执行文件不存在时报8(tmp_path: Path, user_data_dir: Path) -> None:
    """正常流程里这种情况会被配置校验提前拦下（报 4），
    但启动层自己也必须给出确定的错误码，不能依赖上游一定做过校验。
    """
    async with async_playwright() as playwright:
        with pytest.raises(PublishError) as exc_info:
            await launch_context(
                playwright, make_config(str(tmp_path / "不存在.exe")), user_data_dir
            )

    assert exc_info.value.code is ErrorCode.BROWSER_LAUNCH_FAILED


async def test_启动失败的异常携带阶段() -> None:
    """失败必须能定位到阶段，否则上游只知道失败却不知道卡在哪。"""
    async with async_playwright() as playwright:
        with pytest.raises(PublishError) as exc_info:
            await launch_context(
                playwright, make_config("/不存在/chrome.exe"), Path("/tmp")
            )

    assert exc_info.value.stage is not None
    assert exc_info.value.stage.value == "launch"


# ======================================================================
# 正向：启动与关闭
# ======================================================================


@requires_chrome
async def test_能以持久化上下文启动(user_data_dir: Path) -> None:
    exe = await chromium_path()

    async with async_playwright() as playwright:
        context = await launch_context(playwright, make_config(exe), user_data_dir)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("data:text/html,<title>launcher</title>")
            assert await page.title() == "launcher"
        finally:
            await close_context(context)

    # 持久化上下文会把画像数据写进该目录
    assert any(user_data_dir.iterdir()), "用户数据目录未被写入，登录态将无法保留"


@requires_chrome
async def test_重复关闭不抛异常(user_data_dir: Path) -> None:
    """close_context 运行在清理路径上，此处抛错会掩盖真正的失败原因。"""
    exe = await chromium_path()

    async with async_playwright() as playwright:
        context = await launch_context(playwright, make_config(exe), user_data_dir)
        await close_context(context)
        await close_context(context)  # 再关一次，不应抛


# ======================================================================
# 清理保证：异常与取消路径
# ======================================================================


@requires_chrome
async def test_会话内抛异常时仍关闭浏览器(user_data_dir: Path) -> None:
    exe = await chromium_path()
    captured = None

    with pytest.raises(RuntimeError, match="业务异常"):
        async with browser_session(make_config(exe), user_data_dir) as context:
            captured = context
            raise RuntimeError("业务异常")

    assert captured is not None
    # 上下文已关闭时再开页面会失败
    with pytest.raises(Exception):
        await captured.new_page()


@requires_chrome
async def test_会话被取消时仍关闭浏览器(user_data_dir: Path) -> None:
    """竞速中止走的正是这条路径。

    清理若只写在正常返回分支上，哨兵中止后浏览器进程会残留，
    下一次任务将因用户数据目录被占用而启动失败。
    """
    exe = await chromium_path()
    captured: list = []
    closed = asyncio.Event()

    async def session() -> None:
        async with browser_session(make_config(exe), user_data_dir) as context:
            captured.append(context)
            closed.set()
            await asyncio.sleep(60)  # 停在这里等待被取消

    task = asyncio.create_task(session())
    await asyncio.wait_for(closed.wait(), timeout=30)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert captured, "浏览器未成功启动，本条测试没有验证到清理"
    with pytest.raises(Exception):
        await captured[0].new_page()
