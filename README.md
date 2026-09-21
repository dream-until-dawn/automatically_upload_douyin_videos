# 抖音视频自动发布器

驱动本机 Chrome 完成抖音创作者中心的视频发布全流程：
上传视频 → 填写标题与话题 → 配置发布设置 → 自主声明 → 挂载商品 → 设置封面 → 发布。

以命令行子进程的形式被调度方拉起，执行一次任务后退出，
通过 **进程退出码 + 结构化 JSON** 回报结论。

```bash
douyin_publisher.exe publish <Base64 编码的配置>
```

```json
{"schema":1,"type":"result","ok":true,"code":0,"stage":"done","elapsedMs":75692,...}
```

---

## 快速开始

完整步骤见 **[接入指南](docs/getting-started.md)**，三步概览：

```bash
# 1. 装依赖（若提示 uv 不是内部命令，见接入指南第 1 节）
uv sync

# 2. 准备一个已登录抖音的 Chrome 画像（会弹出浏览器让你手动登录）
uv run python scripts/prepare_profile.py C:/douyin_profiles/account_a

# 3. 写配置后，先跑零副作用的探测确认环境没问题
copy config.example.json config.json
uv run python scripts/smoke_real.py config.json
```

确认无误后即可发布。**首次测试建议把 `whoCanSee` 设为「仅自己可见」。**

> 第 2 步是最容易被忽略的：本程序**不做登录**，它复用某个 Chrome 用户数据目录里
> 已有的登录态。并且请使用独立画像目录，不要指向日常浏览器——
> 程序启动前会终止占用该目录的所有进程。

---

## 接到你的程序里

三条约定：**退出码即结果**、**stdout 最后一行是 JSON**、**stderr 是过程日志**。

```dart
final result = await Process.run(exe, ['publish', base64Config]);
final lines = (result.stdout as String).split('\n')
    .map((l) => l.trim()).where((l) => l.isNotEmpty).toList();
final r = jsonDecode(lines.last);

if (r['ok'] == true)            markSuccess();
else if (r['retryable'] == true) scheduleRetry();   // 平台/网络问题
else                             markFailed(r['code'], r['message']);
```

**用 `retryable` 字段决定要不要重试，不要解析日志文本。**
每个错误码的这个标记都是固定的契约。

