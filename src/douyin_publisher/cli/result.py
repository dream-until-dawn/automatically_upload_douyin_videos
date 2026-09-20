"""执行结果的结构化输出。

契约见 docs/cli-protocol.md 第 4 节：

  · stderr —— 人类可读的过程日志
  · stdout —— **有且仅有一行** JSON 结果

分流的意义在于上游只需读 stdout 的最后一行就能拿到完整结论，
不必在混杂的日志里做正则匹配。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from douyin_publisher.core.errors import ErrorCode
from douyin_publisher.core.stages import Stage

# 结果结构的版本号。字段发生破坏性变化时递增，新增字段不递增。
SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class TaskResult:
    """一次执行的最终结果。

    Attributes:
        code: 错误码，同时也是进程退出码。
        stage: 结束时所处的阶段。
        message: 中文描述。
        task_id: 上游任务 ID，原样回传便于对账。
        douyin_id: 抖音账号标识，原样回传。
        elapsed_ms: 执行耗时（毫秒）。
    """

    code: ErrorCode
    stage: Stage
    message: str
    task_id: str = ""
    douyin_id: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, object]:
        """转换为输出用的字典。

        字段名使用小驼峰，与上游既有的配置字段风格保持一致。
        """
        return {
            "schema": SCHEMA_VERSION,
            "ok": self.code.ok,
            "code": self.code.code,
            "name": self.code.name,
            "category": self.code.category.value,
            "retryable": self.code.retryable,
            "stage": self.stage.value,
            "message": self.message,
            "taskId": self.task_id,
            "douyinId": self.douyin_id,
            "elapsedMs": self.elapsed_ms,
        }

    def to_json(self) -> str:
        """序列化为单行 JSON。

        ensure_ascii=False 保留中文原文：上游可能直接把 message 展示给用户，
        转义成 \\uXXXX 只会让排障时多一道解码工序。
        """
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def emit(result: TaskResult) -> None:
    """把结果写到 stdout。

    显式 flush：进程即将退出，缓冲区未刷新会让上游读到空输出——
    这种故障只在特定的管道配置下出现，极难复现。
    """
    print(result.to_json(), file=sys.stdout, flush=True)
