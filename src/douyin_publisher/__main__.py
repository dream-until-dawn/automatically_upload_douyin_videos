"""模块入口：python -m douyin_publisher <子命令> <参数>

打包后的可执行体同样走这里。
"""

import sys

from douyin_publisher.cli.app import main

if __name__ == "__main__":
    sys.exit(main())
