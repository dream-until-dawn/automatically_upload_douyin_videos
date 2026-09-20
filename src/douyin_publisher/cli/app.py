"""命令行入口：子命令分发、流程编排与结果输出。

本模块是 **唯一** 产生进程退出码的地方。业务层只抛 PublishError，
由这里统一翻译成退出码与 JSON 结果——这样才能保证无论走哪条失败路径，
浏览器都会被关闭、结果都会被输出。

调用协议见 docs/cli-protocol.md。
"""

from __future__ import annotations

import asyncio
import time

from douyin_publisher.browser.launcher import browser_session
from douyin_publisher.cli.result import TaskResult, emit
from douyin_publisher.config.loader import (
    load_task_config,
    resolve_user_data_dir,
    verify_local_environment,
)
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.logging import get_logger, setup_logging
from douyin_publisher.core.stages import Stage
from douyin_publisher.pipeline.runner import run_pipeline
from douyin_publisher.system.processes import (
    close_browsers_by_user_data_dir,
    close_video_editor,
)

logger = get_logger("cli")

# 子命令名。同时接受短横线与驼峰两种写法，以便上游无需改动即可对接。
CMD_PUBLISH = "publish"
CMD_CLOSE_CHROME = {"close-chrome", "closeChrome"}
CMD_CLOSE_EDITOR = {"close-jianying", "closeJianying"}
CMD_SELFCHECK = {"selfcheck", "self-check"}


async def _run_selfcheck(finish) -> TaskResult:
    """验证运行时依赖是否齐备。

    打包产物最典型的故障是「能构建、不能跑」：依赖被漏收、
    Playwright 的 driver 在冻结环境下路径解析失败等等——
    这些问题在构建日志里完全看不出来，只有真正跑一次才会暴露。

    因此这里刻意 **真的启动** Playwright 驱动，而不是只做 import 检查：
    import 成功不代表驱动进程能拉起来。
    """
    problems: list[str] = []

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            # 访问 chromium 会促使驱动完成初始化
            _ = playwright.chromium
    except Exception as exc:
        problems.append(f"Playwright 驱动不可用（{type(exc).__name__}: {exc}）")

    try:
        import psutil

        psutil.cpu_count()
    except Exception as exc:
        problems.append(f"psutil 不可用（{type(exc).__name__}: {exc}）")

    if problems:
        return finish(ErrorCode.UNEXPECTED, Stage.CONFIG, "；".join(problems))

    return finish(ErrorCode.SUCCESS, Stage.CONFIG, "自检通过：运行时依赖齐备")


async def run(argv: list[str]) -> TaskResult:
    """执行一次命令。

    Args:
        argv: 不含程序名的命令行参数，即 sys.argv[1:]。

    Returns:
        执行结果。本函数不抛异常：任何失败都被翻译成结果对象，
        以保证调用方的输出与清理逻辑一定会执行。
    """
    started = time.monotonic()

    def finish(
        code: ErrorCode,
        stage: Stage,
        message: str,
        screenshot: str | None = None,
        **ids: str,
    ) -> TaskResult:
        """构造结果并自动补上耗时。

        各条返回路径都经由它，耗时统计因此不会漏填——
        失败路径上的耗时同样有价值，上游据此统计各类失败的成本。
        """
        return TaskResult(
            code=code,
            stage=stage,
            message=message,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            screenshot=screenshot,
            **ids,
        )

    if not argv:
        return finish(
            ErrorCode.ARGS_MISSING, Stage.CONFIG, "缺少子命令，请参考调用协议"
        )

    action = argv[0]

    # ---- 自检 ----
    if action in CMD_SELFCHECK:
        return await _run_selfcheck(finish)

    # ---- 进程清理类子命令 ----
    if action in CMD_CLOSE_EDITOR:
        count = await close_video_editor()
        return finish(ErrorCode.SUCCESS, Stage.CLEANUP, f"已清理 {count} 个剪辑软件进程")

    if action in CMD_CLOSE_CHROME:
        if len(argv) < 2:
            return finish(
                ErrorCode.ARGS_MISSING, Stage.CONFIG, "close-chrome 需要传入用户数据目录"
            )
        try:
            target = resolve_user_data_dir(argv[1])
        except PublishError as exc:
            return finish(exc.code, exc.stage or Stage.CONFIG, exc.message)

        count = await close_browsers_by_user_data_dir(str(target))
        return finish(ErrorCode.SUCCESS, Stage.CLEANUP, f"已清理 {count} 个浏览器进程")

    # ---- 发布 ----
    if action != CMD_PUBLISH:
        return finish(
            ErrorCode.CONFIG_INVALID, Stage.CONFIG, f"未知的子命令：{action}"
        )

    if len(argv) < 2:
        return finish(
            ErrorCode.ARGS_MISSING, Stage.CONFIG, "publish 需要传入任务配置"
        )

    return await _run_publish(argv[1], finish)


async def _run_publish(raw_config: str, finish) -> TaskResult:
    """执行发布流程。

    Args:
        raw_config: 原始配置参数（Base64 或裸 JSON）。
        finish: 结果构造函数，由 run() 提供以统一填充耗时。
    """
    # 一、解析与校验配置
    try:
        config = load_task_config(raw_config)
        user_data_dir = verify_local_environment(config)
    except PublishError as exc:
        return finish(exc.code, exc.stage or Stage.CONFIG, exc.message)

    # 配置解析成功后立刻应用日志级别，让后续输出受控。
    # 此前的解析错误日志仍按默认级别输出——那时还不知道调用方想要什么级别。
    setup_logging(config.log_level.logging_level)

    ids = {"task_id": config.task_id, "douyin_id": config.douyin_id}
    logger.info(f"[启动] {config}")

    # 二、启动前清场：终止仍占用该用户数据目录的历史进程。
    #     不清理的话，浏览器会拒绝启动或复用旧实例，导致自动化失控。
    logger.info(f"[清理] 检查占用 [{user_data_dir}] 的历史浏览器进程")
    await close_browsers_by_user_data_dir(str(user_data_dir))

    # 三、启动浏览器并跑流程
    try:
        async with browser_session(config, user_data_dir) as browser_context:
            # 不传 total_timeout：让 run_pipeline 从 config.timeouts.total 取，
            # 否则调用方配置的总超时会被这里的固定值盖掉。
            result = await run_pipeline(config, browser_context)
    except PublishError as exc:
        return finish(exc.code, exc.stage or Stage.LAUNCH, exc.message, **ids)
    except Exception as exc:
        logger.exception("[启动] 执行过程中出现未捕获异常")
        return finish(
            ErrorCode.UNEXPECTED, Stage.LAUNCH, f"{type(exc).__name__}: {exc}", **ids
        )

    return finish(
        result.code, result.stage, result.message, result.screenshot, **ids
    )


def main(argv: list[str] | None = None) -> int:
    """同步入口，返回进程退出码。

    Args:
        argv: 不含程序名的参数；留空则取 sys.argv[1:]。
    """
    import sys

    setup_logging()
    args = sys.argv[1:] if argv is None else argv

    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        # 用户手动中断不属于程序缺陷，但仍需给出结构化结果
        result = TaskResult(
            ErrorCode.UNEXPECTED, Stage.CONFIG, "执行被手动中断"
        )
    except Exception as exc:
        # 最外层兜底：绝不让进程以裸异常和不确定的退出码结束
        logger.exception("[致命] 顶层未捕获异常")
        result = TaskResult(
            ErrorCode.UNEXPECTED, Stage.CONFIG, f"{type(exc).__name__}: {exc}"
        )

    emit(result)
    return result.code.code
