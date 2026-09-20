"""可取消的异步等待原语。

本模块的所有等待都构建在 `asyncio.sleep` 与 `asyncio.timeout` 之上，
因此每一次等待都是一个 **可取消点**。这正是「事件竞速中止」得以生效的基础：
哨兵命中致命事件后取消主流程，主流程无论卡在哪一次等待上，都会在下一个调度点退出。

强制约束：
    异步路径中 **禁止** 使用 time.sleep。它会钉死整个事件循环，
    使哨兵得不到调度机会，「失败即中止」将彻底失效。
    该约束由 tests/unit/test_no_blocking_sleep.py 强制校验。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

# 轮询类等待的默认间隔。过短会空耗 CPU，过长会拖慢响应，0.25 秒是折中值。
DEFAULT_POLL_INTERVAL = 0.25


class Deadline:
    """总时长预算。

    整个发布流程有一个总超时上限，各步骤从这个共享预算里取用。
    若不做统一预算，各步骤各自设超时，累加起来会远超总上限，
    导致「整体超时」这一兜底形同虚设。

    Example:
        deadline = Deadline(600)
        await something(timeout=deadline.budget(30))  # 最多 30 秒，且不超出总预算
    """

    def __init__(self, total_seconds: float):
        """
        Args:
            total_seconds: 总预算秒数。
        """
        self._total = total_seconds
        self._started = time.monotonic()

    @property
    def elapsed(self) -> float:
        """已用时（秒）。"""
        return time.monotonic() - self._started

    @property
    def remaining(self) -> float:
        """剩余预算（秒），不会小于 0。"""
        return max(0.0, self._total - self.elapsed)

    @property
    def expired(self) -> bool:
        """预算是否已耗尽。"""
        return self.remaining <= 0

    def budget(self, want: float) -> float:
        """在总预算内申请一段时长。

        Args:
            want: 期望的秒数。

        Returns:
            实际可用秒数，即 min(want, remaining)。
        """
        return min(want, self.remaining)

    def __str__(self) -> str:
        return f"Deadline(已用 {self.elapsed:.1f}s / 共 {self._total:.0f}s)"


async def poll_until(
    probe: Callable[[], Awaitable[T | None]],
    *,
    timeout: float,
    interval: float = DEFAULT_POLL_INTERVAL,
) -> T | None:
    """反复调用 probe，直到它返回非 None 的结果或超时。

    用于应对「元素稍后才出现」这类无法用单次等待表达的场景。
    probe 自身抛出的异常会被吞掉并当作「本轮未命中」，
    因为轮询过程中元素处于中间态而报错是常态。

    Args:
        probe: 每轮调用的探测协程。命中时返回结果，未命中返回 None。
        timeout: 超时秒数。
        interval: 两轮之间的间隔秒数。

    Returns:
        命中的结果；超时则返回 None。
    """
    deadline = Deadline(timeout)

    while not deadline.expired:
        try:
            result = await probe()
            if result is not None:
                return result
        except asyncio.CancelledError:
            # 取消必须原样向上传播，绝不能被当成「本轮未命中」吞掉。
            #
            # CancelledError 继承自 BaseException，因此下面的 except Exception
            # 本就抓不到它；这一分支是显式的防御：一旦后人把下面改成
            # except BaseException，取消就会被吞掉，「失败即中止」随之失效。
            # 该退化由 tests/unit/test_waiting.py::test_轮询不吞取消异常 锁死。
            raise
        except Exception:
            # 元素尚未就绪导致的报错属于预期内情况，继续下一轮
            pass

        # 剩余预算不足一个间隔时，按剩余时间睡，避免超出总超时
        await asyncio.sleep(min(interval, deadline.remaining))

    return None


async def wait_for_first(
    *awaitables: Awaitable[T],
    timeout: float | None = None,
) -> tuple[T | None, list[asyncio.Task[T]]]:
    """等待多个可等待对象中最先完成的一个，并取消其余的。

    这是「竞速中止」的通用形式。被取消的任务会被等待回收，
    避免留下悬挂任务（表现为 "Task was destroyed but it is pending" 警告）。

    Args:
        awaitables: 参与竞速的协程，至少一个。
        timeout: 整体超时秒数；None 表示不限时。

    Returns:
        (最先完成者的结果, 被取消的任务列表)。整体超时时结果为 None。
    """
    tasks = [asyncio.ensure_future(item) for item in awaitables]

    try:
        done, pending = await asyncio.wait(
            tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
    except asyncio.CancelledError:
        # 外层取消时，同样要确保内部任务不残留
        for task in tasks:
            task.cancel()
        raise

    cancelled: list[asyncio.Task[T]] = []
    for task in pending:
        task.cancel()
        cancelled.append(task)

    # 等待被取消的任务真正结束，吞掉 CancelledError
    for task in cancelled:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # 任务在取消前恰好因其他异常结束，此处不关心
            pass

    if not done:
        return None, cancelled  # 整体超时，无人完成

    # done 中可能有多个（极罕见的同时完成），取任意一个即可
    winner = next(iter(done))
    return winner.result(), cancelled
