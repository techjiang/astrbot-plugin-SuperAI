# AstrBot 集成契约

**改代码前必读。** 下面每一条都是真实踩过的坑，违反任意一条都会导致
「单元测试全绿、日志没有异常、功能静默失效」。括号里是对应的防回归测试。

## 入口与 `self` 绑定

### 入口必须在插件根目录

AstrBot 的 `PluginManager._get_modules` 只认：

```python
if os.path.exists(os.path.join(path, d, "main.py")):
    module_str = "main"
elif os.path.exists(os.path.join(path, d, d + ".py")):
    module_str = d
else:
    logger.info(f"Plugin {d} has neither main.py nor {d}.py; skipping it.")
    continue
```

入口放在子目录时，日志只留一行 `skipping it.`，插件被静默跳过 ——
表面「装上了」，实际一个钩子都没注册。

（`tests/test_plugin_loader_contract.py`、`scripts/e2e_smoke.py`）

### 插件类与钩子必须在入口模块里

框架用 `metadata.module_path`（入口模块路径）调
`get_handlers_by_module_name()` 找处理器，命中后才执行：

```python
handler.handler = functools.partial(raw_handler, metadata.star_cls)
```

而处理器的 `handler_module_path` 记录的是**装饰器所在模块**。
若插件类定义在子模块、入口只是转口，两者永不相等 → 绑定不发生 →
调用时抛 `TypeError: SuperAIPlugin.on_llm_request() missing 1 required
positional argument: 'req'`，且异常被 `call_event_hook()` 吞掉只记一行 error。

**所以：插件类与所有 `@filter` 钩子放根 `main.py`，`superai/` 包只放纯逻辑。**

（同上；`test_plugin_loader_contract.py` 另有一条测试禁止入口使用相对导入）

## 图标与市场元数据

### 图标只认插件根目录的 `logo.png`

框架里是写死的（`astrbot/core/star/star_manager.py`）：

```python
self.logo_fname = "logo.png"  # 第 213 行
...
logo_path = os.path.join(plugin_dir_path, self.logo_fname)
if os.path.exists(logo_path):  # 第 1376 行
    metadata.logo_path = logo_path
```

注意三件事：

1. **只有一个文件名**。换成 `logo.svg` / `logo.jpg` 不会被识别 ——
   文件夹里放着也没用，框架根本不看。
2. **只做一次 `os.path.exists`**。找不到就回落到默认图标，
   **一行日志都不打**，所以「图标没显示」查日志查不出任何东西。
3. **必须是真 PNG**。把 SVG / JPEG 改扩展名成 `logo.png`，
   框架照文件名交出去，浏览器解码失败就是一个破图。

WebUI 那一侧是另一条链路：`PluginService.resolve_plugin_logo_url()`
把 `logo_path` 注册成临时 token，前端再请求 `/api/file/<token>`。
所以「插件列表图标」与 `pages/studio/logo.png`（静态路由）互不影响，
两条都要各自保证是有效 PNG。

（`tests/test_logo_and_metadata.py`：位置/文件名/大小写/真实格式/PNG 结构/
分辨率/透明背景/多余变体，共 12 项；`scripts/e2e_smoke.py` 用框架真实的
`logo_fname` 复刻查找过程）

### `metadata.yaml` 的 `author` 是商店卡片的「作者」

市场卡片直接读 `metadata.yaml` 的 `author`。它同时参与

```python
plugin_id = metadata.author + "/" + metadata.name
```

这是插件在市场里的**全局唯一标识**，也是已安装用户匹配更新的依据。

- 写成平台账号（如 `cosc`）→ 商店里显示错误的作者；
- 发布之后再改 → 老用户**收不到更新**，必须重新安装。

因此本项目约定：`author` 用作者「科技酱」的**包身份** `TechSauce`
（无空格、无中文，各平台解析行为一致），展示名「科技酱」写在 README 与
`display_name` / `desc` 里。两者的一致性由测试锁住。

（`tests/test_logo_and_metadata.py`、`tests/test_repo_health.py`、
`tests/test_framework_lifecycle.py`：都断言 `author == "TechSauce"`）

## LLM 钩子的形态

### 钩子必须是普通协程，不能是 async generator

`call_event_hook()` 对 `OnLLMRequestEvent` / `OnLLMResponseEvent` 只做
`await handler.handler(event, req)`，**不会** `async for` 迭代生成器。

钩子里只要有 `yield`，函数就变成 async generator，`await` 它会直接抛
`TypeError: object async_generator can't be used in 'await' expression`，
而异常被吞掉只记一行 error —— 结果是**路由、记忆注入、图片保护、
预算拦截全部静默失效**，插件表面「加载成功」。

因此拦截提示必须用 `event.set_result(...)` + `event.stop_event()`，而不是 `yield`。

（`tests/test_llm_hook_contract.py` 用 AST 扫源码固定此约束）

### `plain_result()` 只是「构造」结果

`event.plain_result(text)` 返回一个 `MessageEventResult`，**不会**挂到事件上。
要让用户真的收到话术，必须 `event.set_result(...)`。

## 指令与工具注册

### 指令组不要与同名命令并存

```
CommandGroupFilter  : message_str.startswith(group_names)
CommandFilter       : message_str.startswith(f"{cmd} ")
```

