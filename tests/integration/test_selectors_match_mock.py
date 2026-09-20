"""选择器与模拟页的一致性校验。

## 这条测试在防什么

集成测试有一种最隐蔽的自欺方式：模拟页按「测试作者以为的结构」写，
生产选择器按「真实页面的结构」写，两者各自演化，最后测试全绿而生产全崩。

因此把「模拟页必须能被生产选择器命中」变成一条强制校验：
改了选择器却忘了同步模拟页，或反过来，这里都会立刻变红。

注意这条测试 **不能** 证明选择器与真实抖音页面一致——那只能靠人工冒烟。
它保证的是：模拟页不会悄悄偏离生产代码的结构假设，
从而让其余集成测试的结论保持有意义。
"""

from __future__ import annotations

import pytest
from playwright.async_api import Page

from douyin_publisher.browser import selectors as sel

from tests.integration.conftest import mock_page_url

# (人类可读的名称, 选择器, 可选的文本筛选)
#
# 这里列出的是流程真正依赖的元素。刻意不做成「自动遍历模块里所有常量」：
# 那样会把兜底用的宽泛选择器也算进来，反而削弱了断言的意义。
CRITICAL_SELECTORS: list[tuple[str, str, str | None]] = [
    ("视频上传输入框", sel.Upload.FILE_INPUT_CSS, None),
    ("标题编辑器", sel.Editor.SLATE_CSS, None),
    ("挂车区域容器", sel.Cart.SECTION_XPATH, None),
    ("挂车下拉框", sel.Cart.DROPDOWN_CSS, None),
    ("挂车下拉选项", sel.Cart.OPTION_CSS, sel.Cart.TEXT_OPTION),
    ("商品链接输入框", sel.Cart.LINK_INPUT_CSS, None),
    ("添加链接按钮", sel.Cart.ADD_LINK_BUTTON_CSS, sel.Cart.TEXT_ADD_LINK),
    ("商品弹窗标题", sel.Cart.MODAL_TITLE_CSS, None),
    ("商品原标题", sel.Cart.ORIGIN_TITLE_CSS, None),
    ("商品短标题输入框", sel.Cart.SHORT_TITLE_INPUT_CSS, None),
    ("完成编辑按钮", sel.Cart.FINISH_EDIT_BUTTON_CSS, sel.Cart.TEXT_FINISH_EDIT),
    ("已添加商品卡片", sel.Cart.ADDED_CARD_CSS, None),
    ("发布设置单选项", sel.PublishSetting.RADIO_CSS, "立即发布"),
    ("定时时间输入框", sel.PublishSetting.SCHEDULE_INPUT_CSS, None),
    ("自主声明下拉框", sel.Declaration.SELECT_BOX_XPATH, None),
    ("自主声明选项", sel.Declaration.RADIO_CSS, "无需添加自主声明"),
    ("封面入口", sel.Cover.ENTRY_XPATH, None),
    ("封面弹窗", sel.Cover.MODAL_CSS, None),
    ("设置横封面按钮", sel.Cover.BUTTON_CSS, sel.Cover.TEXT_SET_HORIZONTAL),
    ("发布按钮", sel.Publish.BUTTON_CSS, sel.Publish.TEXT_PUBLISH),
]


@pytest.mark.parametrize(
    ("name", "selector", "has_text"),
    CRITICAL_SELECTORS,
    ids=[item[0] for item in CRITICAL_SELECTORS],
)
async def test_关键选择器能在模拟页命中(
    page: Page, name: str, selector: str, has_text: str | None
) -> None:
    """每个关键选择器都必须在模拟页上找到至少一个元素。

    这里查的是「存在」而非「可见」：弹窗内的元素在初始状态下是隐藏的，
    但结构必须已经就位。
    """
    await page.goto(mock_page_url())

    locator = page.locator(selector, has_text=has_text) if has_text else page.locator(selector)
    count = await locator.count()

    assert count > 0, (
        f"选择器未能在模拟页命中「{name}」：{selector}"
        + (f"（文本筛选：{has_text}）" if has_text else "")
        + "\n生产选择器与模拟页结构已脱节，其余集成测试的结论不再可信"
    )


async def test_模拟页确实加载成功(page: Page) -> None:
    """兜底：若页面根本没加载，上面所有断言都会以误导性的方式失败。"""
    await page.goto(mock_page_url())
    assert await page.title() == "模拟创作者发布页"


async def test_场景参数被页面读取(page: Page) -> None:
    """失败注入依赖查询参数，若 file:// 下读不到，所有反向测试都会失效。"""
    await page.goto(mock_page_url(upload="failure", cart="limit", uploadDelay=123))

    scenario = await page.evaluate("() => window.__mockScenario")
    assert scenario["upload"] == "failure"
    assert scenario["cart"] == "limit", "各环节的场景必须能独立组合"
    assert scenario["uploadDelay"] == 123


@pytest.mark.parametrize(
    ("missing_key", "selector"),
    [
        ("editor", sel.Editor.SLATE_CSS),
        ("upload", sel.Upload.FILE_INPUT_CSS),
        ("cover", sel.Cover.ENTRY_XPATH),
        ("publish", sel.Publish.BUTTON_CSS),
    ],
)
async def test_元素缺失注入确实生效(
    page: Page, missing_key: str, selector: str
) -> None:
    """反向验证失败注入机制本身可用。

    若 missing 参数其实没起作用，那些「元素缺失」的反向测试就会因为
    元素依然存在而走到成功路径——测试全绿，却什么也没测。
    """
    await page.goto(mock_page_url(missing=missing_key))

    locator = page.locator(selector)
    if missing_key == "publish":
        # 发布按钮的选择器是宽泛的 button，页面上还有其他按钮，
        # 因此改为断言「没有文案为发布的按钮」
        locator = page.locator(selector, has_text=sel.Publish.TEXT_PUBLISH)

    assert await locator.count() == 0, f"missing={missing_key} 未能移除对应元素"
