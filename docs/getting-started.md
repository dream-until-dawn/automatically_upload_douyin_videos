# 接入指南

从零跑通第一次发布，再接到你自己的程序里。每一步都给出「怎么确认这步成功了」。

预计耗时：首次约 15 分钟，其中大部分是等依赖下载和登录抖音。

---

## 0. 需要准备什么

| 项 | 要求 | 怎么确认 |
| --- | --- | --- |
| 操作系统 | Windows 10 / 11 | — |
| Python | 3.12+ | `python --version` |
| Chrome | 本机已安装 | 能正常打开浏览器 |
| 抖音账号 | 一个可发布的账号 | 能手动登录创作者中心 |
| 视频文件 | 一个 mp4 | — |

> 用 **打包好的 exe** 的话，Python 这一项可以跳过——exe 自带运行时。
> 见本文 [附录 A](#附录-a只用-exe-不装-python)。

---

## 1. 装依赖

```bash
git clone https://github.com/dream-until-dawn/automatically_upload_douyin_videos.git
cd automatically_upload_douyin_videos
uv sync
```

**如果提示「'uv' 不是内部或外部命令」**：用 `pip install uv` 装的 uv，
其可执行文件在 Python 的 Scripts 目录下，而该目录默认不在 PATH 中。
三种办法任选其一，本文后续所有 `uv xxx` 都适用：

```bash
python -m uv sync                       # ① 用 python -m uv 代替 uv（推荐）
.venv\Scripts\python.exe -m pytest      # ② 装好后直接用虚拟环境里的解释器
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"   # ③ 查出目录加进 PATH
```

**确认成功**：

```bash
uv run douyin-publisher selfcheck
```

看到 `"ok":true` 即依赖齐备。这条命令会真的把浏览器驱动拉起来一次，
不只是检查能不能 import。

---

## 2. 准备一个已登录的 Chrome 画像

这是最容易卡住的一步，也是最关键的一步。

**本程序不做登录**，它复用「某个 Chrome 用户数据目录里已有的登录态」。
所以你得先有这样一个目录。

```bash
uv run python scripts/prepare_profile.py C:/douyin_profiles/account_a
```

脚本会用这个目录启动一个 Chrome，你在弹出的窗口里手动登录抖音，
登录成功后脚本自动检测到并退出，然后打印出可以直接粘贴的配置片段。

> 脚本不碰你的账号密码，登录全程由你自己在浏览器里完成。

**两个要紧的提醒**：

- **不要用你日常的 Chrome 画像**（比如 `C:\Users\你\AppData\Local\Google\Chrome\User Data`）。
  程序在启动浏览器前会**终止所有占用该目录的进程**——指向日常画像就等于
  把你正开着的浏览器关掉。用独立目录能把影响圈在可控范围内。
- **准备好之后别再手动打开这个画像去浏览**，原因同上。

**确认成功**：脚本最后打印出 `"userDataDir": "..."` 那一行，说明登录态已保存。
重新运行脚本会直接提示「该画像已处于登录状态」。

---

## 3. 写配置

```bash
copy config.example.json config.json
```

必填只有 5 项：

```json
{
  "execPath": "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "userDataDir": "C:/douyin_profiles/account_a",
  "taskId": "1",
  "douyinId": "1",
  "videoPath": "C:/videos/demo.mp4"
}
```

其余都可以先不管。几个常用的：

| 字段 | 说明 |
| --- | --- |
| `cartUrl` | 商品链接。**留空就是发纯内容视频**，挂车步骤自动跳过 |
| `title` / `desc` | 标题、话题标签（英文逗号分隔，程序自动加 `#`） |
| `whoCanSee` | **首次测试建议填「仅自己可见」** |
| `publishTimeMode` | `立即发布` 或 `定时发布` |

完整字段见 [CLI 协议](./cli-protocol.md#32-字段定义)。

---

## 4. 先探测，再发布

直接跑真实发布之前，**先用零副作用的探测确认环境没问题**：

```bash
uv run python scripts/smoke_real.py config.json
```

它只打开发布页、逐个检查页面元素能否找到，**不做任何点击、输入或上传**。

**确认成功**：输出「已探测的选择器均能命中」。

若某项「未命中」，说明抖音页面改版了，需要更新
`src/douyin_publisher/browser/selectors.py` 中对应的一行。

> 「发布设置单选项」会显示为**跳过**，这是正常的——该区域要等视频开始上传后
> 才渲染，探测模式看不到它。

### 想再稳一点：演练模式

```bash
uv run python scripts/smoke_real.py config.json --dry-run
```

真实执行上传、填标题、挂车、封面等全部步骤，**但不会点发布**，
账号下会留一条未发布的草稿。发布动作是被**从执行序列里排除**的，
不是「跑到那里再判断」，所以不存在意外发布的可能。

---

## 5. 第一次真实发布

确认 `whoCanSee` 是「仅自己可见」之后：

```bash
uv run python -m douyin_publisher publish "<Base64 编码的配置>"
```

配置要转成 Base64（可规避 Windows 命令行对引号和中文的转义问题）。
PowerShell 下可以这样生成：

```powershell
$json = Get-Content config.json -Raw -Encoding UTF8
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
& .venv\Scripts\python.exe -m douyin_publisher publish $b64
```

**确认成功**：进程退出码为 `0`，且 stdout 最后一行是
`{"schema":1,"type":"result","ok":true,"code":0,...}`。

到创作者后台看一眼，作品应该已经在列表里了。

---

## 6. 接到你自己的程序里

### 调用方式

```
douyin_publisher.exe publish <Base64 编码的配置 JSON>
```

三条约定：

1. **进程退出码**就是结果，`0` 成功，非 `0` 是错误码；
2. **stdout 最后一行**是完整的 JSON 结果；
3. **stderr** 是人类可读的过程日志，可以原样落盘留档。

> 按「读 stdout 最后一行」解析，而不是「整段 json.loads」。
> 默认情况下 stdout 确实只有一行，但一旦开启进度回报就会变成多行，
> 按行读的写法两种情况都成立。

### Dart / Flutter 示例

```dart
import 'dart:convert';
import 'dart:io';

Future<Map<String, dynamic>> publish(Map<String, dynamic> config) async {
  final payload = base64.encode(utf8.encode(jsonEncode(config)));

  final result = await Process.run(
    r'C:\tools\douyin_publisher.exe',
    ['publish', payload],
    stdoutEncoding: utf8,
    stderrEncoding: utf8,
  );

  // 过程日志留档，排查时很有用
  await File('logs/${config["taskId"]}.log')
      .writeAsString(result.stderr as String);

  // 取 stdout 最后一个非空行
  final lines = (result.stdout as String)
      .split('\n')
      .map((l) => l.trim())
      .where((l) => l.isNotEmpty)
      .toList();

  return jsonDecode(lines.last) as Map<String, dynamic>;
}
```

### 根据结果决定下一步

```dart
final r = await publish(config);

if (r['ok'] == true) {
  markSuccess(config['taskId']);
} else if (r['retryable'] == true) {
  // 平台或网络问题，值得重试
  scheduleRetry(config['taskId']);
} else {
  // 重试多少次都一样，需要人介入
  markFailed(config['taskId'], r['code'], r['message']);
  if (r['screenshot'] != null) {
    attachScreenshot(r['screenshot'] as String);  // 失败现场
  }
}
```

**关键：用 `retryable` 字段判断要不要重试，不要自己解析日志文本。**
每个错误码的这个标记都是固定的，见 [错误码契约](./error-codes.md)。

### 结果 JSON 字段

```json
{
  "schema": 1,
  "type": "result",
  "ok": false,
  "code": 21,
  "name": "CART_LIMIT_REACHED",
  "category": "product",
  "retryable": false,
  "stage": "cart",
  "message": "无法添加购物车（已达挂车上限）",
  "taskId": "1",
  "douyinId": "1",
  "elapsedMs": 8423,
  "screenshot": "C:/Temp/douyin_publisher_shots/20260920-173144_1_cart.png"
}
```

| 字段 | 用途 |
| --- | --- |
| `code` | 与进程退出码一致，作为主判据 |
| `retryable` | **决定要不要重试** |
| `category` | 责任方：`config`/`environment`/`user`/`platform`/`page`/`product`/`internal` |
| `stage` | 失败发生在哪一步 |
| `screenshot` | 失败现场截图路径，失败排查时先看这个 |
| `taskId`/`douyinId` | 原样回传，便于对账 |

---

## 7. 按需开启的能力

全部可选，不配置时行为不变。

| 想要 | 配置 |
| --- | --- |
| 视频大、网速慢，上传老超时 | `"timeouts": {"upload": 900}` |
| 批量调度，日志太吵 | `"logLevel": "quiet"` |
| 实时进度与「是否卡死」的判断 | `"progress": {"enabled": true, "heartbeat": 15}` |
| 失败截图存到指定目录 | `"screenshot": {"dir": "D:/logs/shots"}` |
| 走代理 | `"browserArgs": ["--proxy-server=http://127.0.0.1:8080"]` |
| 发纯内容视频 | `cartUrl` 留空即可 |

开启进度回报后，stdout 会变成多行：

```
{"schema":1,"type":"progress","kind":"step","stage":"upload","step":1,"total":9,...}
{"schema":1,"type":"progress","kind":"heartbeat","stage":"await_upload","step":6,"total":9,...}
{"schema":1,"type":"result","ok":true,...}
```

`kind=heartbeat` 是长等待期间的存活信号——**用它区分「正在上传」和「已经卡死」**。
上传一个 276 MB 的视频要一分多钟，没有心跳的话，这段时间和卡死看起来一模一样。

详见 [CLI 协议 3.3~3.9 节](./cli-protocol.md)。

---

## 8. 出问题了怎么办

### 先看这三样

1. **错误码的 `category`** —— 告诉你该找谁：改配置、改环境、还是换商品
2. **`retryable`** —— 告诉你重试有没有意义
3. **`screenshot`** —— 失败时页面长什么样，页面改版类问题看一眼就清楚

### 按错误码处置

| 码 | 含义 | 怎么办 |
| ---: | --- | --- |
| 3 / 19 | 配置有问题 | 看 `message`，它会指出具体哪个字段 |
| 4 | 找不到 Chrome | 检查 `execPath` |
| 5 | 用户数据目录无效 | 目录必须存在且非空，用 `prepare_profile.py` 重新准备 |
| 6 | 视频文件不存在 | 检查 `videoPath` |
| 8 | 浏览器启动失败 | 多半是目录被占用，确认没有手动开着这个画像 |
| 9 | 未登录 | 用 `prepare_profile.py` 重新登录该画像 |
| 10 / 11 / 12 / 13 / 17 | 平台或网络问题 | **可以重试**；上传老超时就调大 `timeouts.upload` |
| 14 / 15 / 16 / 20 / 24 | 页面结构与预期不符 | 多半是抖音改版，跑一次探测确认，然后更新选择器 |
| 21 | 挂车已达上限 | 先在后台下掉一些商品 |
| 22 / 23 | 商品不支持推广 / 已下架 | 换一个商品 |
| 88 | 程序内部异常 | 带上 stderr 日志和截图反馈 |

### 怀疑页面改版了

```bash
uv run python scripts/smoke_real.py config.json
```

零副作用。哪一项「未命中」，就改
`src/douyin_publisher/browser/selectors.py` 里对应的那一行，
同时同步 `tests/fixtures/pages/publish_page.html`——
有一条测试专门盯着这两处不许脱节。

### 常见疑问

**Q：可以多个账号同时跑吗？**
可以，但**每个账号必须用各自独立的画像目录**。共用一个目录会互相清场。

**Q：程序会关掉我自己的浏览器吗？**
只会终止「命令行里带有目标 `--user-data-dir` 的进程」。
只要你的 `userDataDir` 指向专用目录，日常浏览器不受影响。

**Q：失败了会留下草稿吗？**
可能会。失败若发生在视频投递之后，账号下会留一条未发布的草稿，
可到创作者后台删除。

**Q：一次任务要多久？**
主要取决于上传。实测 276 MB 的视频约 66 秒，整个流程约 75 秒。

---

## 附录 A：只用 exe 不装 Python

```bash
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

产出 `dist/douyin_publisher.exe`（约 52 MB，自带运行时）。
构建过程会自动做自检与命令行契约校验。

部署到目标机器后先验证一次：

```bash
douyin_publisher.exe selfcheck
```

> 目标机器仍需安装 Chrome，并准备好已登录的画像目录。

## 附录 B：其他子命令

```bash
douyin_publisher.exe selfcheck                      # 自检运行时依赖
douyin_publisher.exe close-chrome "<画像目录>"       # 清理占用该目录的浏览器
douyin_publisher.exe close-jianying                 # 清理剪辑软件进程
```

---

## 下一步读什么

| 想了解 | 看 |
| --- | --- |
| 完整的配置字段与输出协议 | [CLI 协议](./cli-protocol.md) |
| 每个错误码的确切含义 | [错误码契约](./error-codes.md) |
| 它内部是怎么工作的 | [架构设计](./architecture.md) |
| 为什么这样设计 | [ADR](./adr/) |
