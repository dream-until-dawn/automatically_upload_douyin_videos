"""任务配置的解析与校验。

职责分为三段，各自对应不同的错误码，便于上游精确定位责任方：

  1. **解码**（19 CONFIG_PARSE_FAILED）：把命令行参数还原成 dict。
  2. **结构绑定与必填校验**（19 / 3）：缺字段是格式问题，字段为空是取值问题。
  3. **本机环境校验**（4 / 5 / 6 / 7）：文件与目录是否真实可用。

三段分离的好处是每一段都能被独立测试，而不需要造出一个「什么都对」的完整环境。
"""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from douyin_publisher.config.models import TaskConfig
from douyin_publisher.config.runtime import validate_browser_args, validate_skip_stages
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.stages import Stage

# 命令行传参时可能被 Shell 或上游附加在外层的字符，解析前一律剥除
_WRAPPING_CHARS = "'\" \r\n\t"

# 必填且不允许为空字符串的字段（字段名 -> 面向人的说明）
_REQUIRED_FIELDS = {
    "exec_path": "execPath（浏览器路径）",
    "user_data_dir": "userDataDir（用户数据目录）",
    "task_id": "taskId（任务 ID）",
    "douyin_id": "douyinId（抖音账号标识）",
    "video_path": "videoPath（视频路径）",
    "cart_url": "cartUrl（商品链接）",
}


