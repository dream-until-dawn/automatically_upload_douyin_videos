"""浏览器启动与安全关闭。

采用 **持久化上下文**（persistent context）而非普通的 launch + new_context：
只有持久化上下文才能复用用户数据目录中已有的登录态，
而本程序的前提就是「账号已在该画像中登录」。

关闭逻辑被刻意设计成「无论如何都不抛异常」：它运行在 finally 路径上，
若在此处抛错，会把真正的失败原因掩盖掉，上游看到的将是一个毫不相关的错误码。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright, async_playwright

from douyin_publisher.config.models import TaskConfig
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.logging import get_logger
from douyin_publisher.core.stages import Stage

logger = get_logger("browser.launcher")

# 关闭浏览器的等待上限（秒）。超过即放弃等待，进程退出时系统会回收。
CLOSE_TIMEOUT = 10.0

# 内置启动参数。调用方配置的参数会 **追加** 在其后，不替换这些。
_LAUNCH_ARGS = [
    # 关闭 navigator.webdriver 标记，降低被识别为自动化的概率
    "--disable-blink-features=AutomationControlled",
]


def build_launch_args(extra: list[str]) -> list[str]:
    """把调用方追加的参数拼到内置参数之后。

    追加而非替换：内置参数是程序正常工作的前提，不该被外部配置覆盖掉。
    会破坏运行前提的参数（如 --user-data-dir）在配置校验阶段就被拒绝了，
    见 config/runtime.py 的 validate_browser_args。
    """
    return [*_LAUNCH_ARGS, *extra]


async def launch_context(
    playwright: Playwright,
    config: TaskConfig,
    user_data_dir: Path,
) -> BrowserContext:
    """以持久化上下文启动浏览器。

    Args:
        playwright: 已启动的 Playwright 实例。
        config: 任务配置，提供浏览器路径与无头开关。
        user_data_dir: 已规范化的用户数据目录绝对路径。

    Returns:
        浏览器上下文。

    Raises:
        PublishError: BROWSER_LAUNCH_FAILED。
    """
    extra_note = (
        f"，附加参数 {len(config.browser_args)} 项" if config.browser_args else ""
    )
    logger.info(f"[启动] 正在启动浏览器（无头={config.headless}{extra_note}）")

    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            executable_path=config.exec_path,
            headless=config.headless,
            args=build_launch_args(config.browser_args),
        )
    except Exception as exc:
        raise PublishError(
            ErrorCode.BROWSER_LAUNCH_FAILED,
            str(exc),
            stage=Stage.LAUNCH,
            cause=exc,
        ) from exc

    logger.info("[启动] 浏览器已就绪")
    return context


async def close_context(context: BrowserContext) -> None:
    """安全关闭浏览器上下文。

    本函数 **不会** 抛出异常。它运行在清理路径上，
    此处抛错会掩盖真正的失败原因，让上游拿到一个牛头不对马嘴的错误码。

    同时加了超时保护：浏览器偶尔会卡在关闭过程中，
    没有超时的话整个进程会一直挂着不退出。
    """
    logger.info("[清理] 正在关闭浏览器")
    try:
        async with asyncio.timeout(CLOSE_TIMEOUT):
            await context.close()
        logger.info("[清理] 浏览器已关闭")
    except TimeoutError:
        logger.warning(f"[清理] 关闭浏览器超过 {CLOSE_TIMEOUT:.0f}s，放弃等待")
    except Exception as exc:
        logger.warning(f"[清理] 关闭浏览器时出错（已忽略）：{exc}")


@asynccontextmanager
async def browser_session(
    config: TaskConfig, user_data_dir: Path
) -> AsyncIterator[BrowserContext]:
    """浏览器会话的上下文管理器，确保无论如何都会清理。

    「无论如何」包括任务被取消的情况——这正是竞速中止时会走到的路径。
    若清理只写在正常返回分支上，哨兵中止后浏览器进程会残留下来，
    下一次任务会因用户数据目录被占用而直接启动失败。

    Example:
        async with browser_session(config, data_dir) as context:
            ...
    """
    async with async_playwright() as playwright:
        context = await launch_context(playwright, config, user_data_dir)
        try:
            yield context
        finally:
            # 取消传播到这里时，仍然要完成清理。
            # asyncio.shield 防止清理动作自身被再次取消而半途而废。
            await asyncio.shield(close_context(context))