两者**不是互斥**的。同时注册 `superai` 组下的 `memory` 命令与 `memory` 子指令组时，
`/superai memory list` 会**同时**命中两个 handler，先跑的把事件消费掉，
另一个永远执行不到，而且一句错都不报。

子指令组必须用 `@superai_group.group("memory")` 挂在父组下面，
**不要**用 `@filter.command_group("superai.memory")` —— 后者会把组名注册成
字面指令 `superai.memory`，与框架 `startswith` 的匹配语义对不上。

（`tests/test_command_and_tool_contract.py`）

### 工具的归属模块会被 `add_llm_tools()` 重算

`Context.add_llm_tools()` 用 `_resolve_tool_handler_module_path()` 从工具类的
`__module__` 反推插件归属。工具类定义在 `superai/tools/*` 时反推结果是
顶层模块名 `superai.tools.xxx`，既不在 `star_map` 也不等于入口模块路径，
于是 `_is_plugin_llm_tool()` 一律返回 `False`：

- 仪表盘停用 / 卸载插件时，这些工具**不会被一起停掉**；
- 重载时也不刷新 `active`。

修复方式：**在 `add_llm_tools()` 之后**改写 `handler_module_path`
（之前写会被覆盖），并在 `initialize()` 里再兜一次
（`StarManager` 激活阶段会按入口模块路径重绑）。

（`test_command_and_tool_contract.py` 走真实 `FunctionToolExecutor` 验证）

## 请求与上下文

### 图片不能被清空

AstrBot 可能在 `on_llm_request` 之前完成图片压缩 / 转述，并把
`req.image_urls` 置空。SuperAI 在钩子入口**快照**原始图片，之后补回，
否则多模态请求会退化成纯文本。

### 不要注入「当前时间」

`extra_user_content_parts` 里只要多一个 part，provider 就会把纯字符串 user 消息
升级成多模态 content 数组，**自动前缀缓存随之失效**。

因此遵循「**没内容就不注入**」，默认也不带每轮都变的时间戳。

### 动态内容标记为「仅本轮有效」

注入的 `<superai_context>` 会调用 `mark_as_temp()`，不写进会话历史。
老版本 AstrBot 没有该能力时退化为只写 `system_prompt`。

### 历史分页方向

`get_human_readable_context` 的 `page=1` 是**最旧**的一页，取最近对话
必须先算总页数；`history` 长度恒等于一页大小，**不能**拿它当总轮数
（用 `summary.total_rounds` 累计）。

### 失败信号

AstrBot 用 `LLMResponse.role == "err"` 表示本轮调用失败。
SuperAI 据此统计失败数并给该 provider 记一次「不健康」。

## 模型与 provider

### 「轻量任务」必须兜底到默认模型

滚动摘要、事实抽取、工作流步骤都不走 SuperRouter 的档位选择，
而是走 `MemoryService.resolve_light_candidates()`。

只配了一个默认模型（最常见用法）时档位链是空的，必须回落到
「会话 / 全局默认模型」，否则这些功能直接抛 `ProviderUnavailableError`
并被上层吞掉 —— 表现是「功能全都不工作，但日志只有一行 warning」。

（`tests/test_config.py` / `test_storage.py` 覆盖候选链）

### 路由回落的优先级

只有「一个档位模型都没配」时才回落到会话默认模型，
否则用户配置的档位会被悄悄忽略。

## 存储与生命周期

### 衰减要与调用次数无关

维护循环周期性跑 `memory.decay()`，衰减量必须按「距上次衰减的时间」计算，
否则同一个时间差会被反复相乘，记忆权重指数坍塌。
SuperAI 记录 `decayed_at`，让衰减幂等。

（`tests/test_storage.py`）

### 耗时统计必须配对

`on_llm_request` 里记录起始时间的动作要放在**所有提前 `return` 之后**；
预算拦截路径需显式回收。另设 `REQUEST_STARTED_MAX_AGE`（1 小时）
丢弃过期残留，避免算出几小时的「平均耗时」。

（`tests/test_request_lifecycle.py`）

### 后台任务与有界容器

- 后台任务统一由 `_spawn()` 创建并收进 `_background_tasks`，卸载时回收；
- `_request_started` 用有界 deque，`_last_fact_extract` 有清理逻辑，
  避免长期运行内存增长。

## 检查清单

提交前自问：

- [ ] 新增的 `@filter` 钩子写在根 `main.py` 了吗？
- [ ] 钩子里没有 `yield` 吗（用 `set_result` + `stop_event`）？
- [ ] 新增子指令组用的是 `@xxx_group.group(...)` 而不是 `filter.command_group`？
- [ ] 新增工具后，`_claim_tools()` 还在 `add_llm_tools()` **之后**调用吗？
- [ ] 所有提前 `return` 的路径都不会泄漏 `_request_started` 吗？
- [ ] 新增三方 import 已写进 `requirements.txt` 吗？
- [ ] 换图标后还是 `logo.png`、真 PNG、放在插件根目录吗？
      （`python -m pytest tests/test_logo_and_metadata.py` 一把过）
- [ ] 改 `metadata.yaml` 的 `author` 了吗？——**发布后不要改**，会影响更新检测
- [ ] `ruff check .` / `ruff format --check .` / `python -m pytest tests` 通过了吗？

## 相关文档

- [开发与测试](./development.md)
- [架构总览](./architecture.md)

---

**最后核对**：`v0.2.7`（逐条对照 `tests/` 与测试断言，无凭空描述）
