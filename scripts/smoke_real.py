"""真实环境冒烟脚本：验证选择器与流程在线上页面是否仍然有效。

## 它解决什么问题

自动化测试跑的全是本地模拟页。模拟页保证了「生产代码与我们对页面结构的假设
一致」，但保证不了「这个假设与真实抖音一致」——页面随时可能改版，
而改版不会让任何一条测试变红。

这个脚本就是用来回答那个模拟页回答不了的问题的。

## 两种模式

默认是 **探测模式**（probe）：只打开发布页，逐个检查选择器能否命中，
不做任何点击、输入或上传。零副作用，可以随时跑。

加 `--dry-run` 进入 **演练模式**：真实执行上传、填标题、挂车、封面等全部步骤，
但 **在点击发布之前停住**。这会在账号下留下一条未发布的草稿，
请在确认可以接受后再使用。

无论哪种模式，脚本 **都不会点击发布按钮**——发布步骤被显式排除在执行序列之外，
而不是靠「跑到那里再判断」，以免任何意外导致真实发布。

## 用法

    uv run python scripts/smoke_real.py config.json
    uv run python scripts/smoke_real.py config.json --dry-run

config.json 的字段见 docs/cli-protocol.md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import Page

# 允许以脚本方式直接运行（无需先安装本项目）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from douyin_publisher.browser import selectors as sel  # noqa: E402
from douyin_publisher.browser.launcher import browser_session  # noqa: E402
from douyin_publisher.config.loader import (  # noqa: E402
    bind_task_config,
    verify_local_environment,
)
from douyin_publisher.config.models import TaskConfig  # noqa: E402
from douyin_publisher.core.logging import setup_logging  # noqa: E402
from douyin_publisher.pipeline.runner import DRY_RUN_STEPS, run_pipeline  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")


# 探测模式要检查的选择器：(名称, 选择器, 文本筛选)
#
# 与 tests/integration/test_selectors_match_mock.py 中的清单一致——
# 那边验证模拟页，这边验证真实页面，两边对照就能看出模拟页是否已经过时。
PROBE_TARGETS: list[tuple[str, str, str | None]] = [
    ("视频上传输入框", sel.Upload.FILE_INPUT_CSS, None),
    ("标题编辑器", sel.Editor.SLATE_CSS, None),
    ("挂车区域容器", sel.Cart.SECTION_XPATH, None),
    ("挂车下拉框", sel.Cart.DROPDOWN_CSS, None),
    ("发布设置单选项", sel.PublishSetting.RADIO_CSS, "立即发布"),
    ("自主声明下拉框", sel.Declaration.SELECT_BOX_XPATH, None),
    ("封面入口", sel.Cover.ENTRY_XPATH, None),
    ("发布按钮", sel.Publish.BUTTON_CSS, sel.Publish.TEXT_PUBLISH),
]

# 这些元素要等页面完全渲染后才出现，给足等待时间
PROBE_TIMEOUT_MS = 15_000


def load_config(path: Path) -> TaskConfig:
    """从 JSON 文件读取任务配置。"""
    if not path.is_file():
        raise SystemExit(f"[错误] 配置文件不存在：{path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"[错误] 配置文件不是合法 JSON：{exc}") from exc

    return bind_task_config(payload)


async def probe_selectors(page: Page) -> bool:
    """逐个检查选择器能否在当前页面命中。

    Returns:
        是否全部命中。
    """
    print("\n=== 选择器探测（不做任何交互）===\n")

    all_hit = True
    for name, selector, has_text in PROBE_TARGETS:
        locator = (
            page.locator(selector, has_text=has_text)
            if has_text
            else page.locator(selector)
        )
        try:
            count = await locator.count()
        except Exception as exc:
            count = 0
            print(f"  [异常] {name}：{exc}")

        hit = count > 0
        all_hit = all_hit and hit
        flag = "命中" if hit else "未命中"
        print(f"  [{flag}] {name}（{count} 个）")
        if not hit:
            print(f"         选择器：{selector}")

    print()
    if all_hit:
        print("[结论] 全部选择器均能命中，页面结构与预期一致")
    else:
        print("[结论] 存在未命中的选择器——页面很可能已改版")
        print("       请更新 src/douyin_publisher/browser/selectors.py，")
        print("       并同步 tests/fixtures/pages/publish_page.html")
    return all_hit


async def run_probe(config: TaskConfig, user_data_dir: Path) -> int:
    """探测模式：打开页面检查选择器，不做任何交互。"""
    async with browser_session(config, user_data_dir) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        print(f"[导航] 打开 {sel.PUBLISH_PAGE_URL}")
        await page.goto(sel.PUBLISH_PAGE_URL, wait_until="domcontentloaded")
        print(f"[导航] 页面标题：{await page.title()}")

        # 等上传组件出现，它是「已登录且页面已渲染」的标志
        try:
            await page.locator(sel.Upload.FILE_INPUT_CSS).first.wait_for(
                state="attached", timeout=PROBE_TIMEOUT_MS
            )
        except Exception:
            print("\n[警告] 未等到视频上传组件——账号可能未登录，")
            print("       后续探测结果不具参考价值\n")

        return 0 if await probe_selectors(page) else 1


async def run_dry_run(config: TaskConfig, user_data_dir: Path) -> int:
    """演练模式：执行到点击发布之前。"""
    print("\n=== 演练模式 ===")
    print("将真实执行上传、填标题、挂车、封面等步骤，但不会点击发布。")
    print(f"执行步骤：{' -> '.join(step.title for step in DRY_RUN_STEPS)}\n")

    async with browser_session(config, user_data_dir) as context:
        result = await run_pipeline(config, context, steps=DRY_RUN_STEPS)

    print(f"\n[结论] {result}")
    if result.ok:
        print("       全部步骤执行成功，账号下应已留下一条未发布的草稿")
    else:
        print(f"       失败于阶段「{result.stage.value}」，责任方：{result.code.category.value}")
    return 0 if result.ok else result.code.code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="真实环境冒烟：验证选择器与流程在线上页面是否仍然有效",
    )
    parser.add_argument("config", type=Path, help="任务配置 JSON 文件路径")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="执行完整流程但不点击发布（会在账号下留下草稿）",
    )
    args = parser.parse_args()

    setup_logging()

    config = load_config(args.config)
    user_data_dir = verify_local_environment(config)

    print(f"[配置] {config}")
    print(f"[配置] 用户数据目录：{user_data_dir}")

    runner = run_dry_run if args.dry_run else run_probe
    return asyncio.run(runner(config, user_data_dir))


if __name__ == "__main__":
    sys.exit(main())
