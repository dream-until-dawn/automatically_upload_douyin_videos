"""失败现场截图。

## 为什么值得做

错误码只能告诉你「哪一步挂了」，告诉不了你「页面当时长什么样」。
而页面改版导致的失败，恰恰只有看到当时的页面才能判断：
是元素挪了位置、换了文案，还是弹出了一个没预料到的提示框。

截图把这个信息补上，让改版排查从「反复复现」变成「看一眼」。

## 三条约束

1. **只在失败时截**：成功路径不需要，也不该为此多花时间。
2. **绝不影响主流程**：截图是辅助信息。它失败了就失败了，
   不能反过来把一个有明确错误码的结果变成截图异常。
   因此这里吞掉所有异常，只记一条警告。
3. **不阻塞退出**：带超时保护。浏览器此时可能正处于异常状态，
   没有超时的话，一次截图可能让进程迟迟不退出。
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import time
from pathlib import Path

from playwright.async_api import Page

from douyin_publisher.core.logging import get_logger
from douyin_publisher.core.stages import Stage

logger = get_logger("browser.screenshot")

# 截图超时（秒）。失败时页面可能已处于异常状态，不能无限等待。
CAPTURE_TIMEOUT = 15.0

# 默认存放目录（位于系统临时目录下）
DEFAULT_SUBDIR = "douyin_publisher_shots"

# 文件名中不安全的字符
_UNSAFE = re.compile(r"[^0-9A-Za-z_.-]")


def _safe(text: str, limit: int = 40) -> str:
    """把任意文本转成可安全用作文件名的片段。

    任务 ID 由上游给出，可能含路径分隔符或其他特殊字符；
    不做清洗会让截图写到意料之外的位置，或直接失败。
    """
    cleaned = _UNSAFE.sub("_", text.strip())
    return cleaned[:limit] or "unknown"


def resolve_directory(configured: str) -> Path:
    """决定截图存放目录。

    Args:
        configured: 配置的目录，留空则使用系统临时目录下的默认子目录。
    """
    if configured.strip():
        return Path(configured.strip())
    return Path(tempfile.gettempdir()) / DEFAULT_SUBDIR


def build_filename(task_id: str, stage: Stage) -> str:
    """构造截图文件名：时间 + 任务 + 阶段。

    三者都放进文件名，是为了在一堆截图里不必打开就能定位：
    哪个任务、卡在哪一步、什么时候。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{_safe(task_id)}_{stage.value}.png"


async def capture_failure(
    page: Page, directory: str, task_id: str, stage: Stage
) -> str | None:
    """截取失败现场，返回截图的绝对路径。

    Args:
        page: 目标页面。
        directory: 配置的存放目录，留空则用系统临时目录。
        task_id: 任务标识，写入文件名便于关联。
        stage: 失败所处阶段。

    Returns:
        截图的绝对路径；任何环节出错都返回 None——
        **本函数不抛异常**，截图是辅助信息，不能影响已有的失败结论。
    """
    try:
        target_dir = resolve_directory(directory)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / build_filename(task_id, stage)

        async with asyncio.timeout(CAPTURE_TIMEOUT):
            # full_page=False：只截可视区域。失败现场通常就在当前视口，
            # 整页截图在长页面上既慢又容易超时。
            await page.screenshot(path=str(path), full_page=False)

        logger.info(f"[截图] 已保存失败现场：{path}")
        return str(path)
    except TimeoutError:
        logger.warning(f"[截图] 超过 {CAPTURE_TIMEOUT:.0f}s 未完成，放弃")
        return None
    except Exception as exc:
        # 包括目录不可写、页面已关闭、磁盘满等。
        # 一律降级为警告：此时已经有一个明确的失败原因了，
        # 不该让截图问题把它盖掉。
        logger.warning(f"[截图] 保存失败现场时出错（已忽略）：{exc}")
        return None
