"""轻提示监听器的集成测试：真实 DOM、真实 MutationObserver。

单元测试只能验证「文本 → 事件类型」的翻译，翻译写对了不代表提示能被抓到。
真正容易失败的是采集环节：节点插入时机、文本延迟渲染、提示自动消失、
重复注入导致重复上报——这些都只有在真实浏览器里才会暴露。
"""

from __future__ import annotations

import asyncio
import time

import pytest
from playwright.async_api import Page

from douyin_publisher.browser.toast import install_toast_listener
from douyin_publisher.core.events import EventBus, EventType

from tests.integration.conftest import mock_page_url


async def setup_page(page: Page, **params: str | int) -> EventBus:
    """打开模拟页并安装监听器，返回接收事件的总线。"""
    await page.goto(mock_page_url(**params))
    bus = EventBus()
    await install_toast_listener(page, bus)
    return bus


# ======================================================================
# 正向：提示能被采集到
# ======================================================================


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("视频上传成功", EventType.UPLOAD_SUCCESS),
        ("视频上传失败，请稍后重试", EventType.UPLOAD_FAILURE),
        ("作品发布成功", EventType.PUBLISH_SUCCESS),
        ("发布失败，请检查内容", EventType.PUBLISH_FAILURE),
        ("服务开小差了", EventType.SERVICE_ERROR),
        ("该商品暂不支持在直播/短视频推广", EventType.PRODUCT_NOT_SUPPORTED),
    ],
)
async def test_捕获各类提示(page: Page, text: str, expected: EventType) -> None:
    bus = await setup_page(page)
    sub = bus.subscribe()

    await page.evaluate("(t) => window.__showToast(t)", text)
    event = await sub.wait_for(expected, timeout=5)

    assert event is not None, f"未能捕获提示：{text}"
    assert event.text == text, "提示原文应被原样保留，便于排障"


async def test_文本延迟渲染仍能捕获(page: Page) -> None:
    """模拟页刻意让文本延迟 50ms 才写入节点。

    若监听器在节点插入的瞬间就读取 textContent，会读到空串并放弃，
    导致提示被整条漏掉——这正是重试机制存在的理由。
    """
    bus = await setup_page(page)
    sub = bus.subscribe()

    await page.evaluate("() => window.__showToast('视频上传成功')")
    assert await sub.wait_for(EventType.UPLOAD_SUCCESS, timeout=5) is not None


async def test_提示自动消失前能被捕获(page: Page) -> None:
    """提示 3 秒后自毁。事件驱动的采集不存在采样间隔，不会错过。"""
    bus = await setup_page(page)
    sub = bus.subscribe()

    await page.evaluate("() => window.__showToast('作品发布成功')")
    # 故意等到提示已经从 DOM 上消失之后再去读事件
    await asyncio.sleep(3.5)

    assert await sub.wait_for(EventType.PUBLISH_SUCCESS, timeout=1) is not None, (
        "提示消失后事件也没了，说明采集依赖了 DOM 的当前状态"
    )


async def test_注入前已存在的提示也能被捕获(page: Page) -> None:
    """页面可能在监听器装上之前就弹出了提示，漏掉会白等整个超时窗口。"""
    await page.goto(mock_page_url())
    # 先弹提示，后装监听器
    await page.evaluate("() => window.__showToast('视频上传成功')")
    await asyncio.sleep(0.2)

    bus = EventBus()
    await install_toast_listener(page, bus)
    sub = bus.subscribe()

    assert await sub.wait_for(EventType.UPLOAD_SUCCESS, timeout=5) is not None


async def test_连续多条提示都被捕获(page: Page) -> None:
    bus = await setup_page(page)

    await page.evaluate("() => window.__showToast('视频上传成功')")
    await asyncio.sleep(0.2)
    await page.evaluate("() => window.__showToast('作品发布成功')")
    await asyncio.sleep(0.5)

    captured = {event.type for event in bus.history}
    assert EventType.UPLOAD_SUCCESS in captured
    assert EventType.PUBLISH_SUCCESS in captured


# ======================================================================
# 反向：不该产生事件的情况
# ======================================================================


@pytest.mark.parametrize(
    "text", ["已保存草稿", "请输入标题", "视频正在上传中", "链接复制成功"]
)
async def test_无关提示不产生事件(page: Page, text: str) -> None:
    """页面上大量提示与流程无关，误判会让任务被错误中止。"""
    bus = await setup_page(page)

    await page.evaluate("(t) => window.__showToast(t)", text)
    await asyncio.sleep(0.5)

    assert bus.history == (), f"无关提示「{text}」被误判为事件：{bus.history}"


async def test_同一条提示不重复上报(page: Page) -> None:
    """节点的多次 DOM 变动会反复触发观察器，去重失效会导致事件重复。"""
    bus = await setup_page(page)

    await page.evaluate("() => window.__showToast('视频上传成功')")
    await asyncio.sleep(0.8)

    upload_events = [e for e in bus.history if e.type is EventType.UPLOAD_SUCCESS]
    assert len(upload_events) == 1, f"同一条提示被上报了 {len(upload_events)} 次"


async def test_重复安装监听器不导致重复上报(page: Page) -> None:
    """init script 与一次性 evaluate 都会执行，幂等保护失效就会双份上报。"""
    bus = await setup_page(page)
    await install_toast_listener(page, bus)  # 再装一次

    await page.evaluate("() => window.__showToast('视频上传成功')")
    await asyncio.sleep(0.8)

    assert len(bus.history) == 1, f"重复安装导致事件重复：{bus.history}"


# ======================================================================
# 与真实交互结合：失败即中止的实证
# ======================================================================


async def test_上传失败场景能秒级感知(page: Page, tmp_path) -> None:
    """投递文件后页面弹出上传失败，事件必须在秒级到达。

    这条测试用耗时断言锁死本项目的核心承诺：
    若采集退化成轮询或事件被吞，耗时会显著上升。
    """
    bus = await setup_page(page, upload="failure", uploadDelay=200)
    sub = bus.subscribe()

    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake")

    started = time.monotonic()
    await page.locator("input[name='upload-btn']").set_input_files(str(video))
    event = await sub.wait_for(EventType.UPLOAD_FAILURE, timeout=10)
    elapsed = time.monotonic() - started

    assert event is not None, "未能感知到上传失败"
    assert elapsed < 3.0, f"感知上传失败耗时 {elapsed:.2f}s，远超预期"


async def test_静默场景下确实收不到上传结论(page: Page, tmp_path) -> None:
    """反向：验证 upload_silent 场景真的不给结论。

    若这个场景其实会弹提示，那么「上传超时」的反向测试就测不到超时分支，
    会以「恰好成功」的方式假绿。
    """
    bus = await setup_page(page, upload="silent")
    sub = bus.subscribe()

    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake")
    await page.locator("input[name='upload-btn']").set_input_files(str(video))

    event = await sub.wait_for(
        EventType.UPLOAD_SUCCESS, EventType.UPLOAD_FAILURE, timeout=1.5
    )
    assert event is None, f"静默场景不应给出任何上传结论，却收到了 {event}"
