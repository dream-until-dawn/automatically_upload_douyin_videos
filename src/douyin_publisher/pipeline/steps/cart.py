"""步骤：挂载购物车（关联商品）。

这是流程中环节最多的一步：展开下拉 → 选类型 → 填链接 → 添加 → 读弹窗
→ 填短标题 → 完成编辑 → 校验卡片。

## 失败即中止

按 ADR-0002，任何挂车失败都立即抛出异常终止流程，不继续等待上传。
挂车是业务必须项，商品没挂上则视频发出去也没有价值，
继续等待只会白白占用调度窗口。

三种失败形态保留各自独立的错误码，因为它们指向不同的责任方：

  · 21 挂车上限  —— 账号侧，需要先下掉别的商品
  · 23 商品下架  —— 商品侧，需要换一个商品
  · 14 一般性失败 —— 页面侧，多半是改版，需要升级本程序
"""

from __future__ import annotations

import asyncio

from douyin_publisher.browser.actions import (
    click_usable,
    fill_usable,
    find_usable,
    read_texts,
)
from douyin_publisher.browser.selectors import Cart
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.logging import get_logger
from douyin_publisher.core.waiting import poll_until
from douyin_publisher.pipeline.context import PipelineContext

logger = get_logger("pipeline.cart")
# 探测「是否已存在商品」的超时（秒）。取值很短：绝大多数情况下没有残留商品，
# 等久了纯属浪费——这是一次「有则处理、无则跳过」的探测，不是必须成立的前提。
EXISTING_PROBE_TIMEOUT = 2.0
# 各环节之间的停顿（秒）
STEP_PAUSE = 0.5


async def _remove_existing_product(ctx: PipelineContext) -> None:
    """移除页面上可能残留的已挂商品。

    这是一次尽力而为的清理：没有残留是常态，因此探测超时取得很短，
    且任何环节找不到元素都直接返回，不视为失败。
    """
    removed = await click_usable(
        ctx.page,
        Cart.REMOVE_BUTTON_CSS,
        has_text=Cart.TEXT_REMOVE,
        exact=True,
        timeout=EXISTING_PROBE_TIMEOUT,
    )
    if not removed:
        return

    logger.info("[挂车] 检测到已挂商品，正在移除")
    await asyncio.sleep(STEP_PAUSE)
    await click_usable(
        ctx.page,
        Cart.REMOVE_BUTTON_CSS,
        has_text=Cart.TEXT_CONFIRM,
        exact=True,
        timeout=EXISTING_PROBE_TIMEOUT,
    )
    await asyncio.sleep(STEP_PAUSE)


async def _open_cart_dropdown(ctx: PipelineContext) -> None:
    """展开扩展信息下拉并选中「购物车」。"""
    section = await find_usable(
        ctx.page, Cart.SECTION_XPATH, timeout=ctx.deadline.budget(ctx.config.timeouts.element)
    )
    if section is None:
        raise PublishError(
            ErrorCode.CART_ATTACH_FAILED, "未找到扩展信息区域（页面结构可能已变更）"
        )

    dropdown = section.locator(Cart.DROPDOWN_CSS).first
    try:
        await dropdown.click()
    except Exception as exc:
        raise PublishError(
            ErrorCode.CART_ATTACH_FAILED, f"展开扩展信息下拉失败：{exc}", cause=exc
        ) from exc

    if not await click_usable(
        ctx.page,
        Cart.OPTION_CSS,
        has_text=Cart.TEXT_OPTION,
        exact=True,
        timeout=ctx.deadline.budget(ctx.config.timeouts.element),
    ):
        raise PublishError(ErrorCode.CART_ATTACH_FAILED, "未找到「购物车」下拉选项")

    await asyncio.sleep(STEP_PAUSE)


async def _submit_product_link(ctx: PipelineContext) -> None:
    """填入商品链接并提交。"""
    logger.info(f"[挂车] 填入商品链接：{ctx.config.cart_url}")

    if not await fill_usable(
        ctx.page,
        Cart.LINK_INPUT_CSS,
        ctx.config.cart_url,
        timeout=ctx.deadline.budget(ctx.config.timeouts.element),
    ):
        raise PublishError(ErrorCode.CART_ATTACH_FAILED, "未找到商品链接输入框")

    if not await click_usable(
        ctx.page,
        Cart.ADD_LINK_BUTTON_CSS,
        has_text=Cart.TEXT_ADD_LINK,
        exact=True,
        timeout=ctx.deadline.budget(ctx.config.timeouts.element),
    ):
        raise PublishError(ErrorCode.CART_ATTACH_FAILED, "未找到「添加链接」按钮")


