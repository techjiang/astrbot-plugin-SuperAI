# Studio 面板

SuperAI 在 AstrBot WebUI 里提供一个页面 **SuperAI Studio**
（`pages/studio/`，入口名 `studio`，定义在 `metadata.yaml`）。

打开方式：AstrBot WebUI → 插件 → SuperAI → SuperAI Studio。

## 页面区块

| 区块 | 数据来源 | 内容 |
| --- | --- | --- |
| 运行状态 | `GET /astrbot_plugin_superai/status` | 版本、能力开关（路由/记忆/联网/知识库/Agent/工作流/统计）、可用模型列表、档位→模型映射 |
| 模型健康度 | 同上（`health_detail`） | 每个 provider 的连续失败次数、是否健康、最近失败时间 |
| 用量统计 | `GET .../stats?days=7` | 请求数 / 失败数 / 输入输出与缓存 token / 平均耗时；按模型、按路由、按天的柱状图 |
| 已注册工具 | `GET .../tools` | 工具名 + 中文用途 |
| 会话列表 | `GET .../sessions` | 有记忆或摘要的会话，及各自记忆条数与摘要长度 |
| 记忆查询 | `GET .../memory`、`POST .../memory/clear` | 按会话 UMO 检索记忆、清空会话记忆 |

页面顶部有「刷新」按钮，所有数据都是实时读取插件内存与落盘文件。

## 后端 API

全部注册在 `/{PLUGIN_NAME}/...`（`astrbot_plugin_superai`）：

| 方法 | 路径 | 参数 | 返回 |
| --- | --- | --- | --- |
| GET | `/status` | — | `version`、`enabled`、`hooks_ready`、`features`、`tools`、`providers`、`route_tiers`、`health`、`health_detail`、`memory`、`sessions` |
| GET | `/stats` | `days`（1–90，默认 7） | 与 `/superai stats` 同源的聚合结构 |
| GET | `/memory` | `session`（必填）、`q`、`limit`（1–100） | `total`、`summary`、`covered_rounds`、`entries` |
| POST | `/memory/clear` | JSON `{session}` | `{removed}` |
| GET | `/tools` | — | `tools`（名字数组，向后兼容）、`items`（含 `description` / `active`） |
| GET | `/workflows` | — | `workflows`、`recent_runs` |
| GET | `/sessions` | — | `sessions: [{session, memories, summary_chars}]` |

所有接口都会自己兜住异常并返回 `error_response`，避免单个存储读取失败
导致整个面板白屏。

## `hooks_ready` 是什么

`hooks_ready: true` 表示框架里确实存在由本插件入口模块注册的
`on_llm_request` 处理器。它是**判断插件是否真的在工作**最直接的信号：

- `false` 时接口依然可用，但路由、记忆注入、图片保护、预算拦截都不会生效；
- 如果为 false，按 [FAQ → 钩子未就绪](./faq.md#钩子未就绪) 排查。

## 会话 UMO 是什么

`sessions` / `memory` 接口里的 `session` 是 AstrBot 的 `unified_msg_origin`，
形如：

```
aiocqhttp:GroupMessage:123456789
telegram:PrivateMessage:987654321
```

在「记忆查询」区块填入该值即可检索对应会话。

## 相关文档

- [指令手册](./commands.md)
- [用量统计与成本控制](./usage-and-budget.md)
- [记忆与摘要](./memory.md)

---

**最后核对**：`v0.2.8`（逐条对照 `pages/studio` 与测试断言，无凭空描述）
