"""错误码契约测试：强制代码与文档保持一致。

错误码是本程序与上游之间的正式契约。若文档说 21 不可重试、代码却标成可重试，
上游会按错误的表做重试决策，而这种漂移在功能测试里完全看不出来——
所有测试照样全绿。

因此把「文档与实现一致」本身变成一条会红的测试。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from douyin_publisher.core.errors import Category, ErrorCode

# 文档路径：从本文件向上三级到仓库根
DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "error-codes.md"

# 匹配错误码总表的数据行，例如：
# | 21 | `CART_LIMIT_REACHED` | product | ❌ | 无法添加购物车（已达挂车上限） |
_ROW = re.compile(
    r"^\|\s*(?P<code>\d+)\s*"
    r"\|\s*`(?P<name>[A-Z_]+)`\s*"
    r"\|\s*(?P<category>[a-z—]+)\s*"
    r"\|\s*(?P<retryable>[✅❌—])\s*"
    r"\|"
)


def _parse_doc_table() -> dict[str, dict]:
    """从文档中解析出错误码总表，返回 {名称: 字段字典}。"""
    assert DOC_PATH.is_file(), f"错误码文档不存在：{DOC_PATH}"

    parsed: dict[str, dict] = {}
    for line in DOC_PATH.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line.strip())
        if not match:
            continue
        parsed[match.group("name")] = {
            "code": int(match.group("code")),
            "category": match.group("category"),
            "retryable": match.group("retryable") == "✅",
        }
    return parsed


@pytest.fixture(scope="module")
def doc_table() -> dict[str, dict]:
    table = _parse_doc_table()
    # 表格本身解析失败（例如文档格式被改动）也必须让测试红，而不是静默通过
    assert len(table) >= 20, f"文档中只解析出 {len(table)} 条错误码，疑似格式被破坏"
    return table


def test_文档条目与枚举成员一一对应(doc_table: dict[str, dict]) -> None:
    """文档里有而代码里没有、或反过来，都算漂移。"""
    doc_names = set(doc_table)
    code_names = {member.name for member in ErrorCode}

    assert doc_names - code_names == set(), "文档中存在代码未实现的错误码"
    assert code_names - doc_names == set(), "代码中存在文档未登记的错误码"


@pytest.mark.parametrize("member", list(ErrorCode), ids=lambda m: m.name)
def test_每个错误码的字段与文档一致(member: ErrorCode, doc_table: dict[str, dict]) -> None:
    """逐项比对退出码、责任分类与可重试标记。"""
    expected = doc_table[member.name]

    assert member.code == expected["code"], f"{member.name} 退出码与文档不一致"
    assert member.retryable == expected["retryable"], (
        f"{member.name} 的可重试标记与文档不一致——"
        f"这会直接导致上游做出错误的重试决策"
    )

    # 成功项在文档中用破折号表示「无责任方」
    if expected["category"] == "—":
        assert member.category is Category.NONE
    else:
        assert member.category.value == expected["category"], (
            f"{member.name} 的责任分类与文档不一致"
        )


def test_退出码互不重复() -> None:
    """复用号码会让上游无法区分两种不同的失败。"""
    codes = [member.code for member in ErrorCode]
    duplicated = {code for code in codes if codes.count(code) > 1}
    assert not duplicated, f"存在重复的退出码：{duplicated}"


@pytest.mark.parametrize("member", list(ErrorCode), ids=lambda m: m.name)
def test_退出码落在合法区间(member: ErrorCode) -> None:
    """1、2 为系统保留；超过 254 在 Windows 上行为不确定。"""
    assert member.code == 0 or 3 <= member.code <= 254, (
        f"{member.name} 的退出码 {member.code} 超出允许区间"
    )


def test_只有成功项的退出码为零() -> None:
    zero_codes = [m.name for m in ErrorCode if m.code == 0]
    assert zero_codes == ["SUCCESS"], "退出码 0 必须唯一地表示成功"


def test_按退出码反查() -> None:
    assert ErrorCode.from_code(21) is ErrorCode.CART_LIMIT_REACHED
    assert ErrorCode.from_code(0) is ErrorCode.SUCCESS

    # 反向：未登记的码必须报错，而不是静默返回某个兜底值
    with pytest.raises(ValueError, match="未登记的错误码"):
        ErrorCode.from_code(99)


def test_可重试的错误都归属平台类() -> None:
    """只有平台/网络问题值得重试；配置、页面、商品问题重试多少次都一样。"""
    for member in ErrorCode:
        if member.retryable:
            assert member.category is Category.PLATFORM, (
                f"{member.name} 被标记为可重试，但责任方是 {member.category.value}——"
                f"重试无法解决这类问题"
            )
