"""错误码体系与异常类型。

本模块是错误契约的 **唯一真相源**，与 docs/error-codes.md 保持逐项一致
（该一致性由 tests/unit/test_error_contract.py 强制校验，文档与实现不允许漂移）。

设计约束：

  · 业务代码 **不得** 直接调用 sys.exit(数字)，只能抛出 PublishError。
    退出码的产生只有一个出口（CLI 层），这样才能保证浏览器一定被关闭、
    JSON 结果一定被输出。
  · 每个错误码都自带「责任分类」与「是否可重试」，上游据此决策，
    不需要解析日志文本。
"""

from __future__ import annotations

from enum import Enum

from douyin_publisher.core.stages import Stage


class Category(str, Enum):
    """错误的责任归属分类，决定上游该如何处置。"""

    NONE = "none"  # 成功，无责任方
    CONFIG = "config"  # 传入的任务配置有问题
    ENVIRONMENT = "environment"  # 本机环境问题（浏览器缺失、目录无权限）
    USER = "user"  # 账号态问题（未登录）
    PLATFORM = "platform"  # 抖音服务端或网络问题，可重试
    PAGE = "page"  # 页面结构与预期不符，多为改版
    PRODUCT = "product"  # 商品自身问题（下架、不支持推广、挂车上限）
    INTERNAL = "internal"  # 本程序自身的缺陷


class ErrorCode(Enum):
    """错误码枚举。

    枚举值为四元组：(退出码, 责任分类, 是否可重试, 中文描述)。

    退出码一经发布不得改变含义；新增错误只能追加新号码，不得复用旧号码。
    所有退出码限制在 0 与 3..254 之间：1、2 为系统保留，
    超出 255 的值在 Windows 上行为不确定。
    """

    SUCCESS = (0, Category.NONE, False, "发布成功")

    # ---- 配置与参数 ----
    CONFIG_INVALID = (3, Category.CONFIG, False, "配置字段校验未通过")
    VIDEO_NOT_FOUND = (6, Category.CONFIG, False, "待发布视频不存在或无访问权限")
    ARGS_MISSING = (18, Category.CONFIG, False, "命令行参数缺失")
    CONFIG_PARSE_FAILED = (19, Category.CONFIG, False, "配置解析失败")

    # ---- 本机环境 ----
    CHROME_NOT_FOUND = (4, Category.ENVIRONMENT, False, "浏览器可执行文件不存在或无访问权限")
    USER_DATA_DIR_INVALID = (5, Category.ENVIRONMENT, False, "用户数据目录不存在、非目录或为空")
    PATH_RESOLVE_FAILED = (7, Category.ENVIRONMENT, False, "路径规范化失败")
    BROWSER_LAUNCH_FAILED = (8, Category.ENVIRONMENT, False, "浏览器启动失败")

    # ---- 账号态 ----
    NOT_LOGGED_IN = (9, Category.USER, False, "账号未登录")

    # ---- 平台与网络（可重试）----
    UPLOAD_FAILED = (10, Category.PLATFORM, True, "视频上传失败")
    UPLOAD_TIMEOUT = (11, Category.PLATFORM, True, "视频上传超时")
    PIPELINE_TIMEOUT = (12, Category.PLATFORM, True, "发布流程整体超时")
    SERVICE_ERROR = (13, Category.PLATFORM, True, "抖音服务异常")
    PUBLISH_FAILED = (17, Category.PLATFORM, True, "发布失败")

    # ---- 页面结构（多为改版）----
    CART_ATTACH_FAILED = (14, Category.PAGE, False, "挂载购物车失败")
    DECLARATION_FAILED = (15, Category.PAGE, False, "自主声明配置失败")
    COVER_FAILED = (16, Category.PAGE, False, "封面配置失败")
    PUBLISH_SETTING_FAILED = (20, Category.PAGE, False, "发布设置配置失败")
    TITLE_INPUT_FAILED = (24, Category.PAGE, False, "标题或话题标签填写失败")

    # ---- 商品自身 ----
    CART_LIMIT_REACHED = (21, Category.PRODUCT, False, "无法添加购物车（已达挂车上限）")
    PRODUCT_NOT_SUPPORTED = (22, Category.PRODUCT, False, "该商品暂不支持在直播/短视频推广")
    PRODUCT_NOT_FOUND = (23, Category.PRODUCT, False, "未搜索到对应商品（商品可能已下架）")

    # ---- 程序自身 ----
    UNEXPECTED = (88, Category.INTERNAL, False, "未捕获的程序内部异常")

    def __init__(self, code: int, category: Category, retryable: bool, message: str):
        self.code = code
        self.category = category
        self.retryable = retryable
        self.message = message

    @property
    def ok(self) -> bool:
        """是否表示成功。"""
        return self.code == 0

    @classmethod
    def from_code(cls, code: int) -> ErrorCode:
        """按退出码反查枚举成员，用于测试与日志。"""
        for member in cls:
            if member.code == code:
                return member
        raise ValueError(f"未登记的错误码: {code}")

    def __str__(self) -> str:
        return f"{self.name}({self.code}) {self.message}"


class PublishError(Exception):
    """携带错误码的业务异常。

    这是业务层表达失败的 **唯一** 方式。异常在编排层被统一翻译为退出码与
    JSON 结果，业务层因此完全不需要关心进程如何退出。

    Args:
        code: 错误码。
        detail: 补充说明，会拼接在默认描述之后，用于定位具体原因。
        stage: 失败发生的流程阶段；留空时由编排层按当前阶段补齐。
        cause: 触发本异常的底层异常，仅用于日志。
    """

    def __init__(
        self,
        code: ErrorCode,
        detail: str = "",
        *,
        stage: Stage | None = None,
        cause: BaseException | None = None,
    ):
        self.code = code
        self.detail = detail
        self.stage = stage
        self.cause = cause
        super().__init__(self.message)

    @property
    def message(self) -> str:
        """面向人的完整描述：默认描述 + 补充说明。"""
        return f"{self.code.message}：{self.detail}" if self.detail else self.code.message

    def with_stage(self, stage: Stage) -> PublishError:
        """补齐阶段信息。

        步骤内部抛错时往往不关心自己处于哪个阶段，由编排层在捕获时补上。
        已显式指定阶段的异常不会被覆盖。
        """
        if self.stage is None:
            self.stage = stage
        return self

    def __str__(self) -> str:
        return f"[{self.code.name}/{self.code.code}] {self.message}"
