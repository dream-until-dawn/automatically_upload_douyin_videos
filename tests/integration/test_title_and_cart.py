"""标题填写与挂车的细节验证。

端到端测试只能证明「流程没报错」，证明不了「内容真的写进去了」。
这两类缺陷的差别很关键：

  · 流程报错 —— 会被错误码抓住，上游知道失败了；
  · 内容没写进去 —— 任务照样成功返回 0，视频发出去了却没有标题、没挂上商品。

后者更危险，因此这里逐项断言页面上的 **实际内容**。
"""

from __future__ import annotations

import pytest
from playwright.async_api import Page

from douyin_publisher.config.models import TaskConfig
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.events import EventBus
from douyin_publisher.core.waiting import Deadline
from douyin_publisher.pipeline.context import PipelineContext
from douyin_publisher.pipeline.steps import cart, title

from tests.integration.conftest import mock_page_url


@pytest.fixture(autouse=True)
def fast_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(title, "TAG_INPUT_PAUSE", 0.02)
    monkeypatch.setattr(title, "CLEAR_PAUSE", 0.02)
    monkeypatch.setattr(cart, "STEP_PAUSE", 0.02)
    monkeypatch.setattr(cart, "EXISTING_PROBE_TIMEOUT", 0.5)


def make_config(**overrides: str) -> TaskConfig:
    base = {
        "exec_path": "unused",
        "user_data_dir": "unused",
        "task_id": "t",
        "douyin_id": "d",
        "video_path": "unused",
        "cart_url": "https://example.com/item?id=1",
    }
    return TaskConfig(**{**base, **overrides})


async def make_context(page: Page, config: TaskConfig) -> PipelineContext:
    return PipelineContext(
        config=config, page=page, bus=EventBus(), deadline=Deadline(30)
    )


# ======================================================================
# 标题与话题标签：内容必须真的写进编辑器
# ======================================================================


async def test_标题被写入编辑器(page: Page) -> None:
    await page.goto(mock_page_url())
    config = make_config(title="夏日穿搭分享", desc="")

    await title.run(await make_context(page, config))

    content = await page.locator("#editor").inner_text()
    assert "夏日穿搭分享" in content, f"标题未写入编辑器，实际内容：{content!r}"


async def test_话题标签被逐个写入(page: Page) -> None:
    """标签要带 # 前缀，且逐个输入——一次性粘贴不会触发页面的话题识别。"""
    await page.goto(mock_page_url())
    config = make_config(title="标题", desc="夏日穿搭,清凉一夏,好物分享")

    await title.run(await make_context(page, config))

    content = await page.locator("#editor").inner_text()
    for tag in ("#夏日穿搭", "#清凉一夏", "#好物分享"):
        assert tag in content, f"话题标签 {tag} 未写入，实际内容：{content!r}"


async def test_标题与标签留空时编辑器为空(page: Page) -> None:
    """反向：不该凭空写入任何内容。"""
    await page.goto(mock_page_url())

    await title.run(await make_context(page, make_config(title="", desc="")))

    content = (await page.locator("#editor").inner_text()).strip()
    assert content == "", f"未配置任何内容，编辑器却有：{content!r}"


async def test_会清空编辑器的既有内容(page: Page) -> None:
    """页面可能残留上一次的草稿，不清空会让新旧内容叠加。"""
    await page.goto(mock_page_url())
    await page.evaluate("() => { document.getElementById('editor').textContent = '上次的草稿'; }")

    await title.run(await make_context(page, make_config(title="新标题", desc="")))

    content = await page.locator("#editor").inner_text()
    assert "上次的草稿" not in content, f"残留草稿未被清除：{content!r}"
    assert "新标题" in content


async def test_编辑器缺失报24(page: Page) -> None:
    await page.goto(mock_page_url(missing="editor"))

    with pytest.raises(PublishError) as exc_info:
        await title.run(await make_context(page, make_config(title="标题")))

    assert exc_info.value.code is ErrorCode.TITLE_INPUT_FAILED


# ======================================================================
# 挂车：短标题与链接必须真的填进去
# ======================================================================


async def test_商品链接被填入(page: Page) -> None:
    await page.goto(mock_page_url())
    config = make_config(cart_url="https://example.com/item?id=999")

    await cart.run(await make_context(page, config))

    value = await page.locator("#cart-link-input").input_value()
    assert value == "https://example.com/item?id=999", f"商品链接未正确填入：{value!r}"


