"""失败截图的单元测试（不启动浏览器的部分）。

最关键的一条是 **截图失败不能影响主流程**：
此时已经有了一个明确的失败原因，若截图再抛出异常，
上游拿到的就成了「截图出错」而不是真正的失败原因——
一个辅助功能把主信息盖掉了，这比不截图更糟。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from douyin_publisher.browser.screenshot import (
    DEFAULT_SUBDIR,
    build_filename,
    capture_failure,
    resolve_directory,
)
from douyin_publisher.config.runtime import ScreenshotOptions
from douyin_publisher.core.stages import Stage


# ======================================================================
# 存放目录
# ======================================================================


def test_未配置目录时落到系统临时目录() -> None:
    path = resolve_directory("")
    assert path.name == DEFAULT_SUBDIR
    assert path.is_absolute()


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_空白目录等同于未配置(blank: str) -> None:
    assert resolve_directory(blank).name == DEFAULT_SUBDIR


def test_配置的目录被采用(tmp_path: Path) -> None:
    assert resolve_directory(str(tmp_path)) == tmp_path


def test_配置目录两侧空白被去除(tmp_path: Path) -> None:
    assert resolve_directory(f"  {tmp_path}  ") == tmp_path


# ======================================================================
# 文件名
# ======================================================================


def test_文件名包含任务与阶段() -> None:
    """三要素都在文件名里，才能不打开文件就定位到具体任务与步骤。"""
    name = build_filename("task-42", Stage.COVER)

    assert "task-42" in name
    assert "cover" in name
    assert name.endswith(".png")
    # 形如 20260920-150000_task-42_cover.png
    assert re.match(r"^\d{8}-\d{6}_", name), f"缺少时间前缀：{name}"


@pytest.mark.parametrize(
    "dangerous",
    ["../../etc/passwd", "a/b/c", "a\\b", "任务:1", "task*?<>|", "  "],
)
def test_任务标识中的危险字符被清洗(dangerous: str) -> None:
    """任务 ID 由上游给出，可能含路径分隔符。

    不清洗会让截图写到意料之外的位置，甚至越出目标目录。
    """
    name = build_filename(dangerous, Stage.UPLOAD)

    for bad in ("/", "\\", ":", "*", "?", "<", ">", "|"):
        assert bad not in name, f"文件名未清洗 {bad!r}：{name}"
    assert name.endswith(".png")


def test_空任务标识也能生成文件名() -> None:
    name = build_filename("", Stage.UPLOAD)
    assert name.endswith("_upload.png")
    assert "unknown" in name


def test_超长任务标识被截断() -> None:
    name = build_filename("x" * 500, Stage.UPLOAD)
    assert len(name) < 120, f"文件名过长可能超出文件系统限制：{len(name)}"


@pytest.mark.parametrize("stage", list(Stage), ids=lambda s: s.value)
def test_每个阶段都能生成合法文件名(stage: Stage) -> None:
    name = build_filename("t", stage)
    assert name.endswith(f"_{stage.value}.png")


# ======================================================================
# 反向：截图失败不影响主流程
# ======================================================================


class BrokenPage:
    """截图必定失败的假页面。"""

    async def screenshot(self, **kwargs: object) -> None:
        raise RuntimeError("模拟页面已关闭")


async def test_截图失败时返回None而不抛异常(tmp_path: Path) -> None:
    """本文件最重要的一条。

    此刻已经有了一个明确的失败原因，截图再抛异常会把它盖掉，
    上游看到的将是「截图出错」而非真正的失败原因。
    """
    result = await capture_failure(BrokenPage(), str(tmp_path), "t1", Stage.COVER)
    assert result is None


async def test_目录不可创建时也不抛异常() -> None:
    """磁盘满、路径非法、权限不足等，都不能让主流程受影响。"""
    # 用一个在任何平台上都无法创建的路径
    result = await capture_failure(BrokenPage(), "\x00非法路径", "t1", Stage.UPLOAD)
    assert result is None


# ======================================================================
# 配置默认值
# ======================================================================


def test_默认开启截图() -> None:
    """这类信息的价值几乎全在事后——等出问题才想起开开关，现场已经没了。"""
    assert ScreenshotOptions().on_failure is True


def test_默认目录为空表示用临时目录() -> None:
    assert ScreenshotOptions().dir == ""


def test_可显式关闭截图() -> None:
    assert ScreenshotOptions(onFailure=False).on_failure is False


def test_可指定目录(tmp_path: Path) -> None:
    assert ScreenshotOptions(dir=str(tmp_path)).dir == str(tmp_path)
