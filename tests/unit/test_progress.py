"""进度回报的正反向测试。

守两条契约：

  1. **默认关闭时输出完全不变**——这是既有对接不被弄坏的全部保障；
  2. **每行都是完整的单个 JSON，结果永远在最后一行**——
     上游「读最后一行」的解析方式必须始终成立。
"""

from __future__ import annotations

import json

import pytest

from douyin_publisher.cli.progress import (
    KIND_HEARTBEAT,
    KIND_STEP,
    ProgressReporter,
)
from douyin_publisher.cli.result import SCHEMA_VERSION, TaskResult
from douyin_publisher.config.loader import bind_task_config
from douyin_publisher.config.runtime import ProgressOptions
from douyin_publisher.core.errors import ErrorCode
from douyin_publisher.core.stages import Stage


def read_lines(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    """把捕获到的 stdout 逐行解析为 JSON 对象。"""
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.strip().splitlines() if line.strip()]


# ======================================================================
# 默认关闭
# ======================================================================


def test_默认关闭() -> None:
    """与截图选项相反：进度会改变 stdout 的行数，默认开启会弄坏既有对接。"""
    assert ProgressOptions().enabled is False


def test_配置中默认关闭() -> None:
    config = bind_task_config({
        "execPath": "a", "userDataDir": "b", "taskId": "c",
        "douyinId": "d", "videoPath": "e",
    })
    assert config.progress.enabled is False


def test_关闭时不产生任何输出(capsys: pytest.CaptureFixture[str]) -> None:
    """本文件最重要的一条：关闭即零影响。"""
    reporter = ProgressReporter(enabled=False, total_steps=9)
    reporter.enter_step(Stage.UPLOAD, "投递视频", 1)
    reporter.heartbeat()
    reporter.enter_step(Stage.PUBLISH, "点击发布", 8)

    assert capsys.readouterr().out == "", "关闭状态下仍有输出"


# ======================================================================
# 开启后的输出格式
# ======================================================================


def test_步骤切换产生一行进度(capsys: pytest.CaptureFixture[str]) -> None:
    reporter = ProgressReporter(enabled=True, total_steps=9)
    reporter.enter_step(Stage.UPLOAD, "投递视频", 1)

    lines = read_lines(capsys)
    assert len(lines) == 1

    event = lines[0]
    assert event["type"] == "progress"
    assert event["kind"] == KIND_STEP
    assert event["stage"] == "upload"
    assert event["title"] == "投递视频"
    assert event["step"] == 1
    assert event["total"] == 9
    assert event["schema"] == SCHEMA_VERSION
    assert event["elapsedMs"] >= 0


def test_心跳复用当前步骤信息(capsys: pytest.CaptureFixture[str]) -> None:
    """心跳要能说清「卡在哪一步」，否则只知道活着、不知道在干什么。"""
    reporter = ProgressReporter(enabled=True, total_steps=9)
    reporter.enter_step(Stage.AWAIT_UPLOAD, "等待上传完成", 6)
    reporter.heartbeat()

    lines = read_lines(capsys)
    assert len(lines) == 2

    beat = lines[1]
    assert beat["kind"] == KIND_HEARTBEAT
    assert beat["stage"] == "await_upload"
    assert beat["step"] == 6


def test_尚未进入任何步骤时心跳不输出(capsys: pytest.CaptureFixture[str]) -> None:
    """没有步骤信息的心跳没有意义，不如不发。"""
    ProgressReporter(enabled=True, total_steps=9).heartbeat()
    assert capsys.readouterr().out == ""


def test_每行都是独立的完整JSON(capsys: pytest.CaptureFixture[str]) -> None:
    """上游按行解析，任何一行不完整都会让它出错。"""
    reporter = ProgressReporter(enabled=True, total_steps=3)
    reporter.enter_step(Stage.UPLOAD, "投递视频", 1)
    reporter.heartbeat()
    reporter.enter_step(Stage.PUBLISH, "点击发布", 2)

    out = capsys.readouterr().out
    for line in out.strip().splitlines():
        json.loads(line)  # 逐行可解析即通过
    assert len(out.strip().splitlines()) == 3


def test_进度行中文不转义(capsys: pytest.CaptureFixture[str]) -> None:
    reporter = ProgressReporter(enabled=True, total_steps=9)
    reporter.enter_step(Stage.CART, "挂载购物车", 5)
    assert "挂载购物车" in capsys.readouterr().out


def test_耗时随时间递增(capsys: pytest.CaptureFixture[str]) -> None:
    import time

    reporter = ProgressReporter(enabled=True, total_steps=2)
    reporter.enter_step(Stage.UPLOAD, "投递视频", 1)
    time.sleep(0.05)
    reporter.heartbeat()

    lines = read_lines(capsys)
    assert lines[1]["elapsedMs"] >= lines[0]["elapsedMs"]


def test_总步数至少为一() -> None:
    """避免「第 1 步 / 共 0 步」这种说不通的输出。"""
    reporter = ProgressReporter(enabled=True, total_steps=0)
    reporter.enter_step(Stage.UPLOAD, "投递视频", 1)
    assert reporter._total >= 1


# ======================================================================
# 与结果行的关系
# ======================================================================


def test_结果行与进度行靠type区分() -> None:
    """两者混在同一通道里，必须能区分开。"""
    reporter_line = json.loads(
        _capture_one(Stage.UPLOAD, "投递视频", 1)
    )
    result_line = json.loads(
        TaskResult(ErrorCode.SUCCESS, Stage.DONE, "发布成功").to_json()
    )

    assert reporter_line["type"] == "progress"
    assert result_line["type"] == "result"


def _capture_one(stage: Stage, title: str, index: int) -> str:
    """构造一条进度行的 JSON 文本（不经由 stdout）。"""
    from douyin_publisher.cli.progress import ProgressEvent

    return ProgressEvent(
        stage=stage, step=index, total=9, kind=KIND_STEP, title=title, elapsed_ms=1
    ).to_json()


# ======================================================================
# 心跳间隔配置
# ======================================================================


def test_心跳间隔默认十五秒() -> None:
    assert ProgressOptions().heartbeat == 15.0


def test_心跳间隔可配置为零表示不发() -> None:
    """0 是合法取值：只要步骤级进度、不要心跳。"""
    assert ProgressOptions(heartbeat=0).heartbeat == 0.0


def test_负数心跳间隔被拒绝() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProgressOptions(heartbeat=-1)
