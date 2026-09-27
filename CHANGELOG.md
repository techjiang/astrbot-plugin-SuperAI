# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## v0.2.8

这一版修的是**发布流程本身**的缺陷，而不是插件运行逻辑。

### 修复（安全）

- **发布包曾混进本地运行时文件（含凭据）**。
  发现渠道：在核验 v0.2.7 的 GitHub Release 附件时，把包下载回来解压检查，
  发现包内含有 `data/cmd_config.json`（内含 dashboard 密码哈希）与
  `data/data_v4.db`（完整运行库）。

  根因是 `scripts/build_plugin_zip.sh` 直接 `tar` **工作区**。
  `data/` 虽然被 `.gitignore` 忽略，但只要维护者在打包前本地跑过一次插件或
  端到端联调，工作区里就会留下这些文件，于是被一并打进发往
  AstrBot 官方市场的 ZIP —— 既泄露本机配置，又给用户凭空塞了几百 KB 垃圾。

  修复：打包来源改为 **`git ls-files` 的已提交文件**（按白名单前缀收集）。
  依赖「记得加 `--exclude`」是不可靠的 —— 下次新增运行时目录就会重演；
  改为问 git 要清单，`.gitignore` 天然兜底。

- `build_plugin_zip.sh` 内新增两道**显性自检**（不再静默产出坏包）：
  收集到的文件数过少直接失败；产物中检出 `data/`、`.db`、`cmd_config.json`
  等禁止内容直接失败。

### 测试（+4 项）

`tests/test_repo_health.py` 新增发布包内容契约：

- 禁止运行时产物（数据库 / 配置 / 缓存 / 测试 / 文档）；
- 必需文件齐全（入口、元数据、图标、schema、各子包、面板、i18n）；
- 包内版本与仓库一致（防止打出旧包）；
- 打包脚本必须由 git 清单驱动（根因防线）。

实测：旧脚本会打进 7 个运行时条目；新脚本产出 38 项、零泄漏。

### 同步

- 版本号 v0.2.7 → v0.2.8；新增 `docs/releases/v0.2.8.md`。
- **v0.2.7 的 Release 附件已替换为重新构建的干净包**（该版本插件功能
  本身没有问题，仅发布附件需要更新）。

## v0.2.7

这一版修掉 5 处「不报错、不崩溃，但功能静默失效」的问题。它们的共同根源是
**没有任何测试守住「配置与上游数据的形状」**：框架只用 `_conf_schema.json`
生成默认值，不做类型校验；工具的上游（知识库检索器、SearXNG）也不保证
字段类型。插件只要直接 `int()` / `float()`，就会在真实部署里炸出异常 ——
而异常又被框架的钩子调用器吞掉，界面上一片正常。

### 修复（功能性）

- **数字配置项填成非数字时，插件「加载成功」但每轮静默失效**。
  `on_llm_request` 里有 `int(self.config.router.get("long_context_tokens") or 64000)`
  这一行在**每条消息**上执行；用户把该字段填成 `64k` 时抛
  `ValueError: invalid literal for int()`。异常被框架的 `call_event_hook()`
  捕获后只打一段 traceback（见 `astrbot/core/pipeline/context_utils.py`），
  于是**路由、记忆注入、预算拦截全部静默失效**，而插件看起来一切正常。
  在 `__init__` 路径上则直接导致插件**加载失败**（`max_steps` /
  `retention_days` 被填错时），被 `PluginManager` 记进 `failed_plugin_dict`。
  现在所有面向用户配置的数字转换统一走 `as_int` / `as_float`
  （容错解析 + 区间夹紧），共覆盖 11 处调用点。

- **「最近 N 天」用量统计会跨月取数**。
  `MetricsCollector.range_stats()` 此前是 `sorted(self._days)[-N:]`，
  取的是「最近 N 个**有数据的日期**」而不是自然日区间。只要日期有断档
  （用户停用几天、机器关机、或 `retention_days` 调大后重新统计），
  取到的就是 `['2026-03-11', '2026-09-27']` 这种跨季度的桶 ——
  `/superai stats 7`、Studio 面板与 `summary()` 会把半年前的用量算进来，
  数字凭空翻倍而界面上看不出异常。现在按自然日补全区间，区间内没有数据
  的日期返回**临时空桶**（不写入 `_days`，不会挤占 `retention_days` 名额），
  趋势的 x 轴因此是完整时间轴。

