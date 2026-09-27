<div align="center">

<img src="./logo.png" alt="SuperAI" width="180" />

# SuperAI

**新一代 AstrBot AI 增强插件**

多模型智能路由 · 滚动摘要与长期记忆 · Agent 工具集 · 知识库增强 · 用量成本控制

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.5.7-blue)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-AGPL--3.0-green)](./LICENSE)
[![Version](https://img.shields.io/badge/version-v0.2.6-orange)](./metadata.yaml)
[![Docs](https://img.shields.io/badge/docs-%E4%B8%AD%E6%96%87-blue)](./docs/README.md)

[文档](./docs/README.md) ·
[快速上手](./docs/quickstart.md) ·
[配置手册](./docs/configuration.md) ·
[指令手册](./docs/commands.md) ·
[常见问题](./docs/faq.md) ·
[更新日志](./CHANGELOG.md) ·
[Releases](https://github.com/techjiang/astrbot-plugin-SuperAI/releases)

</div>

---

## 这是什么

AstrBot 本身已经很好用，但当你真正把它跑在群里，很快就会遇到这几类问题：

- 一个 Bot 配了好几个模型，却永远只用「当前使用」的那一个 —— 问候语也走最贵的模型；
- 主模型一旦超时 / 限流，整轮对话直接失败，用户只看到一片沉默；
- 聊了几十轮之后，上下文越堆越长，token 成本飙升，早期信息还被挤掉了；
- 模型不知道「上周你说过你喜欢简洁的回答」；
- 想知道这个月烧了多少 token，只能去服务商后台翻。

**SuperAI 就是为这些问题写的一层增强中间件。** 它挂在 AstrBot 的 `on_llm_request` /
`on_llm_response` 钩子上，不改变 AstrBot 原有流程，只在你需要的地方做加法。

## 核心能力

| 能力 | 一句话 | 文档 |
| --- | --- | --- |
| **SuperRouter** | 按任务类型 / 内容 / 上下文长度自动选模型，失败自动降级，不健康模型自动靠后 | [模型路由](./docs/routing.md) |
| **SuperMemory** | 滚动摘要控制上下文成本，长期记忆记住用户偏好与事实 | [记忆与摘要](./docs/memory.md) |
| **SuperAgent** | 联网搜索、网页抓取、知识库检索、记忆读写、工作流触发 | [工具与工作流](./docs/tools-and-workflows.md) |
| **工作流编排** | 把多步 AI 任务写成配置，支持 `{{prev}}` / `{{input}}` / `{{step}}` | [工具与工作流](./docs/tools-and-workflows.md) |
| **用量统计与配额** | 按天记录 token / 耗时 / 路由，可设每日上限并拦截 | [用量与成本](./docs/usage-and-budget.md) |
| **Studio 面板** | WebUI 里看状态、健康度、用量、工具清单与会话记忆 | [Studio 面板](./docs/studio.md) |

### SuperRouter —— 多模型智能路由

按「任务类型 + 消息内容 + 上下文长度 + 会话偏好」自动把请求分配到最合适的模型：

| 档位 | 适用场景 | 典型配置 |
| --- | --- | --- |
| `cheap` | 闲聊、问候、翻译、改写 | 便宜的小模型 |
| `strong` | 写作、总结、代码、方案 | 主力质量模型 |
| `reasoning` | 数学、逻辑、排错、推导 | 推理模型 |
| `vision` | 带图片的请求 | 多模态模型 |
| `long_context` | 超长上下文 | 长窗口模型 |

四种策略：`rule`（关键词规则，可自定义映射）、`auto`（按长度与内容启发式判断）、
`cheap_first`（省钱优先）、`quality_first`（质量优先）。

**失败自动降级**：主模型失败时按降级链依次重试，并对连续失败的 provider 做健康度排序，
把不稳定模型自动排到后面，保证回复不中断。

### SuperMemory —— 滚动摘要 + 长期记忆

- **滚动摘要**：对话累积超过阈值后，自动用轻量模型把历史压缩成一段摘要，
  用摘要替换冗长历史，显著降低 token 消耗，同时保留关键结论；
- **长期记忆**：自动从对话中抽取稳定偏好与客观事实（如「用户在做 AstrBot 插件」），
  按相关性在后续对话中注入；长时间未命中的记忆按半衰期衰减并清理；
- **模型可主动记忆**：提供 `superai_remember` / `superai_recall` 工具，
  让模型自己决定何时记、何时查（用户说「记住……」即可）。

> 遵循 AstrBot 官方建议：**稳定**内容（角色设定）写入 `system_prompt`，
> **动态**内容（本轮记忆、摘要）走 `extra_user_content_parts`，
> 并且「没内容就不注入」，避免破坏提示词缓存。

### SuperAgent —— 工具增强

| 工具 | 作用 | 开启条件 |
| --- | --- | --- |
| `superai_web_search` | 联网搜索（DuckDuckGo 免 Key / 自建 SearXNG） | 开启联网搜索 |
| `superai_fetch_url` | 抓取网页正文 | 开启联网搜索 |
| `superai_knowledge_search` | 检索 AstrBot 知识库 | 开启知识库工具 |
| `superai_remember` / `superai_recall` | 写入 / 检索长期记忆 | 开启长期记忆 |
| `superai_history_summary` | 读取本会话历史摘要 | 开启长期记忆 + 自动摘要 |
| `superai_run_workflow` | 触发预编排的多步工作流 | 已配置工作流 |

### 工作流编排

把多步 AI 任务写进配置，非技术用户也能用：

```json
{
  "早报": {
    "description": "每日科技早报",
    "route": "cheap",
    "steps": [
      { "name": "收集", "prompt": "用三句话总结今天的科技新闻" },
      { "name": "翻译", "prompt": "把下面内容翻译成英文：\n{{prev}}" }
    ]
  }
}
```

支持 `{{prev}}`（上一步输出）、`{{input}}`（用户输入）、`{{step}}`（当前步号）占位符。

## 快速开始

1. **安装**：插件市场搜索 `SuperAI`，或克隆到 `data/plugins/`：

   ```bash
   cd AstrBot/data/plugins
   # 发布仓库（插件更新的解析来源）
   git clone https://github.com/techjiang/astrbot-plugin-SuperAI.git astrbot_plugin_superai
   # 国内网络更快：开发主仓库，内容与发布仓库一致
   # git clone https://cnb.cool/asoe/TechSauce/astrbot-plugin-SuperAI.git astrbot_plugin_superai
   ```

2. **重载**：重启 AstrBot，或在插件页点「重载插件」；
3. **配置**（可选但推荐）：为 `低成本模型` / `高质量模型` 等档位选择模型。
   一个都不配也能用，路由会回落到会话默认模型；
4. **使用**：

   ```
   /ai 帮我写一段关于云原生构建的推文
   ```

SuperAI 会自动：判断档位 → 选模型 → 拉取相关记忆 → 带上工具 → 生成回复。

> **依赖**：插件仅依赖 `aiohttp`，AstrBot 已内置。
> **版本要求**：AstrBot >= 4.5.7（使用了 `llm_generate` / `tool_loop_agent` 等新 SDK）。

完整步骤见 [快速上手](./docs/quickstart.md)，安装细节见 [安装与升级](./docs/install.md)。

## 常用指令

| 指令 | 说明 |
| --- | --- |
| `/ai <问题>` | 向 SuperAI 提问（自动路由 + 记忆 + 工具），支持附带图片 |
| `/superai status` | 查看运行状态与今日用量 |
| `/superai stats [天数]` | 查看用量统计 |
| `/superai memory list [条数]` | 查看本会话记忆与当前摘要 |
| `/superai memory search <关键词>` | 检索记忆 |
| `/superai memory clear` | 清空本会话记忆（仅管理员） |
| `/superai memory stats` | 查看全局记忆概览 |
| `/superai route` | 查看路由档位、模型映射与当前降级链 |
| `/superai route strong` | 把本会话固定到 `strong` 档位（`auto` 恢复） |
| `/superai tools` | 查看已注册工具及用途 |
| `/superai workflow [名称] [输入]` | 查看或运行工作流 |
| `/superai maintain` | 立即落盘统计并衰减记忆（仅管理员） |
| `/superai help` | 帮助 |

完整说明（含权限与输出示例）见 [指令手册](./docs/commands.md)。

## 文档

| 使用者 | 入口 |
| --- | --- |
| 第一次用 | [安装与升级](./docs/install.md) · [快速上手](./docs/quickstart.md) · [Studio 面板](./docs/studio.md) |
| 调参数 | [配置手册](./docs/configuration.md) · [模型路由](./docs/routing.md) · [记忆与摘要](./docs/memory.md) · [用量与成本](./docs/usage-and-budget.md) |
| 出了问题 | [常见问题与排错](./docs/faq.md) |
| 改代码 | [架构总览](./docs/dev/architecture.md) · [开发与测试](./docs/dev/development.md) · [AstrBot 集成契约](./docs/dev/astrbot-contracts.md) · [发布流程](./docs/dev/release.md) |

全部文档见 [docs/README.md](./docs/README.md)。

## 数据与隐私

插件数据全部落在本地 `data/plugin_data/astrbot_plugin_superai/`：

```
memory.json     # 长期记忆     summary.json    # 会话摘要
metrics.json    # 用量统计     workflows.json  # 工作流与运行记录
router.json     # 模型健康度
```

- **不向任何第三方服务上报数据**；
- 联网搜索只在模型主动调用工具、或问题命中时效性关键词时发生；
- 删除该目录即清空全部记忆与统计；建议升级前备份。

## 架构

```
main.py                  # 插件入口：插件类、@filter 钩子、指令、Web API
superai/
├── agent_runner.py      # 带降级/超时重试的 Agent 执行器
├── prompt.py            # 提示词构建（稳定/动态分离）
├── memory_service.py    # 摘要生成与事实抽取
├── core/                # 配置 / 统计 / 工具函数 / 异常
├── router/router.py     # SuperRouter 路由与降级
├── storage/             # JSON 持久化（记忆 / 摘要 / 工作流）
└── tools/               # function calling 工具集
pages/studio/            # WebUI 面板
```

数据落在 `data/plugin_data/astrbot_plugin_superai/`（遵循官方「持久化数据放 data 目录」原则）。

### 入口为什么必须在根目录

AstrBot 的 `PluginManager` 有两条会让插件**静默失效**的硬约束：

1. **只认 `main.py` 或「与目录同名的 `<dirname>.py`」**。
   入口放在子目录时，日志只留一行 `... skipping it.`，插件被跳过；
2. **插件类与 `@filter` 钩子必须定义在入口模块里**，
   否则框架绑定 `self` 失败，调用时抛 `TypeError`，
   而异常会被 `call_event_hook()` 吞掉 —— 路由 / 记忆注入 / 图片保护 / 预算拦截
   全部静默失效，表面却「加载成功」。

所以：**插件类与钩子放根 `main.py`**，`superai/` 包只放不依赖插件实例的纯逻辑。
完整清单见 [AstrBot 集成契约](./docs/dev/astrbot-contracts.md)。

## 开发

```bash
pip install -r requirements.txt -r requirements-dev.txt

ruff check . && ruff format --check .
python -m pytest tests

# 有真实 AstrBot 源码时可跑端到端联调
git clone --depth 1 https://github.com/AstrBotDevs/AstrBot /tmp/astrbot-ref
ASTRBOT_REF=/tmp/astrbot-ref python scripts/e2e_smoke.py
```

测试分四层：纯逻辑单测、契约测试（防「测试绿、线上死」）、仓库健康检查、
真实 AstrBot 端到端联调。详见 [开发与测试](./docs/dev/development.md)。

## 局限与已知问题

- `long_context` / `vision` 档位需要你配置对应能力模型，否则会回落到其他档位；
- 联网搜索默认引擎（DuckDuckGo HTML 端点）无需 Key，但可能受网络环境影响，
  生产环境建议自建 SearXNG；
- 事实抽取依赖模型输出合法 JSON，抽取失败会静默跳过（不影响对话）；
- 记忆检索使用轻量 n-gram + 关键词相似度而非向量检索，以保持零重依赖；
- 知识库自动注入会增加每轮的一次检索开销，默认关闭；
- 记忆按会话隔离，不跨群共享。

## 贡献

见 [CONTRIBUTING.md](./CONTRIBUTING.md)。反馈问题时请附上：
AstrBot 与 SuperAI 版本、`/superai status` 输出、调试日志（请脱敏）、复现步骤。

安全问题请按 [SECURITY.md](./SECURITY.md) 私下报告。

## 灵感与致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) —— 插件框架与官方开发文档
- [Soulter/helloworld](https://github.com/Soulter/helloworld) —— 插件模板
- 插件市场中的 `astrbot_plugin_treasure_bag` 等开源插件 —— 工程组织参考

## 关于作者

**科技酱**（插件包身份 `TechSauce`）

- 官网：https://docs.asoe.cn
- GitHub：https://github.com/techjiang/
- 哔哩哔哩：https://space.bilibili.com/1768832152
- 玲珑社区：https://forums.asoe.cn/
- QQ 群：291974598
- QQ 群②：474819022

> AstrBot 插件市场卡片上的「作者」读取的是 `metadata.yaml` 的 `author` 字段。
> 它同时参与 `plugin_id = author/name`，是插件在市场里的全局唯一标识，
> 也是老用户匹配更新的依据 —— **请勿随意改动**。
> 一旦改动，已安装用户会收不到更新，需要重新安装。

## 源码仓库

| 用途 | 地址 |
| --- | --- |
| 发布仓库（AstrBot 官方商店读取、插件更新来源） | <https://github.com/techjiang/astrbot-plugin-SuperAI> |
| 开发主仓库（Issue / CI / Releases） | <https://cnb.cool/asoe/TechSauce/astrbot-plugin-SuperAI> |

两者内容一致：CNB 是开发基线，GitHub 是发布镜像（打 tag 时自动同步）。

## License

AGPL-3.0
