# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## v0.2.0

一次以「修 Bug + 补强」为主的版本。下面每条都对应仓库里真实存在的问题
（多数在真实 AstrBot 上复现过），并配有回归测试。

### 修复

- **图片理解退化为纯文本**：AstrBot 在调用 `on_llm_request` 之前可能已经
  走完「图片压缩 / 转述」流程，并把 `req.image_urls` 清空。插件原先不保存
  原始图片，导致后面的多模态消息只剩文字。现在钩子进入时会先快照
  `image_urls`，在 `prepare_request_images` 之后补回，视觉请求不再掉图。
- **滚动摘要永远基于最旧的历史**：`get_human_readable_context(page=1)` 返回的是
  **最旧**的一页，原实现直接取第 1 页，导致摘要与事实抽取长期停留在很久以前
  的对话，新信息永远学不到。现在先算总页数再取最后一页。
- **摘要触发条件恒为真、每轮重复压缩**：`pending = len(history) - covered_rounds`
  里 `history` 只是一页（长度恒等于 page_size），该条件几乎永远成立，
  于是每轮都调用一次模型生成摘要（持续烧钱）。现在按
  `total_rounds - covered_rounds` 判断，并且摘要失败时也会推进进度，避免无限重试。
- **用量统计被自己污染、数字越看越大**：`summary()` 会把聚合结果写进
  「按天」字典，flush 后统计文件里出现 `last_7_days` 这种假日期，
  既破坏保留天数裁剪，又让 `/superai stats` 的数字随调用次数翻倍。
  现在聚合使用临时对象，并新增 `prune_legacy_keys()` 清理历史脏数据。
- **输入 token 统计偏低**：provider 上报的 `input_other` 不含缓存命中部分，
  原实现只记 `input_other`，导致「总 token」少算。现在区分
  `input_other` / `input_cached`，并新增 `input_total` 与 `total_tokens`。
- **长期记忆命中不到就整条丢掉**：原检索是纯子串 / n-gram 匹配，
  用户说「帮我写一段文案」这种与历史偏好无关的请求会得到 0 条记忆，
  「用户喜欢简洁的回答」等于白存。现在加入了英文关键词打分，
  且在**完全无相关**时回落到「权重最高」的若干条，保证模型仍拿得到背景。
- **路由档位被会话默认模型抢走**：`resolve_provider_id` 会把「会话当前模型」
  无条件追加进候选并参与排序，导致用户配置的 `cheap` / `strong` 档位可能被忽略。
  现在只有在「一个档位模型都没配」时才回落到会话默认模型。
- **候选链一失败就中止**：执行层把「同档位 → strong → cheap → reasoning」
  全塞进降级候选，加上 provider 自身的重试，单个请求可能串行尝试 4 个模型 ×
  各自重试，耗时严重超标。现在候选数由 `max_retries` 控制（默认 3 个），
  并对每个候选加上 `router.timeout` 超时，超时即换下一个。
- **失败模型不会被规避**：原先 `mark_failure/mark_success` 没有任何调用点，
  「健康度排序」形同虚设。现在路由决策、Agent 执行、响应钩子都会更新健康度，
  连续失败的模型自动排到候选链末尾，并在 30 分钟后自动遗忘旧故障。
- **`schema` / `i18n` 与代码脱节**：配置项 `knowledge_base.auto_inject`、
  `workflow.max_step_chars` 等此前要么没有被读取，要么没有在 schema 里暴露。
  现在配置项与代码一一对应，并补上缺失的说明。
- **Studio 面板 XSS 与交互缺陷**：会话 UMO、模型名直接拼进 `innerHTML`，
  现在统一转义；同时补上「会话列表点击回填」「7 天请求趋势」。
- **测试互相污染**：冒烟测试在模块导入期改动 `sys.path` / `sys.modules`，
  会让同一进程内的其它测试被真实 AstrBot 的 sqlmodel 表定义冲突打挂。
  真实框架的切换已移到 `conftest.py` 的 `pytest_configure`。

### 新增

- **`superai_history_summary` 工具**：模型可主动读取本会话的历史摘要。
- **知识库自动注入**：`knowledge_base.auto_inject` 真正生效，
  按用户问题检索知识库并注入上下文（默认关闭，按需开启）。
- **详细的失败降级日志**：每次切换模型都会记录原因，便于排障。
- **`/superai memory stats`**：查看全局记忆概览（会话数、各类型条数）。
- **`/superai maintain`**：管理员手动触发落盘与记忆衰减。
- **`/superai route` 展示当前降级链**；`/superai tools` 附带中文用途说明。
- **`logo.png`**：插件 Logo（会被 AstrBot 识别并展示在插件列表中）。
- **Studio 面板**：新增模型健康度、会话列表、每日请求趋势；支持 i18n。
- **测试**：新增 16 项针对上述回归的测试，含真实 AstrBot 上的端到端冒烟。

### 变更

- 动态上下文默认**不再注入当前时间**：注入会让 user 段落每轮变化，
  直接破坏 provider 端的自动前缀缓存（成本/首 token 延迟都会变差）。
  需要时间信息时请在 Agent 附加系统提示词里说明。
- 内部标记从 `【历史对话摘要】` 改为 `&lt;history_summary&gt;` 等标签，
  避免模型把中文方括号内容当成用户正文复述。
- 记忆/摘要的模型调用统一走「候选链 + 重试」，单点故障不再导致记忆功能整体失效。
- `MemoryStore.list` 改为按「权重、更新时间」排序，重要记忆优先展示。

## v0.1.0 (首个版本)

### 新增

- **SuperRouter 多模型路由**：支持 `cheap` / `strong` / `reasoning` / `vision` /
  `long_context` 五个档位，提供 `rule` / `auto` / `cheap_first` / `quality_first`
  四种策略，并支持自定义关键词映射。
- **失败自动降级**：按降级链重试，结合 provider 健康度排序，主模型失败时自动切换。
- **SuperMemory 记忆系统**：滚动摘要（降低 token 消耗）+ 长期记忆自动抽取与相关性注入 +
  记忆权重衰减清理。
- **SuperAgent 工具集**：联网搜索、网页抓取、知识库检索、记忆读写、工作流触发。
- **工作流编排**：配置化多步 AI 任务，支持 `{{prev}}` / `{{input}}` / `{{step}}` 占位符。
- **用量统计与预算**：按天记录 token / 耗时 / 路由分布，支持每日 token 与请求数上限。
- **AI 指令**：`/ai` 快捷问答与 `/superai` 管理指令组。
- **SuperAI Studio**：WebUI 插件页面，查看状态、统计、工具与会话记忆。
