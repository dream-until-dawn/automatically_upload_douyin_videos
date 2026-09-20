"""探针 A：验证「事件竞速中止」机制在真实浏览器等待上确实可取消。

这是 ADR-0001 的可行性前提。要证明的命题是：

    当主流程阻塞在 Playwright 的长等待（例如 wait_for 一个永不出现的元素）时，
    哨兵任务一旦命中致命事件，能够立刻取消主流程并让进程退出，
    而不必等到那个长等待自己超时。

若该命题不成立，整个异步架构失去意义，需要在动工前改变方案。

用法：
    uv run python scripts/probe_cancel.py
"""

import asyncio
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

# Windows 控制台默认使用 GBK 代码页，直接输出中文会乱码、输出 emoji 会抛
# UnicodeEncodeError。所有对外输出必须先把标准流切到 UTF-8。
# 这条约束同样适用于正式代码，见 core/logging.py。
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

# 主流程会阻塞在这个等待上；若取消机制失效，探针将耗时约这么久
LONG_WAIT_MS = 30_000
# 哨兵在这个时刻命中「致命事件」
SENTINEL_HIT_SECONDS = 1.0
# 判定探针通过的总耗时上界：必须远小于 LONG_WAIT_MS
PASS_THRESHOLD_SECONDS = 5.0


async def main_pipeline(page) -> str:
    """模拟主流程：卡在一个永远不会出现的元素上。"""
    print("[主流程] 开始等待一个永不出现的元素 ...", flush=True)
    await page.locator("#this-element-never-exists").wait_for(
        state="visible", timeout=LONG_WAIT_MS
    )
    return "主流程正常完成"  # 不应该走到这里


async def sentinel() -> str:
    """模拟哨兵：若干秒后命中致命事件。"""
    await asyncio.sleep(SENTINEL_HIT_SECONDS)
    print("[哨兵] 命中致命事件，准备中止主流程", flush=True)
    return "致命事件：上传失败"


async def probe() -> bool:
    started = time.monotonic()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        # 用 data URL 作为空白页，避免任何网络依赖
        await page.goto("data:text/html,<html><body>probe</body></html>")

        pipeline_task = asyncio.create_task(main_pipeline(page), name="pipeline")
        sentinel_task = asyncio.create_task(sentinel(), name="sentinel")

        done, pending = await asyncio.wait(
            {pipeline_task, sentinel_task}, return_when=asyncio.FIRST_COMPLETED
        )

        winner = next(iter(done))
        print(f"[竞速] 胜出任务: {winner.get_name()} -> {winner.result()}", flush=True)

        # 取消落败方，并等待它真正结束（吞掉 CancelledError，避免悬挂任务）
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                print(f"[取消] 任务 {task.get_name()} 已被成功取消", flush=True)

        await browser.close()

    elapsed = time.monotonic() - started
    passed = winner.get_name() == "sentinel" and elapsed < PASS_THRESHOLD_SECONDS

    print(f"\n[结果] 总耗时 {elapsed:.2f}s（阈值 {PASS_THRESHOLD_SECONDS}s，"
          f"若取消失效则应接近 {LONG_WAIT_MS / 1000:.0f}s）", flush=True)
    print(f"[结论] 探针 A {'通过' if passed else '失败'}", flush=True)
    return passed


if __name__ == "__main__":
    print(f"Python {sys.version}")
    print(f"工作目录 {Path.cwd()}\n")
    sys.exit(0 if asyncio.run(probe()) else 1)
