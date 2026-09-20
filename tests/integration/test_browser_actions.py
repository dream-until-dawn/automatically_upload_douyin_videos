"""页面动作原语的集成测试。

最关键的一条是 **类名禁用的识别**：目标页面用 `class="... disabled"` 表达禁用，
而 Playwright 的 `is_disabled()` 只认原生 `disabled` 属性。
只判断原生属性会认为按钮可点，点下去却毫无反应——
表现为「流程卡在某一步直到超时」，而日志里看不出任何异常。
"""

from __future__ import annotations

import time

import pytest
from playwright.async_api import Page

from douyin_publisher.browser.actions import (
    click_usable,
    fill_usable,
    find_usable,
    is_usable,
    read_texts,
)

from tests.integration.conftest import mock_page_url


# ======================================================================
# is_usable：可用性判定
# ======================================================================


async def test_可见且未禁用的元素可用(page: Page) -> None:
    await page.goto(mock_page_url())
    assert await is_usable(page.locator("#cart-select"))


async def test_隐藏元素不可用(page: Page) -> None:
    """弹窗内的元素在初始状态下存在但隐藏，不能当作可以操作。"""
    await page.goto(mock_page_url())
    assert not await is_usable(page.locator("#cart-modal"))


async def test_不存在的元素不可用(page: Page) -> None:
    await page.goto(mock_page_url())
    assert not await is_usable(page.locator("#根本不存在的元素"))


async def test_带禁用类名的元素不可用(page: Page) -> None:
    """本文件最关键的一条。

    自主声明的「确定」按钮初始带 disabled 类名但没有 disabled 属性，
    这正是组件库常见的禁用表达方式。
    """
    await page.goto(mock_page_url())
    await page.click("#declaration-select")  # 打开弹窗，按钮此时仍是禁用态

    confirm = page.locator("#declaration-confirm")
    assert await confirm.is_visible(), "前提：按钮应当可见"
    assert not await confirm.is_disabled(), "前提：它并没有原生 disabled 属性"
    assert not await is_usable(confirm), (
        "带 disabled 类名的按钮被判定为可用——点击会静默失效，流程将卡到超时"
    )


async def test_解禁后元素变为可用(page: Page) -> None:
    """反向配对：确认上一条不是因为「永远返回不可用」而通过。"""
    await page.goto(mock_page_url())
    await page.click("#declaration-select")
    await page.click("#declaration-modal label >> nth=0")  # 选中选项后解禁

    assert await is_usable(page.locator("#declaration-confirm"))


# ======================================================================
# find_usable：等待可用
# ======================================================================


async def test_找到可用元素(page: Page) -> None:
    await page.goto(mock_page_url())
    assert await find_usable(page, "#cart-select", timeout=3) is not None


async def test_按文本精确匹配(page: Page) -> None:
    """精确匹配下「完成」不应命中「完成编辑」。"""
    await page.goto(mock_page_url())
    await page.click("#cover-entry")
    await page.click("#cover-horizontal")

    done = await find_usable(page, "button", has_text="完成", exact=True, timeout=3)
    assert done is not None
    assert (await done.inner_text()).strip() == "完成"


async def test_精确匹配不会误命中更长的文本(page: Page) -> None:
    """反向：页面上存在「完成编辑」，精确查找「完成」时不能把它算进来。"""
    await page.goto(mock_page_url())
    await page.click("#cart-select")
    await page.click("#cart-dropdown .select-dropdown-option-video >> nth=1")
    await page.click("#cart-add-link")  # 打开弹窗，「完成编辑」出现

    # 此时封面弹窗未打开，页面上没有文案恰为「完成」的按钮
    assert await find_usable(page, "button", has_text="完成", exact=True, timeout=1) is None


async def test_非精确匹配按子串命中(page: Page) -> None:
    await page.goto(mock_page_url())
    radio = await find_usable(
        page, "label[class*='radio']", has_text="仅自己", exact=False, timeout=3
    )
    assert radio is not None
    assert "仅自己可见" in await radio.inner_text()


async def test_文本含正则特殊字符也能匹配(page: Page) -> None:
    """发布设置的选项文案可能含斜杠、括号等字符。

    精确匹配是用正则实现的，不转义会直接抛语法错误或匹配错位。
    """
    await page.goto(mock_page_url())
    await page.evaluate(
        "() => { const b = document.createElement('button');"
        " b.textContent = '设置(横版)封面/竖版'; document.body.appendChild(b); }"
    )

    found = await find_usable(
        page, "button", has_text="设置(横版)封面/竖版", exact=True, timeout=3
    )
    assert found is not None


