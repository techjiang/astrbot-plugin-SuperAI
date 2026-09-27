# 工具与工作流 SuperAgent

SuperAI 把能力注册为 AstrBot 的 function calling 工具，由模型按需调用；
工作流则把「多步固定流程」配置化，非技术用户也能编排。

## 内置工具

| 工具名 | 用途 | 开启条件 |
| --- | --- | --- |
| `superai_web_search` | 联网搜索实时信息 | `联网搜索 → 启用` |
| `superai_fetch_url` | 抓取网页正文 | `联网搜索 → 启用` |
| `superai_knowledge_search` | 检索 AstrBot 知识库 | `知识库增强 → 启用` 且 **至少配置一个默认知识库** |
| `superai_remember` | 写入长期记忆 | `记忆 → 启用长期记忆` |
| `superai_recall` | 检索长期记忆 | 同上 |
| `superai_history_summary` | 读取本会话历史摘要 | 长期记忆 + 自动摘要均开启 |
| `superai_run_workflow` | 触发预编排工作流 | 工作流已启用**且至少配置一个工作流** |

用 `/superai tools` 或 Studio 面板可以查看当前配置下真实注册了哪些工具。

### 工具参数

- `superai_web_search(query, max_results?)`：返回标题 / 链接 / 摘要列表；
- `superai_fetch_url(url?, query?)`：抓取并抽取网页正文；
- `superai_knowledge_search(query, kb_names?, top_k?)`：
  `kb_names` 留空则用插件配置的默认知识库；
- `superai_remember(content, kind?)`：`kind` ∈ `fact` / `preference` / `event`；
- `superai_recall(query, top_k?)`；
- `superai_history_summary(limit?)`；
- `superai_run_workflow(name, input?)`。

工具异常由 `SuperAITool.call()`（`superai/tools/base.py`）统一兜住：
记一行 error 日志，并把
`工具 <name> 执行失败：<异常>` 作为**工具结果**返回给模型，
**不会中断对话**，模型可以据此换个方式重试。

## 联网搜索

- 默认引擎 `duckduckgo`：使用免 Key 的 HTML 端点，开箱可用，受网络环境影响；
- `searxng`：填自建实例地址（需开启 JSON 输出），可填 API Key；
- 涉及时效性问题时（命中 `最新` / `今天` / `新闻` / `股价` 等关键词），
  SuperAI 会在上下文里插入 `<web_search_hint>`，提示模型优先调用搜索工具，
  而不是凭记忆编造。

只依赖 `aiohttp`（AstrBot 内置）。

## 知识库检索

两条路径：

1. **按需检索**（推荐）：模型判断需要时调用 `superai_knowledge_search`；
2. **自动注入**：开启 `自动检索并注入知识库` 后，每轮都用用户问题检索并注入
   `<knowledge_base>` 片段。**会增加每轮一次检索开销与少量 token，默认关闭。**

`最低相关度阈值` 用于过滤低分片段，避免噪声把提示词撑大。
部分知识库实现不返回分数时该配置无效。

## 工作流编排

### 配置

配置项：`工作流编排 → 工作流定义`（dict）。

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

字段含义：

| 字段 | 说明 |
| --- | --- |
| `description` | 展示用说明 |
| `route` | 本工作流使用的路由档位（留空则按默认候选链） |
| `steps[].name` | 步骤名，出现在输出里 |
| `steps[].prompt` | 该步的提示词，支持占位符 |
| `steps[].use_history` | 是否把上一步结果拼到 prompt 后面（等价于手写 `{{prev}}`） |

步骤也可以写成纯字符串：`"用三句话总结今天的科技新闻"`，
此时 `name` 取前 20 个字符。

### 占位符

| 占位符 | 替换为 |
| --- | --- |
| `{{prev}}` | 上一步的输出 |
| `{{input}}` | 用户输入的初始内容 |
| `{{step}}` | 当前步号（从 1 开始） |

### 运行

```
/superai workflow                 # 列出所有工作流
/superai workflow 早报             # 运行
/superai workflow 早报 只看 AI 相关  # 带输入运行
```

也可以让模型自己触发：用户说「来份早报」，模型调用 `superai_run_workflow`。

### 步骤用什么模型

`route` 指定的档位 → 摘要专用模型 → `strong` → `cheap` → `reasoning`
→ `long_context` → 会话默认模型。**最后一档兜底保证「一个档位都没配」时工作流也能跑。**

### 运行结果

输出形如：

```
🔧 工作流「早报」执行完成

【第 1 步 · 收集】
...

【第 2 步 · 翻译】
...
```

每次运行都会记入 `workflows.json` 的 `workflow_runs`（保留最近 50 条），
可在 Studio 面板的 `/workflows` 接口（`recent_runs`）查看。

失败时记录 `success: false` 与失败步骤，并把错误抛给用户。

## 工具与插件的归属

停用/卸载 SuperAI 时，它注册的 LLM 工具会**一起停用**。
这一点并不自动成立，需要把工具的 `handler_module_path` 改写成插件入口模块路径，
详见 [AstrBot 集成契约](./dev/astrbot-contracts.md#工具的归属模块)。

## 相关文档

- [配置手册 → 工作流编排](./configuration.md#工作流编排)
- [指令手册](./commands.md)

---

**最后核对**：`v0.2.5`（逐条对照 `superai/tools` 与测试断言，无凭空描述）
