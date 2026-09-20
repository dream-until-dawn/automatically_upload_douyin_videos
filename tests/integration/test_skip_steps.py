"""跳过步骤的端到端验证。

## 怎样才算真的验证了「跳过」

断言「配了 skip 之后流程还是成功」是不够的——不跳过时它本来也成功，
这种测试跳没跳都是绿的。

真正的证据是：**让被跳过的那一步本来必定失败**。
模拟页可以注入「挂车必定失败」，此时：

  · 不跳过 → 流程失败，错误码指向挂车；
  · 跳过   → 流程成功。

两者成对出现，才说明跳过确实生效，而不是碰巧。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import BrowserContext

from douyin_publisher.config.models import TaskConfig
from douyin_publisher.core.errors import ErrorCode
from douyin_publisher.core.stages import Stage
from douyin_publisher.pipeline import runner
from douyin_publisher.pipeline.runner import run_pipeline
from douyin_publisher.pipeline.steps import (
    cart,
    cover,
    declaration,
    publish,
    publish_setting,
    title,
)

from tests.integration.conftest import mock_page_url

TEST_TOTAL_TIMEOUT = 40.0


@pytest.fixture(autouse=True)
def fast_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    fast = 0.02
    monkeypatch.setattr(title, "TAG_INPUT_PAUSE", fast)
    monkeypatch.setattr(title, "CLEAR_PAUSE", fast)
    monkeypatch.setattr(publish_setting, "OPTION_PAUSE", fast)
    monkeypatch.setattr(publish_setting, "INITIAL_PAUSE", fast)
    monkeypatch.setattr(declaration, "SELECT_PAUSE", fast)
    monkeypatch.setattr(cart, "STEP_PAUSE", fast)
    monkeypatch.setattr(cart, "EXISTING_PROBE_TIMEOUT", 0.5)
    monkeypatch.setattr(cover, "STEP_PAUSE", fast)
    monkeypatch.setattr(publish, "PRE_CLICK_PAUSE", fast)


def make_config(tmp_path: Path, **extra: object) -> TaskConfig:
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake-video")

    payload = {
        "exec_path": "unused",
        "user_data_dir": "unused",
        "task_id": "t",
        "douyin_id": "d",
        "video_path": str(video),
        "cart_url": "https://example.com/item?id=1",
        "title": "测试标题",
        "desc": "标签一",
        "publish_time_mode": "立即发布",
        "who_can_see": "仅自己可见",
        "save_permission": "不允许",
    }
    payload.update(extra)
    return TaskConfig(**payload)


async def run_with(
    config: TaskConfig, browser_context: BrowserContext, **page_params: str | int
):
    return await run_pipeline(
        config,
        browser_context,
        total_timeout=TEST_TOTAL_TIMEOUT,
        page_url=mock_page_url(**page_params),
    )


# ======================================================================
# 成对验证：同一场景下，跳过与不跳过结果相反
# ======================================================================


async def test_不跳过挂车时因挂车失败而失败(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """配对测试的前半：确认这个场景下挂车确实会失败。

    少了这条，下一条的「成功」就可能只是因为挂车本来也不会失败。
    """
    result = await run_with(make_config(tmp_path), browser_context, cart="limit")

    assert result.code is ErrorCode.CART_LIMIT_REACHED
    assert result.stage is Stage.CART


async def test_跳过挂车后同一场景转为成功(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """配对测试的后半：同样的场景，只多了 skip=['cart']，结果反转。"""
    config = make_config(tmp_path, skip=["cart"])
    result = await run_with(config, browser_context, cart="limit")

    assert result.code is ErrorCode.SUCCESS, (
        f"跳过挂车后仍失败，说明 skip 未生效：{result}"
    )
    assert result.stage is Stage.DONE


async def test_不跳过封面时因封面失败而失败(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    result = await run_with(make_config(tmp_path), browser_context, cover="no_modal")
    assert result.code is ErrorCode.COVER_FAILED


async def test_跳过封面后同一场景转为成功(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    config = make_config(tmp_path, skip=["cover"])
    result = await run_with(config, browser_context, cover="no_modal")

    assert result.code is ErrorCode.SUCCESS, f"跳过封面后仍失败：{result}"


async def test_跳过标题后编辑器缺失也能成功(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """用「元素缺失」这类必定失败的注入来验证，比用正常页面更有说服力。"""
    config = make_config(tmp_path, skip=["title"])
    result = await run_with(config, browser_context, missing="editor")

    assert result.code is ErrorCode.SUCCESS, f"跳过标题后仍失败：{result}"


async def test_同时跳过多个步骤(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """三个环节同时注入失败，全部跳过后流程应当成功。"""
    config = make_config(tmp_path, skip=["cart", "cover", "declaration"])
    result = await run_with(
        config, browser_context, cart="limit", cover="no_modal", missing="declaration"
    )

    assert result.code is ErrorCode.SUCCESS, f"同时跳过多步后仍失败：{result}"


# ======================================================================
# 跳过不该影响其余步骤
# ======================================================================


async def test_跳过挂车不影响标题填写(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """跳过一步不能顺带跳过别的——那会让流程悄悄少做事。"""
    config = make_config(tmp_path, skip=["cart"], title="标题仍应写入", desc="标签甲")
    result = await run_with(config, browser_context)
    assert result.code is ErrorCode.SUCCESS

    page = browser_context.pages[0]
    content = await page.locator("#editor").inner_text()
    assert "标题仍应写入" in content, "跳过挂车后标题没写入，说明跳过影响了别的步骤"
    assert "#标签甲" in content


async def test_跳过标题不影响发布设置(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    config = make_config(tmp_path, skip=["title"], who_can_see="仅自己可见")
    result = await run_with(config, browser_context)
    assert result.code is ErrorCode.SUCCESS

    page = browser_context.pages[0]
    selected = await page.evaluate("() => window.__selectedSettings")
    assert "仅自己可见" in selected, "跳过标题后发布设置也没做"


# ======================================================================
# 未配置跳过时行为不变
# ======================================================================


async def test_未配置跳过时全部步骤都执行(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    config = make_config(tmp_path)
    result = await run_with(config, browser_context)

    assert result.code is ErrorCode.SUCCESS
    page = browser_context.pages[0]
    assert await page.locator("#cart-added").is_visible(), "未配置跳过，挂车却没执行"


def test_跳过后的步骤序列仍保持原有顺序() -> None:
    """顺序错乱会让流程跑出与预期不同的行为。"""
    skipped = {Stage.CART, Stage.COVER}
    remaining = [s.stage for s in runner.STEPS if s.stage not in skipped]
    full_order = [s.stage for s in runner.STEPS]

    positions = [full_order.index(stage) for stage in remaining]
    assert positions == sorted(positions)


# ======================================================================
# 纯内容视频：不配置商品链接即自动跳过挂车
# ======================================================================


async def test_不配置商品链接时自动跳过挂车(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """同样用「挂车必定失败」来证明它真的没执行。

    若只断言「不填链接也能成功」，挂车本来也可能成功，测不出区别。
    """
    config = make_config(tmp_path, cart_url="")
    result = await run_with(config, browser_context, cart="limit")

    assert result.code is ErrorCode.SUCCESS, (
        f"未配置商品链接时仍尝试挂车：{result}"
    )
    assert result.stage is Stage.DONE


async def test_配置了商品链接则照常挂车(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """反向配对：确认上一条不是因为挂车被无条件跳过。"""
    config = make_config(tmp_path, cart_url="https://example.com/item?id=1")
    result = await run_with(config, browser_context, cart="limit")

    assert result.code is ErrorCode.CART_LIMIT_REACHED, (
        f"配置了商品链接却没挂车：{result}"
    )


async def test_纯内容视频其余步骤照常执行(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """跳过挂车不能顺带少做别的事。"""
    config = make_config(tmp_path, cart_url="", title="纯内容视频", desc="标签乙")
    result = await run_with(config, browser_context)
    assert result.code is ErrorCode.SUCCESS

    page = browser_context.pages[0]
    content = await page.locator("#editor").inner_text()
    assert "纯内容视频" in content
    assert "#标签乙" in content
    assert not await page.locator("#cart-added").is_visible(), "不该出现商品卡片"


async def test_空白商品链接等同于不配置(
    tmp_path: Path, browser_context: BrowserContext
) -> None:
    """上游传个空串是常见情形，不该被当成「要挂车但链接是空的」。"""
    config = make_config(tmp_path, cart_url="   ")
    result = await run_with(config, browser_context, cart="limit")

    assert result.code is ErrorCode.SUCCESS
