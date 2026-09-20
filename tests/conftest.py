"""pytest 全局夹具与测试环境设置。"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# 测试用例名与断言信息含中文，而 Windows 控制台默认 GBK，
# 不切换编码会让失败信息变成乱码，严重影响排障效率。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


# 一份字段齐全、取值合法的基准配置。
# 各用例在它之上做最小改动，以保证「只有被测的那一项是变量」。
BASE_PAYLOAD: dict[str, Any] = {
    "execPath": "",  # 由 fixture 填入真实存在的临时文件
    "userDataDir": "",  # 由 fixture 填入真实存在的非空临时目录
    "taskId": "task-1",
    "douyinId": "dy-1",
    "videoPath": "",  # 由 fixture 填入真实存在的临时文件
    "cartUrl": "https://example.com/item?id=1",
    "title": "测试标题",
    "desc": "夏日穿搭,清凉一夏,好物分享",
    "cartTitel": "点击下方",
    "publishTimeMode": "立即发布",
    "publishTime": "24",
    "whoCanSee": "仅自己可见",
    "savePermission": "不允许",
    "selfDeclaration": "无需添加自主声明",
}


@pytest.fixture
def fake_environment(tmp_path: Path) -> dict[str, str]:
    """构造一套真实存在于磁盘上的假环境路径。

    环境校验读的是真实文件系统，因此这些路径必须真的存在，
    不能用打桩绕过——否则测不出校验逻辑本身的问题。

    Returns:
        含 execPath / userDataDir / videoPath 三个键的字典。
    """
    exec_path = tmp_path / "chrome.exe"
    exec_path.write_bytes(b"fake-chrome")

    user_data_dir = tmp_path / "profile"
    user_data_dir.mkdir()
    # 非空目录：校验要求用户数据目录里至少有内容（代表存在登录态）
    (user_data_dir / "Local State").write_text("{}", encoding="utf-8")

    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"fake-video")

    return {
        "execPath": str(exec_path),
        "userDataDir": str(user_data_dir),
        "videoPath": str(video_path),
    }


@pytest.fixture
def valid_payload(fake_environment: dict[str, str]) -> dict[str, Any]:
    """一份完全合法的配置字典。"""
    return {**BASE_PAYLOAD, **fake_environment}


def to_base64(payload: dict[str, Any]) -> str:
    """把配置字典编码为上游实际使用的 Base64 形式。"""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def pytest_collection_modifyitems(items) -> None:
    """自动给 tests/integration 下的用例打上 integration 标记。

    集成测试需要启动真实浏览器，比单元测试慢一个量级。
    自动打标记后，日常开发可以用 `-m "not integration"` 只跑快的那部分，
    而不必依赖每个文件都记得手写标记。
    """
    for item in items:
        if "integration" in str(item.path):
            item.add_marker(pytest.mark.integration)
