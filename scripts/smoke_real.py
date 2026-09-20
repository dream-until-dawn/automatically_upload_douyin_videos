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
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Page

# 允许以脚本方式直接运行（无需先安装本项目）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from douyin_publisher.browser import selectors as sel  # noqa: E402
from douyin_publisher.browser.actions import build_text_matcher, is_usable  # noqa: E402
from douyin_publisher.browser.launcher import browser_session  # noqa: E402
from douyin_publisher.config.loader import (  # noqa: E402
    bind_task_config,
    verify_local_environment,
)
from douyin_publisher.config.models import MODE_IMMEDIATE, TaskConfig  # noqa: E402
from douyin_publisher.core.logging import setup_logging  # noqa: E402
from douyin_publisher.pipeline.runner import DRY_RUN_STEPS, run_pipeline  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class ProbeTarget:
    """一个待探测的元素，**必须与生产代码的查找方式完全一致**。

    这一点是血的教训：初版探测脚本用 `has_text="发布"` 查找，
    而 Playwright 的 `has_text` 是子串匹配，于是命中了「作品发布」按钮；
    生产代码走的却是 `exact=True`（编译成 `^发布$` 正则），只认文本恰为「发布」的那个。
    两者不一致，探测报出的「全部命中」就是虚假的安全感——
    它既可能漏报真实的改版，也可能误报本来没问题的元素。

    Attributes:
        name: 人类可读的名称。
        selector: 选择器。
        has_text: 文本筛选，None 表示不按文本筛选。
        exact: 文本是否精确匹配。必须与生产调用处的取值一致。
        within: 父容器的选择器。生产若是在某个容器内部查找，
            这里也必须如此，否则会捞到页面别处的同类元素。
        requires_upload: 该元素是否要等视频开始上传后才渲染。
            探测模式不上传视频，这类元素必然找不到——
            那是探测模式的固有盲区，不是页面改版。
            不把它标出来，每次探测都会误报，久而久之就没人再认真看结论了。
        attached_only: 生产是否只要求元素「存在」而不要求「可用」。
            文件输入框就是这样：页面把它隐藏起来（透明、1px），
            生产用 wait_for(state="attached") 投递文件，从不检查可见性。
            对它套用可用性判定会稳定误报「元素不可用」。
    """

    name: str
    selector: str
    has_text: str | None = None
    exact: bool = True
    within: str | None = None
    requires_upload: bool = False
    attached_only: bool = False


# 待探测的元素清单。
#
# 每一项的 exact / within 都对照 src/douyin_publisher/pipeline/steps/ 下
# 实际的调用方式填写，改动生产代码时需同步这里。
PROBE_TARGETS: list[ProbeTarget] = [
    # upload.py：wait_for(state="attached") —— 该输入框是隐藏的，只要求存在
    ProbeTarget("视频上传输入框", sel.Upload.FILE_INPUT_CSS, attached_only=True),
    # title.py：find_usable(SLATE_CSS)
    ProbeTarget("标题编辑器", sel.Editor.SLATE_CSS),
    # cart.py：find_usable(SECTION_XPATH)
    ProbeTarget("挂车区域容器", sel.Cart.SECTION_XPATH),
    # cart.py：section.locator(DROPDOWN_CSS).first —— 在挂车容器内部查找
    ProbeTarget("挂车下拉框", sel.Cart.DROPDOWN_CSS, within=sel.Cart.SECTION_XPATH),
    # publish_setting.py：精确匹配优先。
    # 发布设置区域要等视频开始上传后才渲染，探测模式看不到它。
    ProbeTarget(
        "发布设置单选项",
        sel.PublishSetting.RADIO_CSS,
        MODE_IMMEDIATE,
        exact=True,
        requires_upload=True,
    ),
    # declaration.py：click_usable(SELECT_BOX_XPATH)
    ProbeTarget("自主声明下拉框", sel.Declaration.SELECT_BOX_XPATH),
    # cover.py：click_usable(ENTRY_XPATH)
    ProbeTarget("封面入口", sel.Cover.ENTRY_XPATH),
    # publish.py：click_usable(BUTTON_CSS, has_text="发布", exact=True)
    ProbeTarget("发布按钮", sel.Publish.BUTTON_CSS, sel.Publish.TEXT_PUBLISH, exact=True),
]

# 等待页面元素出现的超时
PROBE_TIMEOUT_MS = 15_000

