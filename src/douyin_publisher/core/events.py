"""页面事件类型与异步事件总线。

事件的来源是页面弹出的轻提示（toast）：浏览器端的 DOM 观察器捕获文本后回调进
Python，由 browser/toast.py 翻译成带语义的事件类型，再投递到这里的总线。

本模块只负责「分发」，不含任何业务判断，因此可以脱离浏览器独立测试。

## 为什么需要广播而不是单一队列

总线有两类消费者，且二者同时存在：

  · **哨兵任务** 从流程一开始就持续监听致命事件；
  · **主流程** 在特定步骤等待特定的进度事件（如「上传成功」）。

若共用一个队列，先取到事件的一方会把它从队列里拿走，另一方永远看不到——
哨兵可能吞掉主流程要等的「上传成功」，主流程也可能吞掉哨兵要抓的「上传失败」。
因此总线采用 **广播**：每个订阅者持有独立队列，各自收到全部事件。

## 为什么订阅者要补看历史

主流程往往在事件已经发生之后才开始等待。例如视频体积很小，「上传成功」在第 3 步
就弹了出来，而主流程走到第 6 步才去等它——此时事件早已广播完毕。

因此订阅时会把已发生的事件按原序回放进新订阅者的队列，确保「晚订阅也不漏事件」。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from types import TracebackType

from douyin_publisher.core.errors import ErrorCode


class EventType(str, Enum):
    """页面事件的语义类型。"""

    UPLOAD_SUCCESS = "upload_success"  # 视频上传成功
    UPLOAD_FAILURE = "upload_failure"  # 视频上传失败
    PUBLISH_SUCCESS = "publish_success"  # 发布成功
    PUBLISH_FAILURE = "publish_failure"  # 发布失败
    SERVICE_ERROR = "service_error"  # 服务异常
    PRODUCT_NOT_SUPPORTED = "product_not_supported"  # 商品不支持推广


# 致命事件表：一旦出现，任务结局即已确定，哨兵应立刻中止全流程。
#
# 这些事件的共同点是「在任何阶段出现都意味着终局」，因此无需判断当前步骤。
# 「上传成功 / 发布成功」属于进度事件，由主流程按需等待，不在此表中。
FATAL_EVENTS: dict[EventType, ErrorCode] = {
    EventType.UPLOAD_FAILURE: ErrorCode.UPLOAD_FAILED,
    EventType.SERVICE_ERROR: ErrorCode.SERVICE_ERROR,
    EventType.PRODUCT_NOT_SUPPORTED: ErrorCode.PRODUCT_NOT_SUPPORTED,
    EventType.PUBLISH_FAILURE: ErrorCode.PUBLISH_FAILED,
}


@dataclass(frozen=True, slots=True)
class PageEvent:
    """一条页面事件。

    Attributes:
        type: 事件语义类型。
        text: 触发该事件的原始提示文本，用于日志与排障。
        at: 事件产生时刻（单调时钟秒数），用于耗时分析。
    """

    type: EventType
    text: str
    at: float = field(default_factory=time.monotonic)

    @property
    def is_fatal(self) -> bool:
        """该事件是否为致命事件。"""
        return self.type in FATAL_EVENTS

    @property
    def error_code(self) -> ErrorCode | None:
        """致命事件对应的错误码；非致命事件返回 None。"""
        return FATAL_EVENTS.get(self.type)

    def __str__(self) -> str:
        return f"{self.type.value}({self.text})"


class EventSubscription:
    """事件总线的一个独立订阅者。

    每个订阅者拥有自己的队列，互不影响。用完应调用 close()，
    或直接以上下文管理器方式使用以确保自动退订。
    """

    def __init__(self, bus: EventBus, queue: asyncio.Queue[PageEvent]):
        self._bus = bus
        self._queue = queue
        self._closed = False

    async def next(self) -> PageEvent:
        """取下一条事件，没有则一直等待。

        这是一个可取消点：哨兵绝大多数时间都阻塞在这里，
        主流程结束时取消哨兵即在此处生效。
        """
        return await self._queue.get()

    async def wait_for(
        self,
        *types: EventType,
        timeout: float | None = None,
    ) -> PageEvent | None:
        """等待指定类型中的任意一个事件。

        不匹配的事件会被丢弃（本订阅者视角），不会阻塞后续匹配。

        Args:
            types: 关心的事件类型，至少一个。
            timeout: 超时秒数，None 表示不限时。

        Returns:
            命中的事件；超时则返回 None，由调用方决定对应的错误码。
        """
        wanted = set(types)

        async def _loop() -> PageEvent:
            while True:
                event = await self.next()
                if event.type in wanted:
                    return event

        if timeout is None:
            return await _loop()
        try:
            async with asyncio.timeout(timeout):
                return await _loop()
        except TimeoutError:
            # 超时不是异常情况，交由调用方翻译为恰当的错误码
            return None

    def close(self) -> None:
        """退订。重复调用是安全的。"""
        if not self._closed:
            self._bus._unsubscribe(self._queue)
            self._closed = True

    def __enter__(self) -> EventSubscription:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class EventBus:
    """广播式异步事件总线。

    发布端是同步方法，因为它会被 Playwright 的页面回调直接调用；
    消费端是异步的，供哨兵与主流程使用。
    """

    def __init__(self, replay_history: bool = True):
        """
        Args:
            replay_history: 新订阅者是否回放已发生的事件。默认开启，
                用于解决「主流程晚订阅漏掉早到事件」的问题。
        """
        self._subscribers: list[asyncio.Queue[PageEvent]] = []
        self._history: list[PageEvent] = []
        self._replay_history = replay_history

    def publish(self, event: PageEvent) -> None:
        """广播一条事件给所有订阅者。

        使用 put_nowait：队列无上限，不会阻塞，因此可以安全地在同步回调中调用。
        """
        self._history.append(event)
        for queue in self._subscribers:
            queue.put_nowait(event)

    def subscribe(self) -> EventSubscription:
        """创建一个新订阅者。

        若开启了历史回放，已发生的事件会按原序预先灌入新订阅者的队列。
        """
        queue: asyncio.Queue[PageEvent] = asyncio.Queue()
        if self._replay_history:
            for event in self._history:
                queue.put_nowait(event)
        self._subscribers.append(queue)
        return EventSubscription(self, queue)

    def _unsubscribe(self, queue: asyncio.Queue[PageEvent]) -> None:
        """移除订阅者队列。由 EventSubscription.close() 调用。"""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    @property
    def history(self) -> tuple[PageEvent, ...]:
        """已广播过的全部事件，按发生顺序。只读，用于日志与测试断言。"""
        return tuple(self._history)

    @property
    def subscriber_count(self) -> int:
        """当前订阅者数量，用于测试断言订阅确实被释放。"""
        return len(self._subscribers)