async def test_未指定短标题时截取商品原标题(page: Page) -> None:
    """模拟页的商品原标题是「测试商品原标题很长很长很长」，应截取前 10 字。"""
    await page.goto(mock_page_url())

    await cart.run(await make_context(page, make_config(cart_titel="")))

    value = await page.locator("#cart-short-title").input_value()
    assert value == "测试商品原标题很长很", f"短标题截取不正确：{value!r}"
    assert len(value) == 10


async def test_指定短标题时优先使用指定值(page: Page) -> None:
    await page.goto(mock_page_url())

    await cart.run(await make_context(page, make_config(cart_titel="点击下方")))

    value = await page.locator("#cart-short-title").input_value()
    assert value == "点击下方", f"未使用指定的短标题：{value!r}"


async def test_指定的短标题过长时被截断(page: Page) -> None:
    """平台对短标题有长度上限，超长会被页面拒绝。"""
    await page.goto(mock_page_url())

    await cart.run(await make_context(page, make_config(cart_titel="这是一个超过十个字的很长短标题")))

    value = await page.locator("#cart-short-title").input_value()
    assert len(value) == 10, f"短标题未被截断：{value!r}"


async def test_挂车完成后商品卡片出现(page: Page) -> None:
    await page.goto(mock_page_url())

    await cart.run(await make_context(page, make_config()))

    assert await page.locator("#cart-added").is_visible(), "挂车完成但商品卡片未出现"


# ======================================================================
# 挂车：清理已有商品
# ======================================================================


async def test_先移除页面上已有的商品(page: Page) -> None:
    """页面可能残留上一次挂上的商品，不移除会导致新商品添加失败。

    这条路径在完整流程里几乎不会触发（每次都是全新页面），
    因此必须单独构造场景覆盖，否则它会一直是没被执行过的代码。
    """
    await page.goto(mock_page_url())

    # 构造一个「已挂商品」的现场：一个移除按钮，点击后再出现确定按钮
    await page.evaluate(
        """
        () => {
            window.__removeClicks = [];
            const section = document.getElementById('section-cart');

            const remove = document.createElement('button');
            remove.textContent = '移除';
            remove.addEventListener('click', () => {
                window.__removeClicks.push('移除');
                remove.remove();
                const confirm = document.createElement('button');
                confirm.textContent = '确定';
                confirm.addEventListener('click', () => {
                    window.__removeClicks.push('确定');
                    confirm.remove();
                });
                section.insertBefore(confirm, section.firstChild);
            });
            section.insertBefore(remove, section.firstChild);
        }
        """
    )

    await cart.run(await make_context(page, make_config()))

    clicks = await page.evaluate("() => window.__removeClicks")
    assert clicks == ["移除", "确定"], f"移除已有商品的流程未按预期执行：{clicks}"


async def test_没有已有商品时不做多余点击(page: Page) -> None:
    """反向：正常情况下不该去点任何东西，更不该因为找不到而失败。"""
    await page.goto(mock_page_url())

    # 正常挂车流程应当顺利完成，不因缺少「移除」按钮而报错
    await cart.run(await make_context(page, make_config()))

    assert await page.locator("#cart-added").is_visible()


# ======================================================================
# 挂车：各类失败
# ======================================================================


@pytest.mark.parametrize(
    ("cart_mode", "expected"),
    [
        ("limit", ErrorCode.CART_LIMIT_REACHED),
        ("not_found", ErrorCode.PRODUCT_NOT_FOUND),
        ("no_origin", ErrorCode.CART_ATTACH_FAILED),
        ("no_card", ErrorCode.CART_ATTACH_FAILED),
    ],
    ids=["挂车上限", "商品下架", "读不到原标题", "完成后无卡片"],
)
async def test_挂车失败的错误码(
    page: Page, cart_mode: str, expected: ErrorCode
) -> None:
    await page.goto(mock_page_url(cart=cart_mode))

    with pytest.raises(PublishError) as exc_info:
        await cart.run(await make_context(page, make_config()))

    assert exc_info.value.code is expected, (
        f"挂车模式 {cart_mode} 期望 {expected.name}，实际 {exc_info.value.code.name}"
    )


async def test_挂车区域缺失报14(page: Page) -> None:
    await page.goto(mock_page_url(missing="cart"))

    with pytest.raises(PublishError) as exc_info:
        await cart.run(await make_context(page, make_config()))

    assert exc_info.value.code is ErrorCode.CART_ATTACH_FAILED
