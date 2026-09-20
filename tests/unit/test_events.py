"""事件总线的正反向测试。

重点验证两个容易写错、且写错后果严重的设计点：

  1. **广播**：哨兵与主流程必须各自收到全部事件，谁都不能把事件从对方眼前拿走。
  2. **历史回放**：主流程往往在事件已经发生之后才开始等待，晚订阅不能漏事件。

这两点一旦退化，表现出来的症状是「偶发卡死到超时」，极难排查，
因此必须在单元层面就锁死。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from douyin_publisher.core.errors import ErrorCode
from douyin_publisher.core.events import (
    FATAL_EVENTS,
    EventBus,
    EventType,
    PageEvent,
)


def make_event(event_type: EventType, text: str = "提示文本") -> PageEvent:
    return PageEvent(type=event_type, text=text)


# ======================================================================
# 事件自身的语义
# ======================================================================


@pytest.mark.parametrize(
    ("event_type", "expected_code"),
    [
        (EventType.UPLOAD_FAILURE, ErrorCode.UPLOAD_FAILED),
        (EventType.SERVICE_ERROR, ErrorCode.SERVICE_ERROR),
        (EventType.PRODUCT_NOT_SUPPORTED, ErrorCode.PRODUCT_NOT_SUPPORTED),
        (EventType.PUBLISH_FAILURE, ErrorCode.PUBLISH_FAILED),
    ],
)
def test_致命事件映射到正确的错误码(
    event_type: EventType, expected_code: ErrorCode
) -> None:
    event = make_event(event_type)
    assert event.is_fatal
    assert event.error_code is expected_code


@pytest.mark.parametrize(
    "event_type", [EventType.UPLOAD_SUCCESS, EventType.PUBLISH_SUCCESS]
)
def test_进度事件不是致命事件(event_type: EventType) -> None:
    """把成功事件误标为致命，会让正常任务直接失败——反向锁死。"""
    event = make_event(event_type)
    assert not event.is_fatal
    assert event.error_code is None


def test_致命事件表不包含成功类事件() -> None:
    assert EventType.UPLOAD_SUCCESS not in FATAL_EVENTS
    assert EventType.PUBLISH_SUCCESS not in FATAL_EVENTS


# ======================================================================
# 广播语义
# ======================================================================


async def test_多个订阅者各自收到同一条事件() -> None:
    """核心反向场景：若退化成单一队列，第二个订阅者会永远等不到事件。"""
    bus = EventBus()
    sub_a = bus.subscribe()
    sub_b = bus.subscribe()

    bus.publish(make_event(EventType.UPLOAD_SUCCESS))

    got_a = await asyncio.wait_for(sub_a.next(), timeout=1)
    got_b = await asyncio.wait_for(sub_b.next(), timeout=1)

    assert got_a.type is EventType.UPLOAD_SUCCESS
    assert got_b.type is EventType.UPLOAD_SUCCESS


async def test_订阅者之间互不偷取事件() -> None:
    """模拟哨兵与主流程同时在线的真实情形。"""
    bus = EventBus()
    sentinel = bus.subscribe()
    pipeline = bus.subscribe()

    bus.publish(make_event(EventType.UPLOAD_SUCCESS, "上传成功"))
    bus.publish(make_event(EventType.SERVICE_ERROR, "服务异常"))

    # 哨兵关心致命事件
    fatal = await asyncio.wait_for(
        sentinel.wait_for(*FATAL_EVENTS.keys(), timeout=1), timeout=2
    )
    # 主流程关心上传成功——即便哨兵已经消费过，主流程也必须拿得到
    success = await asyncio.wait_for(
        pipeline.wait_for(EventType.UPLOAD_SUCCESS, timeout=1), timeout=2
    )

    assert fatal is not None and fatal.type is EventType.SERVICE_ERROR
    assert success is not None and success.type is EventType.UPLOAD_SUCCESS


async def test_晚订阅者能补看历史事件() -> None:
    """视频很小时「上传成功」可能早于主流程开始等待，不回放就会卡到超时。"""
    bus = EventBus()
    bus.publish(make_event(EventType.UPLOAD_SUCCESS, "上传成功"))

    late = bus.subscribe()  # 事件发生之后才订阅
    got = await asyncio.wait_for(late.wait_for(EventType.UPLOAD_SUCCESS, timeout=1), timeout=2)

    assert got is not None, "晚订阅者漏掉了已发生的事件"


async def test_历史回放保持原有顺序() -> None:
    bus = EventBus()
    bus.publish(make_event(EventType.UPLOAD_SUCCESS, "第一条"))
    bus.publish(make_event(EventType.SERVICE_ERROR, "第二条"))

    late = bus.subscribe()
    first = await asyncio.wait_for(late.next(), timeout=1)
    second = await asyncio.wait_for(late.next(), timeout=1)

    assert (first.text, second.text) == ("第一条", "第二条")


async def test_关闭回放后晚订阅者收不到历史() -> None:
    """反向验证回放开关确实生效，而不是恒为开。"""
    bus = EventBus(replay_history=False)
    bus.publish(make_event(EventType.UPLOAD_SUCCESS))

    late = bus.subscribe()
    got = await late.wait_for(EventType.UPLOAD_SUCCESS, timeout=0.2)

    assert got is None


# ======================================================================
# 等待语义
# ======================================================================


async def test_等待时忽略不关心的事件() -> None:
    bus = EventBus()
    sub = bus.subscribe()

    bus.publish(make_event(EventType.UPLOAD_SUCCESS, "无关事件"))
    bus.publish(make_event(EventType.PUBLISH_SUCCESS, "目标事件"))

    got = await asyncio.wait_for(sub.wait_for(EventType.PUBLISH_SUCCESS, timeout=1), timeout=2)
    assert got is not None and got.text == "目标事件"


async def test_等待超时返回None而非抛异常() -> None:
    """超时是预期内的流程分支，由调用方翻译成恰当的错误码。"""
    bus = EventBus()
    sub = bus.subscribe()

    got = await sub.wait_for(EventType.UPLOAD_SUCCESS, timeout=0.2)
    assert got is None


async def test_等待超时确实按时返回() -> None:
    """防「假绿」：若超时参数未生效，这里会挂到测试整体超时。"""
    bus = EventBus()
    sub = bus.subscribe()

    started = time.monotonic()
    await sub.wait_for(EventType.UPLOAD_SUCCESS, timeout=0.3)
    elapsed = time.monotonic() - started

    assert 0.2 < elapsed < 1.5, f"超时控制失准，实际耗时 {elapsed:.2f}s"


async def test_等待中途到达的事件() -> None:
    """事件在等待开始之后才发生，属于最常见的真实时序。"""
    bus = EventBus()
    sub = bus.subscribe()

    async def publish_later() -> None:
        await asyncio.sleep(0.1)
        bus.publish(make_event(EventType.UPLOAD_SUCCESS))

    asyncio.create_task(publish_later())
    got = await asyncio.wait_for(sub.wait_for(EventType.UPLOAD_SUCCESS, timeout=2), timeout=3)

    assert got is not None


async def test_等待可被取消() -> None:
    """哨兵长期阻塞在等待上，主流程成功时必须能把它取消掉。"""
    bus = EventBus()
    sub = bus.subscribe()

    task = asyncio.create_task(sub.wait_for(EventType.UPLOAD_SUCCESS))
    await asyncio.sleep(0.05)  # 让任务真正进入等待
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# ======================================================================
# 订阅生命周期
# ======================================================================


async def test_退订后不再收到事件() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    assert bus.subscriber_count == 1

    sub.close()
    assert bus.subscriber_count == 0

    bus.publish(make_event(EventType.UPLOAD_SUCCESS))
    # 已退订的订阅者不应再被投递，其队列保持为空
    got = await sub.wait_for(EventType.UPLOAD_SUCCESS, timeout=0.2)
    assert got is None


async def test_重复退订是安全的() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    sub.close()
    sub.close()  # 不应抛异常
    assert bus.subscriber_count == 0


async def test_上下文管理器自动退订() -> None:
    bus = EventBus()
    with bus.subscribe():
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0, "离开作用域后订阅必须被释放，否则事件会持续堆积"


def test_历史记录只读() -> None:
    """history 返回元组，外部拿到后无法篡改总线内部状态。"""
    bus = EventBus()
    bus.publish(make_event(EventType.UPLOAD_SUCCESS))

    assert isinstance(bus.history, tuple)
    assert len(bus.history) == 1