- **知识库工具会把本可用的检索结果整段丢弃**。
  `retrieve_kb()` 里对相关度解析了两次，第二次写成裸
  `float(item.get("score") or 0)`。上游给出非数字相关度时抛
  `ValueError`，工具返回「工具执行失败：could not convert string to float: 'high'」——
  **检索到的内容全部丢失**。现在只解析一次并复用结果，非数字降级为 `0.00`。

- **联网搜索的配置错误与 JSON 解析失败会穿透**。
  `WebSearchTool.run_tool()` 只捕获 `aiohttp.ClientError` / `TimeoutError`：
  选了 `searxng` 却没填地址抛 `ValueError`；超时字段填成非数字时
  `float()` 抛异常；SearXNG 被反代拦截返回 HTML 时 `json()` 抛
  `JSONDecodeError`。现在全部转成用户可读的文本提示。

- **网页抓取工具的同类问题**。
  `FetchUrlTool` 的 `timeout` / `max_chars` 同样来自配置，
  统一改用 `as_float` / `as_int`。

### 测试（+34 项）

- **`tests/test_config_robustness.py`（12 项）**：配置读取必须容错；
  其中 `test_no_unguarded_numeric_conversion_of_config` 是一道
  **结构性闸门** —— 它扫描全部插件源码，任何新出现的裸
  `int(cfg.get(...))` / `float(cfg.get(...))` 都会被 CI 直接拦下，
  因此这类问题无法「换个写法」再溜进来。
- **`tests/test_tool_robustness.py`（19 项）**：知识库与联网工具在上游
  返回非数字相关度、后端异常、缺少管理器、URL 不合法、超时字段非法等
  情形下都必须优雅降级。
- **`tests/test_metrics.py`（+3 项）**：`range_stats` 必须按自然日补全区间、
  区间外数据不得计入总览、补全空桶不得持久化。

### 同步

- 版本号 v0.2.6 → v0.2.7：`superai/version.py`、`metadata.yaml`、README 徽章，
  以及全部 17 篇文档的「最后核对」标注；新增 `docs/releases/v0.2.7.md`
  与发布说明索引。

### 无破坏性变更

配置项、数据格式、指令签名与 v0.2.6 完全一致。统计口径的修正会让
「最近 N 天」的数字**变小**（此前含区间外数据），这是预期行为。

## v0.2.6

这一版修一个**用户一眼就能看见、但代码不会报错**的问题：插件市场卡片上的
「作者」显示成了平台账号，而不是插件作者；同时把 Logo 的解析链路补上契约测试 ——
此前没有任何测试保证「图标真的能被框架解析出来」。

### 修复

- **市场卡片作者信息错误**。`metadata.yaml` 的 `author` 此前是平台账号 `cosc`，
  而 AstrBot 插件市场卡片的「作者」直接读这个字段，于是商店里显示的是
  「作者：cosc」，真正的插件作者「科技酱」只出现在 README 里，两处说法打架。
  现在 `author` 统一为作者「科技酱」的包身份 `TechSauce=。
  README 的「关于作者」章节同时写明展示名与包身份，并说明两者为何不同。

- **Logo 的解析链路此前没有任何测试守住**。框架不会主动去找图片 ——
  `PluginManager._get_plugin_logo` 在插件目录**根下**按
  `logo.png` → `logo.jpg` → `logo.jpeg` → `logo.webp` → `logo.svg`
  的顺序做一次 `os.path.exists` 探测，找不到就回落到默认图标，
  **一条日志都不打**。于是「图标放错位置」「改名成 logo.svg」
  「JPEG 存成 logo.png」「PNG 被截断」这几类问题都只能等用户反馈。
  现在 `tests/test_logo_and_metadata.py` 把整条链路固化成 21 项断言。

### 增强

- **新增 `tests/test_logo_and_metadata.py`（21 项）**，覆盖两类契约：
  - *Logo 解析链路*：图标在框架查找路径上、文件名与真实格式一致（禁止
    「JPEG 改名 PNG」「SVG 改名 PNG」）、Pillow 可解码、正方形且分辨率 ≥256、
    透明像素占比 ≥20%（防止把白底图当无背景图用）、PNG 结构完整
    （`IHDR` 在最前、`IEND` 存在且 CRC 正确）、根目录不留多份图标变体、
    面板图标是同一份美术资源的缩放版、`index.html` 引用的文件确实存在。
  - *商店元数据*：`author` 是插件作者而非平台账号、与 README「关于作者」
    一致、`display_name` / `short_desc` / `desc` 长度合规、`tags` 无重复无空项、
    `repo` / `social_link` 是干净的 HTTPS 地址、`repo` 指向插件发布仓库、
    版本号是纯三段式语义化版本、YAML 无 Tab 缩进且使用 LF。
- **`metadata.yaml` 与文档同步**：版本号 v0.2.5 → v0.2.6，README 徽章与
  16 篇文档的「最后核对」标注同步（三方一致性由测试守住）。
- **`docs/install.md` 明确图标的落位要求**：写清文件名候选与「必须在插件目录根下」。

### 说明

- `author` 参与 `plugin_id = author + "/" + name`，是插件在市场里的**全局唯一标识**，
  也是已安装插件匹配更新的依据。本次修正发生在插件尚未通过市场审核、
  没有存量用户之前；**发布之后不得再改**，否则老用户会收不到更新。
  测试里对此有明确注释与断言。
- **无破坏性变更**：配置项、数据格式、指令签名与 v0.2.5 完全一致。

## v0.2.5

这一版把**发布通道打通**：`repo` 从 CNB 地址改为 GitHub 仓库，插件可以正式提交
AstrBot 官方插件市场；同时把 v0.2.4 的修复合并进 `main`，形成可发布的稳定基线。

### 变更

- **`metadata.yaml` 的 `repo` 改指 GitHub**：
  `https://github.com/techjiang/astrbot-plugin-SuperAI`。
  官方发布入口 <https://cloud.astrbot.app/publish> 只支持 GitHub 仓库（GitHub App
  授权后读取仓库里的 `metadata.yaml`）或 ZIP 上传，`repo` 必须是 GitHub 地址才
  能被提交与索引 —— 市场现有的插件 `repo` 全部是 `github.com/<owner>/<repo>`。
