"""竞速编排器：主流程与致命事件哨兵并发运行，谁先出结论谁说了算。

这是 [ADR-0001](../../../docs/adr/0001-async-race-abort.md) 的落地实现，
也是本项目相对「顺序执行 + 轮询」方案的全部价值所在。

## 执行语义

    ┌── 主流程任务 ──┐        ┌── 哨兵任务 ──┐
    │ 按顺序跑完步骤 │        │ 持续消费事件  │
    │ 任一步失败即返回│        │ 命中致命即返回│
    └───────┬───────┘        └──────┬──────┘
            └──── FIRST_COMPLETED ───┘
                        │
              胜者的结论即最终结论，败者被取消

哨兵先完成 → 页面已抛出终局性提示，立即取消主流程。
主流程无论卡在哪个 await 上，都会在下一个调度点收到 CancelledError 并退出。

## 总超时兜底

外层设总时长上限，防止两者都不出结论。同时每个步骤通过共享的 Deadline
申请自己的超时，保证各步骤之和不会突破总上限。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from playwright.async_api import BrowserContext, Page

from douyin_publisher.browser.selectors import PUBLISH_PAGE_URL
from douyin_publisher.browser.toast import install_toast_listener
from douyin_publisher.config.models import TaskConfig
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.events import EventBus
from douyin_publisher.core.logging import get_logger
from douyin_publisher.core.stages import Stage
from douyin_publisher.core.waiting import Deadline, wait_for_first
from douyin_publisher.pipeline.context import PipelineContext, Step
from douyin_publisher.pipeline.steps import (
    await_upload,
    cart,
    cover,
    declaration,
    publish,
    publish_setting,
    title,
    upload,
)

logger = get_logger("pipeline.runner")

# 整个发布流程的总时长上限（秒）。
#
# 仅作为调用方未指定时的回退值——实际取值来自 config.timeouts.total，
# 见 docs/adr/0003-runtime-options.md。
TOTAL_TIMEOUT = 600.0


# 流程步骤，按执行顺序排列。
#
# 顺序上有两处不能随意调整：
#   · 「投递视频」必须最早——后续所有操作都与上传并行进行，这是流程快的关键；
#   · 「设置封面」必须在「等待上传完成」之后——封面候选帧从视频里抽取，
#     上传没完成时弹窗里没有可选帧。
STEPS: tuple[Step, ...] = (
    Step(Stage.UPLOAD, "投递视频", upload.run),
    Step(Stage.TITLE, "填写标题与话题", title.run),
    Step(Stage.SETTING, "配置发布设置", publish_setting.run),
    Step(Stage.DECLARATION, "设置自主声明", declaration.run),
    Step(Stage.CART, "挂载购物车", cart.run),
    Step(Stage.AWAIT_UPLOAD, "等待上传完成", await_upload.run),
    Step(Stage.COVER, "设置封面", cover.run),
    Step(Stage.PUBLISH, "点击发布", publish.run_click),
    Step(Stage.AWAIT_PUBLISH, "等待发布结果", publish.run_await_result),
)


# 不含任何发布动作的步骤序列，供真实环境冒烟脚本使用。
#
# 用「从完整流程中排除」而非「跑到发布前再判断」：前者在结构上就不可能发布——
# 发布步骤根本不在序列里，任何意外都不会让它被执行到。
# 该性质由 tests/unit/test_dry_run_steps.py 强制校验，
# 因为它一旦出错，后果是在用户账号下产生真实的发布。
DRY_RUN_STEPS: tuple[Step, ...] = tuple(
    step for step in STEPS if step.stage not in (Stage.PUBLISH, Stage.AWAIT_PUBLISH)
)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """流程的最终结论。"""

    code: ErrorCode
    stage: Stage
    message: str

    @property
    def ok(self) -> bool:
        """是否发布成功。"""
        return self.code.ok

    def __str__(self) -> str:
        return f"[{self.stage.value}] {self.code.name}({self.code.code}) {self.message}"


class _StageTracker:
    """记录主流程当前所处的阶段。

    哨兵本身不知道主流程走到哪了，但它给出的结论需要标注阶段——
    「上传失败」发生在等待上传时还是在填标题时，对排障是完全不同的信息。
    """

    def __init__(self, initial: Stage = Stage.NAVIGATE):
        self.current = initial


async def _run_steps(
    ctx: PipelineContext,
    tracker: _StageTracker,
    steps: tuple[Step, ...] = STEPS,
) -> PipelineResult:
    """按顺序执行给定的步骤。

    任何步骤失败都立即返回，不再执行后续步骤。
    """
    for step in steps:
        tracker.current = step.stage
        logger.info(f"[流程] ===== {step.title} =====")

        try:
            await step.run(ctx)
        except PublishError as exc:
            # 步骤通常不关心自己处于哪个阶段，在这里统一补齐
            exc.with_stage(step.stage)
            logger.error(f"[流程] {step.title} 失败：{exc}")
            return PipelineResult(exc.code, exc.stage or step.stage, exc.message)
        except asyncio.CancelledError:
            # 哨兵中止流程时走到这里，必须原样传播，否则取消会被吞掉
            logger.info(f"[流程] {step.title} 被中止")
            raise
        except Exception as exc:
            # 兜底：未预料的异常也要有确定的错误码，不能让进程裸崩
            logger.exception(f"[流程] {step.title} 出现未捕获异常")
            return PipelineResult(
                ErrorCode.UNEXPECTED, step.stage, f"{type(exc).__name__}: {exc}"
            )

    return PipelineResult(ErrorCode.SUCCESS, Stage.DONE, ErrorCode.SUCCESS.message)


async def _run_sentinel(ctx: PipelineContext, tracker: _StageTracker) -> PipelineResult:
    """持续监听致命事件，命中即返回结论。

    绝大多数时间都阻塞在取事件上，这是一个可取消点：
    主流程正常完成时，编排器会在这里把哨兵取消掉。
    """
    with ctx.bus.subscribe() as subscription:
        while True:
            event = await subscription.next()
            if not event.is_fatal:
                continue

            code = event.error_code
            assert code is not None  # is_fatal 为真时必有错误码
            logger.error(f"[哨兵] 命中致命事件，中止流程：{event}")
            return PipelineResult(code, tracker.current, event.text)


async def run_pipeline(
    config: TaskConfig,
    browser_context: BrowserContext,
    *,
    total_timeout: float | None = None,
    page_url: str = PUBLISH_PAGE_URL,
    steps: tuple[Step, ...] = STEPS,
) -> PipelineResult:
    """执行完整的发布流程。

    Args:
        config: 任务配置。
        browser_context: 已启动的浏览器上下文。
        total_timeout: 总时长上限（秒）。留空则取 config.timeouts.total；
            显式传值主要供测试使用。
        page_url: 发布页地址，测试时指向本地模拟页。
        steps: 要执行的步骤序列。默认是完整流程；
            真实环境的冒烟脚本会传入一个去掉发布动作的子集，
            从而在不产生真实发布的前提下验证前面所有步骤。

    Returns:
        流程结论。本函数不抛异常，所有失败都以结论的形式返回——
        调用方因此可以确信清理逻辑与结果输出一定会执行。
    """
    budget = total_timeout if total_timeout is not None else config.timeouts.total
    deadline = Deadline(budget)
    tracker = _StageTracker()

    # 按配置跳过非必要步骤。白名单已在配置校验阶段把关，
    # 上传与发布这类必要环节不可能出现在这里。
    skipped = config.skip_stages
    if skipped:
        names = "、".join(s.title for s in steps if s.stage in skipped)
        logger.info(f"[流程] 按配置跳过步骤：{names}")
        steps = tuple(s for s in steps if s.stage not in skipped)

    # 一、打开发布页
    page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
    try:
        await _open_publish_page(page, page_url, deadline, config.timeouts.navigate)
    except PublishError as exc:
        return PipelineResult(exc.code, exc.stage or Stage.NAVIGATE, exc.message)

    # 二、装上事件采集。必须在跑流程之前完成，否则早期提示会被漏掉。
    bus = EventBus()
    try:
        await install_toast_listener(page, bus)
    except Exception as exc:
        logger.exception("[流程] 安装轻提示监听器失败")
        return PipelineResult(
            ErrorCode.UNEXPECTED, Stage.NAVIGATE, f"安装事件监听失败：{exc}"
        )

    ctx = PipelineContext(config=config, page=page, bus=bus, deadline=deadline)

    # 三、主流程与哨兵竞速
    logger.info(f"[流程] 开始执行，总时长上限 {budget:.0f}s")
    result, _ = await wait_for_first(
        _run_steps(ctx, tracker, steps),
        _run_sentinel(ctx, tracker),
        timeout=budget,
    )

    if result is None:
        # 两者都没出结论，总超时兜底
        logger.error("[流程] 整体超时")
        return PipelineResult(
            ErrorCode.PIPELINE_TIMEOUT,
            tracker.current,
            f"流程超过 {budget:.0f}s 仍未结束",
        )

    logger.info(f"[流程] 结论：{result}")
    return result


async def _open_publish_page(
    page: Page, url: str, deadline: Deadline, timeout: float
) -> None:
    """打开发布页。

    用 domcontentloaded 而非 load：创作者中心会持续加载各类资源，
    等 load 事件可能要等很久，而 DOM 就绪时页面已经可以操作了。

    Raises:
        PublishError: NOT_LOGGED_IN —— 页面打不开时，最可能的原因是
            未登录导致的重定向。真正的登录态判定在「投递视频」步骤完成，
            那里的信号更可靠。
    """
    logger.info(f"[导航] 打开发布页：{url}")
    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=deadline.budget(timeout) * 1000,
        )
    except Exception as exc:
        raise PublishError(
            ErrorCode.NOT_LOGGED_IN,
            f"打开发布页失败：{exc}",
            stage=Stage.NAVIGATE,
            cause=exc,
        ) from exc

    logger.info(f"[导航] 页面已就绪：{await page.title()}")