def decode_config_payload(raw: str) -> dict[str, Any]:
    """把命令行传入的配置参数还原为 dict。

    支持两种编码，按顺序尝试：

      1. Base64 编码的 UTF-8 JSON（推荐，可规避 Windows 命令行的转义问题）；
      2. 裸 JSON 字符串（仅建议手工调试时使用）。

    Args:
        raw: 原始命令行参数。

    Returns:
        解析出的字典。

    Raises:
        PublishError: CONFIG_PARSE_FAILED —— 两种方式都无法解析。
    """
    cleaned = raw.strip(_WRAPPING_CHARS)
    if not cleaned:
        raise PublishError(
            ErrorCode.CONFIG_PARSE_FAILED, "配置参数为空", stage=Stage.CONFIG
        )

    # 尝试一：Base64。validate=True 让非 Base64 字符直接报错，
    # 避免把一段裸 JSON 误当作 Base64 解出乱码。
    try:
        decoded = base64.b64decode(cleaned, validate=True)
        parsed = json.loads(decoded.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
    except (binascii.Error, ValueError, UnicodeDecodeError):
        pass  # 不是 Base64，继续尝试裸 JSON

    # 尝试二：裸 JSON。部分调用方会把内层引号转义成 \"，一并还原。
    try:
        parsed = json.loads(cleaned.replace(r"\"", '"'))
    except ValueError as exc:
        raise PublishError(
            ErrorCode.CONFIG_PARSE_FAILED,
            f"既不是合法的 Base64 也不是合法的 JSON：{exc}",
            stage=Stage.CONFIG,
            cause=exc,
        ) from exc

    if not isinstance(parsed, dict):
        raise PublishError(
            ErrorCode.CONFIG_PARSE_FAILED,
            f"配置必须是 JSON 对象，实际是 {type(parsed).__name__}",
            stage=Stage.CONFIG,
        )
    return parsed


def bind_task_config(payload: dict[str, Any]) -> TaskConfig:
    """把 dict 绑定为配置模型，并校验必填字段非空。

    这里刻意区分两类问题：

      · **缺少字段** -> 19，属于调用方传参格式不对；
      · **字段存在但为空** -> 3，属于取值不合法。

    上游据此就能知道是自己少拼了字段，还是某个值没取到。

    Args:
        payload: 解码后的配置字典。

    Returns:
        校验通过的配置模型。

    Raises:
        PublishError: CONFIG_PARSE_FAILED 或 CONFIG_INVALID。
    """
    try:
        config = TaskConfig(**payload)
    except ValidationError as exc:
        missing = [
            ".".join(str(part) for part in err["loc"])
            for err in exc.errors()
            if err["type"] == "missing"
        ]
        detail = f"缺少必填字段 {missing}" if missing else str(exc)
        raise PublishError(
            ErrorCode.CONFIG_PARSE_FAILED, detail, stage=Stage.CONFIG, cause=exc
        ) from exc

    empty = [
        label
        for field, label in _REQUIRED_FIELDS.items()
        if not str(getattr(config, field)).strip()
    ]
    if empty:
        raise PublishError(
            ErrorCode.CONFIG_INVALID,
            f"以下必填字段不能为空：{'、'.join(empty)}",
            stage=Stage.CONFIG,
        )

    # 运行时选项的取值校验。
    #
    # 归为 CONFIG_INVALID 而非 CONFIG_PARSE_FAILED：字段本身拼对了，
    # 是取值不合法。上游据此能分清「我 JSON 拼错了」和「我填了个不让填的值」。
    try:
        validate_browser_args(config.browser_args)
        validate_skip_stages(config.skip)
    except ValueError as exc:
        raise PublishError(
            ErrorCode.CONFIG_INVALID, str(exc), stage=Stage.CONFIG, cause=exc
        ) from exc

    return config


def resolve_user_data_dir(user_data_dir: str) -> Path:
    """把用户数据目录规范化为绝对路径。

    规范化是必需的：后续要按 --user-data-dir 命令行参数匹配并清理残留的
    浏览器进程，若两侧路径格式不一致（相对/绝对、大小写、斜杠方向），
    匹配会失败，导致清理漏杀、浏览器因目录被占用而启动失败。

    Raises:
        PublishError: PATH_RESOLVE_FAILED。
    """
    try:
        return Path(user_data_dir).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise PublishError(
            ErrorCode.PATH_RESOLVE_FAILED,
            f"{user_data_dir} -> {exc}",
            stage=Stage.CONFIG,
            cause=exc,
        ) from exc


def verify_local_environment(config: TaskConfig) -> Path:
    """校验本机环境是否满足执行条件。

    检查顺序与错误码一一对应，先检查的问题优先报出。

    Args:
        config: 已通过结构校验的配置。

    Returns:
        规范化后的用户数据目录绝对路径。

    Raises:
        PublishError: CHROME_NOT_FOUND / USER_DATA_DIR_INVALID /
            VIDEO_NOT_FOUND / PATH_RESOLVE_FAILED。
    """
    # 1. 浏览器可执行文件
    if not _is_file(config.exec_path):
        raise PublishError(
            ErrorCode.CHROME_NOT_FOUND, config.exec_path, stage=Stage.CONFIG
        )

    # 2. 用户数据目录：必须存在、是目录、且非空。
    #    空目录意味着没有任何登录态，继续执行必然在「未登录」处失败，
    #    不如在此处就给出更准确的错误码。
    resolved_dir = resolve_user_data_dir(config.user_data_dir)
    if not _is_non_empty_dir(resolved_dir):
        raise PublishError(
            ErrorCode.USER_DATA_DIR_INVALID,
            f"{resolved_dir}（需为存在且非空的目录）",
            stage=Stage.CONFIG,
        )

    # 3. 待发布视频
    if not _is_file(config.video_path):
        raise PublishError(
            ErrorCode.VIDEO_NOT_FOUND, config.video_path, stage=Stage.CONFIG
        )

    return resolved_dir


def load_task_config(raw: str) -> TaskConfig:
    """解码并绑定配置（不含本机环境校验）。

    环境校验单独暴露为 verify_local_environment，便于测试在不造真实文件的
    前提下验证解析逻辑。
    """
    return bind_task_config(decode_config_payload(raw))


# ----------------------------------------------------------------------
# 内部工具：把文件系统异常统一转换为布尔值
# ----------------------------------------------------------------------


def _is_file(path: str | Path) -> bool:
    """路径是否为可访问的文件。权限不足等异常一律视为不可用。"""
    try:
        return Path(path).is_file()
    except (OSError, ValueError):
        return False


def _is_non_empty_dir(path: str | Path) -> bool:
    """路径是否为存在且非空的目录。"""
    try:
        target = Path(path)
        if not target.is_dir():
            return False
        # 只要能取到第一个条目就说明非空，无需完整遍历
        return any(target.iterdir())
    except (OSError, ValueError):
        return False
