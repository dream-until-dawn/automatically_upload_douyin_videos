"""运行时选项：超时、浏览器参数、日志级别、跳过步骤。

这些是允许调用方按自身环境调整的参数。
划分依据见 [ADR-0003](../../../docs/adr/0003-runtime-options.md)：
**只暴露调用方有判断依据去调的那些**。

「等待视频上传完成」调用方能判断——它知道视频多大、带宽多宽；
「点击后停顿 0.5 秒等渲染」调用方无从判断，暴露出去只会得到一个
没人知道怎么填的字段，填错了还会造成时序相关的间歇性失败。
因此后者留在各步骤内部，作为实现细节。
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from douyin_publisher.core.stages import Stage


class LogLevel(str, Enum):
    """日志详细程度。"""

    QUIET = "quiet"  # 仅警告及以上，批量调度时可显著压低日志量
    NORMAL = "normal"  # 默认：各步骤的关键进展
    DEBUG = "debug"  # 追加元素查找等细节，排查页面问题时使用

    @property
    def logging_level(self) -> int:
        """映射到标准库的日志级别。"""
        return {
            LogLevel.QUIET: logging.WARNING,
            LogLevel.NORMAL: logging.INFO,
            LogLevel.DEBUG: logging.DEBUG,
        }[self]


class Timeouts(BaseModel):
    """各环节的等待上限（秒）。

    全部可选，缺省值取自本机实测。各步骤通过共享的 Deadline 申请预算，
    因此即便某一项配得比 `total` 还大，也不会突破总上限。
    """

    model_config = ConfigDict(extra="ignore")

    total: float = Field(600.0, gt=0, description="整个流程的时间上限")
    navigate: float = Field(60.0, gt=0, description="打开发布页")
    page_ready: float = Field(
        30.0, gt=0, alias="pageReady", description="等待页面关键组件就绪"
    )
    # 取 15 而非 10：此前不同步骤分别用 10 与 15，统一成一个字段时取较大者。
    # 往小了统一会缩短等待上限，可能引入新的偶发失败；
    # 往大了统一只影响失败路径的耗时，成功路径一点不变。
    element: float = Field(15.0, gt=0, description="通用的元素等待")
    cart_modal: float = Field(
        15.0, gt=0, alias="cartModal", description="等待商品编辑弹窗"
    )
    upload: float = Field(300.0, gt=0, description="等待视频上传完成")
    cover_frame: float = Field(
        60.0, gt=0, alias="coverFrame", description="等待封面候选帧抽取"
    )
    publish: float = Field(120.0, gt=0, description="等待发布结果")


class ScreenshotOptions(BaseModel):
    """失败现场截图。

    默认开启。理由是这类信息的价值几乎全在「事后」——
    等到出了问题才想起来打开开关，那一次的现场已经没有了。
    """

    model_config = ConfigDict(extra="ignore")

    on_failure: bool = Field(
        True, alias="onFailure", description="失败时是否自动截图"
    )
    dir: str = Field("", description="存放目录，留空则用系统临时目录")


# ----------------------------------------------------------------------
# 浏览器启动参数
# ----------------------------------------------------------------------

# 拒绝接受的参数前缀。
#
# 不是出于洁癖，这三类各自会破坏程序赖以运行的前提：
#   · user-data-dir —— 画像目录由 userDataDir 字段指定。从这里改会让
#     「启动前清场」去清理另一个目录，导致浏览器因目录被占用而启动失败，
#     而报出的原因看起来与真正的起因毫不相干。
#   · remote-debugging-* —— Playwright 靠它与浏览器通信，覆盖即失控。
FORBIDDEN_ARG_PREFIXES: tuple[str, ...] = (
    "--user-data-dir",
    "--remote-debugging-port",
    "--remote-debugging-pipe",
)


def validate_browser_args(args: list[str]) -> list[str]:
    """校验调用方追加的浏览器启动参数。

    Args:
        args: 待校验的参数列表。

    Returns:
        原样返回（校验通过时）。

    Raises:
        ValueError: 含有被拒绝的参数。

    这里选择 **拒绝** 而非静默忽略：配置没生效却不报错，
    会让人对着一个看起来已经配好的选项反复调试，是最难排查的一类问题。
    """
    for arg in args:
        stripped = arg.strip()
        if not stripped.startswith("--"):
            raise ValueError(
                f"浏览器参数必须以 -- 开头：{arg!r}（疑似拼装错误）"
            )
        for forbidden in FORBIDDEN_ARG_PREFIXES:
            if stripped.startswith(forbidden):
                raise ValueError(
                    f"不接受的浏览器参数：{arg!r}。"
                    f"{forbidden} 由程序自身管理，从外部覆盖会破坏运行前提"
                )
    return args


# ----------------------------------------------------------------------
# 可跳过的步骤
# ----------------------------------------------------------------------

# 允许跳过的阶段。
#
# 用白名单而非黑名单：upload / await_upload / publish / await_publish
# 四步构成「发布一个视频」的最小定义，允许跳过它们，
# 这个程序就不再是它声称的那个东西了——跳过上传发布的是空内容，
# 跳过发布则根本没有发布。
#
# 「跑完流程但不发布」有专门的出口（冒烟脚本的演练模式），
# 白名单同时挡住了把生产配置误用作演练开关的路径。
SKIPPABLE_STAGES: frozenset[Stage] = frozenset(
    {Stage.TITLE, Stage.SETTING, Stage.DECLARATION, Stage.CART, Stage.COVER}
)


def validate_skip_stages(names: list[str]) -> list[Stage]:
    """把调用方给的阶段名转换为 Stage，并校验其可跳过。

    Args:
        names: 阶段名列表，取值见 docs/cli-protocol.md 的阶段标识表。

    Returns:
        对应的 Stage 列表。

    Raises:
        ValueError: 阶段名未知，或该阶段不允许跳过。
    """
    resolved: list[Stage] = []
    for name in names:
        try:
            stage = Stage(name.strip())
        except ValueError as exc:
            allowed = "、".join(sorted(s.value for s in SKIPPABLE_STAGES))
            raise ValueError(
                f"未知的阶段名：{name!r}。可跳过的阶段有：{allowed}"
            ) from exc

        if stage not in SKIPPABLE_STAGES:
            allowed = "、".join(sorted(s.value for s in SKIPPABLE_STAGES))
            raise ValueError(
                f"阶段 {stage.value} 不允许跳过——它是「发布一个视频」的必要环节。"
                f"可跳过的阶段有：{allowed}"
            )
        resolved.append(stage)
    return resolved
