# 指令手册

所有指令均由 SuperAI 注册；`/ai` 另有别名 `/提问`、`/Ai`。
群白名单/黑名单、冷却时间、输入长度上限见 [配置手册 → 指令行为](./configuration.md#指令行为)。

## 一览

| 指令 | 权限 | 作用 |
| --- | --- | --- |
| `/ai <问题>` | 所有人 | 走 SuperAI 完整链路（路由 + 记忆 + 工具）回答，可附图片 |
| `/superai help` | 所有人 | 帮助文本 |
| `/superai status` | 所有人 | 运行状态、能力开关、钩子就绪、模型数、记忆量、今日用量 |
| `/superai stats [天数]` | 所有人 | 用量统计（1–90 天，默认 7） |
| `/superai memory list [条数]` | 所有人 | 本会话长期记忆 + 当前摘要 |
| `/superai memory search <关键词>` | 所有人 | 检索本会话记忆 |
| `/superai memory clear` | 管理员 | 清空本会话记忆与摘要 |
| `/superai memory stats` | 所有人 | 全局记忆概览（会话数 / 各类型条数 / 摘要数） |
| `/superai route` | 所有人 | 查看当前档位、档位→模型映射、失败计数、当前降级链 |
| `/superai route <档位>` | 所有人 | 把**本会话**固定到某档位 |
| `/superai tools` | 所有人 | 已注册工具及中文用途 |
| `/superai workflow` | 所有人 | 列出可用工作流 |
| `/superai workflow <名称> [输入]` | 所有人 | 运行工作流 |
| `/superai maintain` | 管理员 | 立即落盘统计并执行记忆衰减 |

> 「管理员」判定使用 AstrBot 的 `event.is_admin()`。

## `/ai`

```
/ai 帮我写一段关于云原生构建的推文
/ai 这张图里有什么                （附图片发送）
```

行为链：群权限校验 → 冷却校验 → 输入裁剪 → 路由选模型 → 组装工具 →
`tool_loop_agent` 执行（带降级重试）→ 记录用量 → 返回文本。

要点：

- **可只带图不带文字**，此时会以「请描述这张图片。」发起；
- 输出会抹掉模型可能回显的内部标记（`<think>`、`<superai_context>` 等）；
- 纯文字 + 不带参数时返回帮助文本；
- Agent 工具未启用时退化为纯文本生成。

## `/superai status` 输出含义

```
⚙️ SuperAI v0.2.3
路由=✅  记忆=✅  联网=❌  知识库=❌  Agent=✅  工作流=✅
钩子：✅ 已就绪
可用模型：3 个（openai-gpt4o, deepseek-chat, ...）
工具：4 个
记忆总量：128 条（会话 6 个）
会话摘要数：4
今日用量：37 次请求 / 51420 tokens
配额：今日已用 37 次请求 / 51420 tokens，token 配额 51420/200000
```

**`钩子：⚠️ 未检测到` 是危险信号**：说明 `on_llm_request` 没有按框架语义
注册成功，此时路由、记忆注入、图片保护、预算拦截都不会生效。
排查见 [FAQ](./faq.md#钩子未就绪)。

## `/superai route`

不带参数时显示：

```
🧭 SuperRouter 状态
当前档位：cheap
策略：rule
档位 -> 模型：
- cheap：deepseek-chat
- strong：gpt-4o
- reasoning：（未配置）
- vision：（未配置）
- long_context：（未配置）
当前降级链：deepseek-chat → gpt-4o
```

带参数时把**本会话**固定档位，立即生效（不写配置文件、重启即失效）：

```
/superai route strong      # 本会话固定走强模型
/superai route auto        # 恢复自动路由（也接受 reset / 默认）
```

可选档位：`cheap`、`strong`、`reasoning`、`vision`、`long_context`、`default`、`auto`。

## `/superai memory`

`memory` 是 `superai` 下的**子指令组**，四个子命令：

```
/superai memory list 20          # 本会话记忆 + 摘要（默认 10 条，上限 50）
/superai memory search 编程语言   # 按关键词做轻量相似度检索，返回 top 5
/superai memory clear            # 清空本会话记忆与摘要（管理员）
/superai memory stats            # 全局概览
```

## 让 Bot 记住事情

两种方式：

1. **显式**：直接说「记住：我喜欢简洁的回答」。模型会调用 `superai_remember` 工具写入；
2. **自动**：开启 `自动抽取事实` 后，后台会周期性从对话中抽取稳定偏好与客观事实。

查看结果用 `/superai memory list`，或在 Studio 面板的「记忆查询」区块按会话检索。

## 相关文档

- [模型路由](./routing.md)
- [记忆与摘要](./memory.md)
- [工具与工作流](./tools-and-workflows.md)
- [常见问题与排错](./faq.md)

---

**最后核对**：`v0.2.5`（逐条对照 `main.py` 与测试断言，无凭空描述）
