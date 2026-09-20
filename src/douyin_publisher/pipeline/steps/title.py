"""步骤：填写标题与话题标签。

编辑器是 Slate.js 的 contenteditable 容器，不是 input，因此：

  · 不能用 `fill()`，只能模拟键盘输入；
  · 清空要靠「全选 + 退格」，直接设置 textContent 不会触发框架的状态更新，
    页面看着变了，提交时却是空的。
"""

from __future__ import annotations

import asyncio

from douyin_publisher.browser.actions import find_usable
from douyin_publisher.browser.selectors import Editor
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.logging import get_logger
from douyin_publisher.pipeline.context import PipelineContext

logger = get_logger("pipeline.title")
# 每个话题标签输入后的停顿（秒）。
# 输入 # 会触发页面的话题联想框，不留时间给它处理，后续字符会被吞掉或错位。
TAG_INPUT_PAUSE = 0.3

# 清空后的短暂停顿，等待框架完成状态同步
CLEAR_PAUSE = 0.2

# 全选编辑器内容的脚本。
# 用 Selection API 而非修改 DOM：Slate 监听的是用户输入事件，
# 直接改 DOM 会造成「视图变了但内部状态没变」的不一致。
_SELECT_ALL_SCRIPT = """
el => {
    el.focus();
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    selection.removeAllRanges();
    selection.addRange(range);
}
"""


async def run(ctx: PipelineContext) -> None:
    """填写标题，并把话题标签逐个输入。

    Raises:
        PublishError: TITLE_INPUT_FAILED —— 找不到编辑器或输入过程出错。
    """
    config = ctx.config
    editor = await find_usable(
        ctx.page, Editor.SLATE_CSS, timeout=ctx.deadline.budget(ctx.config.timeouts.element)
    )
    if editor is None:
        raise PublishError(
            ErrorCode.TITLE_INPUT_FAILED, "未找到标题编辑器（页面结构可能已变更）"
        )

    try:
        await editor.focus()

        # 清空既有内容：页面可能残留上一次的草稿
        await editor.evaluate(_SELECT_ALL_SCRIPT)
        await ctx.page.keyboard.press("Backspace")
        await asyncio.sleep(CLEAR_PAUSE)

        if config.title:
            logger.info(f"[标题] 输入标题：{config.title}")
            await ctx.page.keyboard.type(config.title)
            await asyncio.sleep(TAG_INPUT_PAUSE)

        for tag in config.tags:
            logger.info(f"[标题] 输入话题标签：#{tag}")
            await ctx.page.keyboard.type(f"#{tag}")
            await asyncio.sleep(TAG_INPUT_PAUSE)
            # 空格用于结束当前标签，让页面把它固化成一个话题
            await ctx.page.keyboard.type(" ")
            await asyncio.sleep(TAG_INPUT_PAUSE)

    except asyncio.CancelledError:
        # 哨兵中止流程时会取消到这里，必须原样传播
        raise
    except Exception as exc:
        raise PublishError(
            ErrorCode.TITLE_INPUT_FAILED, f"输入过程出错：{exc}", cause=exc
        ) from exc

    logger.info("[标题] 标题与话题标签填写完成")
