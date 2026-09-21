"""准备一个供自动化使用的 Chrome 画像目录。

## 为什么需要这一步

本程序不做登录，它复用「某个 Chrome 用户数据目录里已有的登录态」。
所以接入的第一件事，是准备一个已登录抖音的画像目录——
这是新使用者最容易卡住的地方，因为它既不是安装步骤，也不在配置文件里。

## 为什么不要直接用你日常的 Chrome 画像

两个原因：

  1. **启动前会清场**：程序在启动浏览器前，会终止所有占用该目录的进程。
     若指向你日常使用的画像，那就是把你正开着的浏览器关掉。
  2. **自动化会操作这个账号**：用独立画像能把影响圈在一个可控范围内。

因此这里创建一个专用目录，与日常浏览完全隔离。

## 用法

    uv run python scripts/prepare_profile.py C:/douyin_profiles/account_a

脚本会用该目录启动一个 Chrome，你在弹出的窗口里手动登录抖音，
登录成功后脚本自动检测到并退出。**脚本不碰你的账号密码**，
全程由你自己在浏览器里操作。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

# 创作者中心发布页。能打开它并看到上传组件，就说明登录态可用。
PUBLISH_URL = (
    "https://creator.douyin.com/creator-micro/content/post/video"
    "?enter_from=publish_page"
)

# 判断「已登录」的依据：发布页上存在视频上传组件。
# 与生产代码用的是同一个判据（见 browser/selectors.py 的 Upload.FILE_INPUT_CSS），
# 这样这里检测通过，生产跑起来就不会在第一步失败。
UPLOAD_INPUT = 'input[type="file"][name="upload-btn"][accept*="mp4"]'

# 轮询检测登录状态的间隔与总时长
CHECK_INTERVAL = 3.0
MAX_WAIT_MINUTES = 10

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def detect_chrome() -> str | None:
    """在常见安装位置探测 Chrome。"""
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    local = Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"
    return str(local) if local.is_file() else None


async def wait_for_login(page) -> bool:
    """轮询等待登录完成。

    Returns:
        是否在限定时间内检测到登录态。
    """
    deadline = asyncio.get_running_loop().time() + MAX_WAIT_MINUTES * 60
    reminded = False

    while asyncio.get_running_loop().time() < deadline:
        try:
            count = await page.locator(UPLOAD_INPUT).count()
            if count > 0:
                return True
        except Exception:
            # 页面正在跳转时查询会失败，属于正常情况
            pass

        if not reminded:
            print("\n请在弹出的浏览器窗口中完成抖音登录……")
            print("（脚本不会碰你的账号密码，全部由你自己在浏览器里操作）")
            print("登录成功后本脚本会自动检测到并退出。\n")
            reminded = True

        await asyncio.sleep(CHECK_INTERVAL)

        # 登录后页面未必会自动跳到发布页，主动回来看看
        try:
            if "creator-micro/content/post" not in page.url:
                await page.goto(PUBLISH_URL, wait_until="domcontentloaded")
        except Exception:
            pass

    return False


async def prepare(profile_dir: Path, exec_path: str) -> int:
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"[画像] 目录：{profile_dir}")
    print(f"[浏览器] {exec_path}")
    print("[启动] 正在打开浏览器……")

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=exec_path,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(PUBLISH_URL, wait_until="domcontentloaded")

            # 先看一眼是否已经登录过了
            await asyncio.sleep(3)
            if await page.locator(UPLOAD_INPUT).count() > 0:
                print("\n[结果] 该画像已处于登录状态，无需重新登录。")
                logged_in = True
            else:
                logged_in = await wait_for_login(page)
        finally:
            await context.close()

    print()
    if not logged_in:
        print(f"[结果] {MAX_WAIT_MINUTES} 分钟内未检测到登录，已退出。")
        print("       可以重新运行本脚本继续。")
        return 1

    print("[结果] 登录态已保存到该画像目录。")
    print()
    print("接下来把这个路径填进配置的 userDataDir：")
    print()
    print(f'    "userDataDir": "{profile_dir.as_posix()}",')
    print()
    print("提示：此后不要再手动打开这个画像去浏览，")
    print("      程序在启动前会终止占用该目录的所有浏览器进程。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="创建并登录一个供自动化使用的 Chrome 画像目录",
    )
    parser.add_argument("profile_dir", type=Path, help="画像目录路径（不存在会自动创建）")
    parser.add_argument(
        "--chrome", default=None, help="Chrome 可执行文件路径（默认自动探测）"
    )
    args = parser.parse_args()

    exec_path = args.chrome or detect_chrome()
    if not exec_path:
        print("[错误] 未能探测到本机 Chrome，请用 --chrome 指定其路径")
        return 1
    if not Path(exec_path).is_file():
        print(f"[错误] Chrome 不存在：{exec_path}")
        return 1

    return asyncio.run(prepare(args.profile_dir.resolve(), exec_path))


if __name__ == "__main__":
    sys.exit(main())
