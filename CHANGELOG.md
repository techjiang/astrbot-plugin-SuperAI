# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## v0.2.2

这一版是**在真实 AstrBot 4.28.1 上跑端到端联调时挖出来的**：v0.2.1 里
插件「日志显示加载成功、CI 全绿」，但实际上**从来没有被框架加载过**。

### 修复（致命）

- **插件入口不在框架认的位置，整个插件被静默跳过**。
  AstrBot 的 `PluginManager._get_modules` 只认插件目录下的 `main.py`
  或「与目录同名的 `<dirname>.py`」；本插件实现主体在 `superai/` 包里，
  根目录没有入口，于是框架只留一行
  `Plugin astrbot_plugin_superai has neither main.py nor astrbot_plugin_superai.py; skipping it.`
  就把插件跳过了 —— 用户看到的是「插件装上了，但一点效果都没有」。
  现在根目录补上 `main.py` 作为入口。
- **插件类定义在子模块，导致 `self` 绑定失败、钩子被静默吞掉**。
  框架用 `metadata.module_path`（入口模块路径）调
  `get_handlers_by_module_name()` 找到处理器后，才会执行
  `handler.handler = functools.partial(raw_handler, metadata.star_cls)` 绑定 `self`；
  而处理器的 `handler_module_path` 记录的是**装饰器所在模块**。
  插件类原先定义在 `superai/main.py`，与入口模块路径不一致 → 绑定不发生 →
  调用时抛 `TypeError: SuperAIPlugin.on_llm_request() missing 1 required
  positional argument: 'req'`，异常被 `call_event_hook()` 吞掉只记一行 error，
  结果是**路由 / 记忆注入 / 图片保护 / 预算拦截全部静默失效**。
  现在插件类与两个 `@filter` 钩子都定义在根 `main.py`，
  `superai/` 包只保留不依赖插件实例的纯逻辑（配置 / 路由 / 存储 / 工具 / 提示词）。

### 新增

- `tests/test_plugin_loader_contract.py`：11 项「加载器契约」测试，固定住
  「入口必须存在」「插件类必须定义在入口模块」「钩子签名必须是
  `(self, event, payload)`」「入口不能有相对导入」等不变量。
- `scripts/e2e_smoke.py`：在**真实 AstrBot** 上按框架的方式加载插件、
  注册处理器、绑定 `self`、`await` 钩子，并断言路由确实改写了请求。
  已接入 CI，专门守住「测试绿、线上死」。
- README 新增「入口为什么必须在根目录」一节，把上述两条硬约束写清楚。

### 验证

- 真实 AstrBot 4.28.1 端到端：WebChat 发消息 → 路由命中 `cheap` 档 →
  mock provider 返回 → 用量落盘（`requests=4`, `total_tokens=6370`,
  `route=cheap`, `failures=0`），Studio 面板全部区块正常渲染。
- `pytest tests` → **132 passed**；`ruff check` + `ruff format --check` 全通过。
- `scripts/e2e_smoke.py` → 全部通过。

## v0.2.1

这一版修的是**最要命的一类问题：插件看着装上了，实际什么都没做**。
所有结论都在真实 AstrBot 4.28.1 上复现过，并配了回归测试。

### 修复

- **`on_llm_request` 被写成了 async generator，导致整个钩子从未执行（最严重）**。
  AstrBot 的 `call_event_hook()` 对 LLM 钩子只做 `await handler.handler(event, req)`，
  并不像普通事件管线那样 `async for` 迭代生成器。原实现在钩子里用
  `yield event.plain_result(...)` 返回超预算提示，于是函数变成 async generator，
  `await` 它直接抛 `TypeError: object async_generator can't be used in 'await' expression`。
  更糟的是 `call_event_hook()` 会把这个异常吞掉、只记一行 error，所以表现为
  **路由、记忆注入、图片保护、预算拦截全部静默失效**，而插件「加载成功」、
  测试也全绿（旧测试用 `async for` 驱动钩子，刚好把错误喂进了测试）。
  现在钩子改回普通协程，拦截提示走 `set_result` + `stop_event`，
  并新增 `tests/test_llm_hook_contract.py`：按 AstrBot 的方式 await 钩子，
  并用 AST 扫源码确保里面不再出现 `yield`。
- **超预算提示根本发不出去**：`event.plain_result()` 只是「构造」一个
  `MessageEventResult`，不会挂到事件上。必须 `event.set_result(...)` 才会真正发给用户，
  同时 `stop_event()` 也只有在结果已挂上时才会把 `result_type` 置为 STOP。
  现在两者都补上了。
