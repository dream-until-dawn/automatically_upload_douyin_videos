"""失败截图的端到端验证：真的截出文件了吗。

单元测试只能验证文件名规则与异常吞噬，证明不了「截图这件事确实发生了」。
这里跑真实流程、造真实失败，再去磁盘上确认文件存在且不是个空壳。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import BrowserContext

from douyin_publisher.config.models import TaskConfig
from douyin_publisher.config.runtime import ScreenshotOptions
from douyin_publisher.core.errors import ErrorCode
from douyin_publisher.core.stages import Stage
from douyin_publisher.pipeline.runner import run_pipeline
from douyin_publisher.pipeline.steps import (
    cart,
    cover,
    declaration,
    publish,
    publish_setting,
    title,
)

from tests.integration.conftest import mock_page_url

TEST_TOTAL_TIMEOUT = 40.0

# PNG 文件头。用它确认截出来的是真图片，而不是一个 0 字节的空壳。
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def fast_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    fast = 0.02
    monkeypatch.setattr(title, "TAG_INPUT_PAUSE", fast)
    monkeypatch.setattr(title, "CLEAR_PAUSE", fast)
    monkeypatch.setattr(publish_setting, "OPTION_PAUSE", fast)
    monkeypatch.setattr(publish_setting, "INITIAL_PAUSE", fast)
    monkeypatch.setattr(declaration, "SELECT_PAUSE", fast)
    monkeypatch.setattr(cart, "STEP_PAUSE", fast)
    monkeypatch.setattr(cart, "EXISTING_PROBE_TIMEOUT", 0.5)
    monkeypatch.setattr(cover, "STEP_PAUSE", fast)
    monkeypatch.setattr(publish, "PRE_CLICK_PAUSE", fast)


def make_config(tmp_path: Path, shot_dir: Path | None, **extra: object) -> TaskConfig:
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake-video")

    payload: dict[str, object] = {
        "exec_path": "unused",
        "user_data_dir": "unused",
        "task_id": "shot-task",
        "douyin_id": "d",
        "video_path": str(video),
        "cart_url": "",
        "title": "截图测试",
        # 元素等待压短，让失败场景尽快到达
        "timeouts": {"element": 3},
    }
    if shot_dir is not None:
        payload["screenshot"] = ScreenshotOptions(dir=str(shot_dir))
    payload.update(extra)
    return TaskConfig(**payload)


async def run_with(config: TaskConfig, browser_context: BrowserContext, **params):
    return await run_pipeline(
        config,
        browser_context,
        total_timeout=TEST_TOTAL_TIMEOUT,
        page_url=mock_page_url(**params),
    )


# ======================================================================
# 正向：失败时确实截出了图
# ======================================================================


async def test_失败时生成截图文件(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    shots = tmp_path / "shots"
    config = make_config(tmp_path, shots)

    result = await run_with(config, browser_context, cover="no_modal")

    assert result.code is ErrorCode.COVER_FAILED
    assert result.screenshot is not None, "失败了却没有截图路径"

    path = Path(result.screenshot)
    assert path.is_file(), f"截图路径存在但文件不在：{path}"
    assert path.read_bytes()[:8] == PNG_MAGIC, "截出来的不是有效的 PNG"
    assert path.stat().st_size > 1000, "截图过小，疑似空白图"


async def test_截图文件名标出任务与失败阶段(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """一堆截图里，不打开就能知道是哪个任务卡在哪一步。"""
    shots = tmp_path / "shots"
    result = await run_with(
        make_config(tmp_path, shots), browser_context, cover="no_modal"
    )

    name = Path(result.screenshot).name
    assert "shot-task" in name
    assert Stage.COVER.value in name


async def test_目录不存在时自动创建(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """上游配一个还不存在的目录是常态，不该因此丢掉现场。"""
    nested = tmp_path / "a" / "b" / "c"
    assert not nested.exists()

    result = await run_with(
        make_config(tmp_path, nested), browser_context, cover="no_modal"
    )

    assert result.screenshot is not None
    assert Path(result.screenshot).is_file()


async def test_哨兵中止时同样留下现场(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """失败有两个来源：步骤抛错、哨兵中止。两者都要能截到。

    截图写在编排层而非各步骤内部，正是为了覆盖后者——
    哨兵中止时主流程是被取消的，根本走不到任何步骤的错误处理分支。
    """
    shots = tmp_path / "shots"
    result = await run_with(
        make_config(tmp_path, shots),
        browser_context,
        upload="failure",
        uploadDelay=200,
    )

    assert result.code is ErrorCode.UPLOAD_FAILED
    assert result.screenshot is not None, "哨兵中止的失败没有截图"
    assert Path(result.screenshot).is_file()


# ======================================================================
# 反向：不该截图的情况
# ======================================================================


async def test_成功时不截图(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """成功路径不需要现场，也不该为此多花时间。"""
    shots = tmp_path / "shots"
    result = await run_with(make_config(tmp_path, shots), browser_context)

    assert result.code is ErrorCode.SUCCESS
    assert result.screenshot is None

    # 目录要么没建，要么是空的
    if shots.exists():
        assert list(shots.iterdir()) == [], "成功却留下了截图"


async def test_显式关闭后不截图(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """反向配对：确认截图确实受开关控制，而不是无条件执行。"""
    shots = tmp_path / "shots"
    config = make_config(
        tmp_path, None, screenshot=ScreenshotOptions(onFailure=False, dir=str(shots))
    )

    result = await run_with(config, browser_context, cover="no_modal")

    assert result.code is ErrorCode.COVER_FAILED, "前提：这个场景本应失败"
    assert result.screenshot is None, "已关闭截图却仍然截了"
    if shots.exists():
        assert list(shots.iterdir()) == []


async def test_截图不改变失败结论(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """截图是辅助信息，不能影响错误码与阶段。"""
    with_shot = await run_with(
        make_config(tmp_path, tmp_path / "s1"), browser_context, cover="no_modal"
    )
    without_shot = await run_with(
        make_config(tmp_path, None, screenshot=ScreenshotOptions(onFailure=False)),
        browser_context,
        cover="no_modal",
    )

    assert with_shot.code is without_shot.code
    assert with_shot.stage is without_shot.stage
    assert with_shot.message == without_shot.message
