"""步骤：配置发布设置（发布时间、可见范围、保存权限）。

三组设置都是单选标签，靠文案区分。文案由上游配置直接给出，
未指定的组会被跳过——保留页面默认值，而不是替上游做决定。
"""

from __future__ import annotations

import asyncio
import time

from douyin_publisher.browser.actions import click_usable, fill_usable
from douyin_publisher.browser.selectors import PublishSetting
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.logging import get_logger
from douyin_publisher.pipeline.context import PipelineContext

logger = get_logger("pipeline.setting")

# 单个选项的等待超时（秒）
OPTION_TIMEOUT = 10.0

# 精确匹配的等待超时（秒）。它只是「优先尝试」，取值要短，
# 否则每次回退都要先白等一轮完整超时。
EXACT_MATCH_TIMEOUT = 3.0

# 点击选项后的停顿（秒）。选中「定时发布」会展开时间输入框，
# 不留时间给它渲染，后续填写会找不到目标。
OPTION_PAUSE = 0.5

# 进入本步骤前的等待（秒）。发布设置区域在视频开始上传后才完整渲染。
INITIAL_PAUSE = 1.0


def compute_publish_time(delay_hours: int, *, now: float | None = None) -> str:
    """计算定时发布的目标时间字符串。

    Args:
        delay_hours: 延后小时数，调用方需保证已归一化。
        now: 当前时间戳，仅供测试注入固定值。

    Returns:
        形如 "2026-09-21 14:30" 的时间字符串。
    """
    base = time.time() if now is None else now
    target = base + delay_hours * 3600
    return time.strftime(PublishSetting.SCHEDULE_TIME_FORMAT, time.localtime(target))


async def _select_option(ctx: PipelineContext, label: str, group: str) -> None:
    """选中一个单选项。

    ## 为什么先精确、再回退到子串

    保存权限的两个选项是「允许」与「不允许」，后者包含前者。
    若直接用子串匹配，选「允许」时两个选项都会命中，最终选中哪一个
    取决于它们在 DOM 里的先后——这是一个会静默选错、且随页面改版
    翻盘的隐患，而任务仍会「成功」发布，只是权限设反了。

    因此先做精确匹配；只有精确匹配找不到时（选项文案带附加说明，
    例如「仅自己可见（他人不可见）」），才退回子串匹配。

    Raises:
        PublishError: PUBLISH_SETTING_FAILED。
    """
    logger.info(f"[设置] {group}：{label}")

    clicked = await click_usable(
        ctx.page,
        PublishSetting.RADIO_CSS,
        has_text=label,
        exact=True,
        timeout=ctx.deadline.budget(EXACT_MATCH_TIMEOUT),
    )
    if not clicked:
        logger.debug(f"[设置] 精确匹配未命中「{label}」，回退到子串匹配")
        clicked = await click_usable(
            ctx.page,
            PublishSetting.RADIO_CSS,
            has_text=label,
            exact=False,
            timeout=ctx.deadline.budget(OPTION_TIMEOUT),
        )

    if not clicked:
        raise PublishError(
            ErrorCode.PUBLISH_SETTING_FAILED,
            f"未找到「{group}」的「{label}」选项",
        )
    await asyncio.sleep(OPTION_PAUSE)


async def run(ctx: PipelineContext) -> None:
    """按配置逐项设置发布选项。

    未在配置中指定的项会被跳过，保留页面默认值。

    Raises:
        PublishError: PUBLISH_SETTING_FAILED。
    """
    config = ctx.config

    # 该区域在视频开始上传后才完整渲染，过早操作会扑空
    await asyncio.sleep(INITIAL_PAUSE)

    # 一、发布时间
    if config.publish_time_mode:
        await _select_option(ctx, config.publish_time_mode, "发布时间")

        if config.is_scheduled:
            target = compute_publish_time(config.publish_delay_hours)
            logger.info(f"[设置] 定时发布时间：{target}")

            filled = await fill_usable(
                ctx.page,
                PublishSetting.SCHEDULE_INPUT_CSS,
                target,
                timeout=ctx.deadline.budget(OPTION_TIMEOUT),
            )
            if not filled:
                raise PublishError(
                    ErrorCode.PUBLISH_SETTING_FAILED, "未找到定时发布的时间输入框"
                )
            await asyncio.sleep(OPTION_PAUSE)

    # 二、可见范围
    if config.who_can_see:
        await _select_option(ctx, config.who_can_see, "谁可以看")

    # 三、保存权限
    if config.save_permission:
        await _select_option(ctx, config.save_permission, "保存权限")

    logger.info("[设置] 发布设置配置完成")
