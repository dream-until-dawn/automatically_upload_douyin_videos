"""进度回报：向 stdout 输出进度行。

协议见 [ADR-0004](../../../docs/adr/0004-progress-reporting.md)：

  · 每行一个 JSON 对象，用 `type` 区分 `progress` 与 `result`；
  · 结果永远是最后一行，「读 stdout 最后一行」的解析方式始终成立；
  · 默认关闭——它会让 stdout 从一行变成多行，而我们无法确定
    上游是按「读最后一行」还是「整段解析」来处理的。

进度只报「第 N 步 / 共 M 步」，不报上传百分比。
百分比要从页面元素里抠，页面一改版抠出来的数字就不对了，
而且不会报错、只会安静地给出错误数字——
一个会静默说谎的进度条比没有进度条更糟，因为调度方会基于它做判断。
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass

from douyin_publisher.core.stages import Stage
from douyin_publisher.cli.result import SCHEMA_VERSION

# 进度行的两种来源
KIND_STEP = "step"  # 步骤切换
KIND_HEARTBEAT = "heartbeat"  # 长等待期间的存活信号


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """一条进度。

    Attributes:
        stage: 当前阶段。
        step: 当前是第几步（从 1 开始）。
        total: 总步数。
        kind: 来源，step（步骤切换）或 heartbeat（心跳）。
        title: 步骤的中文名称。
        elapsed_ms: 从流程开始到现在的耗时。
    """

    stage: Stage
    step: int
    total: int
    kind: str
    title: str
    elapsed_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA_VERSION,
            "type": "progress",
            "kind": self.kind,
            "stage": self.stage.value,
            "title": self.title,
            "step": self.step,
            "total": self.total,
            "elapsedMs": self.elapsed_ms,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


class ProgressReporter:
    """把进度写到 stdout。

    关闭时所有方法都是空操作，调用方无需到处写 if——
    「要不要报进度」这个判断只存在于这一个地方。
    """

    def __init__(self, enabled: bool, total_steps: int):
        """
        Args:
            enabled: 是否真的输出。
            total_steps: 总步数，用于计算「第 N 步 / 共 M 步」。
        """
        self._enabled = enabled
        self._total = max(total_steps, 1)
        self._started = time.monotonic()
        self._current_step = 0
        self._current_stage: Stage | None = None
        self._current_title = ""

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def _emit(self, kind: str) -> None:
        if not self._enabled or self._current_stage is None:
            return

        event = ProgressEvent(
            stage=self._current_stage,
            step=self._current_step,
            total=self._total,
            kind=kind,
            title=self._current_title,
            elapsed_ms=self._elapsed_ms(),
        )
        # 与结果行同为 stdout，且都是单行 JSON。
        # flush 不能省：进度的意义就在于「实时」，留在缓冲区里的进度没有价值。
        print(event.to_json(), file=sys.stdout, flush=True)

    def enter_step(self, stage: Stage, title: str, index: int) -> None:
        """进入某个步骤。

        Args:
            stage: 阶段。
            title: 步骤中文名。
            index: 第几步，从 1 开始。
        """
        self._current_stage = stage
        self._current_title = title
        self._current_step = index
        self._emit(KIND_STEP)

    def heartbeat(self) -> None:
        """发一次心跳，表示任务仍然存活。

        这是进度回报真正解决问题的部分：只在步骤切换时报进度的话，
        等上传的那一分多钟里依然一片寂静，与卡死毫无区别。
        """
        self._emit(KIND_HEARTBEAT)