完整示例与错误处置见 [接入指南](docs/getting-started.md#6-接到你自己的程序里)。

---

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
谁先出结论谁说了算。哨兵一旦命中致命提示，立即取消主流程。

这个差距是实测出来的，不是推演的。把哨兵改成「命中致命事件也不中止」之后
重跑同一组测试：

| | 单条耗时 | 返回的错误码 |
| --- | ---: | --- |
| 竞速中止（现状） | **0.45s** | `PRODUCT_NOT_SUPPORTED(22)` — 真实原因 |
| 哨兵失效（变异） | 37s | `UPLOAD_TIMEOUT(11)` — 兜底超时码 |

设计细节见 [架构设计](docs/architecture.md) 与 [ADR-0001](docs/adr/0001-async-race-abort.md)。

---

## 特性

- **全异步**：基于 asyncio 与 Playwright async API，所有等待都是可取消点。
- **失败即中止**：任何已确定的失败都立即返回，不占用剩余的等待窗口。
- **错误可分类**：每个错误码自带责任归属与「是否值得重试」，调度方无需解析日志。
- **改版友好**：页面选择器集中登记在一个文件，应对改版只改一处。
- **失败留现场**：失败时自动截图并在结果中给出路径。错误码只能告诉你哪一步挂了，
  截图能告诉你页面当时长什么样——页面改版的排查从「反复复现」变成「看一眼」。
- **可选的进度回报**：开启后按行输出进度与心跳，调度方既能展示进度，
  也能据此区分「正在上传」和「已经卡死」。默认关闭，不影响既有对接。
- **测试可信**：正反向配对覆盖，关键路径带耗时上界断言，
  并对核心测试做过变异验证以确认它们真的会红。
- **真实环境已验证**：立即发布与定时发布均在真实账号上端到端跑通，
  并到创作者后台核对了定时时间与可见性确实生效
  （见 [探针结论](docs/probe-results.md)）。

---

## 配置速览

必填只有 5 项，其余都可以先不管：

```json
{
  "execPath": "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "userDataDir": "C:/douyin_profiles/account_a",
  "taskId": "1",
  "douyinId": "1",
  "videoPath": "C:/videos/demo.mp4"
}
```

常用的可选项：

| 想要 | 配置 |
| --- | --- |
| 挂商品 | `"cartUrl": "https://haohuo.jinritemai.com/..."` |
| 发纯内容视频 | `cartUrl` 留空即可，挂车自动跳过 |
| 标题与话题 | `"title": "...", "desc": "标签一,标签二"` |
| 定时发布 | `"publishTimeMode": "定时发布", "publishTime": "24"` |
| 视频大、网速慢 | `"timeouts": {"upload": 900}` |
| 日志太吵 | `"logLevel": "quiet"` |
| 实时进度与心跳 | `"progress": {"enabled": true, "heartbeat": 15}` |
| 走代理 | `"browserArgs": ["--proxy-server=..."]` |

运行时选项全部可选，不配置时行为不变。
只暴露了调用方有判断依据去调的参数，理由见 [ADR-0003](docs/adr/0003-runtime-options.md)。

完整字段见 [CLI 协议](docs/cli-protocol.md)。

---

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

逐码的处置建议见 [接入指南](docs/getting-started.md#按错误码处置)，
完整契约见 [错误码契约](docs/error-codes.md)。

---

## 其他子命令

```bash
douyin_publisher.exe selfcheck                # 自检运行时依赖
douyin_publisher.exe close-chrome "<画像目录>" # 清理占用该目录的浏览器
douyin_publisher.exe close-jianying           # 清理剪辑软件进程
```

清理浏览器时只会终止 **命令行中带有目标 `--user-data-dir` 的进程**，
不会按进程名批量杀 Chrome——你自己开着的浏览器窗口不受影响。

---

## 打包

```bash
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

产出 `dist/douyin_publisher.exe`（约 52 MB，自带运行时，目标机器无需装 Python）。
构建过程会自动做自检与命令行契约校验。

---

## 开发

```bash
uv run pytest                      # 全部测试（串行约 10 分钟）
uv run pytest -n 8                 # 并行，本机实测约 3.5 分钟
uv run pytest -m "not integration" # 只跑单元测试（秒级）
```

集成测试驱动的是 `tests/fixtures/pages/` 下的本地模拟页，
不连真实抖音——线上页面不可控，且会产生真实的发布行为。

### 真实环境冒烟

模拟页能保证「生产代码与我们对页面结构的假设一致」，
但保证不了「这个假设与真实抖音一致」。页面改版不会让任何一条测试变红，
因此需要一个连真实账号的脚本来回答那个问题：

```bash
uv run python scripts/smoke_real.py config.json            # 探测：只检查选择器，零副作用
uv run python scripts/smoke_real.py config.json --dry-run  # 演练：执行到点击发布前停住
```

两种模式 **都不会发布**。发布步骤被从执行序列里排除掉，
而不是「跑到那里再判断要不要点」——后者留有意外发布的可能。

测试理念见 [测试策略](docs/testing.md)，其中详述了防「假绿」的几条机制。

---

## 文档

| 文档 | 内容 |
| --- | --- |
| **[接入指南](docs/getting-started.md)** | **从零跑通到接入上游，先看这个** |
| [CLI 协议](docs/cli-protocol.md) | 调用方式、配置字段、输出格式 |
| [错误码契约](docs/error-codes.md) | 完整错误码表与责任分类 |
| [架构设计](docs/architecture.md) | 分层、目录结构、核心机制 |
| [测试策略](docs/testing.md) | 分层测试与防假绿机制 |
| [探针结论](docs/probe-results.md) | 可行性验证与真实环境验证记录 |
| [分期计划](docs/roadmap.md) | 里程碑与验收标准 |
| [ADR](docs/adr/) | 架构决策记录 |

---

## 环境要求

| 项 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11 |
| Python | 3.12 及以上（用打包好的 exe 则不需要） |
| 浏览器 | 本机安装的 Google Chrome |
| 账号 | 目标账号需已在指定的用户数据目录中登录 |

---

## 声明

本项目仅供学习与自用。使用者需自行遵守目标平台的服务条款，
并对发布内容承担全部责任。

## 许可

[MIT](LICENSE)