async def _await_product_modal(ctx: PipelineContext) -> str:
    """等待商品编辑弹窗出结论，返回商品原标题。

    弹窗有三种结局，靠标题文案区分。每一轮都先检查错误标题再找原标题——
    错误标题一旦出现就不会再变成正常弹窗，早判早退出。

    Raises:
        PublishError: CART_LIMIT_REACHED / PRODUCT_NOT_FOUND / CART_ATTACH_FAILED。
    """
    logger.info("[挂车] 等待商品编辑弹窗")

    # 用列表在闭包内外传递「已确诊的错误」。
    # 不能在 probe 内直接抛异常——poll_until 会把普通异常当作「本轮未命中」吞掉，
    # 结果就是明明已经确诊，却继续轮询到超时，最后报出一个不准确的错误码。
    diagnosed: list[PublishError] = []

    async def probe() -> str | None:
        for title in await read_texts(ctx.page, Cart.MODAL_TITLE_CSS):
            if Cart.TEXT_LIMIT_REACHED in title:
                diagnosed.append(
                    PublishError(ErrorCode.CART_LIMIT_REACHED, title)
                )
                return ""  # 返回非 None 以结束轮询
            if Cart.TEXT_NOT_FOUND in title:
                diagnosed.append(PublishError(ErrorCode.PRODUCT_NOT_FOUND, title))
                return ""

        for origin in await read_texts(ctx.page, Cart.ORIGIN_TITLE_CSS):
            if origin:
                return origin
        return None

    result = await poll_until(probe, timeout=ctx.deadline.budget(ctx.config.timeouts.cart_modal))

    if diagnosed:
        raise diagnosed[0]

    if not result:
        raise PublishError(
            ErrorCode.CART_ATTACH_FAILED,
            "商品编辑弹窗未出现或读不到商品原标题",
        )

    logger.info(f"[挂车] 商品原标题：{result}")
    return result


async def _fill_short_title(ctx: PipelineContext, origin_title: str) -> None:
    """填写商品短标题并完成编辑。"""
    short_title = ctx.config.resolve_short_title(origin_title)
    logger.info(f"[挂车] 使用短标题：{short_title}")

    if not await fill_usable(
        ctx.page,
        Cart.SHORT_TITLE_INPUT_CSS,
        short_title,
        timeout=ctx.deadline.budget(ctx.config.timeouts.element),
    ):
        raise PublishError(ErrorCode.CART_ATTACH_FAILED, "未找到商品短标题输入框")

    await asyncio.sleep(STEP_PAUSE)

    if not await click_usable(
        ctx.page,
        Cart.FINISH_EDIT_BUTTON_CSS,
        has_text=Cart.TEXT_FINISH_EDIT,
        exact=True,
        timeout=ctx.deadline.budget(ctx.config.timeouts.element),
    ):
        raise PublishError(ErrorCode.CART_ATTACH_FAILED, "未找到「完成编辑」按钮")


async def _verify_attached(ctx: PipelineContext) -> None:
    """校验商品卡片确实出现。

    这一步不可省略：前面每一步都可能「点了但没生效」，
    只有看到卡片才能确认商品真的挂上了。
    """
    card = await find_usable(
        ctx.page,
        Cart.ADDED_CARD_CSS,
        has_text=Cart.TEXT_ADDED,
        exact=False,
        timeout=ctx.deadline.budget(ctx.config.timeouts.element),
    )
    if card is None:
        raise PublishError(
            ErrorCode.CART_ATTACH_FAILED,
            "完成编辑后未出现已添加商品卡片，挂车可能未生效",
        )


async def run(ctx: PipelineContext) -> None:
    """执行完整的挂车流程。

    Raises:
        PublishError: CART_ATTACH_FAILED / CART_LIMIT_REACHED / PRODUCT_NOT_FOUND。
    """
    logger.info("[挂车] 开始挂载购物车")

    await _remove_existing_product(ctx)
    await _open_cart_dropdown(ctx)
    await _submit_product_link(ctx)
    origin_title = await _await_product_modal(ctx)
    await _fill_short_title(ctx, origin_title)
    await _verify_attached(ctx)

    logger.info("[挂车] 商品挂载完成")
