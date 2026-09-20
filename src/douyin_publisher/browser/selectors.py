"""页面选择器与文案常量的集中登记。

## 为什么要集中

目标站点是第三方页面，随时可能改版。若选择器散落在各个业务步骤里，
一次改版就要翻遍整个代码库；集中登记后，应对成本降为「改一个文件」。

## 命名约定

  · `*_CSS`   —— CSS 选择器
  · `*_XPATH` —— XPath 表达式
  · `TEXT_*`  —— 用于按文本定位的中文文案

## 关于「或」选择器

部分常量写成 `a, b` 的并列形式，例如 `"span[class^='cart-mybtn-'], button"`。
这是刻意的冗余：页面使用带哈希后缀的 CSS Modules 类名（如 `cart-mybtn-a1b2c3`），
前缀匹配在类名规则变化时会失效，因此保留一个宽泛的兜底选择器，
再配合文本匹配收窄范围。

## 与测试的关系

集成测试使用的模拟页必须能被这里的每一个选择器命中，
该约束由 tests/integration/test_selectors_match_mock.py 强制校验——
这样模拟页就不会悄悄与真实页面的结构假设脱节。
"""

from __future__ import annotations

from typing import Final

# 创作者中心视频发布页
PUBLISH_PAGE_URL: Final = (
    "https://creator.douyin.com/creator-micro/content/post/video"
    "?enter_from=publish_page"
)


class Toast:
    """轻提示（toast）。

    页面使用 Semi Design 组件库，其提示容器有固定类名；
    额外保留一个模糊匹配，以覆盖自定义封装的提示组件。
    """

    CONTAINER_CSS: Final = (
        ".semi-toast-content, .semi-toast-notice, [class*='toast-content']"
    )


class Upload:
    """视频上传。"""

    # 文件输入框：同时限定 name 与 accept，避免命中页面上其他的文件选择器
    FILE_INPUT_CSS: Final = 'input[type="file"][name="upload-btn"][accept*="mp4"]'


class Editor:
    """标题与话题标签编辑器。

    页面使用 Slate.js 富文本编辑器，它不是 input 而是 contenteditable 容器，
    因此不能用 fill()，只能模拟键盘输入。
    """

    SLATE_CSS: Final = '[contenteditable="true"][data-slate-editor="true"]'


class Cart:
    """购物车（商品挂载）。"""

    # 「添加标签」文本所在的表单行容器，下拉框就在其中
    SECTION_XPATH: Final = (
        "//*[contains(text(), '添加标签')]"
        "/ancestor::div[contains(@class, 'form-item')"
        " or contains(@class, 'row')"
        " or contains(@class, 'content')][1]"
    )
    DROPDOWN_CSS: Final = "div[class*='semi-select']"
    OPTION_CSS: Final = "div.select-dropdown-option-video"

    LINK_INPUT_CSS: Final = "input[placeholder*='商品链接'], input[placeholder*='粘贴']"
    ADD_LINK_BUTTON_CSS: Final = "span[class^='cart-mybtn-'], button"

    # 商品编辑弹窗
    MODAL_TITLE_CSS: Final = "div[class^='modal-title-']"
    ORIGIN_TITLE_CSS: Final = "div[class^='item-origin-title-']"
    SHORT_TITLE_INPUT_CSS: Final = "input[placeholder*='短标题']"
    FINISH_EDIT_BUTTON_CSS: Final = "button[class^='button-'], button"

    # 挂载成功后出现的已添加商品卡片
    ADDED_CARD_CSS: Final = (
        "div[class*='cart-goodlist-wrapper'], div[class*='cart-container'], p"
    )

    # 已存在商品时先移除
    REMOVE_BUTTON_CSS: Final = "button"

    TEXT_OPTION: Final = "购物车"
    TEXT_ADD_LINK: Final = "添加链接"
    TEXT_FINISH_EDIT: Final = "完成编辑"
    TEXT_ADDED: Final = "已添加商品"
    TEXT_REMOVE: Final = "移除"
    TEXT_CONFIRM: Final = "确定"

    # 弹窗标题中出现这些文案时，表示商品本身有问题
    TEXT_LIMIT_REACHED: Final = "无法添加购物车"
    TEXT_NOT_FOUND: Final = "未搜索到"


class PublishSetting:
    """发布设置：发布时间、可见范围、保存权限。"""

    # 三组设置都使用单选标签，靠文案区分
    RADIO_CSS: Final = "label[class*='radio'], label"
    SCHEDULE_INPUT_CSS: Final = 'input[class*="semi-input"][placeholder*="日期和时间"]'

    # 定时发布的时间格式
    SCHEDULE_TIME_FORMAT: Final = "%Y-%m-%d %H:%M"


class Declaration:
    """自主声明。"""

    # 「自主声明」文本所在包装容器内的下拉触发区
    SELECT_BOX_XPATH: Final = (
        "//*[contains(text(), '自主声明')]"
        "/ancestor::*[contains(@class, 'wrapper')][1]"
        "//div[contains(@class, 'selectBox')]"
    )
    RADIO_CSS: Final = "label.semi-radio, label[class*='radio'], label"
    CONFIRM_BUTTON_CSS: Final = "button[class*='semi-button'], button"

    TEXT_CONFIRM: Final = "确定"


class Cover:
    """封面设置。"""

    # 「选择封面」按钮位于一个带 filter- 前缀类名的容器中
    ENTRY_XPATH: Final = "//div[contains(@class, 'filter-')][./div[text()='选择封面']]"
    MODAL_CSS: Final = "#dy-creator-content-modal-body"
    BUTTON_CSS: Final = "button, span.semi-button-content"

    TEXT_SET_HORIZONTAL: Final = "设置横封面"
    TEXT_DONE: Final = "完成"


class Publish:
    """发布按钮。"""

    BUTTON_CSS: Final = "button"

    TEXT_PUBLISH: Final = "发布"


# ----------------------------------------------------------------------
# 轻提示文案 -> 事件语义的映射
#
# 顺序有意义：按列表顺序做子串匹配，先命中者胜出。
# 「上传失败」必须排在「上传成功」之后是不够的——两者互不包含，顺序无所谓；
# 但更宽泛的「服务」「不支持」必须排在具体文案之后，否则会抢先命中。
# 该顺序由 tests/unit/test_toast_translation.py 锁死。
# ----------------------------------------------------------------------

TOAST_KEYWORDS: Final[tuple[tuple[str, str], ...]] = (
    ("上传成功", "upload_success"),
    ("上传失败", "upload_failure"),
    ("发布成功", "publish_success"),
    ("发布失败", "publish_failure"),
    ("不支持", "product_not_supported"),
    ("服务", "service_error"),
)
