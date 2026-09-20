"""集成测试夹具：真实浏览器 + 本地模拟页。

## 为什么用真实浏览器

选择器写法、元素可用性判定、DOM 观察器注入这些东西，打桩测不出来——
桩会按你以为的方式工作，而真实 DOM 按它自己的方式工作。
这里用 Playwright 自带的 Chromium 驱动本地模拟页，既真实又无外部依赖。

## 为什么不连真实抖音

线上页面不可控、需要真实账号、且会产生真实的发布行为。
真实页面的结构验证由 scripts/ 下的人工冒烟脚本负责，不进自动化测试。

## 浏览器为何按测试函数启动

每个测试独立启动一个浏览器实例，牺牲一点速度换取完全的状态隔离：
上一个测试留下的 DOM、注入的脚本、已弹出的提示都不会影响下一个测试。
headless Chromium 启动约数百毫秒，整体仍在可接受范围内。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlencode

import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright

# 模拟页所在目录
PAGES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "pages"
PUBLISH_PAGE = PAGES_DIR / "publish_page.html"


def mock_page_url(**params: str | int) -> str:
    """构造模拟页的 URL，查询参数用于注入场景与失败条件。

    Example:
        mock_page_url(scenario="upload_failure", uploadDelay=100)
        mock_page_url(missing="editor")
    """
    assert PUBLISH_PAGE.is_file(), f"模拟页不存在：{PUBLISH_PAGE}"

    base = PUBLISH_PAGE.as_uri()
    if not params:
        return base
    return f"{base}?{urlencode(params)}"


@pytest_asyncio.fixture
async def browser() -> AsyncIterator[Browser]:
    """一个 headless Chromium 实例。"""
    async with async_playwright() as playwright:
        instance = await playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            await instance.close()


@pytest_asyncio.fixture
async def page(browser: Browser) -> AsyncIterator[Page]:
    """一个全新的空白页面。"""
    context = await browser.new_context()
    try:
        yield await context.new_page()
    finally:
        await context.close()
