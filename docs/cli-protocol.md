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
| `cartUrl` | string | ✅ | 商品（购物车）链接 |
| `title` | string | ❌ | 视频标题 |
| `desc` | string | ❌ | 话题标签，英文逗号分隔，程序自动加 `#` |
| `cartTitel` | string | ❌ | 商品短标题；留空则自动截取商品原标题前 10 字 |
| `publishTimeMode` | string | ❌ | `立即发布` 或 `定时发布` |
| `publishTime` | string | ❌ | 定时发布时的延后小时数，有效区间 `1..300`，默认 `80` |
| `whoCanSee` | string | ❌ | `公开`、`好友可见`、`仅自己可见` |
| `savePermission` | string | ❌ | `允许` 或 `不允许` |
| `selfDeclaration` | string | ❌ | 自主声明选项，默认 `无需添加自主声明` |
| `headless` | bool | ❌ | 是否无头运行，默认 `false` |

> `cartTitel` 沿用上游既有拼写，不做更名，以保证对接零改动。

### 3.3 示例

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
  "elapsedMs": 8423
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