# 探测前的额外静置时间（秒）。
#
# 创作者中心的各个区块是异步渲染的，只等上传组件出现并不代表页面已经就绪。
# 实测两次探测得到过不同的元素数量（挂车容器 1 个 vs 0 个），
# 正是探测过早导致的——结论不稳定的探测比没有探测更糟，因为它会误导判断。
PROBE_SETTLE_SECONDS = 5.0


def load_config(path: Path) -> TaskConfig:
    """从 JSON 文件读取任务配置。"""
    if not path.is_file():
        raise SystemExit(f"[错误] 配置文件不存在：{path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"[错误] 配置文件不是合法 JSON：{exc}") from exc

    return bind_task_config(payload)


# 命中多个元素时，最多展开几个做详情说明
MAX_DETAIL = 4


async def describe_element(locator, index: int) -> str:
    """描述单个命中元素：标签名、是否可见、文本与类名摘要。

    只报数量是不够的——流程实际操作的是第一个 **可用** 元素。
    若第一个不可见、或它其实是个包住目标的容器，真正被点到的就不是你以为的那个，
    而这种差异只有把元素本身打印出来才看得见。
    """
    element = locator.nth(index)

    try:
        visible = await element.is_visible()
    except Exception:
        visible = False

    try:
        tag = await element.evaluate("el => el.tagName.toLowerCase()")
    except Exception:
        tag = "?"

    try:
        text = (await element.inner_text()).strip().replace("\n", " ")
    except Exception:
        text = ""
    if len(text) > 30:
        text = text[:30] + "…"

    try:
        cls = (await element.get_attribute("class")) or ""
    except Exception:
        cls = ""
    if len(cls) > 40:
        cls = cls[:40] + "…"

    mark = "可见" if visible else "隐藏"
    return f"<{tag}> [{mark}] 文本={text!r} class={cls!r}"


async def count_independent_visible(locator, count: int) -> int:
    """统计彼此独立的可见元素个数，祖先与其后代只算一个。

    为什么需要这个：组件库的控件往往层层包裹，
    `div[class*='semi-select']` 会同时匹配 select 本体、它的 selection、
    content-wrapper、arrow……真实页面上实测一个下拉框就匹配出 8 个节点。

    若把它们当成 8 个独立候选去告警「可能选错」，告警就会遍地都是，
    真正需要注意的情形（两个并列的、长得一样的按钮）反而被淹没。
    因此只统计互不包含的那些。
    """
    handles = []
    for i in range(min(count, MAX_DETAIL)):
        try:
            element = locator.nth(i)
            if await element.is_visible():
                handle = await element.element_handle()
                if handle is not None:
                    handles.append(handle)
        except Exception:
            continue

    independent: list = []
    for handle in handles:
        contained = False
        for kept in independent:
            try:
                if await kept.evaluate("(el, child) => el.contains(child)", handle):
                    contained = True
                    break
            except Exception:
                continue
        if not contained:
            independent.append(handle)

    return len(independent)


def build_locator(page: Page, target: ProbeTarget):
    """按 **与生产完全相同的方式** 构造定位器。

    两处必须对齐，否则探测结论不可信：
      · 文本匹配走 build_text_matcher，精确匹配会编译成 `^文本$` 正则；
        直接把字符串传给 has_text 是子串匹配，会命中「作品发布」这类更长的文案。
      · 若生产是在某个容器内部查找，这里也必须先定位容器再往里找。
    """
    matcher = build_text_matcher(target.has_text, target.exact)

    root = page.locator(target.within).first if target.within else page
    if matcher is None:
        return root.locator(target.selector)
    return root.locator(target.selector, has_text=matcher)


async def probe_selectors(page: Page) -> bool:
    """逐个检查选择器能否命中，并说明流程实际会操作哪一个。

    Returns:
        是否全部命中。
    """
    print("\n=== 选择器探测（不做任何交互）===\n")

    all_hit = True
    warnings: list[str] = []
    deferred: list[str] = []

    for target in PROBE_TARGETS:
        name, selector = target.name, target.selector
        locator = build_locator(page, target)
        try:
            count = await locator.count()
        except Exception as exc:
            count = 0
            print(f"  [异常] {name}：{exc}")

        hit = count > 0

        # 要等上传后才渲染的元素，在探测模式下找不到是预期结果。
        # 把它算作失败会让每次探测都报「页面已改版」，
        # 真正的改版反而淹没在狼来了里。
        if not hit and target.requires_upload:
            print(f"  [跳过] {name}（需视频上传后才渲染）")
            deferred.append(name)
            continue

        all_hit = all_hit and hit
        flag = "命中" if hit else "未命中"
        print(f"  [{flag}] {name}（{count} 个）")

        if not hit:
            print(f"         选择器：{selector}")
            continue

        # 生产取的是第一个 **可用**（存在 + 可见 + 未禁用）的元素，
        # 而不是第一个匹配到的。这里用同一个判定函数，
        # 才能如实回答「实际会操作哪一个」。
        # attached_only 的元素生产从不检查可见性，这里也不能检查，
        # 否则会对一个本就隐藏的输入框稳定误报「不可用」
        if not target.attached_only:
            usable = await is_usable(locator.first)
            if not usable and count == 1:
                warnings.append(
                    f"{name}：元素存在但当前不可用（不可见或被禁用）"
                    f"——若流程在此刻操作它会失败"
                )

        # 命中多个时逐个展开，让人能判断第一个正是想要的那个
        if count > 1:
            shown = min(count, MAX_DETAIL)
            for i in range(shown):
                detail = await describe_element(locator, i)
                marker = "   <- 流程会操作这个" if i == 0 else ""
                print(f"         [{i}] {detail}{marker}")
            if count > shown:
                print(f"         …… 其余 {count - shown} 个未展开")

            # 只有彼此独立的可见元素才构成「可能选错」的风险；
            # 同一控件的层层嵌套节点不算
            independent = await count_independent_visible(locator, count)
            if independent > 1:
                warnings.append(
                    f"{name}：有 {independent} 个互相独立的可见元素同时匹配，"
                    f"需确认第一个正是目标"
                )
            elif independent == 1:
                print("         （以上为同一控件的嵌套节点，取最外层，无歧义）")

    print()
    if not all_hit:
        print("[结论] 存在未命中的选择器——页面很可能已改版")
        print("       请更新 src/douyin_publisher/browser/selectors.py，")
        print("       并同步 tests/fixtures/pages/publish_page.html")
        return False

    print("[结论] 已探测的选择器均能命中，页面结构与预期一致")

    if deferred:
        print("\n[待验证] 以下元素要等视频上传后才渲染，探测模式覆盖不到：")
        for item in deferred:
            print(f"         · {item}")
        print("         用 --dry-run 演练模式才能验证它们。")

    if warnings:
        print("\n[注意] 以下各项需要人工确认：")
        for item in warnings:
            print(f"       · {item}")
        print("       若标注「流程会操作这个」的那一项不是目标，")
        print("       需要收窄 src/douyin_publisher/browser/selectors.py 中的选择器。")
    return True


async def run_probe(config: TaskConfig, user_data_dir: Path) -> int:
    """探测模式：打开页面检查选择器，不做任何交互。"""
    async with browser_session(config, user_data_dir) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        print(f"[导航] 打开 {sel.PUBLISH_PAGE_URL}")
        await page.goto(sel.PUBLISH_PAGE_URL, wait_until="domcontentloaded")
        print(f"[导航] 页面标题：{await page.title()}")

        # 等上传组件出现，它是「已登录且页面已开始渲染」的标志
        try:
            await page.locator(sel.Upload.FILE_INPUT_CSS).first.wait_for(
                state="attached", timeout=PROBE_TIMEOUT_MS
            )
            # 上传组件出现只说明页面开始渲染，各区块仍在异步加载。
            # 不静置就探测会得到不稳定的结果——实测同一页面两次探测
            # 拿到过不同的元素数量，那种结论比没有结论更容易误导人。
            print(f"[等待] 静置 {PROBE_SETTLE_SECONDS:.0f}s，等各区块异步渲染完成")
            await asyncio.sleep(PROBE_SETTLE_SECONDS)
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

    # 刻意不直接打印 result：它的成功文案是「发布成功」，
    # 那是完整流程跑完时的措辞。演练并没有发布，照搬会让人误以为发了。
    if result.ok:
        print(f"\n[结论] 演练完成：{len(DRY_RUN_STEPS)} 个步骤全部执行成功，**未发布**")
        print("       账号下应已留下一条未发布的草稿，可登录后台查看或删除")
    else:
        print(f"\n[结论] 演练失败：{result.code.name}({result.code.code}) {result.message}")
        print(f"       失败阶段：{result.stage.value}")
        print(f"       责任方：{result.code.category.value}"
              f"，是否值得重试：{'是' if result.code.retryable else '否'}")
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
