# 抖音视频自动发布器

驱动本机 Chrome 完成抖音创作者中心的视频发布全流程：
上传视频 → 填写标题与话题 → 配置发布设置 → 自主声明 → 挂载商品 → 设置封面 → 发布。

以命令行子进程的形式被调度方拉起，执行一次任务后退出，
通过 **进程退出码 + 结构化 JSON** 回报结论。

## 它解决的问题

网页发布流程里有大量长等待：视频上传要几十秒到几分钟，发布结果要几秒到几十秒。
与此同时，页面会在任意时刻弹出终局性的坏消息——上传失败、服务异常、商品不支持推广。

**坏消息的到达时刻，和程序当前所处的等待点，是完全解耦的。**
只在固定位置检查消息的脚本，感知延迟等于「从消息到达，到下一次检查」的间隔——
而这段间隔里往往正阻塞在某个长等待上：

```
t=0s     开始上传
t=3s     页面弹出「上传失败」         ← 结局此刻已定
t=3s     程序正阻塞在某个元素等待上，看不到这条消息
...
t=303s   终于醒来，返回「上传超时」   ← 时间白白浪费，且错误码是错的
```

最后一行的错误码尤其要命：真实原因是「上传失败」，报出来却是「上传超时」，
调度方据此做出的重试决策也跟着错。

本项目采用 **事件竞速中止**：主流程与致命事件哨兵作为两个并发任务同时运行，
谁先出结论谁说了算。哨兵一旦命中致命提示，立即取消主流程——
无论它正卡在哪个等待上，都会在一个调度周期内退出。

这个差距是实测出来的，不是推演的。把哨兵改成「命中致命事件也不中止」之后
重跑同一组测试：

| | 单条耗时 | 返回的错误码 |
| --- | ---: | --- |
| 竞速中止（现状） | **0.45s** | `PRODUCT_NOT_SUPPORTED(22)` — 真实原因 |
| 哨兵失效（变异） | 37s | `UPLOAD_TIMEOUT(11)` — 兜底超时码 |

37 秒是测试里 40 秒预算的上限所致；生产配置下这个数字是上传等待窗口的
300 秒。详见 [测试策略](docs/testing.md)。

设计细节见 [架构设计](docs/architecture.md) 与 [ADR-0001](docs/adr/0001-async-race-abort.md)。

## 特性

- **全异步**：基于 asyncio 与 Playwright async API，所有等待都是可取消点。
- **失败即中止**：任何已确定的失败都立即返回，不占用剩余的等待窗口。
- **错误可分类**：每个错误码自带责任归属与「是否值得重试」，调度方无需解析日志。
- **改版友好**：页面选择器集中登记在一个文件，应对改版只改一处。
- **测试可信**：正反向配对覆盖，关键路径带耗时上界断言，
  并对核心测试做过变异验证以确认它们真的会红。
- **真实环境已验证**：立即发布与定时发布均在真实账号上端到端跑通，
  并到创作者后台核对了定时时间与可见性确实生效
  （见 [探针结论](docs/probe-results.md)）。

## 环境要求

| 项 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11 |
| Python | 3.12 及以上（开发环境为 3.14） |
| 浏览器 | 本机安装的 Google Chrome |
| 账号 | 目标账号需已在指定的用户数据目录中登录 |

## 快速开始

```bash
git clone https://github.com/dream-until-dawn/automatically_upload_douyin_videos.git
cd automatically_upload_douyin_videos
uv sync
```

集成测试需要一份 Chromium（生产运行用的是本机 Chrome，这一步只影响测试）：

```bash
uv run playwright install chromium
```

### 如果提示「'uv' 不是内部或外部命令」

用 `pip install uv` 装的 uv，其可执行文件位于 Python 的 Scripts 目录，
而那个目录默认不在 PATH 中。三种办法任选其一，**本文后续所有 `uv xxx` 命令都适用**：

**① 用 `python -m uv` 代替 `uv`**（无需改环境，推荐）

```bash
python -m uv sync
python -m uv run pytest
```

**② 依赖装好后，直接用虚拟环境里的解释器**（连 uv 都不需要）

```bash
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe scripts\smoke_real.py config.json
```

**③ 把 uv 所在目录加入 PATH**

先查出它在哪：

