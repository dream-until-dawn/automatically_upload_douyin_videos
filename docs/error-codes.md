# 错误码契约

> 本文是本项目与上游调度方之间的 **正式契约**。
> 退出码一经发布不得改变含义；新增错误只能追加新号码，不得复用旧号码。
>
> 想知道**遇到某个错误码该怎么办**，看
> [接入指南 · 按错误码处置](./getting-started.md#按错误码处置)。

## 1. 契约总则

1. 进程退出码是上游判断结果的 **首要依据**，`0` 表示发布成功，非 `0` 一律为失败。
2. 每个错误码都携带一个固定的 **可重试标记**，上游据此决定是否重新调度，
   **不需要、也不应该** 解析日志文本来推断。
3. 退出码的完整元信息同时会输出在 stdout 最后一行的 JSON 结果中（见 [CLI 协议](./cli-protocol.md)）。

## 2. 责任分类

| 分类 | 含义 | 上游建议动作 |
| --- | --- | --- |
| `config` | 传入的任务配置本身有问题 | 修正配置，不要重试 |
| `environment` | 本机环境问题（浏览器缺失、目录无权限） | 检查环境，不要重试 |
| `user` | 账号态问题（未登录） | 提示用户处理，不要重试 |
| `platform` | 抖音服务端或网络问题 | **可重试** |
| `page` | 页面结构与预期不符（多半是改版） | 不要重试，需升级本程序 |
| `product` | 商品自身问题（下架、不支持推广、挂车上限） | 不要重试，需更换商品 |
| `internal` | 本程序自身未预料的缺陷 | 不要重试，需修复程序 |

## 3. 错误码总表

| 码 | 名称 | 分类 | 可重试 | 含义 |
| ---: | --- | --- | :---: | --- |
| 0 | `SUCCESS` | — | — | 发布成功 |
| 3 | `CONFIG_INVALID` | config | ❌ | 配置字段校验未通过（必填缺失、取值非法） |
| 4 | `CHROME_NOT_FOUND` | environment | ❌ | 浏览器可执行文件不存在或无访问权限 |
| 5 | `USER_DATA_DIR_INVALID` | environment | ❌ | 用户数据目录不存在、非目录或为空 |
| 6 | `VIDEO_NOT_FOUND` | config | ❌ | 待发布视频文件不存在或无访问权限 |
| 7 | `PATH_RESOLVE_FAILED` | environment | ❌ | 路径规范化失败 |
| 8 | `BROWSER_LAUNCH_FAILED` | environment | ❌ | 浏览器启动失败 |
| 9 | `NOT_LOGGED_IN` | user | ❌ | 账号未登录（无法进入创作者发布页） |
| 10 | `UPLOAD_FAILED` | platform | ✅ | 页面明确提示视频上传失败 |
| 11 | `UPLOAD_TIMEOUT` | platform | ✅ | 视频上传在限定时间内未出结论 |
| 12 | `PIPELINE_TIMEOUT` | platform | ✅ | 整个发布流程超出总时长上限 |
| 13 | `SERVICE_ERROR` | platform | ✅ | 页面提示服务异常 |
| 14 | `CART_ATTACH_FAILED` | page | ❌ | 挂车流程失败（非上限、非下架的一般性失败） |
| 15 | `DECLARATION_FAILED` | page | ❌ | 自主声明配置失败 |
| 16 | `COVER_FAILED` | page | ❌ | 封面配置失败 |
| 17 | `PUBLISH_FAILED` | platform | ✅ | 点击发布后失败 |
| 18 | `ARGS_MISSING` | config | ❌ | 命令行参数缺失 |
| 19 | `CONFIG_PARSE_FAILED` | config | ❌ | 配置无法解析（Base64 / JSON 格式错误） |
| 20 | `PUBLISH_SETTING_FAILED` | page | ❌ | 发布设置（发布时间 / 可见范围 / 保存权限）配置失败 |
| 21 | `CART_LIMIT_REACHED` | product | ❌ | 无法添加购物车（达到挂车上限） |
| 22 | `PRODUCT_NOT_SUPPORTED` | product | ❌ | 该商品暂不支持在直播 / 短视频推广 |
| 23 | `PRODUCT_NOT_FOUND` | product | ❌ | 未搜索到对应商品（多为商品已下架） |
| 24 | `TITLE_INPUT_FAILED` | page | ❌ | 标题 / 话题标签填写失败 |
| 88 | `UNEXPECTED` | internal | ❌ | 未捕获的程序内部异常 |

### 3.1 关于 24

标题填写此前没有独立码，失败时会落入 `88`，使得「页面改版」被误报成「程序缺陷」。
本项目为其分配独立码 `24`，归类为 `page`，让上游能正确区分责任方。
该号码此前未被占用，新增不影响既有上游逻辑。

### 3.2 保留号码

`1`、`2` 为通用/系统保留，本程序不主动使用。
非 0-255 范围的码在 Windows 上行为不确定，因此所有错误码限制在 `3..254` 内。

## 4. 实现约束

- 错误码集中定义在 `src/douyin_publisher/core/errors.py`，是唯一真相源。
- 业务代码 **不得** 直接 `sys.exit(数字)`，只能抛出携带 `ErrorCode` 的 `PublishError`。
- 退出码的产生只有一处出口：CLI 层。这保证了清理逻辑与 JSON 输出一定会执行。
- 本表与代码枚举之间有一致性测试（见 [测试策略](./testing.md)），文档与实现不允许漂移。
