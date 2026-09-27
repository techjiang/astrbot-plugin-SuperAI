# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## 未发布（文档体系）

只改文档与测试，**无任何运行时行为变更**，无需重载插件之外的额外操作。

### 增强

- **新增 `docs/` 文档体系**（20 篇，按使用者 / 开发者分流）：
  - 使用者：[安装与升级](docs/install.md)、[快速上手](docs/quickstart.md)、
    [配置手册](docs/configuration.md)、[指令手册](docs/commands.md)、
    [模型路由](docs/routing.md)、[记忆与摘要](docs/memory.md)、
    [工具与工作流](docs/tools-and-workflows.md)、
    [用量与成本控制](docs/usage-and-budget.md)、[Studio 面板](docs/studio.md)、
    [常见问题与排错](docs/faq.md)；
  - 开发者：[架构总览](docs/dev/architecture.md)、
    [开发与测试](docs/dev/development.md)、
    [AstrBot 集成契约](docs/dev/astrbot-contracts.md)、
    [发布流程](docs/dev/release.md)；
  - 另新增 [发布说明归档](docs/releases/README.md)。
- **README 重写**：从「长篇能力罗列」改为「定位 → 能力概览 → 快速开始 →
  文档导航」，把实现细节下沉到 `docs/`，并补充数据与隐私说明。
- 新增 [CONTRIBUTING.md](CONTRIBUTING.md)（分支/提交/测试/文档要求）
  与 [SECURITY.md](SECURITY.md)（漏洞报告渠道与既有安全边界）。

### 文档准确性复核

- 逐条对照代码修正 3 处「写得比实现漂亮」的描述：
  [SECURITY.md](SECURITY.md) 原写「面板不裸插动态值（不用 `innerHTML`）」，
  实际是「用了 `innerHTML`，但所有插值经过 `esc()` 转义」；
  [工具与工作流](docs/tools-and-workflows.md) 原来只说「异常被基类兜住」，
  未说明兜住的形态（记 error 日志 + 把失败文本作为工具结果返回），
  容易让人误以为异常被静默吞掉；
  [安装与升级](docs/install.md) 引用了项目里并不存在的环境变量。
- 每篇文档尾部新增「最后核对」标注，写明核对的版本与对照对象，
  下次维护时能直接知道该对着什么看。

### 测试

- 新增 `tests/test_docs_consistency.py`（14 项），把文档里的**可验证事实**与代码对齐：
  - 所有相对链接可解析、文档索引完整、README 有文档入口；
  - 文档中出现的 `/superai <子指令>` 全部真实注册（正反双向校验）；
  - 内置帮助文本提到的指令都在指令手册中；
  - 配置手册覆盖 `_conf_schema.json` 的全部 52 个配置项；
  - README 版本徽章 / `metadata.yaml` / `superai/version.py` 三方一致；
  - 文档里的插件目录名、数据目录名、Studio API 路径与代码一致；
  - FAQ 覆盖「静默失效」类典型故障。
- 上述 14 项之后又新增 4 项**文档准确性**断言（同一个文件）：
  - 每篇文档都有「最后核对」标注；
  - `SECURITY.md` 对面板渲染方式的描述与 `pages/studio/app.js` 实现一致
    （不得声称「不用 innerHTML」）；
  - 文档里出现的 `ASTRBOT_*` 环境变量必须在 `conftest.py` / `e2e_smoke.py` /
    `.cnb.yml` 里真实存在；
  - 文档对「工具异常怎么处理」的描述与 `SuperAITool.call()` 实现一致。

## v0.2.3 — 首个正式发布版本（Release）

首个正式 Release 于 2026-09-27 发布（tag `v0.2.3`，产物为 `main` 分支代码）。
在此之前仓库为空仓库，因此本版等价于「SuperAI 从零到可用」的完整交付：

- 七大能力全部就绪：多模型路由与自动降级、滚动摘要与长期记忆、Agent 工具集、
  工作流编排、用量统计与配额、`/ai` 与 `/superai` 指令组、SuperAI Studio 面板；
- 累计修掉 30+ 个真实缺陷，多数属于「单测全绿但线上静默失效」类型，
  并为其补齐契约测试与真实框架端到端联调；