```bash
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

把输出的目录加进系统环境变量 PATH，重开终端后 `uv` 即可直接使用。

## 使用

### 发布视频

配置以单个命令行参数传入，支持 Base64 编码的 JSON（推荐，可规避 Windows
命令行的转义问题）或裸 JSON 字符串：

```bash
uv run python -m douyin_publisher publish "<Base64 编码的配置>"
```

配置字段：

```json
{
  "execPath": "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "userDataDir": "D:/workspace/chrome_profiles/abc123",
  "taskId": "1",
  "douyinId": "1",
  "videoPath": "C:/videos/demo.mp4",
  "title": "夏日穿搭分享",
  "desc": "夏日穿搭,清凉一夏,好物分享",
  "cartUrl": "https://haohuo.jinritemai.com/...",
  "cartTitel": "点击下方",
  "publishTimeMode": "立即发布",
  "publishTime": "24",
  "whoCanSee": "仅自己可见",
  "savePermission": "不允许",
  "selfDeclaration": "无需添加自主声明"
}
```

字段含义与取值见 [CLI 协议](docs/cli-protocol.md)。

### 清理进程

```bash
uv run python -m douyin_publisher close-chrome "D:/workspace/chrome_profiles/abc123"
uv run python -m douyin_publisher close-jianying
```

清理浏览器时只会终止 **命令行中带有目标 `--user-data-dir` 的进程**，
不会按进程名批量杀 Chrome——你自己开着的浏览器窗口不受影响。

### 输出

| 通道 | 内容 |
| --- | --- |
| stderr | 人类可读的中文过程日志 |
| stdout | 有且仅有一行 JSON 结果 |

```json
{"schema":1,"ok":false,"code":21,"name":"CART_LIMIT_REACHED","category":"product","retryable":false,"stage":"cart","message":"无法添加购物车（已达挂车上限）","taskId":"1","douyinId":"1","elapsedMs":8423}
```

调度方读 stdout 最后一行即可拿到完整结论，无需对日志做正则匹配。
`retryable` 字段直接回答「这次失败值不值得重试」。

## 错误码速查

| 码 | 含义 | 可重试 |
| ---: | --- | :---: |
| 0 | 发布成功 | — |
| 3 / 6 / 18 / 19 | 配置或参数问题 | ❌ |
| 4 / 5 / 7 / 8 | 本机环境问题 | ❌ |
| 9 | 账号未登录 | ❌ |
| 10 / 11 / 12 / 13 / 17 | 平台或网络问题 | ✅ |
| 14 / 15 / 16 / 20 / 24 | 页面结构与预期不符 | ❌ |
| 21 / 22 / 23 | 商品自身问题 | ❌ |
| 88 | 程序内部异常 | ❌ |

完整表格见 [错误码契约](docs/error-codes.md)。

## 开发

```bash
uv run pytest                      # 全部测试
uv run pytest -m "not integration" # 只跑单元测试（秒级）
uv run pytest -m integration       # 只跑集成测试（需浏览器）
```

集成测试驱动的是 `tests/fixtures/pages/` 下的本地模拟页，
不连真实抖音——线上页面不可控，且会产生真实的发布行为。
模拟页支持按环节注入失败场景，可在毫秒级复现各类异常。

### 真实环境冒烟

模拟页能保证「生产代码与我们对页面结构的假设一致」，
但保证不了「这个假设与真实抖音一致」。页面改版不会让任何一条测试变红，
因此需要一个连真实账号的脚本来回答那个问题：

```bash
copy config.example.json config.json   # 按实际情况填写，config.json 不入库

uv run python scripts/smoke_real.py config.json            # 探测：只检查选择器，零副作用
uv run python scripts/smoke_real.py config.json --dry-run  # 演练：执行到点击发布前停住
```

若 `uv` 不可用，等价写法（见上文「如果提示 'uv' 不是内部或外部命令」）：

```bash
.venv\Scripts\python.exe scripts\smoke_real.py config.json
```

两种模式 **都不会发布**。发布步骤被从执行序列里排除掉，
而不是「跑到那里再判断要不要点」——后者留有意外发布的可能。
这个性质由 `tests/unit/test_dry_run_steps.py` 强制校验。

测试理念见 [测试策略](docs/testing.md)，其中详述了防「假绿」的几条机制。

## 文档

| 文档 | 内容 |
| --- | --- |
| [架构设计](docs/architecture.md) | 分层、目录结构、核心机制 |
| [CLI 协议](docs/cli-protocol.md) | 调用方式、配置字段、输出格式 |
| [错误码契约](docs/error-codes.md) | 完整错误码表与责任分类 |
| [测试策略](docs/testing.md) | 分层测试与防假绿机制 |
| [分期计划](docs/roadmap.md) | 里程碑与验收标准 |
| [探针结论](docs/probe-results.md) | 动工前的可行性验证记录 |
| [ADR](docs/adr/) | 架构决策记录 |

## 声明

本项目仅供学习与自用。使用者需自行遵守目标平台的服务条款，
并对发布内容承担全部责任。

## 许可

[MIT](LICENSE)
