# 命令行调用协议

> 上游调度方与本程序之间的调用约定。配套阅读：[错误码契约](./error-codes.md)。

## 1. 调用形式

```
douyin_publisher.exe <子命令> <参数>
```

开发期等价形式：

```bash
python -m douyin_publisher <子命令> <参数>
```

## 2. 子命令

| 子命令 | 参数 | 说明 |
| --- | --- | --- |
| `publish` | 任务配置（Base64 或裸 JSON） | 执行完整的视频发布流程 |
| `close-chrome` | 用户数据目录路径 | 清理占用该目录的浏览器进程 |
| `close-jianying` | 无 | 清理剪辑软件进程 |
| `selfcheck` | 无 | 自检运行时依赖是否齐备，用于部署后快速验证 |

> 兼容性：`closeChrome` / `closeJianying` 这两种驼峰写法同样被接受，
> 以便上游无需改动即可对接。新接入方请使用短横线写法。

## 3. 配置参数

### 3.1 传参方式

配置以 **单个命令行参数** 传入，支持两种编码，程序自动识别：

1. **Base64 编码的 UTF-8 JSON**（推荐）——可彻底规避 Windows 命令行对引号、
   中文、特殊字符的转义问题。
2. **裸 JSON 字符串** —— 仅建议在手工调试时使用。

传入前程序会剥除外层可能被 Shell 附加的引号与空白字符。

### 3.2 字段定义

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `execPath` | string | ✅ | Chrome 可执行文件绝对路径 |
| `userDataDir` | string | ✅ | Chrome 用户数据目录（承载登录态） |
| `taskId` | string | ✅ | 上游任务 ID，仅用于日志与结果回传 |
| `douyinId` | string | ✅ | 抖音账号标识，仅用于日志与结果回传 |
| `videoPath` | string | ✅ | 待发布视频的本地绝对路径 |
| `cartUrl` | string | ❌ | 商品链接。**留空表示发布纯内容视频**，挂车步骤会被自动跳过 |
| `title` | string | ❌ | 视频标题 |
| `desc` | string | ❌ | 话题标签，英文逗号分隔，程序自动加 `#` |
| `cartTitel` | string | ❌ | 商品短标题；留空则自动截取商品原标题前 10 字 |
| `publishTimeMode` | string | ❌ | `立即发布` 或 `定时发布` |
| `publishTime` | string | ❌ | 定时发布时的延后小时数，有效区间 `1..300`，默认 `80` |
| `whoCanSee` | string | ❌ | `公开`、`好友可见`、`仅自己可见` |
| `savePermission` | string | ❌ | `允许` 或 `不允许` |
| `selfDeclaration` | string | ❌ | 自主声明选项，默认 `无需添加自主声明` |
| `headless` | bool | ❌ | 是否无头运行，默认 `false` |
| `timeouts` | object | ❌ | 各环节等待上限，见 3.4 |
| `browserArgs` | string[] | ❌ | 追加的 Chrome 启动参数，见 3.5 |
| `logLevel` | string | ❌ | `quiet` / `normal`（默认）/ `debug` |
| `skip` | string[] | ❌ | 要跳过的阶段，见 3.7 |
| `screenshot` | object | ❌ | 失败现场截图，见 3.8 |

> `cartTitel` 沿用上游既有拼写，不做更名，以保证对接零改动。

### 3.3 运行时选项总则

这四组字段全部可选，**缺省时行为与不配置完全一致**，上游无需改动即可继续使用。

设计上只暴露「调用方有判断依据去调」的参数，其余保持为内部实现细节
（决策过程见 [ADR-0003](./adr/0003-runtime-options.md)）。
比如「等待视频上传完成」调用方能算出合理值，而「点击后停顿多少毫秒等渲染」
调用方无从判断——后者暴露出去只会变成没人知道怎么填的字段，
填错还会造成时序相关的间歇性失败。

### 3.4 timeouts：各环节等待上限（秒）

```json
"timeouts": {
  "total": 600,
  "navigate": 60,
  "pageReady": 30,
  "element": 15,
  "cartModal": 15,
  "upload": 300,
  "coverFrame": 60,
  "publish": 120
}
```

| 字段 | 默认 | 含义与调整依据 |
| --- | ---: | --- |
| `total` | 600 | 整个流程的上限。调度方要控制单任务占用的时间片时调整。 |
| `navigate` | 60 | 打开发布页。网络慢时调大。 |
| `pageReady` | 30 | 等待页面关键组件就绪。机器性能差时调大。 |
| `element` | 15 | 通用元素等待。页面整体卡顿时调大。 |
| `cartModal` | 15 | 等待商品编辑弹窗。商品信息查询慢时调大。 |
| `upload` | 300 | 等待视频上传完成。**最常需要调整**：视频大、上行带宽窄时调大。 |
| `coverFrame` | 60 | 等待封面候选帧抽取。长视频时调大。 |
| `publish` | 120 | 等待发布结果。平台审核前置校验慢时调大。 |

可以只写想改的那几项，未写的保持默认。取值必须为正数，否则报 `19`。

各步骤从共享的总预算中申请时间，因此即便某一项配得比 `total` 还大，
也不会突破总上限——超出时会先触发整体超时（`12`）。

### 3.5 browserArgs：追加的浏览器启动参数

```json
"browserArgs": ["--proxy-server=http://127.0.0.1:8080", "--window-size=1920,1080"]
```

参数**追加**在程序内置参数之后，不替换它们。

以下三类会被拒绝并返回 `3`（而非静默忽略——配置没生效却不报错，
会让人对着一个看起来已配好的选项反复调试）：

