# 快速上手

目标：**10 分钟内**让 SuperAI 在群里跑起来。

## 0. 前置条件

- AstrBot >= 4.5.7（SuperAI 用到 `llm_generate` / `tool_loop_agent` 等新 SDK）；
- 已在 AstrBot WebUI 的「服务提供商」里配置并**启用**至少一个对话模型。

> 一个模型就够。SuperAI 的路由档位、视觉档、长上下文档都是可选的，
> 只配一个默认模型时插件依然完整可用（路由会走「未命中规则 → 会话默认模型」）。

## 1. 安装

见 [安装与升级](./install.md)。最简路径：

```bash
cd AstrBot/data/plugins
git clone https://github.com/techjiang/astrbot-plugin-SuperAI.git astrbot_plugin_superai
```

然后重启 AstrBot（或在 WebUI 插件页点「重载插件」）。

**怎么确认装好了**：插件列表里出现 `SuperAI`，且日志有一行

```
[SuperAI] v0.2.3 已加载 | 路由=开 记忆=开 ...
```

## 2. 配一个模型（关键一步）

打开「插件管理 → SuperAI → 配置」，至少做下面两件事之一：

- **方案 A（最省事）**：什么都不填。SuperAI 会在「档位都没配」时回落到会话默认模型；
- **方案 B（推荐）**：填上 `模型路由 → 低成本模型` 与 `模型路由 → 高质量模型`，
  这样闲聊与复杂任务才会分流。

保存后插件会热加载配置。

## 3. 说第一句话

在任意群里发：

```
/ai 用三句话解释一下什么是云原生构建
```

你应该看到：路由判定档位 → 选中模型 → 生成回复。
开启「调试日志」时，AstrBot 日志会打印：

```
[SuperAI] 路由档位=strong 主模型=xxx 原因=命中高质量任务关键词 降级链=['xxx', 'yyy']
```

## 4. 验证四项主要能力

| 能力 | 怎么验证 | 期望结果 |
| --- | --- | --- |
| 路由 | `/superai route` | 显示当前档位、档位→模型映射、当前降级链 |
| 记忆 | 先对 Bot 说「记住：我习惯用 Python」，再发 `/superai memory list` | 列表里出现该条记忆 |
| 统计 | `/superai stats` | 输出请求数、token、平均耗时、路由分布 |
| 工具 | `/superai tools` | 列出当前配置下已注册的工具及用途 |

## 5. 打开 Studio 面板

AstrBot WebUI → 插件 → SuperAI → **SuperAI Studio**（`pages/studio/`）。
面板能看运行状态、模型健康度、近 7 天用量、工具清单与各会话记忆。
详见 [Studio 面板](./studio.md)。

## 6. 接下来读什么

- 想让便宜的模型处理闲聊、强的模型处理写作 → [模型路由](./routing.md)
- 想控制上下文长度、让 Bot 记住用户偏好 → [记忆与摘要](./memory.md)
- 想省钱、设每日上限 → [用量统计与成本控制](./usage-and-budget.md)
- 想加联网搜索、知识库、多步工作流 → [工具与工作流](./tools-and-workflows.md)
- 出问题了 → [常见问题与排错](./faq.md)

---

**最后核对**：`v0.2.6`（逐条对照 `quickstart` 与测试断言，无凭空描述）