async def test_找不到元素时超时返回None(page: Page) -> None:
    await page.goto(mock_page_url())
    assert await find_usable(page, "#不存在", timeout=0.8) is None


async def test_超时确实按时返回(page: Page) -> None:
    """防「假绿」：超时若未生效，这里会挂到测试整体超时。"""
    await page.goto(mock_page_url())

    started = time.monotonic()
    await find_usable(page, "#不存在", timeout=1.0)
    elapsed = time.monotonic() - started

    assert 0.8 < elapsed < 3.0, f"超时控制失准，实际耗时 {elapsed:.2f}s"


async def test_等待稍后才出现的元素(page: Page) -> None:
    """元素延迟出现是页面常态，必须等得到而不是一次查不到就放弃。"""
    await page.goto(mock_page_url())
    await page.evaluate(
        "() => setTimeout(() => {"
        " const d = document.createElement('div'); d.id = 'late';"
        " d.textContent = '迟到的元素'; document.body.appendChild(d); }, 600)"
    )

    assert await find_usable(page, "#late", timeout=5) is not None


async def test_等待元素从禁用变为可用(page: Page) -> None:
    """按钮先渲染成禁用态、稍后解禁，是页面上非常常见的时序。"""
    await page.goto(mock_page_url())
    await page.click("#declaration-select")
    # 600ms 后模拟用户完成选择，按钮解禁
    await page.evaluate(
        "() => setTimeout(() =>"
        " document.getElementById('declaration-confirm')"
        ".classList.remove('disabled'), 600)"
    )

    assert await find_usable(page, "#declaration-confirm", timeout=5) is not None


# ======================================================================
# click_usable / fill_usable / read_texts
# ======================================================================


async def test_点击可用元素(page: Page) -> None:
    await page.goto(mock_page_url())
    assert await click_usable(page, "#cart-select", timeout=3)
    assert await page.locator("#cart-dropdown").is_visible()


async def test_点击不存在的元素返回假(page: Page) -> None:
    """返回布尔值而非抛异常，让调用方给出带业务语义的错误码。"""
    await page.goto(mock_page_url())
    assert not await click_usable(page, "#不存在", timeout=0.8)


async def test_点击前滚动到可视区域(page: Page) -> None:
    """页面较长时元素可能在视口外，不滚动会因遮挡而点不中。"""
    await page.goto(mock_page_url())
    await page.evaluate(
        "() => { const d = document.createElement('button');"
        " d.id = 'far'; d.textContent = '很远的按钮';"
        " d.style.marginTop = '3000px'; document.body.appendChild(d); }"
    )

    assert await click_usable(page, "#far", timeout=3)


async def test_填写输入框(page: Page) -> None:
    await page.goto(mock_page_url())
    await page.click("#cart-select")
    await page.click("#cart-dropdown .select-dropdown-option-video >> nth=1")

    assert await fill_usable(page, "#cart-link-input", "https://example.com/item")
    assert await page.locator("#cart-link-input").input_value() == "https://example.com/item"


async def test_填写不存在的输入框返回假(page: Page) -> None:
    await page.goto(mock_page_url())
    assert not await fill_usable(page, "#不存在", "内容", timeout=0.8)


async def test_读取多个元素的文本(page: Page) -> None:
    await page.goto(mock_page_url())
    texts = await read_texts(page, "#setting-visibility label")
    assert texts == ["公开", "好友可见", "仅自己可见"]


async def test_读取不存在的元素返回空列表(page: Page) -> None:
    await page.goto(mock_page_url())
    assert await read_texts(page, "#不存在") == []


@pytest.mark.parametrize("cart_mode", ["limit", "not_found"])
async def test_读取弹窗标题以识别商品问题(page: Page, cart_mode: str) -> None:
    """挂车分支依赖读取弹窗标题来区分上限与下架，这里验证读得到。"""
    await page.goto(mock_page_url(cart=cart_mode))
    await page.click("#cart-select")
    await page.click("#cart-dropdown .select-dropdown-option-video >> nth=1")
    await page.click("#cart-add-link")

    titles = await read_texts(page, "div[class^='modal-title-']")
    assert titles, "未能读到弹窗标题，挂车失败将无法定性"
