"""进度回报的端到端验证。

单元测试能验证输出格式，验证不了「进度真的随流程推进」与「长等待时有心跳」。
这里跑真实流程，把 stdout 收集起来逐行检查。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.async_api import BrowserContext

from douyin_publisher.cli.progress import KIND_HEARTBEAT, KIND_STEP
from douyin_publisher.config.models import TaskConfig
from douyin_publisher.config.runtime import ProgressOptions
from douyin_publisher.core.errors import ErrorCode
from douyin_publisher.core.stages import Stage
from douyin_publisher.pipeline.runner import run_pipeline
from douyin_publisher.pipeline.steps import (
    cart,
    cover,
    declaration,
    publish,
    publish_setting,
    title,
)

from tests.integration.conftest import mock_page_url

TEST_TOTAL_TIMEOUT = 40.0


@pytest.fixture(autouse=True)
def fast_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    fast = 0.02
    monkeypatch.setattr(title, "TAG_INPUT_PAUSE", fast)
    monkeypatch.setattr(title, "CLEAR_PAUSE", fast)
    monkeypatch.setattr(publish_setting, "OPTION_PAUSE", fast)
    monkeypatch.setattr(publish_setting, "INITIAL_PAUSE", fast)
    monkeypatch.setattr(declaration, "SELECT_PAUSE", fast)
    monkeypatch.setattr(cart, "STEP_PAUSE", fast)
    monkeypatch.setattr(cart, "EXISTING_PROBE_TIMEOUT", 0.5)
    monkeypatch.setattr(cover, "STEP_PAUSE", fast)
    monkeypatch.setattr(publish, "PRE_CLICK_PAUSE", fast)


def make_config(tmp_path: Path, progress: ProgressOptions | None = None) -> TaskConfig:
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake-video")

    payload: dict[str, object] = {
        "exec_path": "unused",
        "user_data_dir": "unused",
        "task_id": "p-task",
        "douyin_id": "d",
        "video_path": str(video),
        "cart_url": "",
        "title": "进度测试",
        "timeouts": {"element": 3},
        # 截图会在失败用例里产生文件，这里用不到，关掉省时间
        "screenshot": {"onFailure": False},
    }
    if progress is not None:
        payload["progress"] = progress
    return TaskConfig(**payload)


async def run_with(config: TaskConfig, browser_context: BrowserContext, **params):
    return await run_pipeline(
        config,
        browser_context,
        total_timeout=TEST_TOTAL_TIMEOUT,
        page_url=mock_page_url(**params),
    )


def parse_stdout(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.strip().splitlines() if line.strip()]


# ======================================================================
# 关闭时零影响
# ======================================================================


async def test_默认不产生进度输出(
    tmp_path: Path, browser_context: BrowserContext, capsys: pytest.CaptureFixture[str]
) -> None:
    """既有对接不被弄坏的全部保障就在这一条。"""
    result = await run_with(make_config(tmp_path), browser_context)

    assert result.code is ErrorCode.SUCCESS
    assert parse_stdout(capsys) == [], "默认配置下不该有任何 stdout 输出"


# ======================================================================
# 开启后进度随流程推进
# ======================================================================


async def test_每个步骤产生一条进度(
    tmp_path: Path, browser_context: BrowserContext, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_config(tmp_path, ProgressOptions(enabled=True, heartbeat=0))
    result = await run_with(config, browser_context)
    assert result.code is ErrorCode.SUCCESS

    events = [e for e in parse_stdout(capsys) if e["kind"] == KIND_STEP]

    # 未配置商品链接，挂车被跳过，故步骤数比完整流程少一步
    assert len(events) == 8, f"步骤进度条数不符：{[e['stage'] for e in events]}"
    assert events[0]["stage"] == Stage.UPLOAD.value
    assert events[-1]["stage"] == Stage.AWAIT_PUBLISH.value


async def test_步骤序号连续递增(
    tmp_path: Path, browser_context: BrowserContext, capsys: pytest.CaptureFixture[str]
) -> None:
    """序号跳号或倒退会让进度条表现得莫名其妙。"""
    config = make_config(tmp_path, ProgressOptions(enabled=True, heartbeat=0))
    await run_with(config, browser_context)

    steps = [e["step"] for e in parse_stdout(capsys) if e["kind"] == KIND_STEP]
    assert steps == list(range(1, len(steps) + 1)), f"序号不连续：{steps}"


async def test_总步数与实际执行步数一致(
    tmp_path: Path, browser_context: BrowserContext, capsys: pytest.CaptureFixture[str]
) -> None:
    """跳过步骤后总数要跟着变，否则进度永远走不到 100%。"""
    config = make_config(tmp_path, ProgressOptions(enabled=True, heartbeat=0))
    await run_with(config, browser_context)

    events = [e for e in parse_stdout(capsys) if e["kind"] == KIND_STEP]
    total = events[0]["total"]

    assert all(e["total"] == total for e in events), "总步数中途变了"
    assert events[-1]["step"] == total, (
        f"最后一步是 {events[-1]['step']}/{total}，进度走不到头"
    )


async def test_失败时进度停在出问题的步骤(
    tmp_path: Path, browser_context: BrowserContext, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_config(tmp_path, ProgressOptions(enabled=True, heartbeat=0))
    result = await run_with(config, browser_context, cover="no_modal")

    assert result.code is ErrorCode.COVER_FAILED
    events = [e for e in parse_stdout(capsys) if e["kind"] == KIND_STEP]
    assert events[-1]["stage"] == Stage.COVER.value


# ======================================================================
# 心跳：进度回报真正解决的问题
# ======================================================================


async def test_长等待期间发出心跳(
    tmp_path: Path, browser_context: BrowserContext, capsys: pytest.CaptureFixture[str]
) -> None:
    """这一条才是进度回报存在的理由。

    只在步骤切换时报进度的话，等上传的那段时间里一片寂静，
    调度方无法区分「正在上传」与「已经卡死」。
    """
    config = make_config(tmp_path, ProgressOptions(enabled=True, heartbeat=0.3))
    # 上传永不给结论，制造一段长等待
    result = await run_with(config, browser_context, upload="silent")

    assert result.code is ErrorCode.UPLOAD_TIMEOUT

    beats = [e for e in parse_stdout(capsys) if e["kind"] == KIND_HEARTBEAT]
    assert len(beats) >= 3, f"长等待期间只收到 {len(beats)} 次心跳"
    assert any(b["stage"] == Stage.AWAIT_UPLOAD.value for b in beats), (
        "心跳没有指出当前卡在哪一步"
    )


async def test_心跳间隔为零时不发心跳(
    tmp_path: Path, browser_context: BrowserContext, capsys: pytest.CaptureFixture[str]
) -> None:
    """反向配对：确认心跳确实受配置控制。"""
    config = make_config(tmp_path, ProgressOptions(enabled=True, heartbeat=0))
    await run_with(config, browser_context, upload="silent")

    beats = [e for e in parse_stdout(capsys) if e["kind"] == KIND_HEARTBEAT]
    assert beats == [], "配置了不发心跳，却仍然发了"


async def test_流程结束后心跳停止(
    tmp_path: Path, browser_context: BrowserContext, capsys: pytest.CaptureFixture[str]
) -> None:
    """心跳任务必须在所有退出路径上被取消，否则会留下悬挂任务。

    这里在流程结束后再等一段远大于心跳间隔的时间，
    确认期间没有新的心跳冒出来。
    """
    import asyncio

    config = make_config(tmp_path, ProgressOptions(enabled=True, heartbeat=0.2))
    await run_with(config, browser_context)

    before = len([e for e in parse_stdout(capsys) if e["kind"] == KIND_HEARTBEAT])
    await asyncio.sleep(1.0)
    after_extra = len([e for e in parse_stdout(capsys) if e["kind"] == KIND_HEARTBEAT])

    assert after_extra == 0, (
        f"流程已结束，之后又冒出 {after_extra} 次心跳（此前 {before} 次）——"
        f"心跳任务没有被取消"
    )
