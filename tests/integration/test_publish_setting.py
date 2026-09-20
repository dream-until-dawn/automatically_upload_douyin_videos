"""发布设置的集成测试，重点是 **选中的到底是哪一个选项**。

## 这里防的是一类特别隐蔽的缺陷

保存权限的两个选项是「允许」与「不允许」，后者包含前者。
若按子串匹配，选「允许」时两个选项都会命中，最终选中哪一个取决于它们在
DOM 里的先后。

这种缺陷的可怕之处在于：**任务照样成功发布**，错误码是 0，全部测试通过，
只是视频的保存权限被悄悄设反了。没有人会发现，直到用户来投诉。

因此必须断言「实际点中的是哪一个」，而不只是断言「流程没报错」。
模拟页为此记录了每次点击的选项文案。
"""

from __future__ import annotations

import pytest
from playwright.async_api import Page

from douyin_publisher.config.models import TaskConfig
from douyin_publisher.core.events import EventBus
from douyin_publisher.core.waiting import Deadline
from douyin_publisher.pipeline import context as ctx_module
from douyin_publisher.pipeline.steps import publish_setting

from tests.integration.conftest import mock_page_url


@pytest.fixture(autouse=True)
def fast_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    """压缩等待渲染的固定停顿，模拟页是瞬时渲染的。"""
    monkeypatch.setattr(publish_setting, "OPTION_PAUSE", 0.02)
    monkeypatch.setattr(publish_setting, "INITIAL_PAUSE", 0.02)


def make_config(**overrides: str) -> TaskConfig:
    base = {
        "exec_path": "unused",
        "user_data_dir": "unused",
        "task_id": "t",
        "douyin_id": "d",
        "video_path": "unused",
        "cart_url": "https://example.com/1",
        "publish_time_mode": "",
        "who_can_see": "",
        "save_permission": "",
    }
    return TaskConfig(**{**base, **overrides})


# 颠倒保存权限两个选项在 DOM 中的先后顺序。
#
# 这是本文件的关键手法：子串匹配的错误在「允许」排在前面时恰好被掩盖
# （`.first` 碰巧选对了）。只有把顺序倒过来，缺陷才会显形。
# 真实页面的顺序我们无从保证，正确的实现本就不该依赖它。
_REVERSE_SAVE_ORDER = """
() => {
    const group = document.getElementById('setting-save');
    const labels = Array.from(group.querySelectorAll('label'));
    labels.reverse().forEach(el => group.appendChild(el));
    return labels.map(el => el.textContent.trim());
}
"""


async def run_setting(
    page: Page, config: TaskConfig, *, reverse_save_order: bool = False
) -> list[str]:
    """跑一遍发布设置步骤，返回页面上被实际点中的选项文案。

    Args:
        reverse_save_order: 是否先把保存权限的选项顺序颠倒过来。
    """
    await page.goto(mock_page_url())

    if reverse_save_order:
        order = await page.evaluate(_REVERSE_SAVE_ORDER)
        assert order == ["不允许", "允许"], f"顺序颠倒未生效：{order}"

    ctx = ctx_module.PipelineContext(
        config=config, page=page, bus=EventBus(), deadline=Deadline(30)
    )
    await publish_setting.run(ctx)
    return await page.evaluate("() => window.__selectedSettings")


# ======================================================================
# 核心：包含关系的选项不能选错
# ======================================================================


@pytest.mark.parametrize("reversed_order", [False, True], ids=["常规顺序", "顺序颠倒"])
async def test_选允许时不会误选不允许(page: Page, reversed_order: bool) -> None:
    """本文件存在的理由。

    「不允许」包含「允许」，子串匹配会同时命中两者，最终选中哪一个取决于
    它们在 DOM 里的先后。选错不会让流程失败，只会让保存权限悄悄设反。

    两种 DOM 顺序都要测：「允许」在前时，子串匹配碰巧也能选对，
    缺陷被完全掩盖；只有顺序颠倒时它才会显形。
    """
    selected = await run_setting(
        page, make_config(save_permission="允许"), reverse_save_order=reversed_order
    )

    assert "允许" in selected, f"未选中「允许」，实际点击：{selected}"
    assert "不允许" not in selected, (
        f"误选了「不允许」——保存权限将被设反，而任务仍会「成功」返回 0。"
        f"实际点击：{selected}"
    )


@pytest.mark.parametrize("reversed_order", [False, True], ids=["常规顺序", "顺序颠倒"])
async def test_选不允许时确实选中不允许(page: Page, reversed_order: bool) -> None:
    """反向配对：确认上一条不是因为「永远只选第一个」而通过。"""
    selected = await run_setting(
        page, make_config(save_permission="不允许"), reverse_save_order=reversed_order
    )

    assert selected == ["不允许"], f"期望只点中「不允许」，实际点击：{selected}"


# ======================================================================
# 其余各组设置
# ======================================================================


@pytest.mark.parametrize("option", ["公开", "好友可见", "仅自己可见"])
async def test_可见范围各选项都能正确选中(page: Page, option: str) -> None:
    selected = await run_setting(page, make_config(who_can_see=option))
    assert option in selected, f"未选中「{option}」，实际点击：{selected}"


@pytest.mark.parametrize("mode", ["立即发布", "定时发布"])
async def test_发布时间模式都能正确选中(page: Page, mode: str) -> None:
    config = make_config(publish_time_mode=mode, publish_time="24")
    selected = await run_setting(page, config)
    assert mode in selected, f"未选中「{mode}」，实际点击：{selected}"


async def test_定时发布会填入时间(page: Page) -> None:
    config = make_config(publish_time_mode="定时发布", publish_time="24")
    await run_setting(page, config)

    value = await page.locator("#schedule-input").input_value()
    assert value, "定时发布未填入目标时间"
    # 形如 2026-09-21 14:30
    assert len(value) == 16 and value[4] == "-" and value[13] == ":", (
        f"时间格式不符合页面要求：{value}"
    )


async def test_立即发布不填时间(page: Page) -> None:
    """反向：非定时模式下不该去碰时间输入框。"""
    await run_setting(page, make_config(publish_time_mode="立即发布"))
    assert await page.locator("#schedule-input").input_value() == ""


# ======================================================================
# 跳过语义
# ======================================================================


async def test_未指定的设置项被跳过(page: Page) -> None:
    """未指定就保留页面默认值，而不是替上游做决定。"""
    selected = await run_setting(page, make_config())
    assert selected == [], f"未指定任何设置，却点击了：{selected}"


async def test_只设置一项时不碰其他项(page: Page) -> None:
    selected = await run_setting(page, make_config(who_can_see="公开"))
    assert selected == ["公开"], f"多点了不该点的选项：{selected}"


# ======================================================================
# 时间计算
# ======================================================================


def test_定时发布时间按小时数偏移() -> None:
    """用固定基准时间验证，避免依赖真实时钟导致偶发失败。"""
    base = 1758300000.0  # 一个固定的时间戳
    zero = publish_setting.compute_publish_time(0, now=base)
    one_hour = publish_setting.compute_publish_time(1, now=base)

    assert zero != one_hour, "偏移小时数没有生效"
    assert len(one_hour) == 16