- 质量门禁：`pytest` 154 passed（真实 AstrBot 4.28.1）、`ruff check` /
  `ruff format --check` 全通过、`scripts/e2e_smoke.py` 全通过、CI 三段全绿。

### 发布流程

- `feat/superai-core` 合并进 `main`（PR #2），`main` 自此为发布基线；
- Release 标签指向合并前的功能提交，保证标签内容只包含插件本体，
  不含仓库初始化提交（该提交内容为空的树）；
- 后续版本发布直接推 `main` 并打 tag，不再走 PR。

## v0.2.3

这一版继续在**真实 AstrBot 4.28.1** 上做端到端联调，又挖出四个
「不报错、不崩溃，但功能静默失效」的问题。它们的共同特点是
**单元测试全绿、日志没有任何异常**，只能靠与框架语义对齐的契约测试拦住。

### 修复（功能性）

- **`/superai memory` 子指令整体不可达**。
  插件里同时存在两个东西：用 `@filter.command_group("superai.memory")`
  注册的**指令组**，以及 `@superai_group.command("memory")` 注册的
  **同名兼容命令**。AstrBot 唤醒阶段对两者的匹配语义并不互斥 ——
  只要消息以 `superai memory` 开头，**两个 handler 都会命中**。
  于是 `/superai memory list` / `search` / `clear` / `stats`
  全部被那个兼容命令接走，它拿着 `action="list"`、`query=""` 自己跑掉，
  子指令组里的实现**一次都执行不到**，而整条链路一句错都不报。
  现在把 `memory` 改成挂在 `superai` 下的**真子组**
  （`@superai_group.group("memory")`），并删掉同名兼容命令
  （它本就没有存在的必要：子组的 `list` 默认行为已经覆盖了它）。

- **工具与插件的归属关系断裂**。
  `Context.add_llm_tools()` 会用
  `_resolve_tool_handler_module_path()` 从工具类的 `__module__` 反推归属。
  SuperAI 的工具类定义在 `superai/tools/*` 里，反推得到的是**顶层模块名**
  `superai.tools.memory_tools` —— 既不在 `star_map` 中，也不等于插件入口模块路径。
  后果是 `PluginManager._is_plugin_llm_tool()` 对所有 SuperAI 工具一律返回 `False`：
  在仪表盘上**停用 / 卸载插件时，这些工具不会被一起停用**；
  重新加载插件时也不会刷新工具的 `active` 状态。
  现在在 `add_llm_tools()` **之后**（这一点很关键，该 API 会覆盖写入的值）
  调用新增的 `_claim_tools()` 把 `handler_module_path` 统一改写成插件入口模块路径，
  并在 `initialize()` 里再兜一次（`StarManager` 激活阶段会按入口模块路径重绑）。

- **被拒绝的请求会污染耗时统计**。
  `on_llm_request` 在**所有提前 `return` 之前**就把「本轮起始时间」压进了
  `_request_started`，而 `on_llm_response` 只在真正跑完 LLM 时才会被调用。
  于是这三条路径都会**只进不出**地泄漏记录，每来一条消息漏一条：

  1. 群被 `commands.deny_groups` 拒绝；
  2. 任务类型不在 `enabled_tasks` 白名单里；
  3. 触发每日预算拦截。

  等真正需要统计时，`_pop_request_started` 会取到很久以前那条旧时间戳，
  单次延迟被算成**几小时** —— `/superai stats` 与 Studio 面板的
  「平均耗时」因此彻底失真。
  现在把压栈动作挪到所有提前 `return` 之后（那时才真正由 SuperAI 接管本轮），
  预算拦截路径显式回收刚写入的记录；并新增
  `REQUEST_STARTED_MAX_AGE`（1 小时）让 `_pop_request_started` 主动丢弃
  过期残留，即使将来又出现没配对的路径也不会算出荒谬数字。