- **版本号 v0.2.4 → v0.2.5**。`superai/version.py`、`metadata.yaml`、README 徽章与
  各篇文档的「最后核对」标注同步更新（三方一致性由测试守住）。
- **`main` 合并 v0.2.4**（PR #4）。此前 v0.2.4 的 tag 打在功能分支的提交上，
  `main` 仍停留在 v0.2.3；现在 `main` 即发布基线。

### 说明

- 本仓库仍托管在 CNB，日常开发、CI 与 Release 都在 CNB；GitHub 仓库作为
  **发布镜像**，用于官方商店提交与 GitHub 用户安装。
- **无破坏性变更**：配置项、数据格式、指令签名与 v0.2.4 完全一致。

## v0.2.4

这一版做两件事：**按要求修正 AstrBot 官方商店发布信息**，以及
**再一次深挖「静默失效」类缺陷** —— 这轮找到了一个比之前所有问题都更隐蔽的
Bug：它只影响「已经产生过摘要」的会话，而且**永远不会报错、永远不会写日志**。

### 修复（功能性）

- **自动事实抽取对「任何产生过摘要的会话」永久失效**（本轮最严重）。
  ``_prepare_memory`` 里曾经写成：

  ```python
  summary = await self.memory_service.maybe_summarize(session, history)
  if summary:
      return summary  # ← 这一行是元凶
  if self._should_extract_facts(session, history):
      ...  # 后台抽取事实
  ```

  ``maybe_summarize`` 在**未达到摘要阈值**时会把**已有摘要**原样返回
  （这是它设计上的行为：调用方需要拿到「当前摘要」）。而摘要一旦生成过
  就一直非空 —— 于是后面那段在几乎所有轮次里都不可达。

  表现：用户的长期记忆只能靠手动 ``/superai memory add`` 或模型调用
  ``superai_remember`` 工具来写；「自动抽取偏好与事实」这个主打功能
  **一次都不会执行**。日志干净、单测全绿、CI 通过，只有把
  「摘要非空」与「应触发抽取」两个条件放在一起测才能发现。

  现在改为无条件执行抽取判断（是否真的抽取由 ``_should_extract_facts``
  的节流逻辑决定），并新增结构性断言防止该写法被重新引入。

- **只读统计接口会凭空造出「幽灵日期」，污染趋势与保留窗口**。
  ``MetricsCollector.today_stats()`` 经由 ``_bucket()`` **无条件**
  ``self._days[key] = stats``，而它是被 ``/superai status``、Studio 面板与
  每日预算检查调用的**只读**接口。后果：

  1. 只要用户打开过面板，即使当天一条消息都没有，也会多出一个
     「0 请求」的日期；
  2. 它会挤占 ``retention_days`` 的保留名额，把真正的历史数据挤出裁剪窗口；
  3. 「最近 N 天趋势」里出现无意义的 0 值空洞。

  现在 ``_bucket(create=False)`` 只读返回临时桶（不落盘、不进 ``_days``），
  写入路径行为不变。