| 被拒绝的 | 原因 |
| --- | --- |
| `--user-data-dir` | 画像目录由 `userDataDir` 指定。从这里改会让启动前的进程清理去清错目录，导致浏览器因目录被占用而启动失败，且报出的原因与真正的起因毫不相干。 |
| `--remote-debugging-port` / `--remote-debugging-pipe` | 程序靠它与浏览器通信，被覆盖会直接失去控制。 |
| 不以 `--` 开头的项 | 大概率是拼装错误。 |

### 3.6 纯内容视频

`cartUrl` 留空（或只填空白）即表示发布不带商品的视频，挂车步骤自动跳过。
日志中会明确打出一行：

```
[流程] 未配置商品链接，跳过挂车（按纯内容视频处理）
```

这条日志是刻意保留的：自动行为不该静默发生。
若本想挂车却漏填了链接，任务会「成功」但商品没挂上，
而这行日志是事后唯一能发现此事的线索。

效果等同于 `"skip": ["cart"]`，两者同时配置也不冲突。

### 3.7 skip：跳过非必要步骤

```json
"skip": ["cart", "cover"]
```

可跳过：`title`、`setting`、`declaration`、`cart`、`cover`

不可跳过：`upload`、`await_upload`、`publish`、`await_publish` —— 这四步构成
「发布一个视频」的最小定义，配置它们会返回 `3`。

典型用途：发布不带商品的纯内容视频时 `"skip": ["cart"]`。

> 若需要「跑完流程但不发布」，那是冒烟脚本演练模式的职责，
> 不要试图用 `skip` 去掉发布步骤。

### 3.8 screenshot：失败现场截图

```json
"screenshot": { "onFailure": true, "dir": "D:/logs/shots" }
```

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `onFailure` | `true` | 失败时是否自动截图 |
| `dir` | `""` | 存放目录，留空则用系统临时目录下的 `douyin_publisher_shots` |

**默认开启**的理由：这类信息的价值几乎全在事后。
等出了问题才想起来打开开关，那一次的现场已经没有了。

截图路径会出现在结果 JSON 的 `screenshot` 字段中（成功时为 `null`）。
文件名形如 `20260920-173144_task-42_cover.png`，含时间、任务 ID 与失败阶段，
不必打开就能定位是哪个任务卡在哪一步。

三点说明：

- 截图**不会影响结果**。目录不可写、页面已关闭、磁盘满等情况一律降级为警告——
  此时已经有一个明确的失败原因了，不该让截图问题把它盖掉。
- 只在失败时截，成功路径不产生文件。
- 截图可能包含账号信息，注意存放目录的权限与清理策略。

### 3.9 示例

```json
{
  "execPath": "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "userDataDir": "D:/workspace/chrome_profiles/abc123",
  "taskId": "1",
  "douyinId": "1",
  "videoPath": "C:/videos/demo.mp4",
  "title": "夏日穿搭分享",
  "desc": "夏日穿搭,清凉一夏,好物分享",
  "cartUrl": "https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?id=123456",
  "cartTitel": "点击下方",
  "publishTimeMode": "立即发布",
  "publishTime": "24",
  "whoCanSee": "仅自己可见",
  "savePermission": "不允许",
  "selfDeclaration": "无需添加自主声明"
}
```

## 4. 输出协议

### 4.1 双通道分流

| 通道 | 内容 |
| --- | --- |
| **stderr** | 人类可读的中文过程日志，带阶段标记。上游可原样落盘留档。 |
| **stdout** | **有且仅有一行**：结束时输出的单行 JSON 结果。 |

上游只需读取 stdout 的最后一行即可得到完整结论，无需对日志做正则匹配。

### 4.2 结果 JSON 结构

```json
{
  "schema": 1,
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

| 字段 | 说明 |
| --- | --- |
| `schema` | 结果结构版本号，当前为 `1`。结构发生破坏性变化时递增。 |
| `ok` | 是否成功，等价于 `code == 0` |
| `code` | 退出码，与进程退出码完全一致 |
| `name` | 错误码常量名 |
| `category` | 责任分类，见错误码契约 |
| `retryable` | 上游是否应当重试 |
| `stage` | 失败发生的流程阶段；成功时为 `done` |
| `message` | 中文描述 |
| `taskId` / `douyinId` | 原样回传，便于上游对账 |
| `elapsedMs` | 本次执行耗时（毫秒） |
| `screenshot` | 失败现场截图的路径；成功或未启用时为 `null` |

### 4.3 兼容性承诺

- 进程退出码语义与既有约定完全一致，上游即使完全忽略 stdout 也能正常工作。
- JSON 结果为 **增量能力**，只增字段不删字段；破坏性变化会递增 `schema`。

## 5. 阶段标识

`stage` 的取值与流程步骤一一对应：

| stage | 含义 |
| --- | --- |
| `config` | 参数解析与校验 |
| `cleanup` | 启动前进程清场 |
| `launch` | 浏览器启动 |
| `navigate` | 打开发布页 |
| `upload` | 视频上传 |
| `title` | 标题与话题标签 |
| `setting` | 发布设置 |
| `declaration` | 自主声明 |
| `cart` | 挂载购物车 |
| `await_upload` | 等待上传完成 |
| `cover` | 设置封面 |
| `publish` | 点击发布 |
| `await_publish` | 等待发布结果 |
| `done` | 全部完成 |

## 6. 调用示例

```bash
python -m douyin_publisher publish "eyJleGVjUGF0aCI6IC4uLn0="
```

```bash
python -m douyin_publisher close-chrome "D:/workspace/chrome_profiles/abc123"
```

```bash
douyin_publisher.exe selfcheck
```

自检会 **真正启动** Playwright 驱动，而不只是做 import 检查——
打包产物最典型的故障是「能构建、不能跑」，import 成功并不代表驱动进程拉得起来。

