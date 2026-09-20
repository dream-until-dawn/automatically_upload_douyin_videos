"""日志初始化与双通道输出。

通道划分（见 docs/cli-protocol.md 第 4 节）：

  · stderr —— 人类可读的中文过程日志，供开发者排障；
  · stdout —— 有且仅有一行：结束时输出的单行 JSON 结果，供上游程序解析。

这样分流的意义在于：上游只需读 stdout 最后一行即可拿到完整结论，
不必在混杂的日志里做正则匹配。

编码约束（由 M0 探针实测确认）：
    Windows 控制台默认使用 GBK 代码页，直接写入中文会乱码，写入 emoji 会直接
    抛出 UnicodeEncodeError 导致进程异常退出。因此两个标准流都必须显式切到
    UTF-8，且日志文本中不使用 emoji。
"""

from __future__ import annotations

import logging
import sys
from typing import Final

_LOGGER_NAME: Final = "douyin_publisher"

# 模块级标记，避免重复初始化导致日志重复输出
_configured = False


def force_utf8_streams() -> None:
    """把标准输出与标准错误切换到 UTF-8。

    必须在任何输出之前调用。errors="replace" 作为兜底：
    即使遇到极少数无法编码的字符，也只会显示成替换符，而不会让进程崩溃。
    """
    for stream in (sys.stdout, sys.stderr):
        # 在极少数被重定向到非文本流的场景下可能没有 reconfigure，容错处理
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # 流已被关闭或不支持重配置时忽略，不影响主流程
                pass


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化日志系统，返回本项目的根 logger。

    所有日志一律写入 stderr —— stdout 被保留给机器可读的 JSON 结果。

    Args:
        level: 日志级别，默认 INFO。

    Returns:
        本项目的根 logger。
    """
    global _configured

    force_utf8_streams()
    logger = logging.getLogger(_LOGGER_NAME)

    if _configured:
        logger.setLevel(level)
        return logger

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger.setLevel(level)
    logger.addHandler(handler)
    # 不向 root logger 冒泡，避免第三方库的配置导致日志重复
    logger.propagate = False

    _configured = True
    return logger


def get_logger(module: str | None = None) -> logging.Logger:
    """获取子 logger。

    Args:
        module: 子模块名，例如 "pipeline.upload"。留空返回根 logger。
    """
    if module:
        return logging.getLogger(f"{_LOGGER_NAME}.{module}")
    return logging.getLogger(_LOGGER_NAME)


def reset_logging() -> None:
    """清理日志配置，仅供测试使用。

    测试会反复初始化日志，若不清理会导致 handler 累积、输出成倍重复。
    """
    global _configured

    logger = logging.getLogger(_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    _configured = False
