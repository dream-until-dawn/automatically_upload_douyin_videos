"""运行时选项的正反向测试。

覆盖 ADR-0003 的三处设计约束：

  1. **默认不变**：不配置任何运行时选项时，行为与此前完全一致。
     这是向后兼容的全部意义所在——上游不改一行也能继续用。
  2. **浏览器参数的黑名单**：拒绝而非静默忽略。
  3. **跳过步骤的白名单**：构成「发布一个视频」的必要环节不可跳过。

后两条各自守着一类风险：传入破坏运行前提的参数，
以及把流程掏空到不再是它声称的那个东西。
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from douyin_publisher.browser.launcher import _LAUNCH_ARGS, build_launch_args
from douyin_publisher.config.loader import bind_task_config
from douyin_publisher.config.models import TaskConfig
from douyin_publisher.config.runtime import (
    FORBIDDEN_ARG_PREFIXES,
    SKIPPABLE_STAGES,
    LogLevel,
    Timeouts,
    validate_browser_args,
    validate_skip_stages,
)
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.logging import get_logger, reset_logging, setup_logging
from douyin_publisher.core.stages import Stage
from douyin_publisher.pipeline.runner import STEPS


def make_payload(**extra: Any) -> dict[str, Any]:
    base = {
        "execPath": "a",
        "userDataDir": "b",
        "taskId": "c",
        "douyinId": "d",
        "videoPath": "e",
        "cartUrl": "f",
    }
    return {**base, **extra}


# ======================================================================
# 默认值：不配置时行为不变
# ======================================================================


def test_不配置运行时选项时全部取默认值() -> None:
    """向后兼容的核心断言：上游不改一行也能继续用。"""
    config = bind_task_config(make_payload())

    assert config.timeouts.total == 600.0
    assert config.timeouts.upload == 300.0
    assert config.timeouts.publish == 120.0
    assert config.browser_args == []
    assert config.log_level is LogLevel.NORMAL
    assert config.skip == []
    assert config.skip_stages == frozenset()


def test_只配置部分超时时其余保持默认() -> None:
    """常见用法：只调「等上传」一项，不该牵连其他。"""
    config = bind_task_config(
        make_payload(timeouts={"upload": 900})
    )

    assert config.timeouts.upload == 900.0
    assert config.timeouts.total == 600.0, "未配置的项应保持默认"
    assert config.timeouts.element == 15.0


@pytest.mark.parametrize(
    ("alias", "field", "value"),
    [
        ("pageReady", "page_ready", 45),
        ("cartModal", "cart_modal", 25),
        ("coverFrame", "cover_frame", 90),
    ],
)
def test_小驼峰别名可用(alias: str, field: str, value: int) -> None:
    """配置字段对外统一用小驼峰，与既有字段风格一致。"""
    config = bind_task_config(make_payload(timeouts={alias: value}))
    assert getattr(config.timeouts, field) == float(value)


def test_未知的超时字段被忽略() -> None:
    """上游将来多传字段时，旧版本不应因此报错。"""
    config = bind_task_config(make_payload(timeouts={"someFutureTimeout": 5}))
    assert config.timeouts.total == 600.0


# ======================================================================
# 反向：非法超时
# ======================================================================


@pytest.mark.parametrize("bad", [0, -1, -600])
def test_非正数超时被拒绝(bad: int) -> None:
    """0 或负数会让对应的等待立即超时，等于把功能静默关掉。"""
    with pytest.raises(PublishError) as exc_info:
        bind_task_config(make_payload(timeouts={"total": bad}))
    assert exc_info.value.code is ErrorCode.CONFIG_PARSE_FAILED


def test_超时字段类型错误被拒绝() -> None:
    with pytest.raises(PublishError) as exc_info:
        bind_task_config(make_payload(timeouts={"total": "很久"}))
    assert exc_info.value.code is ErrorCode.CONFIG_PARSE_FAILED


# ======================================================================
# 浏览器启动参数
# ======================================================================


def test_合法参数被接受() -> None:
    args = ["--proxy-server=http://127.0.0.1:8080", "--window-size=1920,1080"]
    assert validate_browser_args(args) == args


@pytest.mark.parametrize("forbidden", FORBIDDEN_ARG_PREFIXES)
def test_破坏运行前提的参数被拒绝(forbidden: str) -> None:
    """逐个覆盖黑名单，新增条目时这条测试会自动跟上。"""
    with pytest.raises(ValueError, match="不接受的浏览器参数"):
        validate_browser_args([f"{forbidden}=whatever"])


@pytest.mark.parametrize(
    "bad", ["proxy-server=x", "-single-dash", "", "   ", "window-size=800,600"]
)
def test_不以双横线开头的参数被拒绝(bad: str) -> None:
    """大概率是拼装错误。静默忽略会让人对着不生效的配置反复调试。"""
    with pytest.raises(ValueError, match="必须以 -- 开头"):
        validate_browser_args([bad])


def test_非法浏览器参数在配置层报3() -> None:
    """字段拼对了、取值不合法，归为 CONFIG_INVALID 而非解析失败。"""
    with pytest.raises(PublishError) as exc_info:
        bind_task_config(make_payload(browserArgs=["--user-data-dir=/tmp/x"]))
    assert exc_info.value.code is ErrorCode.CONFIG_INVALID


def test_附加参数拼在内置参数之后() -> None:
    """追加而非替换：内置参数是程序正常工作的前提。"""
    result = build_launch_args(["--proxy-server=http://127.0.0.1:8080"])

    assert result[: len(_LAUNCH_ARGS)] == _LAUNCH_ARGS, "内置参数必须保留且在前"
    assert result[-1] == "--proxy-server=http://127.0.0.1:8080"


def test_无附加参数时与内置参数一致() -> None:
    assert build_launch_args([]) == _LAUNCH_ARGS


# ======================================================================
# 跳过步骤
# ======================================================================


@pytest.mark.parametrize(
    "stage", sorted(SKIPPABLE_STAGES, key=lambda s: s.value), ids=lambda s: s.value
)
def test_白名单内的阶段可跳过(stage: Stage) -> None:
    assert validate_skip_stages([stage.value]) == [stage]


@pytest.mark.parametrize(
    "stage",
    [Stage.UPLOAD, Stage.AWAIT_UPLOAD, Stage.PUBLISH, Stage.AWAIT_PUBLISH],
    ids=lambda s: s.value,
)
def test_必要环节不可跳过(stage: Stage) -> None:
    """这四步构成「发布一个视频」的最小定义。

    允许跳过它们，这个程序就不再是它声称的那个东西——
    跳过上传发布的是空内容，跳过发布则根本没有发布。
    """
    with pytest.raises(ValueError, match="不允许跳过"):
        validate_skip_stages([stage.value])


def test_未知阶段名被拒绝并提示可选值() -> None:
    with pytest.raises(ValueError, match="未知的阶段名") as exc_info:
        validate_skip_stages(["nonsense"])
    # 报错要能指路，而不是只说「错了」
    assert "cart" in str(exc_info.value)


def test_跳过必要环节在配置层报3() -> None:
    with pytest.raises(PublishError) as exc_info:
        bind_task_config(make_payload(skip=["publish"]))
    assert exc_info.value.code is ErrorCode.CONFIG_INVALID


def test_可同时跳过多个阶段() -> None:
    config = bind_task_config(make_payload(skip=["cart", "cover"]))
    assert config.skip_stages == frozenset({Stage.CART, Stage.COVER})


def test_白名单与实际流程步骤对得上() -> None:
    """白名单里的阶段必须真的存在于流程中，否则是一条永远无效的配置。"""
    pipeline_stages = {step.stage for step in STEPS}
    assert SKIPPABLE_STAGES <= pipeline_stages, (
        f"白名单中存在流程里没有的阶段：{SKIPPABLE_STAGES - pipeline_stages}"
    )


def test_白名单未覆盖全部步骤() -> None:
    """反向：若白名单等于全部步骤，等于没有限制。"""
    pipeline_stages = {step.stage for step in STEPS}
    assert SKIPPABLE_STAGES < pipeline_stages, "必须存在不可跳过的步骤"


# ======================================================================
# 日志级别
# ======================================================================


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (LogLevel.QUIET, logging.WARNING),
        (LogLevel.NORMAL, logging.INFO),
        (LogLevel.DEBUG, logging.DEBUG),
    ],
)
def test_日志级别映射(level: LogLevel, expected: int) -> None:
    assert level.logging_level == expected


def test_可用字符串配置日志级别() -> None:
    config = bind_task_config(make_payload(logLevel="quiet"))
    assert config.log_level is LogLevel.QUIET


def test_非法日志级别被拒绝() -> None:
    with pytest.raises(PublishError) as exc_info:
        bind_task_config(make_payload(logLevel="verbose"))
    assert exc_info.value.code is ErrorCode.CONFIG_PARSE_FAILED


def test_安静模式确实比默认更安静() -> None:
    """用级别数值表达意图，而不是只比对枚举值。"""
    assert LogLevel.QUIET.logging_level > LogLevel.NORMAL.logging_level
    assert LogLevel.DEBUG.logging_level < LogLevel.NORMAL.logging_level


@pytest.mark.parametrize(
    ("level", "info_visible", "debug_visible"),
    [
        (LogLevel.QUIET, False, False),
        (LogLevel.NORMAL, True, False),
        (LogLevel.DEBUG, True, True),
    ],
    ids=["安静", "默认", "调试"],
)
def test_日志级别真的影响输出(
    capsys: pytest.CaptureFixture[str],
    level: LogLevel,
    info_visible: bool,
    debug_visible: bool,
) -> None:
    """只断言「映射到了正确的数值」是不够的——那只证明了一张查找表。

    这里真的打日志再读回来，确认级别确实在起作用。
    少了这条，logLevel 可能只是个写进配置却毫无效果的摆设字段。
    """
    reset_logging()
    try:
        setup_logging(level.logging_level)
        log = get_logger("runtime_test")
        log.debug("调试级日志")
        log.info("信息级日志")
        log.warning("警告级日志")

        captured = capsys.readouterr().err

        assert ("信息级日志" in captured) is info_visible
        assert ("调试级日志" in captured) is debug_visible
        # 警告在任何级别下都必须可见——它是出问题时唯一的线索
        assert "警告级日志" in captured
    finally:
        reset_logging()


# ======================================================================
# 日志摘要
# ======================================================================


def test_配置摘要标出非默认的运行时选项() -> None:
    """排查时第一个问题往往是「配置到底生效没有」，摘要要能直接回答。"""
    config = bind_task_config(
        make_payload(skip=["cart"], timeouts={"total": 900},
                     browserArgs=["--proxy-server=http://127.0.0.1:1"])
    )
    summary = str(config)

    assert "skip=" in summary
    assert "900" in summary
    assert "browserArgs=1项" in summary


def test_默认配置的摘要不含运行时噪音() -> None:
    """没改过的选项不该出现在摘要里，否则关键信息会被淹没。"""
    summary = str(bind_task_config(make_payload()))

    assert "skip=" not in summary
    assert "browserArgs" not in summary
    assert "total=" not in summary


def test_默认超时模型可独立构造() -> None:
    """Timeouts 不依赖 TaskConfig，便于单独测试与复用。"""
    assert Timeouts().total == 600.0
    assert Timeouts(total=30).total == 30.0


def test_可用下划线字段名构造配置() -> None:
    config = TaskConfig(
        exec_path="a", user_data_dir="b", task_id="c",
        douyin_id="d", video_path="e", cart_url="f",
        timeouts=Timeouts(upload=1000),
    )
    assert config.timeouts.upload == 1000.0
