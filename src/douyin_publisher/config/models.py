"""任务配置数据模型。

字段命名遵循 docs/cli-protocol.md 第 3.2 节的约定：
对外使用上游既有的小驼峰别名，对内使用 Python 惯用的下划线命名。

模型只负责「结构与取值」的表达，不负责「本机环境是否满足」的校验
（例如文件是否存在）——那属于 loader.py 的职责，二者分离便于单独测试。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# 发布时间模式的两个取值
MODE_IMMEDIATE = "立即发布"
MODE_SCHEDULED = "定时发布"

# 定时发布的延后小时数：默认值与取值区间
DEFAULT_PUBLISH_DELAY_HOURS = 80
MIN_PUBLISH_DELAY_HOURS = 1
MAX_PUBLISH_DELAY_HOURS = 300

# 自主声明的默认选项
DEFAULT_SELF_DECLARATION = "无需添加自主声明"

# 商品短标题的长度上限（抖音侧限制）
SHORT_TITLE_MAX_LENGTH = 10


class TaskConfig(BaseModel):
    """一次发布任务的完整配置。

    必填字段由 pydantic 保证 key 存在；「存在但为空字符串」的情况
    由 loader.py 单独校验，二者对应不同的错误码（19 与 3）。
    """

    model_config = ConfigDict(
        # 允许用下划线字段名或小驼峰别名两种方式构造，便于测试直接传 Python 命名
        populate_by_name=True,
        # 上游未来新增字段时不应导致本程序报错
        extra="ignore",
    )

    # ---- 必填 ----
    exec_path: str = Field(alias="execPath", description="Chrome 可执行文件绝对路径")
    user_data_dir: str = Field(alias="userDataDir", description="Chrome 用户数据目录")
    task_id: str = Field(alias="taskId", description="上游任务 ID，仅用于日志与回传")
    douyin_id: str = Field(alias="douyinId", description="抖音账号标识，仅用于日志与回传")
    video_path: str = Field(alias="videoPath", description="待发布视频的本地路径")
    cart_url: str = Field(alias="cartUrl", description="商品（购物车）链接")

    # ---- 选填：内容 ----
    title: str = Field("", description="视频标题")
    desc: str = Field("", description="话题标签，英文逗号分隔，程序自动加 #")
    # 沿用上游既有拼写，不做更名，以保证对接零改动
    cart_titel: str = Field("", alias="cartTitel", description="商品短标题")

    # ---- 选填：发布设置 ----
    publish_time_mode: str = Field(
        "", alias="publishTimeMode", description="立即发布 或 定时发布"
    )
    publish_time: str = Field(
        "", alias="publishTime", description="定时发布的延后小时数"
    )
    who_can_see: str = Field(
        "", alias="whoCanSee", description="公开 / 好友可见 / 仅自己可见"
    )
    save_permission: str = Field("", alias="savePermission", description="允许 或 不允许")
    self_declaration: str = Field("", alias="selfDeclaration", description="自主声明选项")

    # ---- 选填：运行方式 ----
    headless: bool = Field(False, description="是否无头运行")

    # ------------------------------------------------------------------
    # 派生属性：把上游传来的原始字符串翻译成流程可直接使用的值
    # ------------------------------------------------------------------

    @property
    def is_scheduled(self) -> bool:
        """是否为定时发布。"""
        return self.publish_time_mode == MODE_SCHEDULED

    @property
    def publish_delay_hours(self) -> int:
        """定时发布的延后小时数，已归一化到有效区间。

        归一化规则（容错优先，不因取值异常而让任务失败）：
          · 非数字或缺失 -> 默认 80 小时
          · 小于等于 0   -> 默认 80 小时
          · 大于 300     -> 截断为 300 小时
        """
        try:
            hours = int(str(self.publish_time).strip())
        except (ValueError, TypeError):
            return DEFAULT_PUBLISH_DELAY_HOURS

        if hours < MIN_PUBLISH_DELAY_HOURS:
            return DEFAULT_PUBLISH_DELAY_HOURS
        return min(hours, MAX_PUBLISH_DELAY_HOURS)

    @property
    def effective_self_declaration(self) -> str:
        """自主声明选项，留空时取默认值。"""
        return self.self_declaration or DEFAULT_SELF_DECLARATION

    @property
    def tags(self) -> list[str]:
        """从 desc 解析出的话题标签列表，已去除空白项。

        上游以英文逗号分隔传入，例如 "夏日穿搭,清凉一夏"。
        """
        if not self.desc:
            return []
        return [tag.strip() for tag in self.desc.split(",") if tag.strip()]

    def resolve_short_title(self, origin_title: str) -> str:
        """决定商品短标题。

        优先使用上游指定的短标题；未指定时截取商品原标题。
        无论来源如何都会截断到平台允许的长度上限。

        Args:
            origin_title: 从页面读到的商品原标题。
        """
        source = self.cart_titel or origin_title
        return source[:SHORT_TITLE_MAX_LENGTH]

    def __str__(self) -> str:
        """日志友好的摘要。

        刻意不输出完整配置：user_data_dir 指向用户的浏览器画像目录，
        属于敏感路径，日志中只保留定位问题所必需的字段。
        """
        return (
            f"TaskConfig(taskId={self.task_id}, douyinId={self.douyin_id}, "
            f"video={self.video_path}, mode={self.publish_time_mode or '未指定'}, "
            f"headless={self.headless})"
        )
