"""冒烟探测清单的一致性校验。

探测脚本的价值完全建立在「它查找元素的方式与生产一致」之上。
一旦两者漂移，探测给出的「全部命中」就是虚假的安全感——
它既会漏报真实的改版，也会把正常元素报成改版，
几次之后这份报告就没人认真看了。

这类漂移不会让任何功能测试变红，因此在这里专门守住。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 冒烟脚本不在包内，需要把 scripts 目录加进搜索路径
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import smoke_real  # noqa: E402

from douyin_publisher.browser import selectors as sel  # noqa: E402
from douyin_publisher.config import models  # noqa: E402
from douyin_publisher.pipeline.runner import DRY_RUN_STEPS  # noqa: E402


def _collect_strings(module_or_class) -> set[str]:
    """收集一个模块或类中公开的字符串常量。"""
    values: set[str] = set()
    for name in dir(module_or_class):
        if name.startswith("_"):
            continue
        member = getattr(module_or_class, name)
        if isinstance(member, str):
            values.add(member)
    return values


def _selector_constants() -> set[str]:
    """收集集中登记的选择器与文案。

    范围含两处：browser/selectors.py 登记选择器与页面文案，
    config/models.py 登记配置取值的文案（如「立即发布」）。
    两者都属于「集中登记」，探测脚本引用哪一处都可以，
    但不允许自己写死字面量。
    """
    values: set[str] = _collect_strings(models)
    for name in dir(sel):
        if name.startswith("_"):
            continue
        member = getattr(sel, name)
        if isinstance(member, str):
            values.add(member)
            continue
        # 分组常量以嵌套类的形式组织
        if isinstance(member, type):
            for attr in dir(member):
                if attr.startswith("_"):
                    continue
                value = getattr(member, attr)
                if isinstance(value, str):
                    values.add(value)
    return values


def test_探测清单非空() -> None:
    """空清单会让探测「全部通过」却什么也没查。"""
    assert len(smoke_real.PROBE_TARGETS) >= 6


@pytest.mark.parametrize(
    "target", smoke_real.PROBE_TARGETS, ids=lambda t: t.name
)
def test_选择器来自集中登记而非硬编码(target) -> None:
    """探测脚本里不允许出现自己写死的选择器。

    一旦硬编码，改了 selectors.py 而忘了改这里，探测就会继续用旧选择器，
    报出与生产完全无关的结论。
    """
    known = _selector_constants()
    assert target.selector in known, (
        f"「{target.name}」的选择器不在 browser/selectors.py 中："
        f"{target.selector}——探测脚本不得硬编码选择器"
    )
    if target.within is not None:
        assert target.within in known, (
            f"「{target.name}」的父容器选择器不在集中登记中：{target.within}"
        )


@pytest.mark.parametrize(
    "target", smoke_real.PROBE_TARGETS, ids=lambda t: t.name
)
def test_文本筛选项来自集中登记(target) -> None:
    """按文本定位时，文案同样应取自登记的常量，而不是随手写的字面量。"""
    if target.has_text is None:
        return
    known = _selector_constants()
    assert target.has_text in known, (
        f"「{target.name}」的文本筛选「{target.has_text}」不在集中登记中"
    )


def test_探测覆盖了演练会用到的关键环节() -> None:
    """演练要跑的每个环节，探测里都应有对应的元素。

    否则探测通过、演练却在某个没被探测到的环节上失败，
    探测的预警作用就打了折扣。
    """
    probed = {t.name for t in smoke_real.PROBE_TARGETS}
    expected = {
        "视频上传输入框", "标题编辑器", "挂车区域容器",
        "发布设置单选项", "自主声明下拉框", "封面入口",
    }
    assert expected <= probed, f"探测清单缺少：{expected - probed}"


def test_发布按钮被探测但不在演练序列中() -> None:
    """一个容易混淆的组合，值得写清楚：

    · 探测 **要** 查发布按钮——它是判断页面是否改版的重要信号；
    · 演练 **不能** 点它——点了就是真实发布。

    两者并不矛盾：查找元素没有副作用，点击才有。
    """
    probed = {t.name for t in smoke_real.PROBE_TARGETS}
    assert "发布按钮" in probed, "探测应当覆盖发布按钮"

    dry_run_stages = {step.stage.value for step in DRY_RUN_STEPS}
    assert "publish" not in dry_run_stages, "演练序列不得包含发布动作"


def test_文本精确匹配的取值与生产一致() -> None:
    """发布按钮在生产中用精确匹配，探测若退回子串匹配会命中「作品发布」。

    这是实测踩到过的坑：真实页面上同时存在「作品发布」与「发布」两个按钮。
    """
    publish = next(t for t in smoke_real.PROBE_TARGETS if t.name == "发布按钮")
    assert publish.exact is True, (
        "发布按钮必须用精确匹配——子串匹配会命中「作品发布」，"
        "从而误报流程会点错按钮"
    )


def test_隐藏输入框只要求存在() -> None:
    """文件输入框在页面上是隐藏的，生产只要求它 attached。

    对它做可用性判定会稳定误报「元素不可用」。
    """
    upload = next(t for t in smoke_real.PROBE_TARGETS if t.name == "视频上传输入框")
    assert upload.attached_only is True