- **``MemoryStore.search()`` 的 ``min_relevance`` 是死参数**。
  它写在签名里、写在文档里（还附带一大段关于「稳定偏好」的解释），
  但实现中**从未被读取** —— 调用方以为过滤生效了，实际拿到的是
  「权重最高的若干条」。现在两条返回路径都会真正应用它：
  ``> 0`` 时只回落到 ``preference`` 类稳定偏好。

- **``router.ordered_candidates()`` 绕开了「不健康」判定里的一道保护**。
  ``is_unhealthy()`` 的语义是「连续失败达到阈值，**而且还有别的可用 provider**」
  （否则所有模型都在报错时会把全部候选都判为不健康，反而失去意义）。
  ``ordered_candidates`` 调用时没有传 ``available_ids``，等于绕开了这道保护。
  虽然排序结果碰巧相同，但语义已经错了 —— 单模型部署下唯一可用的模型
  会被标记为「不健康」。现在把链上实际候选作为 ``available_ids`` 传入。

### 修复（发布信息 / AstrBot 官方商店）

- **``metadata.author`` 改回 ``cosc``**。
  AstrBot 插件市场规范（Schema Version 1）把 ``plugin_id`` 定义为
  ``metadata.author + "/" + metadata.name``，且明确要求
  「``author`` 和 ``name`` 应该是**稳定的包身份值**，而不是展示名」。
  它是插件在市场里的全局唯一标识，也是已安装插件匹配更新的依据 ——
  改成展示名「科技酱」会让老用户无法收到更新。
  「科技酱」作为作者展示信息保留在 README 的「关于作者」章节。

- **补齐市场可选字段**：``tags``（8 个：AI / LLM / 模型路由 / 记忆 / 知识库 /
  Agent / 工作流 / 用量统计）与 ``social_link``（作者官网）。
  两者都会进入插件市场的**搜索与分类**索引（见
  ``dashboard/src/utils/pluginSearch.js``），不填就等于在市场里搜不到。

- **``astrbot_version`` 保持 ``>=4.5.7``**：已用 PEP 440 ``SpecifierSet``
  校验可被框架解析，且满足当前 AstrBot 4.28.1。

### 工程

- 新增 ``tests/test_framework_lifecycle.py``（21 项）：
  用**真实 ``PluginManager`` 完整加载插件**（发现入口 → import →
  读 metadata → 注册工具 → 绑定 self → ``initialize()``），
  再按框架语义驱动钩子与全部指令。它能拦住「每个局部单测都过、
  但整体集成后失效」的问题（例如 metadata 版本校验不过、
  指令注册了但 filter 匹配不到、``initialize()`` 抛异常导致插件被回滚）。
  缺少真实 AstrBot 源码时整体 skip。
- 新增 ``tests/test_memory_pipeline.py``（8 项）：记忆/摘要/事实抽取链路，
  含防止早退写法回归的结构性断言。
- ``tests/test_metrics.py`` / ``tests/test_router.py`` /
  ``tests/test_storage.py`` 共补 9 项针对上述 Bug 的回归测试。
- ``tests/test_repo_health.py`` 补 4 项：按官方市场规范校验
  ``metadata.yaml``（必填字段、``plugin_id`` 约束、URL 可达性、版本一致性、
  包身份稳定性）。

### 关于「发布到 AstrBot 官方商店」的说明

官方发布入口是 <https://cloud.astrbot.app/publish>，它只支持两种来源：

1. **GitHub 仓库**（通过 GitHub App 授权，读取仓库里的 ``metadata.yaml``）；
2. **ZIP 压缩包上传**。

按官方文档与市场规范，``repo`` 字段应当是 **GitHub 仓库地址**
（市场现有 1329 个插件的 ``repo`` 100% 是 ``https://github.com/<owner>/<repo>``）。
本仓库目前托管在 CNB，``repo`` 指向 CNB（真实可达、被 AstrBot 客户端
provider-neutral 的解析逻辑支持），**但无法直接用于官方商店提交**。

要上架需要作者提供 GitHub 仓库（例如 ``techjiang/astrbot_plugin_superai``），
届时把 ``metadata.yaml`` 的 ``repo`` 改指 GitHub 即可，其余字段已就绪。
这一项已记录在 ``docs/install.md`` 的发布说明中。

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
