"""步骤：设置自主声明。

交互是三段式：展开下拉 → 选中选项 → 点确定。
「确定」按钮在未选中任何选项时是禁用的，而且禁用状态只体现在类名上——
这正是 `actions.is_usable` 必须同时检查类名的现实来源。
"""

from __future__ import annotations

import asyncio

from douyin_publisher.browser.actions import click_usable
from douyin_publisher.browser.selectors import Declaration
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.logging import get_logger
from douyin_publisher.pipeline.context import PipelineContext

logger = get_logger("pipeline.declaration")
# 选中选项后的停顿（秒），等待「确定」按钮解禁
SELECT_PAUSE = 0.5


async def run(ctx: PipelineContext) -> None:
    """按配置设置自主声明。

    Raises:
        PublishError: DECLARATION_FAILED —— 任一环节的元素缺失或不可用。
    """
    option = ctx.config.effective_self_declaration
    logger.info(f"[声明] 准备设置自主声明：{option}")

    # 一、展开下拉
    if not await click_usable(
        ctx.page,
        Declaration.SELECT_BOX_XPATH,
        timeout=ctx.deadline.budget(ctx.config.timeouts.element),
    ):
        raise PublishError(
            ErrorCode.DECLARATION_FAILED, "未找到自主声明下拉框（页面结构可能已变更）"
        )

    # 二、选中目标选项。
    #     用精确匹配：选项之间存在包含关系的可能性不低，
    #     子串匹配可能选错一个语义完全不同的声明类型。
    if not await click_usable(
        ctx.page,
        Declaration.RADIO_CSS,
        has_text=option,
        exact=True,
        timeout=ctx.deadline.budget(ctx.config.timeouts.element),
    ):
        raise PublishError(
            ErrorCode.DECLARATION_FAILED, f"未找到自主声明选项「{option}」"
        )
    await asyncio.sleep(SELECT_PAUSE)

    # 三、确认。
    #     click_usable 会一直等到按钮真正可用为止——
    #     选中选项后按钮才解禁，这里依赖的正是这个等待语义。
    if not await click_usable(
        ctx.page,
        Declaration.CONFIRM_BUTTON_CSS,
        has_text=Declaration.TEXT_CONFIRM,
        exact=True,
        timeout=ctx.deadline.budget(ctx.config.timeouts.element),
    ):
        raise PublishError(
            ErrorCode.DECLARATION_FAILED, "「确定」按钮未出现或始终处于禁用状态"
        )

    logger.info("[声明] 自主声明设置完成")