- **`enabled_tasks` 完全没生效**：WebUI 里让用户勾选「接管哪些任务类型」，
  配置读进来了但没有任何代码消费它，等于勾选无效。现在钩子入口会先判断任务类型
  （`chat` / `agent` / `long_context` / `image`），未勾选的类型完全不动请求。
  同时把 `image` 加进默认列表 —— 否则「有图片就走视觉档」默认就是关闭的，
  用户很难从「任务白名单」联想到这一点。
- **事实抽取每轮都调一次模型**：`extract_facts` 原先没有任何节流，5 轮对话就是
  5 次额外的模型调用（摘要另算）。现在加了 180 秒时间节流 + 对话内容指纹去重，
  6 轮对话的额外调用从 6 次降到 2 次。
- **摘要触发条件依赖「历史是否读得到」**：轮数原先只在成功读到历史后才累加，
  会话刚建立或 DB 抖动时轮数永远不涨，摘要永远不触发。现在先累加轮数再读历史。
- **记忆衰减会随维护次数指数坍塌**：`decay()` 用 `now - updated_at` 当衰减区间，
  而 `updated_at` 在衰减时不会推进 —— 每跑一次维护就把同一个时间差再乘一遍。
  一条 1 天前、半衰期 30 天的记忆，本该只衰减一次到 0.977，实际会在连续维护中
  一路滑到 0.91、0.89……直至被当垃圾清掉。现在记录 `decayed_at`，衰减按
  「距上次衰减的时间」计算，**幂等**，与调用次数无关。
- **Studio 面板接口会把异常抛到 Web 层**：`api_sessions` 完全没有兜底，
  一次磁盘异常就让整个面板白屏；`api_memory` / `api_memory_clear` /
  `api_workflows` / `api_tools` / `api_stats` 也缺 try/except。现在统一返回
  500 + 错误信息，前端能优雅提示。
- **工作流在没有配置档位模型时直接失败**：`_workflow_candidates()` 只从档位配置里
  取模型，一个都没配就返回空列表，`run_workflow` 随即抛
  「没有可用的模型提供商」—— 而系统里明明有会话默认模型可用。
  现在会兜底到会话/全局默认模型，并过滤掉当前未加载的 provider。
- **`/ai` 指令的用量统计不记 token**：指令路径自己发起 LLM 调用、不经过
  `on_llm_response`，原实现只记「1 次请求」而不记 token，
  导致「按 token 计的每日配额」在指令路径上形同虚设。现在从
  `LLMResponse.usage` 取真实的输入 / 缓存命中 / 输出 token。
- **`JsonStore` 会把 key 拼进文件名**：`router.json` + key `router_health`
  会生成 `router_router_health.json`，难读且容易被下游当成新文件。
  现在一个 `JsonStore` 对应一个文件，key 只作为文件内的分区。
- **`logo.png` 带白色背景**：不透明白底在 Studio 面板的深色主题上非常刺眼。
  已替换为设计师提供的**无背景**版本，并重新编码（去白边、裁掉空白边距、
  补回透明像素的抗锯齿颜色），插件图标与 Studio 面板共用同一份资源。

### 变更

- 配置项与代码一一对应：删除从未被读取的 `agent.stream`（流式由 AstrBot 主流程控制），
  移除 `router.needs_web()` / `ConfigStore.int_option()` / `relative_tier()` /
  `describe_tools()` / `PlaceholderTool` / `task_group()` / `dumps()` 等
  无人调用的死代码。
- `knowledge_base.score_threshold` 真正生效：低于相关度门槛的片段不再进上下文。
- `router.needs_web()` 真正生效：问到时效性问题且联网工具可用时，
  注入 `<web_search_hint>` 建议模型主动检索。
- `enabled_tasks` 默认值补上 `image`（同步更新 `_conf_schema.json`）。
- Studio 面板改用 `pages/studio/logo.png`（与插件图标同源），移除手绘 `logo.svg`。
- `requirements-dev.txt` 增加 `Pillow`（测试需要校验 Logo 的透明通道），
  并给 CI 依赖自检加上 `PIL -> pillow` 的包名映射。
- 测试从 106 项增至 **121 项**，新增钩子契约、Studio 容错、记忆衰减幂等、
  Logo 透明通道等回归用例。

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
