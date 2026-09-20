"""发布流程的端到端测试：真实浏览器驱动模拟页，跑完整条流水线。

这是对整个项目的验收：每一个错误码都必须能被真实地逼出来，
而不是只存在于枚举里。

## 关于耗时断言

竞速中止是本项目的核心承诺，而它一旦退化，**功能测试依然全绿**——
错误码还是对的，只是返回得慢了几百倍。因此这类测试必须同时断言总耗时上界。

举例：`upload=failure` 时若竞速失效，流程会一路走到「等待上传完成」，
在那里空等 300 秒才返回。错误码仍是 10，看不出任何异常，
只有耗时断言能把它抓出来。

## 关于停顿加速

各步骤中有若干用于等待页面渲染的固定停顿，端到端测试会把它们缩短。
模拟页的渲染是瞬时的，缩短不改变任何逻辑分支；
而耗时断言关心的是「有没有空等某个长超时」，量级相差两个数量级，
不受这点停顿影响。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from playwright.async_api import BrowserContext

from douyin_publisher.config.models import TaskConfig
from douyin_publisher.config.runtime import Timeouts
from douyin_publisher.core.errors import ErrorCode
from douyin_publisher.core.stages import Stage
from douyin_publisher.pipeline import runner
from douyin_publisher.pipeline.steps import (
    cart,
    cover,
    declaration,
    publish,
    publish_setting,
    title,
)
from douyin_publisher.pipeline.runner import PipelineResult, run_pipeline

from tests.integration.conftest import mock_page_url

# 端到端测试的总时长上限（秒）。远小于生产的 600s，让超时类用例跑得快。
TEST_TOTAL_TIMEOUT = 40.0


@pytest.fixture(autouse=True)
def fast_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    """把各步骤中用于等待渲染的固定停顿压缩到接近零。

    这些停顿在真实页面上是必要的（等框架完成渲染与状态同步），
    但模拟页是瞬时的，保留只会让每条用例白白多花十几秒。
    """
    fast = 0.02
    monkeypatch.setattr(title, "TAG_INPUT_PAUSE", fast)
    monkeypatch.setattr(title, "CLEAR_PAUSE", fast)
    monkeypatch.setattr(publish_setting, "OPTION_PAUSE", fast)
    monkeypatch.setattr(publish_setting, "INITIAL_PAUSE", fast)
    monkeypatch.setattr(declaration, "SELECT_PAUSE", fast)
    monkeypatch.setattr(cart, "STEP_PAUSE", fast)
    monkeypatch.setattr(cover, "STEP_PAUSE", fast)
    monkeypatch.setattr(publish, "PRE_CLICK_PAUSE", fast)


@pytest.fixture
def task_config(tmp_path: Path) -> TaskConfig:
    """一份完整的任务配置。

    浏览器路径与用户数据目录在端到端测试中用不上——浏览器由夹具启动，
    但视频文件必须真实存在，因为它会被真的投递进上传框。
    """
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake-video-content")

    return TaskConfig(
        exec_path="unused",
        user_data_dir="unused",
        task_id="task-1",
        douyin_id="dy-1",
        video_path=str(video),
        cart_url="https://example.com/item?id=1",
        title="测试标题",
        desc="夏日穿搭,好物分享",
        cart_titel="",
        publish_time_mode="立即发布",
        who_can_see="仅自己可见",
        save_permission="不允许",
        self_declaration="无需添加自主声明",
    )


async def run_with(
    config: TaskConfig,
    browser_context: BrowserContext,
    *,
    total_timeout: float = TEST_TOTAL_TIMEOUT,
    steps: tuple | None = None,
    **page_params: str | int,
) -> tuple[PipelineResult, float]:
    """跑一次流程，返回结论与耗时。

    Args:
        steps: 自定义步骤序列；留空则使用完整流程。
    """
    extra = {"steps": steps} if steps is not None else {}
    started = time.monotonic()
    result = await run_pipeline(
        config,
        browser_context,
        total_timeout=total_timeout,
        page_url=mock_page_url(**page_params),
        **extra,
    )
    return result, time.monotonic() - started


# ======================================================================
# 正向：完整成功路径
# ======================================================================


async def test_完整流程成功(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    result, elapsed = await run_with(task_config, browser_context)

    assert result.code is ErrorCode.SUCCESS, f"流程未成功：{result}"
    assert result.stage is Stage.DONE
    assert result.ok
    assert elapsed < 25, f"正向流程耗时 {elapsed:.1f}s，过长"


async def test_定时发布模式(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    """定时发布会多走一步「填写目标时间」，同样应当成功。"""
    config = task_config.model_copy(
        update={"publish_time_mode": "定时发布", "publish_time": "24"}
    )
    result, _ = await run_with(config, browser_context)
    assert result.code is ErrorCode.SUCCESS, f"定时发布流程失败：{result}"


async def test_选填项全部留空也能成功(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    """未指定的发布设置应被跳过，保留页面默认值，而不是失败。"""
    config = task_config.model_copy(
        update={
            "title": "", "desc": "", "publish_time_mode": "",
            "who_can_see": "", "save_permission": "",
        }
    )
    result, _ = await run_with(config, browser_context)
    assert result.code is ErrorCode.SUCCESS, f"选填项留空导致失败：{result}"


# ======================================================================
# 反向：页面元素缺失（多为改版）
# ======================================================================


@pytest.mark.parametrize(
    ("missing", "expected_code", "expected_stage"),
    [
        ("upload", ErrorCode.NOT_LOGGED_IN, Stage.UPLOAD),
        ("editor", ErrorCode.TITLE_INPUT_FAILED, Stage.TITLE),
        ("setting", ErrorCode.PUBLISH_SETTING_FAILED, Stage.SETTING),
        ("declaration", ErrorCode.DECLARATION_FAILED, Stage.DECLARATION),
        ("cart", ErrorCode.CART_ATTACH_FAILED, Stage.CART),
        ("cover", ErrorCode.COVER_FAILED, Stage.COVER),
        ("publish", ErrorCode.PUBLISH_FAILED, Stage.PUBLISH),
    ],
    ids=["上传框缺失", "编辑器缺失", "发布设置缺失", "自主声明缺失",
         "挂车区域缺失", "封面入口缺失", "发布按钮缺失"],
)
async def test_元素缺失时报出对应错误码(
    task_config: TaskConfig,
    browser_context: BrowserContext,
    missing: str,
    expected_code: ErrorCode,
    expected_stage: Stage,
) -> None:
    result, _ = await run_with(task_config, browser_context, missing=missing)

    assert result.code is expected_code, (
        f"缺失 {missing} 时期望 {expected_code.name}，实际 {result.code.name}：{result}"
    )
    assert result.stage is expected_stage, "失败阶段标注错误，上游将无法定位问题"


async def test_封面弹窗不出现时报16(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    """与「封面入口缺失」是两条不同的失败分支。

    入口还在、点得动，但弹窗始终不出现——这种「点了没反应」的情形
    在页面改版时比元素彻底消失更常见，必须单独覆盖。
    """
    result, _ = await run_with(task_config, browser_context, cover="no_modal")

    assert result.code is ErrorCode.COVER_FAILED, f"期望封面失败，实际：{result}"
    assert result.stage is Stage.COVER


# ======================================================================
# 反向：平台侧失败（由哨兵中止）
# ======================================================================


@pytest.mark.parametrize(
    ("upload_mode", "expected_code"),
    [
        ("failure", ErrorCode.UPLOAD_FAILED),
        ("service_error", ErrorCode.SERVICE_ERROR),
        ("not_supported", ErrorCode.PRODUCT_NOT_SUPPORTED),
    ],
    ids=["上传失败", "服务异常", "商品不支持推广"],
)
async def test_致命提示被哨兵捕获并立即中止(
    task_config: TaskConfig,
    browser_context: BrowserContext,
    upload_mode: str,
    expected_code: ErrorCode,
) -> None:
    """本文件最重要的一组。

    致命提示在投递文件后 200ms 弹出，此时主流程还在填标题/发布设置。
    哨兵必须立刻中止整条流水线。

    若竞速失效，流程会一路走到「等待上传完成」，在那里空等到超时——
    错误码可能仍然对得上，但耗时会暴涨。耗时断言是这条测试的关键。
    """
    result, elapsed = await run_with(
        task_config, browser_context, upload=upload_mode, uploadDelay=200
    )

    assert result.code is expected_code, f"期望 {expected_code.name}，实际：{result}"
    assert elapsed < 15, (
        f"感知致命提示后耗时 {elapsed:.1f}s——竞速中止可能已失效，"
        f"流程疑似空等到了上传超时"
    )


async def test_发布失败被哨兵捕获(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    result, _ = await run_with(task_config, browser_context, publish="failure")
    assert result.code is ErrorCode.PUBLISH_FAILED, f"期望发布失败，实际：{result}"


async def test_发布阶段服务异常(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    result, _ = await run_with(task_config, browser_context, publish="service_error")
    assert result.code is ErrorCode.SERVICE_ERROR, f"期望服务异常，实际：{result}"


# ======================================================================
# 反向：超时
# ======================================================================


async def test_上传无结论则报上传超时(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    """上传既不成功也不失败，应在预算耗尽后报 11 而非其他码。"""
    result, elapsed = await run_with(
        task_config, browser_context, total_timeout=12, upload="silent"
    )

    assert result.code is ErrorCode.UPLOAD_TIMEOUT, f"期望上传超时，实际：{result}"
    assert result.stage is Stage.AWAIT_UPLOAD
    assert elapsed < 20, f"超时控制失准，耗时 {elapsed:.1f}s"


async def test_发布无结论则报流程超时(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    """点击发布后平台不给任何结论，归为整体超时（12），与上传超时（11）区分。"""
    result, elapsed = await run_with(
        task_config, browser_context, total_timeout=12, publish="silent"
    )

    assert result.code is ErrorCode.PIPELINE_TIMEOUT, f"期望流程超时，实际：{result}"
    assert result.stage is Stage.AWAIT_PUBLISH
    assert elapsed < 20, f"超时控制失准，耗时 {elapsed:.1f}s"


# ======================================================================
# 反向：挂车（ADR-0002 失败即中止）
# ======================================================================


@pytest.mark.parametrize(
    ("cart_mode", "expected_code"),
    [
        ("limit", ErrorCode.CART_LIMIT_REACHED),
        ("not_found", ErrorCode.PRODUCT_NOT_FOUND),
        ("no_card", ErrorCode.CART_ATTACH_FAILED),
        ("no_origin", ErrorCode.CART_ATTACH_FAILED),
    ],
    ids=["挂车上限", "商品下架", "完成后无卡片", "读不到原标题"],
)
async def test_挂车失败报出对应错误码(
    task_config: TaskConfig,
    browser_context: BrowserContext,
    cart_mode: str,
    expected_code: ErrorCode,
) -> None:
    result, _ = await run_with(task_config, browser_context, cart=cart_mode)

    assert result.code is expected_code, (
        f"挂车模式 {cart_mode} 期望 {expected_code.name}，实际：{result}"
    )
    assert result.stage is Stage.CART


@pytest.mark.parametrize(
    ("cart_mode", "expected_code"),
    [
        ("limit", ErrorCode.CART_LIMIT_REACHED),
        ("not_found", ErrorCode.PRODUCT_NOT_FOUND),
        ("no_card", ErrorCode.CART_ATTACH_FAILED),
    ],
    ids=["挂车上限", "商品下架", "完成后无卡片"],
)
async def test_挂车失败时不空等上传(
    task_config: TaskConfig,
    browser_context: BrowserContext,
    cart_mode: str,
    expected_code: ErrorCode,
) -> None:
    """ADR-0002 的直接验收。

    组合两个条件：挂车失败，同时上传永远不给结论。
    正确行为是挂车一失败就立即返回；若保留了「延迟判定」，
    流程会继续走到「等待上传完成」并在那里空等到超时。

    这正是场景参数必须正交可组合的原因——单一场景名表达不了这种组合。

    ## 为什么显式配一个很短的 element 超时

    本条要断言的是「有没有空等上传」，而不是挂车步骤自身失败得快不快。
    部分挂车场景（如「完成后无卡片」）要等满元素超时才能判定失败，
    那段耗时属于挂车步骤本身，会干扰这里的判断——
    调整元素超时的默认值时，这条测试就会因为无关原因而变红。

    把元素超时压到很短，两者的量级差距就一目了然：
    挂车自身失败约数秒，而空等上传会接近总超时（{TEST_TOTAL_TIMEOUT} 秒）。
    """
    # 模拟页的元素都是即时渲染的，3 秒足够；真实页面不会用这个值
    config = task_config.model_copy(update={"timeouts": Timeouts(element=3)})

    result, elapsed = await run_with(
        config,
        browser_context,
        total_timeout=TEST_TOTAL_TIMEOUT,
        cart=cart_mode,
        upload="silent",
    )

    assert result.code is expected_code, f"期望 {expected_code.name}，实际：{result}"
    assert elapsed < 15, (
        f"挂车失败后耗时 {elapsed:.1f}s——流程疑似继续去等待上传，"
        f"违反 ADR-0002「挂车失败立即中止」"
    )


# ======================================================================
# 结论对象自身
# ======================================================================


async def test_成功结论的字段自洽(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    result, _ = await run_with(task_config, browser_context)

    assert result.ok is True
    assert result.code.code == 0
    assert result.code.retryable is False


async def test_失败结论携带可重试标记(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    """上游据此决定是否重试，标记错了会导致无意义的重试或该重试却不重试。"""
    platform_result, _ = await run_with(
        task_config, browser_context, upload="failure", uploadDelay=100
    )
    assert platform_result.code.retryable is True, "平台侧失败应当可重试"

    page_result, _ = await run_with(task_config, browser_context, missing="editor")
    assert page_result.code.retryable is False, "页面结构问题重试无用"


async def test_流程不会抛异常只会返回结论(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    """run_pipeline 承诺不抛异常——调用方因此可以确信清理与结果输出一定执行。"""
    config = task_config.model_copy(update={"video_path": "/不存在的路径/x.mp4"})

    result, _ = await run_with(config, browser_context)
    assert isinstance(result, PipelineResult)
    assert not result.ok


async def test_可注入自定义步骤序列(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    """冒烟脚本靠这个能力做到「只验证不发布」。

    用 DRY_RUN_STEPS 跑完整页面交互，流程应当成功结束，
    且页面上不会出现任何发布结果——因为发布按钮根本没被点过。
    """
    from douyin_publisher.pipeline.runner import DRY_RUN_STEPS

    result, _ = await run_with(
        task_config, browser_context, steps=DRY_RUN_STEPS, publish="failure"
    )

    # publish=failure 意味着「一旦点了发布就会失败」。
    # 结果为成功，正说明发布按钮确实没被点击。
    assert result.code is ErrorCode.SUCCESS, (
        f"演练流程未成功：{result}"
    )
    assert result.stage is Stage.DONE


async def test_步骤顺序中上传排在最前(
    task_config: TaskConfig, browser_context: BrowserContext
) -> None:
    """顺序约束的静态锁定：投递视频必须最早，后续操作与上传并行才跑得快。"""
    stages = [step.stage for step in runner.STEPS]

    assert stages[0] is Stage.UPLOAD, "投递视频不在第一步，流程将失去并行性"
    assert stages.index(Stage.COVER) > stages.index(Stage.AWAIT_UPLOAD), (
        "设置封面必须在等待上传完成之后——上传没完成时没有候选帧可选"
    )
