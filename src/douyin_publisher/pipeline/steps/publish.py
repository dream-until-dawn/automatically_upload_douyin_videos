"""步骤：点击发布按钮，以及等待发布结果。

两件事拆成两个步骤：点击是瞬时动作，等待结果可能耗时数十秒。
拆开后，「点不到按钮」（17，页面问题）与「发布没有结论」（12，平台问题）
各自有清晰的归属，不会混成一个含糊的错误。
"""

from __future__ import annotations

import asyncio

from douyin_publisher.browser.actions import click_usable
from douyin_publisher.browser.selectors import Publish
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.events import EventType
from douyin_publisher.core.logging import get_logger
from douyin_publisher.pipeline.context import PipelineContext

logger = get_logger("pipeline.publish")

# 发布按钮的等待超时（秒）
BUTTON_TIMEOUT = 15.0

# 点击发布前的停顿（秒）。
# 前一步刚关闭封面弹窗，页面需要时间收起遮罩；遮罩未消失时点击会被拦截。
PRE_CLICK_PAUSE = 1.5

# 等待发布结果的超时（秒）
RESULT_TIMEOUT = 120.0


async def run_click(ctx: PipelineContext) -> None:
    """点击发布按钮。

    Raises:
        PublishError: PUBLISH_FAILED —— 找不到可用的发布按钮。
    """
    await asyncio.sleep(PRE_CLICK_PAUSE)
    logger.info("[发布] 点击发布按钮")

    if not await click_usable(
        ctx.page,
        Publish.BUTTON_CSS,
        has_text=Publish.TEXT_PUBLISH,
        exact=True,
        timeout=ctx.deadline.budget(BUTTON_TIMEOUT),
    ):
        raise PublishError(
            ErrorCode.PUBLISH_FAILED, "未找到可用的「发布」按钮"
        )


async def run_await_result(ctx: PipelineContext) -> None:
    """等待发布结果。

    只等待「发布成功」。「发布失败」与「服务异常」都是致命事件，
    由哨兵负责中止流程——它们会带着更准确的错误码（17 / 13）直接结束任务。

    Raises:
        PublishError: PIPELINE_TIMEOUT —— 超时仍未收到任何发布结论。
    """
    budget = ctx.deadline.budget(RESULT_TIMEOUT)
    logger.info(f"[发布] 等待发布结果（最多 {budget:.0f}s）")

    with ctx.bus.subscribe() as subscription:
        event = await subscription.wait_for(
            EventType.PUBLISH_SUCCESS, timeout=budget
        )

    if event is None:
        raise PublishError(
            ErrorCode.PIPELINE_TIMEOUT,
            f"点击发布后等待 {budget:.0f}s 仍未收到结论",
        )

    logger.info(f"[发布] 发布成功：{event.text}")
