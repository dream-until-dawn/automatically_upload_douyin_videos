"""步骤：设置视频封面。

交互是三段式：点击封面入口 → 选择横版封面 → 完成。

必须放在「等待上传完成」之后：封面候选帧是从视频里抽取的，
上传没完成时弹窗里没有可选帧，「设置横封面」按钮会一直是禁用态。
"""

from __future__ import annotations

import asyncio

from douyin_publisher.browser.actions import click_usable, find_usable
from douyin_publisher.browser.selectors import Cover
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.logging import get_logger
from douyin_publisher.pipeline.context import PipelineContext

logger = get_logger("pipeline.cover")

# 封面入口与弹窗的等待超时（秒）
ELEMENT_TIMEOUT = 10.0

# 等待「设置横封面」可用的超时（秒）。
# 它要等页面抽完候选帧才解禁，比一般元素慢得多。
FRAME_READY_TIMEOUT = 60.0

# 各环节之间的停顿（秒）
STEP_PAUSE = 0.5


async def run(ctx: PipelineContext) -> None:
    """打开封面设置弹窗，选用横版封面并保存。

    Raises:
        PublishError: COVER_FAILED —— 任一环节的元素缺失或始终不可用。
    """
    logger.info("[封面] 开始设置封面")

    # 一、打开封面弹窗
    if not await click_usable(
        ctx.page, Cover.ENTRY_XPATH, timeout=ctx.deadline.budget(ELEMENT_TIMEOUT)
    ):
        raise PublishError(
            ErrorCode.COVER_FAILED, "未找到「选择封面」入口（页面结构可能已变更）"
        )

    if await find_usable(
        ctx.page, Cover.MODAL_CSS, timeout=ctx.deadline.budget(ELEMENT_TIMEOUT)
    ) is None:
        raise PublishError(ErrorCode.COVER_FAILED, "封面设置弹窗未出现")

    # 二、选用横版封面。等待时间给得长，因为要等候选帧抽取完成。
    if not await click_usable(
        ctx.page,
        Cover.BUTTON_CSS,
        has_text=Cover.TEXT_SET_HORIZONTAL,
        exact=True,
        timeout=ctx.deadline.budget(FRAME_READY_TIMEOUT),
    ):
        raise PublishError(
            ErrorCode.COVER_FAILED,
            "「设置横封面」按钮未出现或始终处于禁用状态",
        )
    await asyncio.sleep(STEP_PAUSE)

    # 三、保存
    if not await click_usable(
        ctx.page,
        Cover.BUTTON_CSS,
        has_text=Cover.TEXT_DONE,
        exact=True,
        timeout=ctx.deadline.budget(ELEMENT_TIMEOUT),
    ):
        raise PublishError(ErrorCode.COVER_FAILED, "未找到封面设置的「完成」按钮")

    logger.info("[封面] 封面设置完成")
