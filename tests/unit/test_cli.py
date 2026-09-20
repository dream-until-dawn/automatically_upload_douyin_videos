"""命令行层的正反向测试。

覆盖两件事：

  1. **子命令分发与参数校验** —— 各类错误输入都要落到确定的错误码；
  2. **输出契约** —— stdout 有且仅有一行 JSON，且其 code 与进程退出码一致。

第 2 点尤其重要：上游靠这两者做决策，一旦不一致，
上游会同时收到两个互相矛盾的结论。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from douyin_publisher.cli import app
from douyin_publisher.cli.result import SCHEMA_VERSION, TaskResult
from douyin_publisher.core.errors import Category, ErrorCode
from douyin_publisher.core.stages import Stage

from tests.conftest import to_base64


@pytest.fixture(autouse=True)
def no_real_process_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """拦截进程清理，避免测试真的去杀本机上的浏览器或剪辑软件。

    这是一条安全护栏：少了它，跑一次测试就可能关掉开发者自己开着的浏览器。
    """

    async def fake_close_browsers(user_data_dir: str) -> int:
        return 0

    async def fake_close_editor() -> int:
        return 0

    monkeypatch.setattr(app, "close_browsers_by_user_data_dir", fake_close_browsers)
    monkeypatch.setattr(app, "close_video_editor", fake_close_editor)


# ======================================================================
# 子命令分发
# ======================================================================


async def test_无参数报18() -> None:
    result = await app.run([])
    assert result.code is ErrorCode.ARGS_MISSING


async def test_未知子命令报3() -> None:
    result = await app.run(["不存在的命令"])
    assert result.code is ErrorCode.CONFIG_INVALID
    assert "不存在的命令" in result.message


async def test_publish缺少配置报18() -> None:
    result = await app.run(["publish"])
    assert result.code is ErrorCode.ARGS_MISSING


async def test_关闭浏览器缺少目录报18() -> None:
    result = await app.run(["close-chrome"])
    assert result.code is ErrorCode.ARGS_MISSING


@pytest.mark.parametrize("command", ["close-jianying", "closeJianying"])
async def test_关闭剪辑软件成功(command: str) -> None:
    """同时接受短横线与驼峰写法，上游无需改动即可对接。"""
    result = await app.run([command])
    assert result.code is ErrorCode.SUCCESS
    assert result.stage is Stage.CLEANUP


@pytest.mark.parametrize("command", ["close-chrome", "closeChrome"])
async def test_关闭浏览器成功(command: str, tmp_path: Path) -> None:
    result = await app.run([command, str(tmp_path)])
    assert result.code is ErrorCode.SUCCESS
    assert result.stage is Stage.CLEANUP


@pytest.mark.parametrize("command", ["selfcheck", "self-check"])
async def test_自检通过(command: str) -> None:
    """开发环境依赖齐备时自检应当通过。"""
    result = await app.run([command])
    assert result.code is ErrorCode.SUCCESS, f"自检未通过：{result.message}"


async def test_依赖缺失时自检失败(monkeypatch: pytest.MonkeyPatch) -> None:
    """反向：把 Playwright 弄坏，自检必须报错而不是照样通过。

    没有这条，自检一旦退化成「永远返回成功」也无人察觉，
    它在部署后发现问题的能力就名存实亡了。
    """
    import playwright.async_api

    def broken(*args: Any, **kwargs: Any):
        raise RuntimeError("模拟驱动损坏")

    monkeypatch.setattr(playwright.async_api, "async_playwright", broken)

    result = await app.run(["selfcheck"])
    assert result.code is ErrorCode.UNEXPECTED
    assert "Playwright" in result.message


# ======================================================================
# 配置错误：在启动浏览器之前就被拦下
# ======================================================================


async def test_配置无法解析报19() -> None:
    result = await app.run(["publish", "这不是合法配置"])
    assert result.code is ErrorCode.CONFIG_PARSE_FAILED
    assert result.stage is Stage.CONFIG


async def test_缺必填字段报19() -> None:
    result = await app.run(["publish", to_base64({"taskId": "1"})])
    assert result.code is ErrorCode.CONFIG_PARSE_FAILED


async def test_浏览器路径不存在报4(valid_payload: dict[str, Any], tmp_path: Path) -> None:
    payload = {**valid_payload, "execPath": str(tmp_path / "不存在.exe")}
    result = await app.run(["publish", to_base64(payload)])
    assert result.code is ErrorCode.CHROME_NOT_FOUND


async def test_视频不存在报6(valid_payload: dict[str, Any], tmp_path: Path) -> None:
    payload = {**valid_payload, "videoPath": str(tmp_path / "不存在.mp4")}
    result = await app.run(["publish", to_base64(payload)])
    assert result.code is ErrorCode.VIDEO_NOT_FOUND


async def test_配置错误时携带任务标识为空(valid_payload: dict[str, Any]) -> None:
    """配置都没解析成功时拿不到 taskId，只能留空，不能编造。"""
    result = await app.run(["publish", "垃圾数据"])
    assert result.task_id == ""


# ======================================================================
# 输出契约
# ======================================================================


def test_结果json包含全部约定字段() -> None:
    result = TaskResult(
        code=ErrorCode.CART_LIMIT_REACHED,
        stage=Stage.CART,
        message="无法添加购物车",
        task_id="t1",
        douyin_id="d1",
        elapsed_ms=8423,
    )
    payload = json.loads(result.to_json())

    assert payload == {
        "schema": SCHEMA_VERSION,
        "ok": False,
        "code": 21,
        "name": "CART_LIMIT_REACHED",
        "category": "product",
        "retryable": False,
        "stage": "cart",
        "message": "无法添加购物车",
        "taskId": "t1",
        "douyinId": "d1",
        "elapsedMs": 8423,
        "screenshot": None,
    }


def test_失败结果可携带截图路径() -> None:
    """截图路径要能传到上游，否则截了也没人知道在哪。"""
    result = TaskResult(
        code=ErrorCode.COVER_FAILED,
        stage=Stage.COVER,
        message="封面配置失败",
        screenshot=r"C:\shots60920-150000_t1_cover.png",
    )
    payload = json.loads(result.to_json())
    assert payload["screenshot"].endswith("_cover.png")


def test_成功结果的截图字段为空() -> None:
    """成功时不该有截图——成功路径本就不截。"""
    result = TaskResult(ErrorCode.SUCCESS, Stage.DONE, "发布成功")
    assert json.loads(result.to_json())["screenshot"] is None


def test_成功结果的ok为真() -> None:
    result = TaskResult(ErrorCode.SUCCESS, Stage.DONE, "发布成功")
    assert json.loads(result.to_json())["ok"] is True


def test_json为单行() -> None:
    """上游按「读最后一行」解析，多行输出会让它只读到半截。"""
    result = TaskResult(ErrorCode.SUCCESS, Stage.DONE, "发布成功")
    assert "\n" not in result.to_json()


def test_中文不被转义() -> None:
    """message 可能被上游直接展示给用户，转义只会多一道解码工序。"""
    result = TaskResult(ErrorCode.UPLOAD_FAILED, Stage.UPLOAD, "视频上传失败")
    assert "视频上传失败" in result.to_json()


@pytest.mark.parametrize("member", list(ErrorCode), ids=lambda m: m.name)
def test_每个错误码都能正常序列化(member: ErrorCode) -> None:
    """任何一个错误码在输出时炸掉，都会让上游彻底拿不到结论。"""
    payload = json.loads(TaskResult(member, Stage.DONE, member.message).to_json())

    assert payload["code"] == member.code
    assert payload["name"] == member.name
    assert payload["retryable"] == member.retryable
    assert payload["category"] == member.category.value


def test_退出码与json中的code一致(capsys: pytest.CaptureFixture[str]) -> None:
    """两者不一致会让上游同时收到两个互相矛盾的结论。"""
    exit_code = app.main(["不存在的命令"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])

    assert exit_code == payload["code"], "进程退出码与 JSON 结果不一致"
    assert exit_code == ErrorCode.CONFIG_INVALID.code


def test_日志走stderr而结果走stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """分流失效会让上游在解析结果时读到日志文本。"""
    app.main(["publish", "垃圾数据"])
    captured = capsys.readouterr()

    lines = [line for line in captured.out.strip().splitlines() if line]
    assert len(lines) == 1, f"stdout 应当只有一行 JSON，实际 {len(lines)} 行：{lines}"
    json.loads(lines[0])  # 能解析即说明没混入日志


def test_成功时退出码为零(capsys: pytest.CaptureFixture[str]) -> None:
    assert app.main(["close-jianying"]) == 0


# ======================================================================
# 耗时统计
# ======================================================================


async def test_结果携带耗时() -> None:
    result = await app.run(["close-jianying"])
    assert result.elapsed_ms >= 0


async def test_失败结果同样携带耗时() -> None:
    """失败路径上也要有耗时，否则上游无法统计各类失败的成本。"""
    result = await app.run(["publish", "垃圾数据"])
    assert result.elapsed_ms >= 0


# ======================================================================
# 分类一致性
# ======================================================================


def test_成功结果的责任分类为无() -> None:
    result = TaskResult(ErrorCode.SUCCESS, Stage.DONE, "发布成功")
    assert json.loads(result.to_json())["category"] == Category.NONE.value
