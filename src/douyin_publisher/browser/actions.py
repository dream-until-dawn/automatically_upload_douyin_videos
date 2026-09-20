"""通用页面动作原语。

目标页面大量使用 Semi Design 组件，它们有两个特点会让朴素的 Playwright 调用踩坑：

  1. **按钮的禁用状态不一定体现在 `disabled` 属性上**，也可能只是加了一个含
     `disabled` 的类名。只判断原生属性会误以为按钮可点，点下去毫无反应。
  2. **元素往往延迟出现**：弹窗、下拉项、异步加载的卡片都需要等待，
     而且经常需要「既存在、又可见、还可点」三个条件同时满足。

因此这里提供一组「等到真正可用为止」的封装，业务步骤只调用它们，
不直接操作原始 Locator。
"""

from __future__ import annotations

import re

from playwright.async_api import Locator, Page

from douyin_publisher.core.logging import get_logger
from douyin_publisher.core.waiting import poll_until

logger = get_logger("browser.actions")

# 元素等待的默认超时（秒）
DEFAULT_ELEMENT_TIMEOUT = 10.0


async def is_usable(locator: Locator) -> bool:
    """判断元素是否真正可用：存在、可见、且未被禁用。

    「未被禁用」同时检查原生 `disabled` 属性与类名中的 `disabled` 标记，
    后者是组件库常见的禁用表达方式，漏掉会导致点击无效却看不出原因。

    任何一步抛异常都视为不可用——元素可能正在重新渲染，此刻不该去碰它。
    """
    try:
        if await locator.count() == 0:
            return False
        if not await locator.is_visible():
            return False
        if await locator.is_disabled():
            return False

        class_name = await locator.get_attribute("class") or ""
        return "disabled" not in class_name.lower()
    except Exception:
        # 元素在判断过程中被移除或重绘，视作暂不可用，交给调用方继续轮询
        return False


def _build_text_matcher(text: str | None, exact: bool) -> re.Pattern[str] | str | None:
    """构造 Playwright 的文本匹配条件。

    Args:
        text: 目标文本；None 表示不按文本筛选。
        exact: True 时要求整体完全相等，False 时按子串匹配。

    精确匹配用正则的 `^...$` 实现，并对文本做转义——
    发布设置里的选项文案可能含有正则特殊字符（如括号），不转义会直接语法报错。
    """
    if text is None:
        return None
    if exact:
        return re.compile(f"^{re.escape(text)}$")
    return text


async def find_usable(
    page: Page,
    selector: str,
    *,
    has_text: str | None = None,
    exact: bool = True,
    timeout: float = DEFAULT_ELEMENT_TIMEOUT,
) -> Locator | None:
    """轮询查找一个真正可用的元素。

    与 Playwright 自带的 `wait_for` 的区别在于：本函数会持续重新求值，
    直到元素「可用」而不仅仅是「出现」。按钮先渲染成禁用态、稍后才解禁，
    是页面上非常常见的时序。

    Args:
        page: 目标页面。
        selector: CSS 或 XPath 选择器。
        has_text: 按文本筛选；用于在一组同类元素（如多个单选项）中定位目标。
        exact: 文本是否需要完全匹配。
        timeout: 超时秒数。

    Returns:
        可用的元素；超时未找到则返回 None，由调用方翻译成恰当的错误码。
    """
    matcher = _build_text_matcher(has_text, exact)

    async def probe() -> Locator | None:
        candidate = page.locator(selector, has_text=matcher).first
        return candidate if await is_usable(candidate) else None

    result = await poll_until(probe, timeout=timeout)
    if result is None:
        label = f"{selector}" + (f" [文本={has_text}]" if has_text else "")
        logger.debug(f"[元素] 等待 {timeout:.0f}s 仍不可用：{label}")
    return result


async def click_usable(
    page: Page,
    selector: str,
    *,
    has_text: str | None = None,
    exact: bool = True,
    timeout: float = DEFAULT_ELEMENT_TIMEOUT,
    scroll: bool = True,
) -> bool:
    """等待元素可用后点击它。

    Args:
        scroll: 点击前是否先滚动到可视区域。页面较长时，
            不滚动可能因元素被遮挡而点不中。

    Returns:
        是否成功点击。未找到可用元素时返回 False，不抛异常——
        调用方通常需要据此给出带业务语义的错误码。
    """
    target = await find_usable(
        page, selector, has_text=has_text, exact=exact, timeout=timeout
    )
    if target is None:
        return False

    try:
        if scroll:
            await target.scroll_into_view_if_needed()
        await target.click()
        return True
    except Exception as exc:
        logger.debug(f"[元素] 点击失败：{selector} -> {exc}")
        return False


async def read_texts(page: Page, selector: str) -> list[str]:
    """读取匹配元素的全部文本，已去除空白项。

    用于那些「需要看到内容才能判断下一步」的场景，
    例如从商品编辑弹窗里读出原标题，或检查弹窗标题是否为错误提示。
    """
    try:
        elements = await page.locator(selector).all()
    except Exception:
        return []

    texts: list[str] = []
    for element in elements:
        try:
            text = (await element.inner_text()).strip()
            if text:
                texts.append(text)
        except Exception:
            # 单个元素读取失败不应影响其余元素
            continue
    return texts


async def fill_usable(
    page: Page,
    selector: str,
    value: str,
    *,
    timeout: float = DEFAULT_ELEMENT_TIMEOUT,
) -> bool:
    """等待输入框可用后填入内容。

    Returns:
        是否成功填入。
    """
    target = await find_usable(page, selector, timeout=timeout)
    if target is None:
        return False

    try:
        await target.focus()
        await target.fill(value)
        return True
    except Exception as exc:
        logger.debug(f"[元素] 填写失败：{selector} -> {exc}")
        return False
