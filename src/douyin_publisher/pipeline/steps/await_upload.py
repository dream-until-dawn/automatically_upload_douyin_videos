"""步骤：等待视频上传完成。

本步骤只等待「上传成功」这一个进度事件。
「上传失败」「服务异常」等致命事件由哨兵负责——它一旦命中就会取消整个主流程，
包括正阻塞在这里的等待。因此这里 **不需要** 也 **不应该** 自己去判断失败：
重复判断会让同一个事件出现两条处理路径，行为难以预测。
"""

from __future__ import annotations

from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.events import EventType
from douyin_publisher.core.logging import get_logger
from douyin_publisher.pipeline.context import PipelineContext

logger = get_logger("pipeline.await_upload")

# 等待上传完成的超时（秒）。上传耗时取决于文件大小与网络，给足 5 分钟。
UPLOAD_TIMEOUT = 300.0


async def run(ctx: PipelineContext) -> None:
    """阻塞直到收到上传成功事件。

    Raises:
        PublishError: UPLOAD_TIMEOUT —— 超时仍未收到任何上传结论。
    """
    budget = ctx.deadline.budget(UPLOAD_TIMEOUT)
    logger.info(f"[等待] 等待视频上传完成（最多 {budget:.0f}s）")

    # 用上下文管理器确保退订：不退订的话，后续事件会持续堆进这个已无人读取的
    # 队列，在长流程中造成无谓的内存增长。
    with ctx.bus.subscribe() as subscription:
        event = await subscription.wait_for(
            EventType.UPLOAD_SUCCESS, timeout=budget
        )

    if event is None:
        raise PublishError(
            ErrorCode.UPLOAD_TIMEOUT,
            f"等待 {budget:.0f}s 仍未收到上传结论",
        )

    logger.info(f"[等待] 上传完成：{event.text}")
