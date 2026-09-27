# 架构总览

## 一句话

SuperAI 挂在 AstrBot 的 `on_llm_request` / `on_llm_response` 两个钩子上，
在不改变原流程的前提下做加法：**选模型、塞上下文、加工具、记用量**。

## 目录结构

```
main.py                       # 插件入口：插件类、@filter 钩子、指令、Studio API
superai/
├── agent_runner.py           # 带降级/超时重试的 Agent 执行器
├── prompt.py                 # 提示词构建（稳定/动态分离）
├── memory_service.py         # 摘要生成与事实抽取
├── version.py                # 版本号单一来源
├── core/
│   ├── config.py             # 配置视图与默认值
│   ├── metrics.py            # 用量统计与预算
│   ├── utils.py              # 文本 / token 估算 / 重试
│   └── errors.py             # 统一异常
├── router/router.py          # SuperRouter 路由与降级
├── storage/                  # JSON 持久化（记忆 / 摘要 / 工作流）
├── tools/                    # function calling 工具集
└── main.py                   # 兼容垫片（旧导入路径 re-export 根 main.py）
pages/studio/                 # WebUI 面板（html/css/js）
tests/                        # 单元测试 + 契约测试 + AstrBot 替身
scripts/e2e_smoke.py          # 真实 AstrBot 端到端联调
.ci/Dockerfile, .cnb.yml      # CI 流水线
_conf_schema.json             # WebUI 配置模式
metadata.yaml                 # 插件元数据
```

## 分层原则

| 层 | 位置 | 约束 |
| --- | --- | --- |
| 插件层 | 根 `main.py` | **必须**定义插件类与所有 `@filter` 钩子；可以访问 `self.context` / 配置 / 存储 |
| 逻辑层 | `superai/*` | 纯逻辑，**不依赖插件实例**；需要插件时通过 `ToolContext` 或构造参数注入 |
| 数据层 | `superai/storage/*` | 基于 `JsonStore` 的原子写；不感知业务 |
| 表现层 | `pages/studio/*` | 只消费插件注册的 HTTP API |

为什么插件层必须留在根 `main.py`，见
[AstrBot 集成契约](./astrbot-contracts.md#入口与-self-绑定)。

## 一次请求的完整生命周期

### 走 AstrBot 默认管线

```
用户消息
  └─ AstrBot 事件管线（唤醒/图片预处理/历史拼装）
       └─ call_event_hook(OnLLMRequestEvent) → SuperAIPlugin.on_llm_request
            1. 总开关 / 群权限校验
            2. 快照原始 image_urls（防止框架预处理清空图片）
            3. 任务类型白名单校验（chat/agent/long_context/image）
            4. 记录本轮起始时间（必须在所有提前 return 之后）
            5. 预算检查（超限 → set_result 提示 + stop_event）
            6. 追加稳定指令到 system_prompt
            7. 记忆：累计轮数 → 读历史 → 必要时生成滚动摘要 → 检索并注入记忆
            8. 知识库自动注入（若开启）
            9. 时效性问题 → 插入 web_search_hint
           10. 路由决策 → 改写 req.model
           11. 组装动态块（没内容就不注入 → 保护前缀缓存）
           12. 补回图片 URL
       └─ provider 真实调用
       └─ call_event_hook(OnLLMResponseEvent) → SuperAIPlugin.on_llm_response
            1. 配对本轮起始时间，算耗时
            2. role == "err" → 记失败 + 标记 provider 不健康
            3. 否则记 token 用量 + 标记成功
```

### 走 `/ai` 指令

```
/ai <问题>
  └─ ai_command：开关 → 群权限 → 冷却 → 输入裁剪 → 取图片
       └─ _run_agent
            1. SuperRouter 决策 → 候选链（含健康度排序）
            2. 组装 ToolSet
            3. AgentExecutor.run：
                 对每个候选模型 asyncio.wait_for 调用
                   - 有工具 → context.tool_loop_agent
                   - 无工具 → context.llm_generate
                 失败/超时/空回复 → mark_failure + 下一个
            4. 记录用量（真实 token）
       └─ strip_markup 后回复
```

`/ai` 不会触发 `on_llm_response`，所以用量由 `AgentOutcome` 显式带回。

## 关键数据结构

| 结构 | 位置 | 说明 |
| --- | --- | --- |
| `SuperAIConfig` | `core/config.py` | 强类型配置视图；`provider_map()` / `group_allowed()` 等派生方法 |
| `RouteDecision` | `router/router.py` | 档位 + 原因 + 候选链 |
| `AgentOutcome` | `agent_runner.py` | 文本 + provider + 尝试次数 + 错误列表 + token |
| `MemoryEntry` | `storage/memory.py` | 记忆条目（内容指纹做 id） |
| `SessionSummary` | `storage/summary.py` | 滚动摘要（覆盖轮数 / 历史存档） |
| `Workflow` / `WorkflowStep` | `storage/workflows.py` | 工作流定义 |
| `TokenUsageRecord` / `DailyStats` | `core/metrics.py` | 明细与日聚合 |
| `ToolContext` | `tools/base.py` | 工具运行时依赖（插件实例包装） |

## 并发与后台任务

- `_spawn()` 统一创建后台任务并收进 `_background_tasks`，卸载时统一回收；
- 后台维护循环每 `MAINTENANCE_INTERVAL`（600s）跑一次：落盘统计 + 记忆衰减；
- 事实抽取在后台异步执行，不阻塞对话；
- `_request_started` 是有界 deque（500），避免长期运行内存泄漏。

## 相关文档

- [AstrBot 集成契约](./astrbot-contracts.md)
- [开发与测试](./development.md)

---

**最后核对**：`v0.2.9`（逐条对照 `superai/` 与测试断言，无凭空描述）
