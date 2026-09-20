"""探针 C：验证以持久化上下文接管本机 Chrome 的可行性。

要证明的命题是：

  1. 能用指定的 executable_path 启动 **本机安装的 Chrome**（而非 Playwright 自带的
     Chromium），这是复用用户登录态的前提；
  2. 能用指定的 user_data_dir 启动持久化上下文，且启动后该目录确实被写入；
  3. 启动的进程命令行中带有 --user-data-dir 参数，
     这是「按用户数据目录精确清理残留进程」这一机制能够成立的前提。

第 3 点尤其重要：清理逻辑依赖命令行参数匹配，若 Playwright 启动时不透传该参数，
清理将误伤或漏杀，必须提前确认。

用法：
    uv run python scripts/probe_chrome.py [chrome路径] [用户数据目录]

两个参数都可省略：Chrome 路径会在常见安装位置自动探测，
用户数据目录默认使用临时目录（仅验证机制，不涉及真实登录态）。
"""

import asyncio
import sys
import tempfile
from pathlib import Path

import psutil
from playwright.async_api import async_playwright

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

# Chrome 在 Windows 上的常见安装位置
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def detect_chrome() -> str | None:
    """在常见安装位置中探测 Chrome 可执行文件。"""
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    # 退而求其次：查找 LocalAppData 下的用户级安装
    local = Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"
    return str(local) if local.is_file() else None


def find_processes_by_user_data_dir(target_dir: str) -> list[int]:
    """按 --user-data-dir 命令行参数匹配浏览器进程，返回 PID 列表。

    这正是正式代码中进程清理所依赖的匹配方式，此处用于验证其有效性。
    """
    target = target_dir.lower().replace("\\", "/")
    matched: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "chrome.exe" not in name:
                continue
            cmdline = " ".join(proc.info.get("cmdline") or []).lower().replace("\\", "/")
            if "--user-data-dir=" in cmdline and target in cmdline:
                matched.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return matched


async def probe(exec_path: str, user_data_dir: str) -> bool:
    print(f"[配置] Chrome 路径      : {exec_path}")
    print(f"[配置] 用户数据目录     : {user_data_dir}\n")

    async with async_playwright() as p:
        print("[启动] 正在以持久化上下文启动本机 Chrome ...")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            executable_path=exec_path,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("data:text/html,<title>probe-c</title><body>ok</body>")
            title = await page.title()
            print(f"[启动] 成功，页面标题: {title}")

            # 验证命令行参数匹配是否可行（进程清理机制的前提）
            pids = find_processes_by_user_data_dir(user_data_dir)
            print(f"[匹配] 按 --user-data-dir 命中的 Chrome 进程: {pids or '无'}")

            # 验证用户数据目录确实被写入
            written = list(Path(user_data_dir).iterdir())
            print(f"[落盘] 用户数据目录条目数: {len(written)}")

            ok = bool(title) and bool(pids) and len(written) > 0
        finally:
            await context.close()
            print("[清理] 浏览器上下文已关闭")

    print(f"\n[结论] 探针 C {'通过' if ok else '失败'}")
    if not ok:
        print("       提示：若进程未命中，说明清理逻辑不能依赖命令行参数匹配，需改用其他手段")
    return ok


if __name__ == "__main__":
    exec_path = sys.argv[1] if len(sys.argv) > 1 else detect_chrome()
    if not exec_path:
        print("[错误] 未能探测到本机 Chrome，请将其路径作为第一个参数传入")
        sys.exit(1)

    if len(sys.argv) > 2:
        data_dir = sys.argv[2]
    else:
        data_dir = tempfile.mkdtemp(prefix="probe_chrome_")
        print(f"[提示] 未指定用户数据目录，使用临时目录: {data_dir}\n")

    sys.exit(0 if asyncio.run(probe(exec_path, data_dir)) else 1)
