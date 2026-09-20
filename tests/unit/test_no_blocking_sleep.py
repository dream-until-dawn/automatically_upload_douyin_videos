"""防「假绿」哨卫：禁止异步路径中出现同步阻塞调用。

为什么需要这条测试：

    time.sleep 会钉死整个事件循环。它一旦出现在异步路径上，哨兵任务在这段时间
    内完全得不到调度，「失败即中止」静默失效——而所有功能测试依然全绿，
    因为错误码还是对的，只是返回得慢了。

这类退化无法靠功能测试发现，只能靠静态检查锁死。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "douyin_publisher"

# 禁止在源码中出现的同步阻塞调用（模块名, 属性名）
_BANNED_CALLS = {
    ("time", "sleep"): "请改用 await asyncio.sleep()，time.sleep 会钉死事件循环",
}


def _iter_source_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def test_源码目录非空() -> None:
    """若扫描不到任何文件，这条哨卫就成了永远绿的摆设。"""
    files = _iter_source_files()
    assert len(files) >= 5, f"只扫描到 {len(files)} 个源文件，路径配置可能有误"


@pytest.mark.parametrize(
    "source_file", _iter_source_files(), ids=lambda p: p.stem
)
def test_源文件不含同步阻塞调用(source_file: Path) -> None:
    """用 AST 扫描而非字符串匹配，避免被注释和字符串里的同名文本误伤。"""
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # 只关心 `模块.属性()` 形式的调用，例如 time.sleep()
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            continue

        key = (func.value.id, func.attr)
        if key in _BANNED_CALLS:
            violations.append(
                f"{source_file.name}:{node.lineno} 出现 {key[0]}.{key[1]}() "
                f"—— {_BANNED_CALLS[key]}"
            )

    assert not violations, "检测到同步阻塞调用：\n" + "\n".join(violations)


def test_哨卫本身能抓出违规() -> None:
    """自检：验证这条哨卫不是摆设。

    构造一段确实含有 time.sleep 的代码，确认扫描逻辑能识别出来。
    若这里通不过，说明上面的扫描有缺陷，其「全绿」毫无意义。
    """
    bad_code = "import time\n\n\ndef f():\n    time.sleep(1)\n"
    tree = ast.parse(bad_code)

    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and (node.func.value.id, node.func.attr) in _BANNED_CALLS
    ]

    assert len(found) == 1, "哨卫的扫描逻辑失效，无法识别已知的违规代码"
