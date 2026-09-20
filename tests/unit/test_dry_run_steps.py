"""演练步骤序列的安全性校验。

## 这条测试守的是什么

`DRY_RUN_STEPS` 供真实环境冒烟脚本使用。它一旦出错，后果不是测试变红，
而是 **在用户的真实抖音账号下发布一条视频**——不可撤销，且发生在
一个本应「只验证、不发布」的操作里。

这类「错了就无法挽回」的常量必须有专门的测试，而不能只靠写的时候小心。
"""

from __future__ import annotations

import pytest

from douyin_publisher.core.stages import Stage
from douyin_publisher.pipeline.runner import DRY_RUN_STEPS, STEPS

# 任何会导致真实发布的阶段
_PUBLISHING_STAGES = {Stage.PUBLISH, Stage.AWAIT_PUBLISH}


def test_演练序列不含任何发布动作() -> None:
    """本文件的核心断言。"""
    offending = [step for step in DRY_RUN_STEPS if step.stage in _PUBLISHING_STAGES]

    assert not offending, (
        f"演练序列中含有发布动作：{[str(s) for s in offending]}——"
        f"冒烟脚本会在用户的真实账号下发布视频"
    )


def test_完整序列确实含有发布动作() -> None:
    """反向配对。

    若完整流程本身就不含发布步骤，上面那条断言会「因为无事可排除」而通过，
    变成一条永远绿的摆设。
    """
    publishing = [step for step in STEPS if step.stage in _PUBLISHING_STAGES]

    assert len(publishing) == 2, (
        f"完整流程中的发布动作数量异常：{[str(s) for s in publishing]}"
    )


def test_演练序列保留了其余全部步骤() -> None:
    """只应去掉发布动作，不该顺手少做别的——那会让冒烟失去验证价值。"""
    expected = [step for step in STEPS if step.stage not in _PUBLISHING_STAGES]
    assert list(DRY_RUN_STEPS) == expected


def test_演练序列保持原有顺序() -> None:
    """顺序错乱会让冒烟跑出与生产不同的行为，结论不可信。"""
    full_order = [step.stage for step in STEPS]
    dry_order = [step.stage for step in DRY_RUN_STEPS]

    positions = [full_order.index(stage) for stage in dry_order]
    assert positions == sorted(positions), "演练序列的步骤顺序与完整流程不一致"


def test_演练序列非空() -> None:
    """空序列会让冒烟「全部通过」却什么也没验证。"""
    assert len(DRY_RUN_STEPS) >= 5


@pytest.mark.parametrize("stage", sorted(_PUBLISHING_STAGES, key=lambda s: s.value))
def test_逐个确认发布阶段被排除(stage: Stage) -> None:
    assert stage not in {step.stage for step in DRY_RUN_STEPS}
