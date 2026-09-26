<div align="center">

# SuperAI

**新一代 AstrBot AI 增强插件**

多模型智能路由 · 滚动摘要与长期记忆 · Agent 工具 · 知识库增强 · 用量成本控制

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.5.7-blue)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-AGPL--3.0-green)](./LICENSE)
[![Version](https://img.shields.io/badge/version-v0.1.0-orange)](./metadata.yaml)

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

### 1. SuperRouter —— 多模型智能路由

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

### 2. SuperMemory —— 滚动摘要 + 长期记忆

- **滚动摘要**：对话累积超过阈值后，自动用「便宜档」模型把历史压缩成一段摘要，
  用摘要替换冗长历史，显著降低 token 消耗，同时保留关键结论。
- **长期记忆**：自动从对话中抽取稳定偏好与客观事实（如「用户在做 AstrBot 插件」），
  按相关性在后续对话中注入；长时间未命中的记忆按半衰期衰减并清理。
- **模型可主动记忆**：提供 `superai_remember` / `superai_recall` 工具，
  让模型自己决定何时记、何时查（用户说「记住……」即可）。

> 遵循 AstrBot 官方建议：**稳定**内容（角色设定）写入 `system_prompt`，
> **动态**内容（时间、本轮记忆、摘要）走 `extra_user_content_parts`，避免破坏提示词缓存。

### 3. SuperAgent —— 工具增强

注册为 AstrBot 的 function calling 工具，模型按需调用：

| 工具 | 作用 |
| --- | --- |
| `superai_web_search` | 联网搜索（DuckDuckGo 免 Key / 自建 SearXNG） |
| `superai_fetch_url` | 抓取网页正文 |
| `superai_knowledge_search` | 检索 AstrBot 知识库 |
| `superai_remember` / `superai_recall` | 写入 / 检索长期记忆 |
| `superai_run_workflow` | 触发预编排的多步工作流 |

### 4. 工作流编排

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

### 5. 用量统计与成本控制

- 记录每次请求的 token / 耗时 / 路由档位，按天聚合落盘；
- 可设置**每日 token 上限**与**请求数上限**，超限自动拦截并提示；
- `/superai stats` 指令与 **Studio 面板** 可视化查看。

## 安装

1. 在 AstrBot WebUI 的「插件市场」搜索 `SuperAI` 安装，或手动克隆到 `data/plugins/`：

   ```bash
   cd AstrBot/data/plugins
   git clone https://cnb.cool/asoe/TechSauce/astrbot-plugin-SuperAI.git astrbot_plugin_superai
   ```

2. 重启 AstrBot，或在插件页点击「重载插件」；
3. 进入插件配置，为各路由档位选择模型（至少配置一个）。

> **依赖**：插件仅依赖 `aiohttp`，AstrBot 已内置，无需额外安装；如在无 AstrBot 的环境中运行测试，请先 `pip install -r requirements.txt -r requirements-dev.txt`。
> **版本要求**：AstrBot >= 4.5.7（使用了 `llm_generate` / `tool_loop_agent` 等新 SDK）。

## 快速上手

配置好之后，在群里直接用：

```
/ai 帮我写一段关于云原生构建的推文
```

SuperAI 会自动：判断档位 → 选模型 → 拉取相关记忆 → 带上工具 → 生成回复。

常用指令：

| 指令 | 说明 |
| --- | --- |
| `/ai <问题>` | 向 SuperAI 提问（自动路由 + 记忆 + 工具），支持附带图片 |
| `/superai status` | 查看运行状态与今日用量 |
| `/superai stats [天数]` | 查看用量统计 |
| `/superai memory list` | 查看本会话记忆与当前摘要 |
| `/superai memory search <关键词>` | 检索记忆 |
| `/superai memory clear` | 清空本会话记忆（仅管理员） |
| `/superai route` | 查看路由档位与模型映射 |
| `/superai route strong` | 把本会话固定到 `strong` 档位（`auto` 恢复） |
| `/superai tools` | 查看已注册工具 |
| `/superai workflow [名称] [输入]` | 查看或运行工作流 |
| `/superai help` | 帮助 |

## 配置说明

配置项在 WebUI 插件配置页可视化编辑，主要分组：

- **基础**：总开关、启用的任务类型、调试日志
- **模型路由**：策略、各档位模型、长上下文阈值、自定义关键词映射、重试次数
- **记忆与摘要**：摘要触发轮数、摘要专用模型、长期记忆条数与注入开关、衰减半衰期
- **联网搜索**：引擎（DuckDuckGo / SearXNG）、结果条数、超时
- **知识库**：默认知识库、检索条数
- **Agent**：最大步数、工具超时、附加系统提示词
- **指令行为**：群白名单 / 黑名单、冷却时间、输入长度上限
- **用量统计**：每日 token / 请求数上限、保留天数
- **工作流**：工作流定义

## Studio 面板

插件在 WebUI 中提供一个 **SuperAI Studio** 页面（`pages/studio/`），可以：

- 查看各能力开关、可用模型、路由档位映射、模型健康度；
- 查看近 7 天的请求数 / token / 平均耗时，以及模型与路由分布；
- 查看已注册工具；
- 按会话 UMO 检索或清空记忆。

## 架构

```
superai/
├── main.py              # 插件入口：钩子、指令、Web API
├── prompt.py            # 提示词构建（稳定/动态分离）
├── memory_service.py    # 摘要生成与事实抽取
├── core/
│   ├── config.py        # 配置视图与默认值
│   ├── metrics.py       # 用量统计与预算
│   ├── utils.py         # 文本 / token 估算 / 重试
│   └── errors.py        # 统一异常
├── router/router.py     # SuperRouter 路由与降级
├── storage/             # JSON 持久化（记忆 / 摘要 / 工作流）
└── tools/               # function calling 工具集
```

数据落在 `data/plugin_data/astrbot_plugin_superai/`（遵循官方「持久化数据放 data 目录」原则）。

## 开发

```bash
# 运行依赖（AstrBot 已内置 aiohttp；单独跑测试时需自行安装）
pip install -r requirements.txt
# 开发 / CI 依赖（ruff + pytest 等）
pip install -r requirements-dev.txt

# 代码风格
ruff check .
ruff format --check .

# 单元测试
python -m pytest tests
```

`tests/stubs/astrbot/` 是一个最小的 AstrBot 替身，让纯逻辑模块（路由、配置、存储、
统计、工具装配）在没有 AstrBot 的环境下也能被测试。
若能提供真实的 AstrBot 源码路径（默认探测 `/tmp/astrbot-ref`），
`tests/test_plugin_smoke.py` 会自动用真实框架跑一遍实例化与钩子冒烟测试。

## 局限与已知问题

- `long_context` 与 `vision` 档位需要你配置对应能力模型，否则会回落到其他档位；
- 联网搜索默认引擎（DuckDuckGo HTML 端点）无需 Key，但可能受网络环境影响；
  生产环境建议自建 SearXNG 并在配置中切换；
- 事实抽取依赖模型输出合法 JSON，抽取失败会静默跳过（不影响对话）；
- 记忆检索使用轻量 n-gram 相似度而非向量检索，以保持零重依赖。

## 灵感与致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) —— 插件框架与官方开发文档
- [Soulter/helloworld](https://github.com/Soulter/helloworld) —— 插件模板
- 插件市场中的 `astrbot_plugin_treasure_bag` 等开源插件 —— 工程组织参考

## License

AGPL-3.0
