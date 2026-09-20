"""异步等待原语的正反向测试。

这些原语是「事件竞速中止」的地基。其中最容易被写坏、且坏了极难发现的一条是：

    poll_until 会吞掉 probe 抛出的异常（因为元素未就绪而报错是常态），
    但它 **绝不能** 把 CancelledError 一起吞掉。

一旦吞掉，被取消的主流程会继续轮询到超时，「失败即中止」彻底失效，
而功能测试依然全绿——因此这里用一条专门的测试锁死它。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from douyin_publisher.core.waiting import Deadline, poll_until, wait_for_first


# ======================================================================
# Deadline：共享时长预算
# ======================================================================


def test_预算初始值() -> None:
    deadline = Deadline(10)
    assert deadline.remaining == pytest.approx(10, abs=0.1)
    assert not deadline.expired


async def test_预算随时间减少() -> None:
    deadline = Deadline(1)
    await asyncio.sleep(0.3)
    assert deadline.remaining == pytest.approx(0.7, abs=0.15)
    assert deadline.elapsed == pytest.approx(0.3, abs=0.15)


async def test_预算耗尽后归零而非变负() -> None:
    deadline = Deadline(0.2)
    await asyncio.sleep(0.4)
    assert deadline.remaining == 0.0
    assert deadline.expired


def test_申请超过剩余的预算会被削减() -> None:
    """若不削减，各步骤超时累加会远超总上限，整体超时形同虚设。"""
    deadline = Deadline(5)
    assert deadline.budget(3) == pytest.approx(3, abs=0.1)
    assert deadline.budget(100) == pytest.approx(5, abs=0.1)


async def test_耗尽后申请预算得到零() -> None:
    deadline = Deadline(0.1)
    await asyncio.sleep(0.2)
    assert deadline.budget(30) == 0.0


# ======================================================================
# poll_until：轮询等待
# ======================================================================


async def test_轮询命中后立即返回() -> None:
    calls = 0

    async def probe() -> str | None:
        nonlocal calls
        calls += 1
        return "命中" if calls >= 3 else None

    started = time.monotonic()
    result = await poll_until(probe, timeout=5, interval=0.05)
    elapsed = time.monotonic() - started

    assert result == "命中"
    assert calls == 3
    assert elapsed < 1.0, "命中后应立刻返回，不该继续等满超时"


async def test_轮询首轮即命中() -> None:
    async def probe() -> str | None:
        return "立即命中"

    assert await poll_until(probe, timeout=5) == "立即命中"


async def test_轮询超时返回None() -> None:
    async def probe() -> str | None:
        return None

    assert await poll_until(probe, timeout=0.3, interval=0.05) is None


async def test_轮询超时确实按时结束() -> None:
    """防「假绿」：超时参数若未生效，这里会挂到测试整体超时。"""

    async def probe() -> str | None:
        return None

    started = time.monotonic()
    await poll_until(probe, timeout=0.4, interval=0.05)
    elapsed = time.monotonic() - started

    assert 0.3 < elapsed < 1.5, f"超时控制失准，实际耗时 {elapsed:.2f}s"


async def test_轮询吞掉probe的普通异常() -> None:
    """元素处于中间态而报错是常态，不应让整个流程失败。"""
    calls = 0

    async def probe() -> str | None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("元素尚未就绪")
        return "最终命中"

    assert await poll_until(probe, timeout=5, interval=0.05) == "最终命中"


async def test_轮询不吞取消异常() -> None:
    """本文件最重要的一条：取消必须穿透轮询，否则竞速中止彻底失效。"""

    async def probe() -> str | None:
        # 模拟 probe 内部的等待被外部取消
        await asyncio.sleep(10)
        return None

    task = asyncio.create_task(poll_until(probe, timeout=30, interval=0.05))
    await asyncio.sleep(0.1)  # 让任务真正进入 probe
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_轮询在两轮间隔中也能被取消() -> None:
    """取消可能发生在 sleep 期间而非 probe 期间，同样必须生效。"""

    async def probe() -> str | None:
        return None

    task = asyncio.create_task(poll_until(probe, timeout=30, interval=0.5))
    await asyncio.sleep(0.1)  # 此时大概率停在 sleep 上
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# ======================================================================
# wait_for_first：竞速中止的通用形式
# ======================================================================


async def test_取先完成者并取消其余() -> None:
    async def fast() -> str:
        await asyncio.sleep(0.05)
        return "快"

    async def slow() -> str:
        await asyncio.sleep(10)
        return "慢"

    started = time.monotonic()
    result, cancelled = await wait_for_first(fast(), slow())
    elapsed = time.monotonic() - started

    assert result == "快"
    assert len(cancelled) == 1
    assert all(task.cancelled() for task in cancelled), "落败任务必须真的被取消"
    assert elapsed < 2.0, f"应在快者完成时立即返回，实际耗时 {elapsed:.2f}s"


async def test_竞速整体超时返回None() -> None:
    async def slow() -> str:
        await asyncio.sleep(10)
        return "慢"

    result, cancelled = await wait_for_first(slow(), timeout=0.3)

    assert result is None
    assert len(cancelled) == 1


async def test_落败任务被回收不留悬挂() -> None:
    """悬挂任务会在退出时打印 "Task was destroyed" 警告，属于资源泄漏。"""
    started = False

    async def slow() -> str:
        nonlocal started
        started = True
        await asyncio.sleep(10)
        return "慢"

    async def fast() -> str:
        return "快"

    _, cancelled = await wait_for_first(fast(), slow())

    for task in cancelled:
        assert task.done(), "被取消的任务必须已完结，而不是仍在后台挂着"


async def test_外层被取消时内部任务也被取消() -> None:
    """避免主流程被取消后，它派生出的子任务还在后台跑。"""
    inner_cancelled = asyncio.Event()

    async def inner() -> str:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            inner_cancelled.set()
            raise
        return "不会到这里"

    task = asyncio.create_task(wait_for_first(inner()))
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # 给事件循环一个调度周期，让取消真正传播到内部任务
    await asyncio.sleep(0.05)
    assert inner_cancelled.is_set(), "外层取消未能传播到内部任务"
