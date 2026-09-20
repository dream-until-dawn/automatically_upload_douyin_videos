"""进程清理的正反向测试。

这里最需要锁死的是 **不能误杀**：用户自己开着的浏览器窗口必须毫发无损。
一个过于宽松的匹配（例如只按进程名）在正向测试里完全看不出问题，
但会在真实环境中关掉用户正在用的浏览器。
"""

from __future__ import annotations

from typing import Any

import psutil
import pytest

from douyin_publisher.system import processes
from douyin_publisher.system.processes import (
    _normalize_path,
    find_browsers_by_user_data_dir,
)

TARGET_DIR = r"D:\workspace\chrome_profiles\abc123"
OTHER_DIR = r"D:\workspace\chrome_profiles\xyz789"


class FakeProcess:
    """psutil.Process 的最小替身，只提供匹配逻辑所需的字段。"""

    def __init__(self, pid: int, name: str, cmdline: list[str]):
        self.pid = pid
        self.info: dict[str, Any] = {"pid": pid, "name": name, "cmdline": cmdline}


class RaisingProcess(FakeProcess):
    """访问属性即抛异常的进程，模拟「进程已退出」或「无权访问」。"""

    def __init__(self, pid: int, error: type[Exception]):
        super().__init__(pid, "chrome.exe", [])
        self._error = error

    @property  # type: ignore[override]
    def info(self) -> dict[str, Any]:
        raise self._error(self.pid)

    @info.setter
    def info(self, value: dict[str, Any]) -> None:
        pass


@pytest.fixture
def patch_process_iter(monkeypatch: pytest.MonkeyPatch):
    """把 psutil.process_iter 替换为返回给定假进程列表。"""

    def _apply(fake_processes: list[Any]) -> None:
        monkeypatch.setattr(
            psutil, "process_iter", lambda *args, **kwargs: iter(fake_processes)
        )

    return _apply


def chrome_with_dir(pid: int, directory: str, name: str = "chrome.exe") -> FakeProcess:
    return FakeProcess(
        pid, name, ["chrome.exe", f"--user-data-dir={directory}", "--no-first-run"]
    )


# ======================================================================
# 路径归一化
# ======================================================================


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (r"D:\Profiles\abc", "d:/profiles/abc"),  # 斜杠方向 + 大小写
        (r"D:\Profiles\abc\\", r"D:\Profiles\abc"),  # 尾随分隔符
        ("D:/Profiles/ABC", r"d:\profiles\abc"),
    ],
)
def test_同一目录的不同写法归一化后相等(left: str, right: str) -> None:
    """不归一化会导致匹配失败，清理漏杀，浏览器随后因目录被占用而启动失败。"""
    assert _normalize_path(left) == _normalize_path(right)


def test_不同目录归一化后仍不相等() -> None:
    assert _normalize_path(TARGET_DIR) != _normalize_path(OTHER_DIR)


# ======================================================================
# 正向：能匹配到目标进程
# ======================================================================


def test_匹配到使用目标目录的chrome(patch_process_iter) -> None:
    patch_process_iter([chrome_with_dir(1001, TARGET_DIR)])
    assert [p.pid for p in find_browsers_by_user_data_dir(TARGET_DIR)] == [1001]


def test_匹配到使用目标目录的edge(patch_process_iter) -> None:
    """Edge 同为 Chromium 内核，同样支持 --user-data-dir。"""
    patch_process_iter([chrome_with_dir(1002, TARGET_DIR, name="msedge.exe")])
    assert [p.pid for p in find_browsers_by_user_data_dir(TARGET_DIR)] == [1002]


def test_路径写法不同也能匹配(patch_process_iter) -> None:
    """进程命令行里是正斜杠，传入的是反斜杠，必须仍然命中。"""
    patch_process_iter([chrome_with_dir(1003, "d:/workspace/chrome_profiles/abc123")])
    assert [p.pid for p in find_browsers_by_user_data_dir(TARGET_DIR)] == [1003]


def test_匹配多个同目录进程(patch_process_iter) -> None:
    """Chrome 会派生多个子进程，它们共享同一个 --user-data-dir。"""
    patch_process_iter([
        chrome_with_dir(1001, TARGET_DIR),
        chrome_with_dir(1002, TARGET_DIR),
        chrome_with_dir(1003, TARGET_DIR),
    ])
    assert len(find_browsers_by_user_data_dir(TARGET_DIR)) == 3


# ======================================================================
# 反向：绝不能误杀
# ======================================================================


def test_不误杀使用其他目录的浏览器(patch_process_iter) -> None:
    """最关键的一条：用户自己开着的浏览器必须毫发无损。"""
    patch_process_iter([
        chrome_with_dir(2001, OTHER_DIR),
        chrome_with_dir(2002, r"C:\Users\someone\AppData\Local\Google\Chrome\User Data"),
    ])
    assert find_browsers_by_user_data_dir(TARGET_DIR) == []


def test_不误杀没有用户数据目录参数的浏览器(patch_process_iter) -> None:
    """用户直接双击打开的 Chrome 不带该参数，属于个人使用，不得触碰。"""
    patch_process_iter([FakeProcess(2003, "chrome.exe", ["chrome.exe", "--no-first-run"])])
    assert find_browsers_by_user_data_dir(TARGET_DIR) == []


