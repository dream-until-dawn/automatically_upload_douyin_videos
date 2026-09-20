"""流程阶段标识。

阶段名会原样出现在 stdout 的 JSON 结果中（字段 `stage`），
用于让上游知道失败发生在哪一步，与 docs/cli-protocol.md 第 5 节一致。

单独成模块是为了避免 errors 与 pipeline 之间产生循环依赖：
错误需要携带阶段，而阶段属于流程概念。
"""

from __future__ import annotations

from enum import Enum


class Stage(str, Enum):
    """发布流程的阶段。取值顺序即流程的实际执行顺序。"""

    CONFIG = "config"  # 参数解析与校验
    CLEANUP = "cleanup"  # 启动前进程清场
    LAUNCH = "launch"  # 浏览器启动
    NAVIGATE = "navigate"  # 打开发布页
    UPLOAD = "upload"  # 投递视频文件
    TITLE = "title"  # 标题与话题标签
    SETTING = "setting"  # 发布设置
    DECLARATION = "declaration"  # 自主声明
    CART = "cart"  # 挂载购物车
    AWAIT_UPLOAD = "await_upload"  # 等待上传完成
    COVER = "cover"  # 设置封面
    PUBLISH = "publish"  # 点击发布
    AWAIT_PUBLISH = "await_publish"  # 等待发布结果
    DONE = "done"  # 全部完成

    def __str__(self) -> str:
        return self.value
