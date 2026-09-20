"""配置解析与校验的正反向测试。

覆盖 docs/testing.md 第 3.1 节列出的全部配置层场景。
每条反向用例都断言 **具体的错误码**，而不是只断言「抛了异常」——
若只断言抛异常，把错误码写错也测不出来，而错误码正是上游的决策依据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from douyin_publisher.config.loader import (
    bind_task_config,
    decode_config_payload,
    load_task_config,
    verify_local_environment,
)
from douyin_publisher.config.models import (
    DEFAULT_PUBLISH_DELAY_HOURS,
    DEFAULT_SELF_DECLARATION,
    MAX_PUBLISH_DELAY_HOURS,
    TaskConfig,
)
from douyin_publisher.core.errors import ErrorCode, PublishError

from tests.conftest import to_base64


def assert_error_code(exc_info: pytest.ExceptionInfo[PublishError], expected: ErrorCode) -> None:
    """断言异常携带的错误码，失败时给出可读的对比信息。"""
    actual = exc_info.value.code
    assert actual is expected, f"期望错误码 {expected.name}，实际为 {actual.name}"


# ======================================================================
# 正向：解码
# ======================================================================


def test_能解析base64编码的配置(valid_payload: dict[str, Any]) -> None:
    result = decode_config_payload(to_base64(valid_payload))
    assert result["taskId"] == "task-1"


def test_能解析裸json配置(valid_payload: dict[str, Any]) -> None:
    result = decode_config_payload(json.dumps(valid_payload, ensure_ascii=False))
    assert result["taskId"] == "task-1"


@pytest.mark.parametrize(
    "wrapper",
    ['"{payload}"', "'{payload}'", " {payload} ", "{payload}\r\n"],
    ids=["双引号", "单引号", "两侧空格", "尾随换行"],
)
def test_能剥除外层包裹字符(valid_payload: dict[str, Any], wrapper: str) -> None:
    """Shell 与上游可能在参数外层附加引号或空白，必须容忍。"""
    encoded = to_base64(valid_payload)
    result = decode_config_payload(wrapper.format(payload=encoded))
    assert result["taskId"] == "task-1"


# ======================================================================
# 反向：解码失败 -> 19
# ======================================================================


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "这不是配置",
        "{不是合法的json}",
        "[1, 2, 3]",  # 合法 JSON，但不是对象
        to_base64({}).replace("=", "@"),  # 破坏 Base64 字符集
    ],
    ids=["空串", "纯空白", "无意义文本", "残缺JSON", "JSON数组非对象", "非法Base64字符"],
)
def test_无法解析的配置报19(raw: str) -> None:
    with pytest.raises(PublishError) as exc_info:
        decode_config_payload(raw)
    assert_error_code(exc_info, ErrorCode.CONFIG_PARSE_FAILED)


def test_base64解出的内容不是json也报19() -> None:
    import base64 as b64

    raw = b64.b64encode("这是一段纯文本不是 JSON".encode()).decode()
    with pytest.raises(PublishError) as exc_info:
        decode_config_payload(raw)
    assert_error_code(exc_info, ErrorCode.CONFIG_PARSE_FAILED)


# ======================================================================
# 反向：结构绑定 -> 19（缺字段）与 3（字段为空）
# ======================================================================


@pytest.mark.parametrize(
    "missing_field",
    ["execPath", "userDataDir", "taskId", "douyinId", "videoPath", "cartUrl"],
)
def test_缺少必填字段报19(valid_payload: dict[str, Any], missing_field: str) -> None:
    """缺字段属于调用方拼装格式有误，归类为解析失败。"""
    payload = {k: v for k, v in valid_payload.items() if k != missing_field}
    with pytest.raises(PublishError) as exc_info:
        bind_task_config(payload)
    assert_error_code(exc_info, ErrorCode.CONFIG_PARSE_FAILED)
    assert missing_field in str(exc_info.value), "错误信息应指出缺了哪个字段"


@pytest.mark.parametrize(
    "empty_field",
    ["execPath", "userDataDir", "taskId", "douyinId", "videoPath", "cartUrl"],
)
def test_必填字段为空字符串报3(valid_payload: dict[str, Any], empty_field: str) -> None:
    """字段存在但为空属于取值不合法，与缺字段区分开。"""
    payload = {**valid_payload, empty_field: ""}
    with pytest.raises(PublishError) as exc_info:
        bind_task_config(payload)
    assert_error_code(exc_info, ErrorCode.CONFIG_INVALID)


def test_必填字段为纯空白同样报3(valid_payload: dict[str, Any]) -> None:
    """只有空格的值等同于空，不能蒙混过关。"""
    payload = {**valid_payload, "taskId": "   "}
    with pytest.raises(PublishError) as exc_info:
        bind_task_config(payload)
    assert_error_code(exc_info, ErrorCode.CONFIG_INVALID)


def test_选填字段缺失不报错(valid_payload: dict[str, Any]) -> None:
    """选填字段全部拿掉也应成功，并落到各自的默认值。"""
    required = {
        "execPath", "userDataDir", "taskId", "douyinId", "videoPath", "cartUrl",
    }
    payload = {k: v for k, v in valid_payload.items() if k in required}

    config = bind_task_config(payload)
    assert config.title == ""
    assert config.headless is False
    assert config.effective_self_declaration == DEFAULT_SELF_DECLARATION


def test_未知字段被忽略(valid_payload: dict[str, Any]) -> None:
    """上游未来新增字段时，旧版本程序不应因此报错。"""
    payload = {**valid_payload, "someFutureField": "whatever"}
    config = bind_task_config(payload)
    assert config.task_id == "task-1"


# ======================================================================
# 派生属性：归一化逻辑
# ======================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("24", 24),
        ("1", 1),
        ("300", MAX_PUBLISH_DELAY_HOURS),
        (" 48 ", 48),
        ("301", MAX_PUBLISH_DELAY_HOURS),  # 超上限截断
        ("9999", MAX_PUBLISH_DELAY_HOURS),
        ("0", DEFAULT_PUBLISH_DELAY_HOURS),  # 非正数回落默认
        ("-5", DEFAULT_PUBLISH_DELAY_HOURS),
        ("", DEFAULT_PUBLISH_DELAY_HOURS),  # 缺失回落默认
        ("abc", DEFAULT_PUBLISH_DELAY_HOURS),  # 非数字回落默认
        ("12.5", DEFAULT_PUBLISH_DELAY_HOURS),  # 小数不接受，回落默认
    ],
)
def test_定时发布小时数归一化(
    valid_payload: dict[str, Any], raw: str, expected: int
) -> None:
    """取值异常时采用容错策略，不因此让整个任务失败。"""
    config = bind_task_config({**valid_payload, "publishTime": raw})
    assert config.publish_delay_hours == expected


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        ("夏日穿搭,清凉一夏", ["夏日穿搭", "清凉一夏"]),
        ("  带空格 , 也要清理 ", ["带空格", "也要清理"]),
        ("空项,,被丢弃", ["空项", "被丢弃"]),
        ("", []),
        (",,,", []),
    ],
)
def test_话题标签解析(valid_payload: dict[str, Any], desc: str, expected: list[str]) -> None:
    config = bind_task_config({**valid_payload, "desc": desc})
    assert config.tags == expected


@pytest.mark.parametrize(
    ("cart_titel", "origin", "expected"),
    [
        ("指定短标题", "商品原标题很长很长很长", "指定短标题"),
        ("", "商品原标题很长很长很长很长", "商品原标题很长很长很"),  # 截取前 10 字
        ("超过十个字的指定短标题啊啊啊", "原标题", "超过十个字的指定短标"),  # 指定值同样截断
        ("", "短", "短"),
    ],
)
def test_商品短标题决策(
    valid_payload: dict[str, Any], cart_titel: str, origin: str, expected: str
) -> None:
    config = bind_task_config({**valid_payload, "cartTitel": cart_titel})
    assert config.resolve_short_title(origin) == expected


def test_发布模式判定(valid_payload: dict[str, Any]) -> None:
    assert bind_task_config({**valid_payload, "publishTimeMode": "定时发布"}).is_scheduled
    assert not bind_task_config({**valid_payload, "publishTimeMode": "立即发布"}).is_scheduled
    assert not bind_task_config({**valid_payload, "publishTimeMode": ""}).is_scheduled


def test_日志摘要不泄露用户数据目录(valid_payload: dict[str, Any]) -> None:
    """用户画像目录属于敏感路径，日志摘要中不应出现。"""
    config = bind_task_config(valid_payload)
    assert config.user_data_dir not in str(config)
    assert config.task_id in str(config)


def test_可用下划线字段名直接构造() -> None:
    """便于测试与内部代码构造配置，无需走别名。"""
    config = TaskConfig(
        exec_path="a", user_data_dir="b", task_id="c",
        douyin_id="d", video_path="e", cart_url="f",
    )
    assert config.task_id == "c"


# ======================================================================
# 反向：本机环境校验 -> 4 / 5 / 6
# ======================================================================


def test_环境完整时校验通过(valid_payload: dict[str, Any]) -> None:
    config = bind_task_config(valid_payload)
    resolved = verify_local_environment(config)
    assert resolved.is_absolute(), "返回的用户数据目录必须是绝对路径"
    assert resolved.is_dir()


def test_浏览器不存在报4(valid_payload: dict[str, Any], tmp_path: Path) -> None:
    config = bind_task_config({**valid_payload, "execPath": str(tmp_path / "缺失.exe")})
    with pytest.raises(PublishError) as exc_info:
        verify_local_environment(config)
    assert_error_code(exc_info, ErrorCode.CHROME_NOT_FOUND)


def test_浏览器路径指向目录也报4(valid_payload: dict[str, Any], tmp_path: Path) -> None:
    """路径存在但不是文件，同样不可用。"""
    directory = tmp_path / "是个目录"
    directory.mkdir()
    config = bind_task_config({**valid_payload, "execPath": str(directory)})
    with pytest.raises(PublishError) as exc_info:
        verify_local_environment(config)
    assert_error_code(exc_info, ErrorCode.CHROME_NOT_FOUND)


def test_用户数据目录不存在报5(valid_payload: dict[str, Any], tmp_path: Path) -> None:
    config = bind_task_config({**valid_payload, "userDataDir": str(tmp_path / "不存在")})
    with pytest.raises(PublishError) as exc_info:
        verify_local_environment(config)
    assert_error_code(exc_info, ErrorCode.USER_DATA_DIR_INVALID)


def test_用户数据目录为空目录报5(valid_payload: dict[str, Any], tmp_path: Path) -> None:
    """空目录意味着没有任何登录态，提前报错比走到「未登录」更准确。"""
    empty = tmp_path / "空目录"
    empty.mkdir()
    config = bind_task_config({**valid_payload, "userDataDir": str(empty)})
    with pytest.raises(PublishError) as exc_info:
        verify_local_environment(config)
    assert_error_code(exc_info, ErrorCode.USER_DATA_DIR_INVALID)


def test_用户数据目录是文件报5(valid_payload: dict[str, Any], tmp_path: Path) -> None:
    file_path = tmp_path / "是个文件.txt"
    file_path.write_text("x", encoding="utf-8")
    config = bind_task_config({**valid_payload, "userDataDir": str(file_path)})
    with pytest.raises(PublishError) as exc_info:
        verify_local_environment(config)
    assert_error_code(exc_info, ErrorCode.USER_DATA_DIR_INVALID)


def test_路径规范化失败报7(monkeypatch: pytest.MonkeyPatch) -> None:
    """路径解析失败在正常环境下几乎不会发生，只能靠打桩构造。

    但它必须有确定的错误码：少了这条分支，一个罕见的文件系统异常
    会一路冒泡成兜底的 88，让上游误以为是程序缺陷。
    """
    from douyin_publisher.config.loader import resolve_user_data_dir

    def boom(self: Path, *args: Any, **kwargs: Any) -> Path:
        raise OSError("模拟路径解析失败")

    monkeypatch.setattr(Path, "resolve", boom)

    with pytest.raises(PublishError) as exc_info:
        resolve_user_data_dir("D:/whatever")
    assert_error_code(exc_info, ErrorCode.PATH_RESOLVE_FAILED)


def test_视频不存在报6(valid_payload: dict[str, Any], tmp_path: Path) -> None:
    config = bind_task_config({**valid_payload, "videoPath": str(tmp_path / "缺失.mp4")})
    with pytest.raises(PublishError) as exc_info:
        verify_local_environment(config)
    assert_error_code(exc_info, ErrorCode.VIDEO_NOT_FOUND)


def test_校验顺序为浏览器优先于目录优先于视频(
    valid_payload: dict[str, Any], tmp_path: Path
) -> None:
    """三项同时出问题时，必须报出最先检查的那一项，保证错误码稳定可预期。"""
    config = bind_task_config({
        **valid_payload,
        "execPath": str(tmp_path / "无"),
        "userDataDir": str(tmp_path / "也无"),
        "videoPath": str(tmp_path / "还是无"),
    })
    with pytest.raises(PublishError) as exc_info:
        verify_local_environment(config)
    assert_error_code(exc_info, ErrorCode.CHROME_NOT_FOUND)


# ======================================================================
# 端到端：从原始参数到配置对象
# ======================================================================


def test_完整加载流程(valid_payload: dict[str, Any]) -> None:
    config = load_task_config(to_base64(valid_payload))
    assert config.task_id == "task-1"
    assert config.tags == ["夏日穿搭", "清凉一夏", "好物分享"]
    assert config.publish_delay_hours == 24


def test_异常携带阶段信息() -> None:
    """失败必须能定位到阶段，否则上游只知道「失败了」却不知道「卡在哪」。"""
    with pytest.raises(PublishError) as exc_info:
        load_task_config("垃圾数据")
    assert exc_info.value.stage is not None
    assert exc_info.value.stage.value == "config"
