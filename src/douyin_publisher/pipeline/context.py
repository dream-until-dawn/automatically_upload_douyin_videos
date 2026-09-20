"""流程上下文与步骤契约。

上下文是步骤之间唯一的数据通道：步骤不持有彼此的引用，也不共享模块级状态，
需要什么都从上下文里取。这样每个步骤都能被单独测试、单独跳过、单独调整顺序。

上下文是 **只读** 的：步骤不向其中写入状态。
这一点是 [ADR-0002](../../../docs/adr/0002-fail-fast-on-cart-failure.md) 的直接结果——
既然任何失败都立即中止，就不存在需要跨步骤传递的「待决错误」，
上下文因此保持纯粹。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from playwright.async_api import Page

from douyin_publisher.config.models import TaskConfig
from douyin_publisher.core.events import EventBus
from douyin_publisher.core.stages import Stage
from douyin_publisher.core.waiting import Deadline


@dataclass(frozen=True, slots=True)
class PipelineContext:
    """一次发布流程的执行上下文。

    Attributes:
        config: 任务配置。
        page: 发布页。
        bus: 页面事件总线，哨兵与等待类步骤都从这里取事件。
        deadline: 整个流程共享的时长预算。各步骤通过 `deadline.budget(n)`
            申请自己的超时，从而保证所有步骤加起来不会超出总上限。
    """

    config: TaskConfig
    page: Page
    bus: EventBus
    deadline: Deadline


# 步骤的统一签名：接收上下文，成功则正常返回，失败则抛 PublishError。
#
# 刻意不设计成「返回错误码」：返回值容易被忽略，而异常无法被无声地丢掉。
StepRunner = Callable[[PipelineContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Step:
    """流程中的一个步骤。

    Attributes:
        stage: 所属阶段，失败时会被填进错误结果的 `stage` 字段。
        title: 中文名称，用于日志。
        run: 步骤本体。
    """

    stage: Stage
    title: str
    run: StepRunner

    def __str__(self) -> str:
        return f"{self.title}({self.stage.value})"
