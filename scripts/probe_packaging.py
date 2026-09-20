"""探针 B 的被打包目标：一个「最小但具代表性」的可执行程序。

代表性体现在，它包含了真实产物中最容易触发杀毒软件启发式判定的几类行为：

  1. 打包为单文件可执行体（自解压到临时目录后执行，是启发式的重点关注对象）；
  2. 枚举系统进程（psutil），这是进程注入类恶意软件的典型前置动作；
  3. 携带 Playwright 驱动（体积大、含 Node 运行时）。

若这个最小产物都无法通过 Windows 安全中心，那么完整产物同样无法通过，
交付形态必须在动工前重新考虑。

用法：由 scripts/run_packaging_probe.ps1 打包与调用，不建议直接运行。
"""

import sys

import psutil
from pydantic import BaseModel

# 与正式代码一致：Windows 控制台默认 GBK，中文输出必须先把标准流切到 UTF-8。
# 在冻结环境下这一步同样有效，本探针顺带验证之。
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")


class ProbeResult(BaseModel):
    """用数据模型走一遍 pydantic，确保其在冻结环境下可正常工作。"""

    python_version: str
    process_count: int
    frozen: bool


def main() -> int:
    # 枚举系统进程：杀软启发式的敏感行为，必须纳入探针
    count = sum(1 for _ in psutil.process_iter(["pid", "name"]))

    # 确认 Playwright 能在冻结环境下完成导入（驱动路径解析是常见打包坑点）
    from playwright.async_api import async_playwright  # noqa: F401

    result = ProbeResult(
        python_version=sys.version.split()[0],
        process_count=count,
        # getattr 兼容非冻结环境（解释器直接运行时没有该属性）
        frozen=getattr(sys, "frozen", False),
    )
    print(result.model_dump_json())
    print("[探针 B] 打包产物可正常执行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