- **只配了一个默认模型的用户，摘要与自动事实抽取静默失效**。
  这是**最常见**的部署方式：用户只想用记忆功能，不会去配 SuperRouter 的五档位。
  但「轻量任务」（滚动摘要 / 事实抽取 / 工作流步骤）的候选链此前只取
  「摘要专用模型 → `cheap` 档 → `strong` 档」，这种情况下是**空列表**，
  `agent.simple()` 立刻抛 `ProviderUnavailableError`，被 `summarize()` 吞掉后
  返回空串 —— 表现就是两个主打功能一个都不工作，而日志里只有一行
  「生成摘要失败：没有可用的模型提供商」，用户完全看不出哪里配错了。
  现在新增 `MemoryService.resolve_light_candidates()`：档位模型全都没配时，
  兜底到「会话 / 全局默认模型」；配了档位则仍然优先用档位模型（不给用户添乱）。
  同时给插件补了公开的 `fallback_provider_id()`，
  让 `superai` 包内的服务不必去碰私有方法。

- **`/superai route` 展示的降级链与真实路由不一致**。
  预览用的 primary 硬编码取 `strong` 档模型，当会话被固定为
  `cheap` / `reasoning` / `vision` 时，展示出来的链与真正会走的链不符，
  容易让人误以为配置没生效。现在改为跟随当前生效档位，
  并在档位没配模型时兜到会话默认模型。

### 新增

- `tests/test_command_and_tool_contract.py`（11 项）：把上面两个陷阱固化成契约。
  - 用**真实** `CommandFilter` / `CommandGroupFilter` 驱动一遍
    `/superai memory <子指令>`，断言命中的是真正的子指令，
    并断言 `superai` 组下不再存在同名 `memory` 命令；
  - 断言 `memory` 组的完整指令名是 `superai memory`
    （用 `@filter.command_group("superai.memory")` 会把组名注册成字面指令，
    与框架 `startswith` 的匹配语义对不上）；
  - 断言所有工具的 `handler_module_path` 等于插件入口模块路径，
    且框架的 `_is_plugin_llm_tool()` 返回 `True`；
  - 走一遍真实的 `FunctionToolExecutor` + `_PermissionGuardedTool`，
    确认改写归属**没有**破坏工具执行（回归保护）。
- `tests/test_plugin_smoke.py` 新增 4 项「轻量任务模型兜底」测试：
  断言档位全空时 `resolve_light_candidates()` 会兜底到会话默认模型、
  摘要**真的会去调模型**（而不是直接放弃）、配了档位时仍优先用档位模型、
  以及 `fallback_provider_id()` 作为公开入口可用。
- `tests/test_request_lifecycle.py`（7 项）：把「起始时间必须成对」固化成契约 ——
  逐条覆盖被拒绝的群 / 未启用的任务类型 / 超预算三条提前返回路径，
  断言它们**不留下**任何记录；再反向确认正常路径仍能取到真实起始时间、
  过期记录会被丢弃、弹出操作不会误伤其它会话，
  以及一轮完整请求记录下来的平均耗时是「毫秒级」而不是「小时级」。
- `scripts/e2e_smoke.py` 增补两组断言：指令路由可达性、工具归属。
  这两类问题单元测试用替身测不出来，必须由真框架联调守住。

### 增强

- Studio 面板与 `/tools` 接口现在会带上工具的中文用途说明
  （`api_tools` 新增 `items` 字段，`tools` 字段保持向后兼容），
  面板不再只显示一串英文工具名。
- 修正 `tests/stubs/astrbot/api/provider` 里 `LLMResponse` 的替身签名：
  真实实现是手写 `__init__` 而非 dataclass，替身写成 dataclass 会让
  `LLMResponse(role=..., completion_text=...)` 直接抛 `TypeError`，
  使测试在无 AstrBot 环境下以与真实行为无关的方式失败。

### 其他

- `tests/stubs/astrbot/api/event`：`.group(...)` 替身原先返回裸装饰器，
  无法支持「子指令组继续级联注册子指令」的写法（`@superai_group.group("memory")`
  之后再 `@superai_memory_group.command("list")`），会直接抛
  `AttributeError: 'function' object has no attribute 'command'`。已修正。

### 验证

- `pytest tests`（真实 AstrBot 4.28.1）→ **154 passed**
- `ASTRBOT_REF=/nonexistent pytest tests`（无 AstrBot 环境）→ 139 passed, 1 skipped
  （新增的契约测试依赖真实框架语义，无框架时整体跳过，避免「测试绿但没测到」）
- `ruff check .` + `ruff format --check .` → 全通过
- `scripts/e2e_smoke.py` → 全部通过（含新增的指令路由与工具归属断言）

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
