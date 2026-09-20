"""轻提示文本到事件语义的翻译测试。

这里最需要锁死的是 **匹配顺序**。关键词采用子串匹配，宽泛的词（「服务」「不支持」）
若排在具体文案之前，会抢先命中本该归类为上传/发布结果的提示。

举个会真实发生的例子：「视频上传失败，请稍后重试」同时含有「上传失败」，
而某些服务异常提示形如「服务开小差了」——若把「服务」提到最前，
前者不受影响，但一条形如「上传失败，服务异常」的提示就会被错判为可重试的 13，
而它本应是 10。顺序错误带来的是 **错误码错判**，而非崩溃，因此极难发现。
"""

from __future__ import annotations

import pytest

from douyin_publisher.browser.selectors import TOAST_KEYWORDS
from douyin_publisher.browser.toast import translate_toast
from douyin_publisher.core.events import EventType


# ======================================================================
# 正向：各类提示被正确识别
# ======================================================================


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("上传成功", EventType.UPLOAD_SUCCESS),
        ("视频上传成功", EventType.UPLOAD_SUCCESS),
        ("上传失败", EventType.UPLOAD_FAILURE),
        ("视频上传失败，请稍后重试", EventType.UPLOAD_FAILURE),
        ("发布成功", EventType.PUBLISH_SUCCESS),
        ("作品发布成功！", EventType.PUBLISH_SUCCESS),
        ("发布失败", EventType.PUBLISH_FAILURE),
        ("发布失败，请检查内容", EventType.PUBLISH_FAILURE),
        ("服务异常", EventType.SERVICE_ERROR),
        ("服务开小差了，请稍后重试", EventType.SERVICE_ERROR),
        ("该商品暂不支持在直播/短视频推广", EventType.PRODUCT_NOT_SUPPORTED),
        ("暂不支持此类商品", EventType.PRODUCT_NOT_SUPPORTED),
    ],
)
def test_识别关键提示(text: str, expected: EventType) -> None:
    assert translate_toast(text) is expected


@pytest.mark.parametrize(
    "text",
    ["  上传成功  ", "\n发布成功\n", "\t服务异常\t"],
    ids=["两侧空格", "换行包裹", "制表符包裹"],
)
def test_忽略首尾空白(text: str) -> None:
    """DOM 读出的文本常带缩进空白，不能因此识别失败。"""
    assert translate_toast(text) is not None


# ======================================================================
# 反向：无关提示不被误判
# ======================================================================


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "请输入标题",
        "已保存草稿",
        "正在上传中",  # 含「上传」但不是结果，不应命中
        "复制成功",  # 含「成功」但不是上传/发布结果
        "网络连接中",
    ],
    ids=["空串", "纯空白", "输入提示", "草稿提示", "上传中", "复制成功", "网络提示"],
)
def test_无关提示不产生事件(text: str) -> None:
    """页面上大量提示与流程无关，误判会导致任务被错误地中止或放行。"""
    assert translate_toast(text) is None


def test_正在上传中不会被误判为上传成功() -> None:
    """反向锁死：若把关键词放宽成「上传」，这条会变红。"""
    assert translate_toast("视频正在上传中，请稍候") is None


def test_复制成功不会被误判为发布成功() -> None:
    """反向锁死：若把关键词放宽成「成功」，这条会变红。"""
    assert translate_toast("链接复制成功") is None


# ======================================================================
# 匹配顺序
# ======================================================================


def test_具体文案优先于宽泛关键词() -> None:
    """同时含具体文案与宽泛词时，必须按具体文案归类。

    「上传失败，服务异常」应判为 10（上传失败）而非 13（服务异常）——
    前者是确定的上传结果，后者只是伴随说明。判错会让上游按错误的方式重试。
    """
    assert translate_toast("上传失败，服务异常") is EventType.UPLOAD_FAILURE
    assert translate_toast("发布失败，服务开小差") is EventType.PUBLISH_FAILURE


def test_关键词登记顺序符合预期() -> None:
    """直接锁死登记表的顺序，任何调整都必须显式修改本测试。"""
    keywords = [keyword for keyword, _ in TOAST_KEYWORDS]
    assert keywords == [
        "上传成功",
        "上传失败",
        "发布成功",
        "发布失败",
        "不支持",
        "服务",
    ], "关键词顺序被改动——宽泛词前移会导致错误码错判"


def test_宽泛关键词排在具体文案之后() -> None:
    """用结构化断言表达意图，而不只是比对一个固定列表。"""
    keywords = [keyword for keyword, _ in TOAST_KEYWORDS]
    broad = {"服务", "不支持"}
    specific = [k for k in keywords if k not in broad]
    broad_positions = [keywords.index(k) for k in broad]

    assert min(broad_positions) > max(keywords.index(k) for k in specific), (
        "宽泛关键词必须排在所有具体文案之后"
    )


# ======================================================================
# 登记表自身的完整性
# ======================================================================


def test_每个关键词都映射到合法的事件类型() -> None:
    """拼错事件名会在运行时才炸，这里提前抓出来。"""
    for keyword, event_name in TOAST_KEYWORDS:
        assert EventType(event_name) is not None, f"关键词 {keyword} 映射到未知事件"


def test_所有事件类型都有对应的关键词() -> None:
    """反向：定义了事件却没有任何提示能触发它，说明漏了登记。"""
    mapped = {event_name for _, event_name in TOAST_KEYWORDS}
    declared = {event.value for event in EventType}
    assert declared - mapped == set(), "存在无法被任何提示触发的事件类型"
