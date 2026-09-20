"""步骤：投递视频文件。

本步骤只负责把文件塞进上传框，**不等待上传完成**。
等待是 `await_upload` 步骤的事，中间还夹着填标题、发布设置、挂车等操作——
让它们与上传并行进行，是整个流程能跑得快的关键。
"""

from __future__ import annotations

from pathlib import Path

from douyin_publisher.browser.selectors import Upload
from douyin_publisher.core.errors import ErrorCode, PublishError
from douyin_publisher.core.logging import get_logger
from douyin_publisher.pipeline.context import PipelineContext

logger = get_logger("pipeline.upload")
async def run(ctx: PipelineContext) -> None:
    """把本地视频文件投递到上传框。

    Raises:
        PublishError:
            NOT_LOGGED_IN —— 页面上没有上传组件。未登录时会被重定向到登录页，
                表现就是找不到这个组件，因此把它当作未登录的判定依据。
            UPLOAD_FAILED —— 文件投递本身失败。
    """
    video = Path(ctx.config.video_path).resolve()
    logger.info(f"[上传] 准备投递视频：{video}")

    file_input = ctx.page.locator(Upload.FILE_INPUT_CSS).first

    # 用 attached 而非 visible：真实页面的文件输入框是隐藏的（透明、尺寸为 1px），
    # 要求可见会永远等不到。
    try:
        await file_input.wait_for(
            state="attached",
            timeout=ctx.deadline.budget(ctx.config.timeouts.page_ready) * 1000,
        )
    except Exception as exc:
        raise PublishError(
            ErrorCode.NOT_LOGGED_IN,
            "页面上找不到视频上传组件，通常意味着账号未登录或被重定向到了登录页",
            cause=exc,
        ) from exc

    try:
        await file_input.set_input_files(str(video))
    except Exception as exc:
        raise PublishError(
            ErrorCode.UPLOAD_FAILED, f"投递文件失败：{exc}", cause=exc
        ) from exc

    logger.info("[上传] 文件已投递，上传在后台进行，流程继续")