def test_命令行偶然包含路径但无参数时不匹配(patch_process_iter) -> None:
    """路径出现在其他位置（如作为下载目录）不代表它在占用该画像。"""
    patch_process_iter([
        FakeProcess(2004, "chrome.exe", ["chrome.exe", f"--log-file={TARGET_DIR}/log.txt"])
    ])
    assert find_browsers_by_user_data_dir(TARGET_DIR) == []


def test_不匹配非浏览器进程(patch_process_iter) -> None:
    """即便命令行里带有同样的参数，非浏览器进程也不在清理范围内。"""
    patch_process_iter([
        FakeProcess(2005, "python.exe", ["python.exe", f"--user-data-dir={TARGET_DIR}"])
    ])
    assert find_browsers_by_user_data_dir(TARGET_DIR) == []


@pytest.mark.parametrize(
    "blank",
    ["", " ", "   ", "\t", "\n", " \t\n "],
    ids=["空串", "单空格", "多空格", "制表符", "换行", "混合空白"],
)
def test_空白目录参数不匹配任何进程(patch_process_iter, blank: str) -> None:
    """传入空值时必须匹配为空，绝不能退化成「匹配所有」。

    单空格尤其危险：进程命令行是用空格拼接的，若空白未被视作空，
    " " 会命中每一个带 --user-data-dir 的浏览器进程，造成无差别误杀。
    """
    patch_process_iter([chrome_with_dir(1001, TARGET_DIR)])
    assert find_browsers_by_user_data_dir(blank) == []


# ======================================================================
# 反向：异常容错
# ======================================================================


@pytest.mark.parametrize(
    "error",
    [psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess],
    ids=["进程已退出", "权限不足", "僵尸进程"],
)
def test_单个进程访问异常不影响其他进程(patch_process_iter, error) -> None:
    """一个进程读不到，不能让整批清理失败。"""
    patch_process_iter([RaisingProcess(9999, error), chrome_with_dir(1001, TARGET_DIR)])
    assert [p.pid for p in find_browsers_by_user_data_dir(TARGET_DIR)] == [1001]


def test_获取进程列表失败时返回空列表(monkeypatch: pytest.MonkeyPatch) -> None:
    """拿不到进程列表时安全降级，而不是让整个任务崩溃。"""

    def boom(*args: Any, **kwargs: Any):
        raise psutil.Error("模拟失败")

    monkeypatch.setattr(psutil, "process_iter", boom)
    assert find_browsers_by_user_data_dir(TARGET_DIR) == []


# ======================================================================
# 终止逻辑
# ======================================================================


def test_无匹配进程时终止数为零(patch_process_iter) -> None:
    patch_process_iter([])
    assert processes.kill_browsers_by_user_data_dir(TARGET_DIR) == 0


def test_终止流程先温和后强制(
    patch_process_iter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """先 terminate 给 Chrome 落盘用户数据的机会，超时未退再 kill。

    直接 kill 有概率损坏用户数据目录，导致下次启动丢失登录态。
    """
    calls: list[tuple[str, int]] = []

    class TrackingProcess(FakeProcess):
        def terminate(self) -> None:
            calls.append(("terminate", self.pid))

        def kill(self) -> None:
            calls.append(("kill", self.pid))

    stubborn = TrackingProcess(
        3001, "chrome.exe", ["chrome.exe", f"--user-data-dir={TARGET_DIR}"]
    )
    patch_process_iter([stubborn])
    # 模拟温和终止后进程仍然存活，迫使流程走到强制结束
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: ([], list(procs)))

    assert processes.kill_browsers_by_user_data_dir(TARGET_DIR) == 1
    assert calls == [("terminate", 3001), ("kill", 3001)], (
        f"终止顺序不符合预期：{calls}"
    )


def test_温和终止成功则不强制结束(
    patch_process_iter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向：进程已乖乖退出时，不应再多余地 kill 一次。"""
    calls: list[str] = []

    class TrackingProcess(FakeProcess):
        def terminate(self) -> None:
            calls.append("terminate")

        def kill(self) -> None:
            calls.append("kill")

    patch_process_iter([
        TrackingProcess(3002, "chrome.exe", ["chrome.exe", f"--user-data-dir={TARGET_DIR}"])
    ])
    # 模拟进程已全部正常退出
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: (list(procs), []))

    processes.kill_browsers_by_user_data_dir(TARGET_DIR)
    assert calls == ["terminate"], "进程已退出仍执行了强制结束"


# ======================================================================
# 剪辑软件清理
# ======================================================================


def test_按关键字匹配剪辑软件(patch_process_iter, monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []

    class TrackingProcess(FakeProcess):
        def terminate(self) -> None:
            killed.append(self.pid)

        def kill(self) -> None:
            killed.append(self.pid)

    patch_process_iter([
        TrackingProcess(4001, "JianyingPro.exe", []),
        TrackingProcess(4002, "chrome.exe", []),  # 无关进程
    ])
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: (list(procs), []))

    assert processes.kill_video_editor() == 1
    assert killed == [4001], "误伤了无关进程"


def test_无剪辑软件运行时返回零(patch_process_iter) -> None:
    patch_process_iter([FakeProcess(4002, "chrome.exe", [])])
    assert processes.kill_video_editor() == 0


# ======================================================================
# 异步封装
# ======================================================================


async def test_异步封装返回与同步一致(patch_process_iter) -> None:
    """异步版本只是把阻塞调用挪到线程池，结果必须完全一致。"""
    patch_process_iter([])
    assert await processes.close_browsers_by_user_data_dir(TARGET_DIR) == 0
