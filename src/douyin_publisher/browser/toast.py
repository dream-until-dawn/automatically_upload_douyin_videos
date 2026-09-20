"""轻提示（toast）监听与语义翻译。

页面把上传结果、发布结果、服务异常等关键信息都以轻提示的形式弹出，
它们是本程序感知「结局已定」的主要信号源，也是事件竞速中止的输入。

## 采集方式

在页面中注入一个 `MutationObserver`，监听提示容器的插入与文本变动，
命中后通过 Playwright 的 binding 回调进 Python，再翻译成带语义的事件。

为什么不用轮询查询 DOM：轻提示存在时间很短（通常 3 秒后自动消失），
轮询间隔稍大就会整条漏掉，而漏掉一条致命提示意味着白等整个超时窗口。
MutationObserver 是事件驱动的，不存在采样间隔问题。

## 三个容易踩的坑

1. **文本延迟渲染**：提示节点挂载时 `textContent` 可能还是空的，
   立刻读会拿到空串。因此命中后要短暂重试几次再放弃。
2. **重复上报**：同一个节点可能因多次变动被反复命中，用 WeakSet 去重。
3. **导航丢失**：页面跳转后注入的脚本会被清除。
   因此同时使用 `add_init_script`（对后续导航生效）与一次性 `evaluate`
   （对当前页面生效）。
"""

from __future__ import annotations

import json

from playwright.async_api import Page

from douyin_publisher.browser.selectors import TOAST_KEYWORDS, Toast
from douyin_publisher.core.events import EventBus, EventType, PageEvent
from douyin_publisher.core.logging import get_logger

logger = get_logger("browser.toast")

# 暴露给页面 JS 的回调函数名。加前缀是为了避免与页面自身的全局变量冲突。
_BINDING_NAME = "__dy_publisher_on_toast"

# 注入页面的监听脚本。
#
# 脚本自身带幂等保护：重复注入（例如导航后 init script 与 evaluate 都跑了一遍）
# 不会装上两个观察器，否则每条提示都会被上报两次。
_OBSERVER_SCRIPT = """
(args) => {
    // Playwright 的 evaluate 只接受单个参数，因此把两个入参打包成数组传入
    const [selector, bindingName] = args;

    // 幂等保护：同一个页面只装一次观察器
    if (window.__dyPublisherToastInstalled) return;
    window.__dyPublisherToastInstalled = true;

    // 已上报过的节点，避免重复上报
    const reported = new WeakSet();

    // 文本可能延迟渲染，命中后短暂重试几次
    const MAX_RETRY = 10;
    const RETRY_INTERVAL_MS = 100;

    function reportWhenTextReady(el) {
        if (!el || reported.has(el)) return;

        let retries = 0;
        const timer = setInterval(() => {
            const text = (el.innerText || el.textContent || '').trim();
            if (text) {
                clearInterval(timer);
                if (!reported.has(el)) {
                    reported.add(el);
                    window[bindingName](text);
                }
            } else if (++retries > MAX_RETRY) {
                // 始终读不到文本，放弃这个节点
                clearInterval(timer);
            }
        }, RETRY_INTERVAL_MS);
    }

    function scanAll() {
        document.querySelectorAll(selector).forEach(reportWhenTextReady);
    }

    // 注入时页面上可能已经有提示了，先扫一遍，否则会漏掉注入前弹出的提示
    scanAll();

    const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            if (mutation.type === 'childList') {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType !== Node.ELEMENT_NODE) return;
                    // 新节点自身可能就是提示，也可能提示藏在它的子树里
                    if (node.matches && node.matches(selector)) {
                        reportWhenTextReady(node);
                    } else if (node.querySelectorAll) {
                        node.querySelectorAll(selector).forEach(reportWhenTextReady);
                    }
                });
            } else {
                // 部分提示不销毁节点，只改写文本，只能整体重扫
                scanAll();
            }
        }
    });

    observer.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true,
    });
}
"""


def translate_toast(text: str) -> EventType | None:
    """把轻提示文本翻译为事件类型。

    按 `TOAST_KEYWORDS` 的登记顺序做子串匹配，先命中者胜出。
    顺序很关键：宽泛的关键词（如「服务」「不支持」）必须排在具体文案之后，
    否则会抢先命中本该归类为上传/发布结果的提示。

    Args:
        text: 提示原文。

    Returns:
        对应的事件类型；无法识别时返回 None（大量提示与流程无关，属于正常情况）。
    """
    if not text:
        return None

    trimmed = text.strip()
    for keyword, event_name in TOAST_KEYWORDS:
        if keyword in trimmed:
            return EventType(event_name)
    return None


async def install_toast_listener(page: Page, bus: EventBus) -> None:
    """在页面上安装轻提示监听器，命中的提示会被翻译后投递到事件总线。

    Args:
        page: 目标页面。
        bus: 接收事件的总线。
    """

    def on_toast(text: str) -> None:
        """由页面 JS 调用的回调。

        这是同步函数：EventBus.publish 使用 put_nowait，不会阻塞，
        因此可以安全地在 binding 回调中直接调用。
        """
        trimmed = (text or "").strip()
        if not trimmed:
            return

        event_type = translate_toast(trimmed)
        if event_type is None:
            # 与流程无关的提示很常见，记为调试级别即可，不污染正常日志
            logger.debug(f"[提示] 收到无关提示：{trimmed}")
            return

        logger.info(f"[提示] {event_type.value} <- {trimmed}")
        bus.publish(PageEvent(type=event_type, text=trimmed))

    # binding 注册在页面级，重复注册会抛错；同一个页面只需装一次
    try:
        await page.expose_function(_BINDING_NAME, on_toast)
    except Exception as exc:  # pragma: no cover - 正常流程中不会重复安装
        logger.debug(f"[提示] 回调已存在，跳过注册：{exc}")

    # 对后续导航生效：页面跳转后脚本会被清除，需要自动重装
    script_args = [Toast.CONTAINER_CSS, _BINDING_NAME]
    await page.add_init_script(
        f"({_OBSERVER_SCRIPT})({json.dumps(script_args)})"
    )
    # 对当前页面生效：init script 只影响之后的导航，当前页面要手动执行一次
    await page.evaluate(_OBSERVER_SCRIPT, script_args)

    logger.info("[提示] 轻提示监听器已安装")
