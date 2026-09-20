"""本机进程清理。

两个用途：

  · **启动前清场**：以某个用户数据目录启动 Chrome 前，必须先终止仍占用该目录的
    历史进程，否则 Chrome 会拒绝启动或复用旧实例，导致自动化失控。
  · **外部命令**：上游可以单独调用子命令来清理浏览器或剪辑软件进程。

匹配方式刻意做得很窄：只终止 **命令行中带有目标 --user-data-dir 参数** 的进程。
绝不按进程名批量杀 Chrome —— 用户自己的浏览器窗口必须毫发无损。
该匹配方式的有效性已由 M0 探针 C 在真实 Chrome 上验证。
"""

from __future__ import annotations

import asyncio

import psutil

from douyin_publisher.core.logging import get_logger

logger = get_logger("system.processes")

# 需要纳入匹配的浏览器进程名（小写）。Edge 同为 Chromium 内核，
# 同样支持 --user-data-dir，因此一并覆盖。
_BROWSER_PROCESS_NAMES = ("chrome.exe", "msedge.exe")

# 剪辑软件进程名的匹配关键字（小写子串）
_EDITOR_PROCESS_KEYWORD = "jianying"

# 终止进程后等待其真正退出的时间上限（秒）
_TERMINATE_TIMEOUT = 3.0


def _normalize_path(path: str) -> str:
    """把路径归一化为「小写 + 正斜杠」的形式，用于跨写法比对。

    同一个目录在命令行里可能写成 `D:\\Profiles\\abc` 或 `D:/Profiles/abc`，
    大小写也可能不同。不做归一化会导致匹配失败，清理形同虚设。

    首先 strip 的原因不只是整洁：进程命令行是用空格拼接的，若把 " " 这类
    纯空白当成有效目标，它会成为每一条命令行的子串，从而命中 **所有** 浏览器
    进程，造成无差别误杀。strip 后这类输入会归为空串并被上游直接拒绝。
    """
    return path.strip().lower().replace("\\", "/").rstrip("/")


def find_browsers_by_user_data_dir(user_data_dir: str) -> list[psutil.Process]:
    """查找命令行中使用了指定用户数据目录的浏览器进程。

    Args:
        user_data_dir: 目标用户数据目录，建议传入已规范化的绝对路径。

    Returns:
        匹配到的进程对象列表；获取进程列表失败时返回空列表。
    """
    target = _normalize_path(user_data_dir)
    if not target:
        return []

    try:
        candidates = list(psutil.process_iter(["pid", "name", "cmdline"]))
    except psutil.Error as exc:
        logger.warning(f"[清理] 获取系统进程列表失败：{exc}")
        return []

    matched: list[psutil.Process] = []
    for proc in candidates:
        try:
            name = (proc.info.get("name") or "").lower()
            if name not in _BROWSER_PROCESS_NAMES:
                continue

            cmdline = proc.info.get("cmdline") or []
            if not cmdline:
                continue

            normalized = _normalize_path(" ".join(cmdline))
            # 必须同时满足：带有 --user-data-dir 参数，且参数值命中目标目录。
            # 只判断后者会误伤命令行里偶然出现该路径的无关进程。
            if "--user-data-dir=" in normalized and target in normalized:
                matched.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # 进程已退出或无权访问，跳过即可
            continue

    return matched


def _terminate(processes: list[psutil.Process], what: str) -> int:
    """终止给定进程，先温和后强制。

    先 terminate 给进程保存状态的机会（Chrome 需要落盘用户数据），
    超时未退出的再 kill。直接 kill 有概率损坏用户数据目录。

    Args:
        processes: 待终止的进程。
        what: 日志中对这批进程的称呼。

    Returns:
        实际终止的进程数量。
    """
    if not processes:
        logger.info(f"[清理] 未发现运行中的{what}")
        return 0

    pids = [p.pid for p in processes]
    logger.info(f"[清理] 发现 {len(pids)} 个{what}（PID: {pids}），正在终止")

    for proc in processes:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # 等待温和退出，返回仍存活的进程
    _, alive = psutil.wait_procs(processes, timeout=_TERMINATE_TIMEOUT)

    for proc in alive:
        try:
            logger.warning(f"[清理] PID {proc.pid} 未响应终止信号，强制结束")
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    logger.info(f"[清理] {what}清理完毕")
    return len(pids)


def kill_browsers_by_user_data_dir(user_data_dir: str) -> int:
    """终止占用指定用户数据目录的浏览器进程（同步实现）。

    Returns:
        实际终止的进程数量。
    """
    return _terminate(
        find_browsers_by_user_data_dir(user_data_dir),
        f"占用目录 [{user_data_dir}] 的浏览器进程",
    )


def kill_video_editor() -> int:
    """终止剪辑软件进程（同步实现）。

    使用 psutil 逐进程匹配，而非调用系统命令行工具：
    后者会拉起额外的控制台进程，容易被安全软件判定为可疑行为。

    Returns:
        实际终止的进程数量。
    """
    try:
        candidates = list(psutil.process_iter(["pid", "name"]))
    except psutil.Error as exc:
        logger.warning(f"[清理] 获取系统进程列表失败：{exc}")
        return 0

    matched: list[psutil.Process] = []
    for proc in candidates:
        try:
            name = (proc.info.get("name") or "").lower()
            if _EDITOR_PROCESS_KEYWORD in name:
                matched.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return _terminate(matched, "剪辑软件进程")


# ----------------------------------------------------------------------
# 异步封装
#
# psutil 的进程枚举是阻塞调用，在有数百个进程的机器上可达数百毫秒。
# 直接在事件循环里执行会钉住循环，使哨兵在这段时间内失去响应能力，
# 因此统一丢到线程池中执行。
# ----------------------------------------------------------------------


async def close_browsers_by_user_data_dir(user_data_dir: str) -> int:
    """终止占用指定用户数据目录的浏览器进程（异步）。"""
    return await asyncio.to_thread(kill_browsers_by_user_data_dir, user_data_dir)


async def close_video_editor() -> int:
    """终止剪辑软件进程（异步）。"""
    return await asyncio.to_thread(kill_video_editor)
