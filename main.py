"""SuperAI 插件入口（AstrBot 加载器要求的 ``main.py``）。

**为什么插件类必须定义在这个文件里**：AstrBot 的 ``PluginManager`` 用
``metadata.module_path``（即插件入口模块，如
``data.plugins.astrbot_plugin_superai.main``）去 ``get_handlers_by_module_name()``
查找处理器，命中后才会执行

```python
handler.handler = functools.partial(raw_handler, metadata.star_cls)
```

把插件实例绑定成 ``self``。而处理器的 ``handler_module_path`` 记录的是
**装饰器所在模块**（``@filter.on_llm_request()`` 定义在哪个文件，就是哪个文件）。
如果插件类定义在子模块（例如 ``superai/plugin.py``）而入口只是转口，
两者的模块路径不一致，绑定就不会发生 —— 于是调用时 ``self`` 缺失，
抛 ``TypeError: SuperAIPlugin.on_llm_request() missing 1 required
positional argument: 'req'``，且异常被 ``call_event_hook()`` 吞掉只记一行 error，
表现为**路由 / 记忆注入 / 图片保护 / 预算拦截全部静默失效**。

因此：插件类与所有 ``@filter`` 钩子必须定义在本文件；``superai/`` 包只放
不依赖插件实例的纯逻辑（配置、路由、存储、工具、提示词等）。

SuperAI 是面向 AstrBot 的 AI 增强层，核心能力：

1. **SuperRouter**：多模型智能路由 + 失败降级，让每次请求都落到合适的模型上。
2. **SuperMemory**：滚动摘要 + 长期记忆自动抽取与注入，长对话不失忆、成本可控。
3. **SuperAgent**：注册联网搜索、记忆、知识库、工作流等 function calling 工具。
4. **知识库增强**：可选的 KB 自动检索注入。
5. **AI 快捷指令**：``/ai`` 指令。
6. **工作流编排**：把多步 AI 任务配置化。
7. **用量统计与成本控制**：token / 请求数预算拦截 + Studio 面板。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys

# AstrBot 以 ``data.plugins.<插件目录>.main`` 的形式导入插件入口，
# 此时插件目录本身不在 ``sys.path`` 上，包内实现（``superai/``）无法作为顶层包
# 导入。这里把插件目录显式加入 ``sys.path``；若已被上层以包形式导入
# （例如测试里 ``import astrbot_plugin_superai.main``），则跳过避免重复插入。
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import time
from collections import deque
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from superai.agent_runner import AgentExecutor
from superai.assets import (
    format_logo_report,
    format_logo_token_report,
    inspect_logo,
    probe_logo_token_service,
)
from superai.core.config import _as_bool, _as_float, _as_int, build_config
from superai.core.errors import ProviderUnavailableError, SuperAIError
from superai.core.metrics import MetricsCollector
from superai.core.utils import (
    estimate_tokens,
    fingerprint,
    normalize_space,
    strip_markup,
    truncate,
)
from superai.memory_service import MemoryService
from superai.prompt import (
    append_dynamic,
    apply_stable_prompt,
    build_dynamic_block,
    has_dynamic_content,
)
from superai.router.router import ALL_TIERS, TIER_DEFAULT, SuperRouter
from superai.storage.memory import MemoryStore
from superai.storage.store import JsonStore
from superai.storage.summary import SummaryStore
from superai.storage.workflows import Workflow, WorkflowStore
from superai.tools.registry import build_toolset
from superai.version import __version__

PLUGIN_NAME = "astrbot_plugin_superai"
DATA_DIR_NAME = PLUGIN_NAME

#: 一次请求最多注入多少条长期记忆
MAX_INJECTED_MEMORIES = 8

#: 单会话最多保留多少条「请求起始时间」记录，防止长期运行内存泄漏
MAX_PENDING_REQUESTS = 500

#: 记忆维护的节流间隔（秒），避免每轮对话都全量扫描
MAINTENANCE_INTERVAL = 600

#: 事实抽取的节流间隔（秒）。抽取要调一次模型，不节流的话每轮都会烧钱；
#: 同一条对话内容也会被指纹去重，所以间隔可以取得比较宽松。
FACT_EXTRACT_INTERVAL = 180

#: 单个会话最多保留多少条「上次事实抽取时间」记录
MAX_FACT_EXTRACT_SESSIONS = 2000

#: 可被 /superai route 指定的档位
SELECTABLE_TIERS = (*ALL_TIERS, TIER_DEFAULT)


def _top_pairs(data: Any, limit: int = 5) -> str:
    """把 ``{"key": count}`` 渲染成「key×count」的一行文本。"""
    if not isinstance(data, dict) or not data:
        return ""
    ordered = sorted(data.items(), key=lambda item: item[1], reverse=True)[:limit]
    return "，".join(f"{key or 'default'}×{value}" for key, value in ordered)


class SuperAIPlugin(Star):
    """SuperAI 主插件类。

    插件元数据以 ``metadata.yaml`` 为准（展示名、描述、版本、支持的平台等），
    因此不再使用已弃用的 ``@register`` 装饰器。
    """

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.context = context
        self._raw_config = config
        self.config = build_config(config)

        # --- 持久化 -----------------------------------------------------
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / DATA_DIR_NAME
        data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        self.memory = MemoryStore(JsonStore(data_dir / "memory.json"))
        self.summaries = SummaryStore(JsonStore(data_dir / "summary.json"))
        self.workflows = WorkflowStore(
            JsonStore(data_dir / "workflows.json"),
            # 配置值一律走容错解析：WebUI 里这些是文本框，用户填 "8步" /
            # "abc" 都会原样存下来（框架只用 schema 生成默认值，不做类型校验），
            # 直接 int() 会在插件初始化时抛异常，整个插件加载失败。
            max_steps=_as_int(self.config.workflow.get("max_steps"), 8, minimum=1),
        )
        self.metrics = MetricsCollector(
            data_dir,
            retention_days=_as_int(self.config.metrics.get("retention_days"), 30, minimum=1),
            debug=self.config.debug,
        )

        # --- 运行时状态 -------------------------------------------------
        self.router = SuperRouter(self.config, store=JsonStore(data_dir / "router.json"))
        self.agent = AgentExecutor(self)
        self.memory_service = MemoryService(self)

        self._session_cooldown: dict[str, float] = {}
        self._session_tier: dict[str, str] = {}
        self._last_fact_extract: dict[str, float] = {}
        """会话 -> 上次事实抽取时间，用于节流（否则每轮都调一次模型）"""
        self._last_fact_fingerprint: dict[str, str] = {}
        """会话 -> 上次抽取过的对话指纹，内容没变就不再抽"""
        self._request_started: deque[tuple[str, float]] = deque(maxlen=MAX_PENDING_REQUESTS)
        """(会话, 本轮请求起始时间)，用于统计耗时；有界以免长期运行泄漏"""

        self._maintenance_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        """后台任务集合，卸载时统一回收"""
        self._last_active_ts: int = int(time.time())
        self._last_maintenance: float = 0.0
        self._installed_hooks = False

        # 供工具通过 ``context._superai_plugin`` 反查插件实例
        context._superai_plugin = self

        # 注册 LLM 工具（>= v4.5.1 推荐方式）
        self._register_tools()

        # 注册 WebUI Studio 面板 API
        self._register_web_apis()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        """插件加载后的异步初始化。"""
        self._install_llm_hooks()
        # 重新声明工具归属：StarManager 会在激活阶段按入口模块路径重绑工具，
        # 这里再兜一次，保证「仪表盘停用插件 = 工具一起失效」成立。
        self._claim_tools(getattr(self, "_tools", []))
        # 清理老版本误写入统计文件的聚合键（形如 last_7_days）
        try:
            if self.metrics.prune_legacy_keys():
                self.metrics.flush()
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响启动
            logger.debug(f"[SuperAI] 清理历史统计数据失败：{exc}")

        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        self._report_asset_health()
        logger.info(
            f"[SuperAI] v{__version__} 已加载 | 路由={'开' if self.config.router_enabled else '关'} "
            f"记忆={'开' if self.config.memory_enabled else '关'} "
            f"联网={'开' if self.config.web_enabled else '关'} "
            f"知识库={'开' if self.config.kb_enabled else '关'} "
            f"Agent={'开' if self.config.agent_enabled else '关'} "
            f"工具={len(self.tool_names)} 个"
        )

    def _report_asset_health(self) -> None:
        """启动时自检图标等静态资源，并把结论写进日志。

        图标链路的共同点是**没有任何反馈**：框架找不到 ``logo.png`` 会静默
        回落到默认图标，商店详情页也不会告诉你「图片太大所以一直加载不出来」。
        用户能看到的只是「图标不显示」，而维护者从日志里查不到任何线索。

        这里把检查结果写进启动日志：正常时一行 info，异常时逐条列出症状 /
        原因 / 修复方向。检查本身不抛异常 —— 图标坏了不该影响插件工作。
        """
        try:
            level, message = format_logo_report(inspect_logo())
        except Exception as exc:  # noqa: BLE001 - 自检失败不影响插件功能
            logger.debug(f"[SuperAI] 图标自检未能完成：{exc}")
        else:
            if level == "warning":
                logger.warning(message)
            else:
                logger.info(message)

        # 图标文件本身没问题，却「刷新后变成默认星形」——这是框架侧的一次性令牌
        # 问题（file_token_service.handle_file 用 pop 消费令牌，见 assets.py 说明）。
        # 插件改不了框架渲染的列表卡片，但至少能让日志一眼指向根因。
        try:
            token_report = format_logo_token_report(probe_logo_token_service())
        except Exception as exc:  # noqa: BLE001 - 探测失败不影响插件功能
            logger.debug(f"[SuperAI] 图标令牌探测未能完成：{exc}")
            return
        if token_report is not None:
            logger.warning(token_report[1])

    async def terminate(self) -> None:
        """插件卸载 / 停用时清理资源。"""
        task, self._maintenance_task = self._maintenance_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        # 回收所有后台任务（记忆抽取等），避免卸载后仍持有引用
        pending = [item for item in self._background_tasks if not item.done()]
        for item in pending:
            item.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._background_tasks.clear()

        self.metrics.flush()
        logger.info("[SuperAI] 已卸载，统计数据已落盘。")

    def _spawn(self, coro: Any, *, name: str = "superai-bg") -> asyncio.Task:
        """启动一个受管的后台任务。"""
        task = asyncio.ensure_future(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        def _log_failure(finished: asyncio.Task) -> None:
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                logger.debug(f"[SuperAI] 后台任务 {name} 异常：{exc}")

        task.add_done_callback(_log_failure)
        return task

    async def _maintenance_loop(self) -> None:
        """后台维护：定期落盘统计、衰减记忆。"""
        while True:
            try:
                await asyncio.sleep(MAINTENANCE_INTERVAL)
                await self.run_maintenance()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 后台任务不可崩溃
                logger.warning(f"[SuperAI] 后台维护任务异常：{exc}")

    async def run_maintenance(self, *, force: bool = False) -> None:
        """执行一次维护：落盘 + 记忆衰减。"""
        now = time.time()
        if not force and now - self._last_maintenance < 60:
            return
        self._last_maintenance = now
        self.metrics.flush()
        if self.config.long_term_enabled:
            self.memory.decay(
                half_life_days=_as_float(self.config.memory.get("decay_half_life_days"), 30.0)
            )

    # ------------------------------------------------------------------
    # 工具注册
    # ------------------------------------------------------------------
    def _register_tools(self) -> None:
        """把 SuperAI 工具注册到 AstrBot。

        ``context.add_llm_tools()`` 会用 ``_resolve_tool_handler_module_path()``
        从工具类的 ``__module__`` 反推「插件归属」。SuperAI 的工具类定义在
        ``superai/tools/*`` 子模块里，反推结果是 ``superai.tools.memory_tools``
        这种**顶层模块名**，既不在 ``star_map`` 中，也不等于插件入口模块路径。

        后果（都会静默发生，不报错）：

        - ``PluginManager._is_plugin_llm_tool()`` 判定为「不属于任何插件」，
          于是在仪表盘里禁用/卸载本插件时，这些工具**不会被一起停用**，
          重新加载插件时也不会刷新 ``active`` 状态；
        - ``_plugin_tool_fix()`` 用 ``star_map.get(mp)`` 查不到归属，只好保守保留，
          工具虽然还能被调用，但插件的「工具清单」与实际注册状态脱节。

        这里在注册前显式把 ``handler_module_path`` 统一改成插件入口模块路径，
        与 ``@filter`` 钩子的约束保持同一个原则。
        """
        try:
            toolset = build_toolset(self.config)
            tools = list(toolset.tools)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] 工具装配失败：{exc}", exc_info=True)
            tools = []

        if tools:
            try:
                self.context.add_llm_tools(*tools)
            except Exception as exc:  # noqa: BLE001 - 老版本可能不支持
                logger.warning(f"[SuperAI] 注册 LLM 工具失败：{exc}")

        # 必须在 add_llm_tools **之后** 修正：该 API 内部会用
        # _resolve_tool_handler_module_path() 重新赋值 handler_module_path，
        # 之前写进去的值会被它覆盖掉。
        self._claim_tools(tools)
        self._tools = tools

    def _claim_tools(self, tools: list[Any]) -> None:
        """把工具的归属模块改成本插件入口模块（幂等，可重复调用）。

        见 :meth:`_register_tools` 的说明：工具类定义在 ``superai/tools/*``
        子模块时，AstrBot 反推出的归属路径是顶层模块名 ``superai.tools.xxx``，
        既不在 ``star_map`` 里，也不等于插件入口模块，于是
        ``PluginManager._is_plugin_llm_tool()`` 一律返回 ``False`` ——
        仪表盘停用/卸载插件时这些工具不会被一起停掉，重载时也不会刷新 ``active``。

        另外必须在 :meth:`initialize` 里再调一次：AstrBot 的
        ``StarManager`` 会在插件激活时用入口模块路径做一次「重新绑定」，
        需要 ``handler_module_path`` 已经是入口模块才能命中那个分支。
        """
        entry_module = self.__class__.__module__
        for tool in tools:
            try:
                tool.handler_module_path = entry_module
            except Exception:  # noqa: BLE001 - 个别工具可能是只读属性
                logger.debug(f"[SuperAI] 无法修正工具 {getattr(tool, 'name', '?')} 的归属模块")

    @property
    def tool_names(self) -> list[str]:
        return [tool.name for tool in getattr(self, "_tools", [])]

    def _register_web_apis(self) -> None:
        """注册 Studio 面板后端 API。"""
        routes = [
            (f"/{PLUGIN_NAME}/status", self.api_status, ["GET"], "SuperAI 运行状态"),
            (f"/{PLUGIN_NAME}/stats", self.api_stats, ["GET"], "SuperAI 用量统计"),
            (f"/{PLUGIN_NAME}/memory", self.api_memory, ["GET"], "查询会话记忆"),
            (f"/{PLUGIN_NAME}/memory/clear", self.api_memory_clear, ["POST"], "清空会话记忆"),
            (f"/{PLUGIN_NAME}/tools", self.api_tools, ["GET"], "已注册工具列表"),
            (f"/{PLUGIN_NAME}/workflows", self.api_workflows, ["GET"], "工作流列表"),
            (f"/{PLUGIN_NAME}/sessions", self.api_sessions, ["GET"], "有数据的会话列表"),
        ]
        for route, handler, methods, desc in routes:
            try:
                self.context.register_web_api(route, handler, methods, desc)
            except Exception as exc:  # noqa: BLE001 - 旧版本可能不支持
                logger.debug(f"[SuperAI] 注册 Web API {route} 失败：{exc}")

    # ------------------------------------------------------------------
    # 基础辅助
    # ------------------------------------------------------------------
    def last_active_ts(self) -> int:
        """返回最近一次活跃时间戳。"""
        return self._last_active_ts

    def _available_provider_ids(self) -> set[str]:
        """当前已加载的 provider id 集合。"""
        try:
            providers = self.context.get_all_providers()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[SuperAI] 获取模型列表失败：{exc}")
            return set()
        result: set[str] = set()
        for provider in providers or []:
            try:
                pid = provider.meta().id
            except Exception:  # noqa: BLE001 - 个别 provider 元数据异常
                continue
            if pid:
                result.add(str(pid))
        return result

    async def fallback_provider_id(self, session: str = "") -> str:
        """返回「会话默认 / 全局默认」的 provider id（公开给其它模块用）。

        这是没有任何档位模型可用时的最后兜底。与
        :meth:`_current_provider_id` 是同一个实现，只是给 ``superai`` 包内的
        服务留一个稳定的公开入口，避免它们直接依赖私有方法。
        """
        return await self._current_provider_id(session)

    async def _current_provider_id(self, session: str) -> str:
        """获取会话当前使用（或全局默认）的 provider id。"""
        if session:
            try:
                pid = await self.context.get_current_chat_provider_id(umo=session)
            except Exception:  # noqa: BLE001 - 会话可能还没有绑定模型
                logger.debug("[SuperAI] 未找到会话级默认模型，尝试用全局默认模型")
                pid = ""
            if pid:
                return str(pid)
        providers = self.context.get_all_providers() or []
        for provider in providers:
            try:
                pid = provider.meta().id
            except Exception:  # noqa: BLE001
                continue
            if pid:
                return str(pid)
        return ""

    def _cooldown_ok(self, session: str) -> bool:
        """指令冷却检查。"""
        cooldown = _as_int(self.config.commands.get("cooldown_seconds"), 0)
        if cooldown <= 0:
            return True
        now = time.time()
        last = self._session_cooldown.get(session, 0.0)
        if now - last < cooldown:
            return False
        self._session_cooldown[session] = now
        # 顺手清理过期的冷却记录，避免长期运行累积
        if len(self._session_cooldown) > MAX_PENDING_REQUESTS:
            for key, ts in list(self._session_cooldown.items()):
                if now - ts > cooldown * 10:
                    self._session_cooldown.pop(key, None)
        return True

    def _extract_image_urls(self, event: AstrMessageEvent, req: ProviderRequest) -> list[str]:
        """取出本轮请求关联的图片地址。

        优先使用 ``ProviderRequest.image_urls``（AstrBot 已完成预处理），
        失败时再兜底扫描消息链。
        """
        urls: list[str] = []
        for item in getattr(req, "image_urls", None) or []:
            text = str(item or "").strip()
            if text:
                urls.append(text)
        if urls:
            return list(dict.fromkeys(urls))

        try:
            import astrbot.api.message_components as Comp
        except ImportError:  # pragma: no cover - 老版本
            return []
        for component in event.get_messages():
            if isinstance(component, Comp.Image):
                url = (
                    getattr(component, "url", "")
                    or getattr(component, "file", "")
                    or getattr(component, "path", "")
                )
                if url:
                    urls.append(str(url))
        return list(dict.fromkeys(urls))

    async def _history_texts(
        self, event: AstrMessageEvent, limit: int = 40, *, latest: bool = True
    ) -> list[str]:
        """异步取回会话历史（人类可读文本）。

        参数:
            limit: 最多返回多少条。
            latest: ``True`` 表示取**最新**的一页。

        注意：AstrBot 的 ``get_human_readable_context`` 是「按页」返回的，
        ``page=1`` 拿到的是**最旧**的一页。想拿最近的对话必须先算总页数，
        否则摘要会一直基于很久以前的内容，长期记忆也抽不到新信息。
        """
        session = event.unified_msg_origin
        manager = self.context.conversation_manager
        try:
            conversation_id = await manager.get_curr_conversation_id(session)
            if not conversation_id:
                return []
            page = 1
            if latest:
                _, total_pages = await manager.get_human_readable_context(
                    session, conversation_id, page=1, page_size=max(1, limit)
                )
                page = max(1, int(total_pages or 1))
            texts, _ = await manager.get_human_readable_context(
                session, conversation_id, page=page, page_size=max(1, limit)
            )
            return [normalize_space(text) for text in texts if text]
        except Exception as exc:  # noqa: BLE001 - 历史读取失败不影响对话
            logger.debug(f"[SuperAI] 读取会话历史失败：{exc}")
            return []

    # ------------------------------------------------------------------
    # LLM 钩子安装
    # ------------------------------------------------------------------
    def _install_llm_hooks(self) -> None:
        """记录 LLM 钩子是否已就绪（供状态接口展示）。"""
        try:
            from astrbot.core.star.star_handler import EventType, star_handlers_registry

            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.OnLLMRequestEvent
            )
            self._installed_hooks = any(
                getattr(item, "handler_name", "") == "on_llm_request"
                and getattr(item, "handler_module_path", "") == self.__class__.__module__
                for item in handlers
            )
        except Exception as exc:  # noqa: BLE001 - 仅用于状态展示，失败无所谓
            logger.debug(f"[SuperAI] 检查 LLM 钩子失败：{exc}")
            self._installed_hooks = False

    # ------------------------------------------------------------------
    # LLM 钩子：路由 + 记忆注入
    # ------------------------------------------------------------------
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """在请求 LLM 前完成：预算检查 → 记忆/摘要注入 → 模型路由。

        注意：这里**必须是普通协程**，不能写成 async generator。
        AstrBot 的 ``call_event_hook()`` 对 LLM 钩子只做 ``await handler(event, req)``
        （见 ``astrbot/core/pipeline/context_utils.py``），并不像普通事件管线那样
        ``async for`` 迭代生成器。如果这里出现 ``yield``，函数会变成 async
        generator，``await`` 它会直接抛 ``TypeError: object async_generator
        can't be used in 'await' expression``，被 ``call_event_hook`` 静默吞掉，
        结果是**路由、记忆注入、图片保护全部失效**而日志里只有一行 error。
        """
        if not self.config.enabled:
            return
        self._last_active_ts = int(time.time())

        session = event.unified_msg_origin

        if not self.config.group_allowed(event.get_group_id(), is_private=event.is_private_chat()):
            return

        # 0) 保存原始图片，避免 AstrBot 的图片预处理把 image_urls 清空后
        #    多模态请求退化成纯文本（见 README「与 AstrBot 的协作」一节）
        original_images = list(getattr(req, "image_urls", None) or [])

        # 0.1) 按「任务类型」开关决定本轮是否接管。
        #      enabled_tasks 是 WebUI 里给用户的任务白名单；此前虽然读进了配置，
        #      但没有任何代码消费它，等于用户勾选无效。
        if not self.config.is_task_enabled(self._task_kind(req, original_images)):
            return

        # 0.2) 到这里才认为「本轮由 SuperAI 接管」，记录起始时间用于统计耗时。
        #      必须放在所有提前 return 之后：``on_llm_response`` 只在真正跑完
        #      LLM 时才会被调用，提前返回的分支永远不会把记录弹出。
        #      之前把 append 放在最前面，被拒绝的群 / 未启用的任务类型每来一条
        #      消息就泄漏一条记录；等真正需要统计时，``_pop_request_started``
        #      会拿到很久以前的旧时间戳，单次延迟被算成几小时，
        #      ``/superai stats`` 与 Studio 面板的「平均耗时」因此彻底失真。
        self._request_started.append((session, time.time()))

        # 1) 预算检查
        self.metrics.flush()
        over_budget = self.metrics.check_budget(
            token_budget=_as_int(self.config.metrics.get("daily_token_budget"), 0),
            request_budget=_as_int(self.config.metrics.get("daily_request_budget"), 0),
        )
        if over_budget:
            logger.warning(f"[SuperAI] {over_budget}")
            # 本轮不会有 on_llm_response（请求根本没发出去），
            # 要把刚记下的起始时间收回，否则会污染后续的耗时统计。
            self._pop_request_started(session)
            # 钩子是普通协程，不能用 yield 返回结果，否则会退化成 async generator；
            # 也不能只调 plain_result()（它只是「构造」结果，不会挂到事件上），
            # 必须 set_result() 才能把话术真正发给用户。
            event.set_result(event.plain_result(over_budget))
            event.stop_event()
            return

        # 2) 稳定指令注入（不随轮次变化，保护提示词缓存）
        apply_stable_prompt(req, extra=str(self.config.agent.get("system_prompt") or ""))

        # 3) 动态上下文：摘要 + 长期记忆 + 知识库
        summary = ""
        memories: list[str] = []
        if self.config.memory_enabled:
            summary = await self._prepare_memory(event, req, session)
            if self.config.inject_memories:
                memories = self._recall_for_prompt(session, req.prompt or "")
                if memories:
                    self._push_injected_memories(event, memories)

        kb_context = await self._maybe_recall_knowledge(req.prompt or "")

        # 3.5) 若问到时效性问题且联网工具可用，提示模型主动搜索。
        #      Router 的 needs_web() 早前没有任何调用点，等于关键词表是死的。
        web_hint = ""
        if (
            self.config.web_enabled
            and "superai_web_search" in self.tool_names
            and self.router.needs_web(req.prompt or "")
        ):
            web_hint = (
                "用户的问题可能涉及实时信息，"
                "请优先调用 superai_web_search 获取最新结果后再回答；"
                "如果搜索结果不足以支撑结论，请如实说明。"
            )

        # 4) 路由决策
        route_tier = ""
        if self.config.router_enabled:
            route_tier = await self._apply_routing(event, req, image_count=len(original_images))

        # 5) 组装动态块（没内容就不注入，保护前缀缓存）
        tier_label = self._session_tier.get(session, route_tier)
        route_block_tier = tier_label if self.config.debug else ""
        if has_dynamic_content(
            summary=summary,
            memories=memories,
            kb_context=kb_context,
            route_tier=route_block_tier,
            web_hint=web_hint,
        ):
            block = build_dynamic_block(
                summary=summary,
                memories=memories,
                kb_context=kb_context,
                route_tier=route_block_tier,
                web_hint=web_hint,
                include_time=False,
            )
            if block and not append_dynamic(req, block):
                logger.debug("[SuperAI] 当前 AstrBot 版本不支持动态上下文注入，已跳过")

        # 6) 恢复图片（必须在 AstrBot 的 prepare_request_images 之后生效，
        #    所以这里只做「补回」，不改动已存在的值）
        if original_images:
            current = list(getattr(req, "image_urls", None) or [])
            merged = list(dict.fromkeys([*current, *original_images]))
            req.image_urls = merged

    def _task_kind(self, req: ProviderRequest, images: list[str]) -> str:
        """判断本轮请求属于哪种任务类型（对应 ``enabled_tasks`` 白名单）。

        - ``image``：带图片的多模态请求；
        - ``agent``：请求已经挂了工具（说明处于工具调用链路）；
        - ``long_context``：上下文 token 数超过长上下文阈值；
        - ``chat``：其余普通对话。
        """
        if images or getattr(req, "audio_urls", None):
            return "image"
        if getattr(req, "func_tool", None) is not None:
            return "agent"
        contexts = getattr(req, "contexts", None) or []
        tokens = estimate_tokens(req.prompt or "") + sum(
            estimate_tokens(str(item)) for item in contexts[-10:]
        )
        # 这一行在**每条消息**的 on_llm_request 上执行，绝不能因为用户把该字段
        # 填成非数字（WebUI 是文本框，框架不做类型校验）就抛 int() 异常 ——
        # 那会让整个钩子被框架的 call_event_hook 吞掉，表现为「路由/记忆/预算
        # 全部静默失效」，而用户只在日志里看到一段 traceback。
        threshold = _as_int(self.config.router.get("long_context_tokens"), 64000)
        if threshold > 0 and tokens >= threshold:
            return "long_context"
        return "chat"

    async def _prepare_memory(
        self, event: AstrMessageEvent, req: ProviderRequest, session: str
    ) -> str:
        """更新滚动摘要，并在必要时触发后台事实抽取。"""
        try:
            # 先累计轮数：即使历史读取失败（会话刚建立、DB 抖动），
            # 轮数也应该增长，否则摘要触发条件永远不成立。
            self.summaries.bump_rounds(session, 1)

            history = await self._history_texts(event)
            if not history:
                return self.summaries.get(session).summary

            summary = await self.memory_service.maybe_summarize(session, history)

            # 事实抽取要额外调一次模型，必须节流：否则每轮对话都会烧一次钱。
            #
            # 注意：这里**不能**写成 ``if summary: return summary``。
            # ``maybe_summarize`` 在「未达到摘要阈值」时会把**已有摘要**原样返回，
            # 而摘要一旦生成过就一直是非空的 —— 于是下面这段在几乎所有轮次里
            # 都不可达，「自动抽取事实」这个主打功能对任何产生过摘要的会话
            # **永远不会执行**，且没有任何日志或异常提示。
            if self._should_extract_facts(session, history):
                self._spawn(
                    self.memory_service.extract_facts(session, "\n".join(history[-10:])),
                    name="fact-extract",
                )
            return summary
        except Exception as exc:  # noqa: BLE001 - 记忆处理失败不应影响对话
            logger.debug(f"[SuperAI] 会话记忆处理失败：{exc}")
            return self.summaries.get(session).summary

    def _should_extract_facts(self, session: str, history: list[str]) -> bool:
        """判断本轮是否需要跑事实抽取。

        抽取要调用一次模型，成本不低，所以加两层节流：

        1. **时间节流**：同一会话至少在 ``FACT_EXTRACT_INTERVAL`` 秒内只抽一次；
        2. **内容指纹**：最近一段对话与上次抽取过的完全相同则跳过
           （例如用户连发同一句话、或历史还没更新）。
        """
        if not (self.config.long_term_enabled and self.config.memory.get("extract_facts", True)):
            return False
        if not history:
            return False

        now = time.time()
        dialogue = "\n".join(history[-10:])
        stamp = fingerprint(dialogue, length=16)

        last_ts = self._last_fact_extract.get(session, 0.0)
        if now - last_ts < FACT_EXTRACT_INTERVAL:
            return False
        if self._last_fact_fingerprint.get(session) == stamp:
            return False

        self._last_fact_extract[session] = now
        self._last_fact_fingerprint[session] = stamp
        # 顺手防止字典无限增长（长期运行的 Bot 会话会很多）
        if len(self._last_fact_extract) > MAX_FACT_EXTRACT_SESSIONS:
            cutoff = now - FACT_EXTRACT_INTERVAL * 10
            for key, ts in list(self._last_fact_extract.items()):
                if ts < cutoff:
                    self._last_fact_extract.pop(key, None)
                    self._last_fact_fingerprint.pop(key, None)
        return True

    def _recall_for_prompt(self, session: str, query: str) -> list[str]:
        """检索本轮要注入的长期记忆。"""
        top_k = max(1, min(_as_int(self.config.memory.get("long_term_top_k"), 5), 20))
        try:
            query = normalize_space(query)
            entries = (
                self.memory.search(session, query, top_k=top_k)
                if query
                else self.memory.list(session, limit=top_k)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[SuperAI] 记忆检索失败：{exc}")
            return []
        return [entry.content for entry in entries][:MAX_INJECTED_MEMORIES]

    async def _maybe_recall_knowledge(self, prompt: str) -> str:
        """知识库自动注入的开关判断。"""
        if not (self.config.kb_enabled and _as_bool(self.config.knowledge_base.get("auto_inject"))):
            return ""
        return await self._recall_knowledge(prompt)

    async def _recall_knowledge(self, query: str) -> str:
        """按需检索知识库并返回可注入的文本。"""
        query = normalize_space(query)
        if not query:
            return ""
        kb_names = [
            str(name).strip()
            for name in (self.config.knowledge_base.get("kb_names") or [])
            if str(name).strip()
        ]
        if not kb_names:
            return ""
        top_k = max(1, min(_as_int(self.config.knowledge_base.get("top_k"), 5), 20))
        from superai.tools.kb_tools import retrieve_kb

        try:
            return await retrieve_kb(
                self.context,
                query,
                kb_names,
                top_k=top_k,
                score_threshold=_as_float(self.config.knowledge_base.get("score_threshold"), 0.0),
            )
        except Exception as exc:  # noqa: BLE001 - 知识库失败不应影响对话
            logger.debug(f"[SuperAI] 知识库自动检索失败：{exc}")
            return ""

    def _push_injected_memories(self, event: AstrMessageEvent, memories: list[str]) -> None:
        """记录本轮注入的记忆（供 Studio 面板展示）。"""
        try:
            injected = event.get_extra("superai_injected_memories") or []
            injected.extend(memories)
            event.set_extra("superai_injected_memories", injected[-10:])
        except Exception:  # noqa: BLE001 - 事件不支持 extra 时忽略
            pass

    async def _apply_routing(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        image_count: int = 0,
    ) -> str:
        """根据路由决策切换本轮使用的模型，返回命中的档位。"""
        session = event.unified_msg_origin
        prompt = req.prompt or ""
        context_tokens = estimate_tokens(prompt) + sum(
            estimate_tokens(str(item)) for item in (req.contexts or [])[-10:]
        )
        decision = self.router.decide(
            prompt=prompt,
            has_image=image_count > 0,
            has_audio=bool(getattr(req, "audio_urls", None)),
            context_tokens=context_tokens,
            session_tier=self._session_tier.get(session, ""),
        )

        available = self._available_provider_ids()
        session_provider = await self._current_provider_id(session)
        try:
            primary = self.router.resolve_provider_id(
                decision,
                session_provider_id=session_provider,
                available_ids=available or None,
            )
        except SuperAIError as exc:
            logger.warning(f"[SuperAI] 路由失败：{exc.friendly()}")
            return decision.tier

        chain = self.router.ordered_candidates(
            decision,
            primary=primary,
            session_provider_id=session_provider,
            available_ids=available or None,
        )
        if hasattr(req, "model"):
            req.model = primary
        try:
            event.set_extra("superai_route", decision.to_dict())
            event.set_extra("superai_route_chain", chain)
            event.set_extra("superai_provider", primary)
        except Exception:  # noqa: BLE001
            pass
        self._session_tier[session] = decision.tier
        logger.debug(
            f"[SuperAI] 路由档位={decision.tier} 主模型={primary} "
            f"原因={decision.reason} 降级链={chain}"
        )
        return decision.tier

    # ------------------------------------------------------------------
    # LLM 响应钩子：用量统计 + 失败标记
    # ------------------------------------------------------------------
    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        """记录本轮用量；失败时把 provider 标记为不健康。"""
        if not self.config.enabled:
            return
        session = event.unified_msg_origin
        started = self._pop_request_started(session)
        latency_ms = max(0, int((time.time() - started) * 1000)) if started else 0

        route = event.get_extra("superai_route") or {}
        route_tier = str(route.get("tier") or "") if isinstance(route, dict) else ""
        provider_id = str(event.get_extra("superai_provider") or "")

        role = str(getattr(resp, "role", "") or "")
        if role == "err":
            # AstrBot 用 role="err" 表示本轮 LLM 调用失败
            self.router.mark_failure(provider_id)
            if self.config.metrics_enabled:
                self.metrics.record_failure(
                    session=session,
                    provider_id=provider_id,
                    route=route_tier,
                    latency_ms=latency_ms,
                    error=str(getattr(resp, "completion_text", "") or "LLM 返回错误")[:200],
                )
            return

        if provider_id:
            self.router.mark_success(provider_id)
        if not self.config.metrics_enabled:
            return

        try:
            usage = getattr(resp, "usage", None)
            self.metrics.record(
                session=session,
                provider_id=provider_id,
                route=route_tier,
                input_tokens=_as_int(getattr(usage, "input_other", 0), 0),
                output_tokens=_as_int(getattr(usage, "output", 0), 0),
                cached_tokens=_as_int(getattr(usage, "input_cached", 0), 0),
                latency_ms=latency_ms,
                success=True,
            )
        except Exception as exc:  # noqa: BLE001 - 统计失败不影响对话
            logger.debug(f"[SuperAI] 记录用量失败：{exc}")

    #: 一条「请求起始时间」记录最多被认为有效的时长（秒）。
    #: 超过这个时长的记录一定是没配对的残留（例如提前 return 没回收、
    #: 或插件被卸载导致 on_llm_response 从未触发），用它算耗时会得到
    #: 几小时这种荒谬的数字，因此直接丢弃。
    REQUEST_STARTED_MAX_AGE = 3600.0

    def _pop_request_started(self, session: str) -> float:
        """取出并移除某会话最近一次的请求起始时间。

        返回 ``0.0`` 表示「没有可用的起始时间」（调用方据此跳过耗时统计）。
        会顺手丢弃同一会话里过期的残留记录，避免它们被当成有效起点。
        """
        now = time.time()
        found = 0.0
        stale: list[int] = []
        for index in range(len(self._request_started) - 1, -1, -1):
            key, started = self._request_started[index]
            if key != session:
                continue
            if now - started > self.REQUEST_STARTED_MAX_AGE:
                stale.append(index)
                continue
            found = started
            del self._request_started[index]
            break
        for index in sorted(stale, reverse=True):
            del self._request_started[index]
        return found

    # ------------------------------------------------------------------
    # 指令：/ai
    # ------------------------------------------------------------------
    @filter.command("ai", alias={"提问", "Ai"})
    async def ai_command(self, event: AstrMessageEvent, prompt: str = ""):
        """SuperAI 快捷问答：/ai <你的问题>（可附图片）"""
        cfg = self.config
        if not cfg.enabled:
            yield event.plain_result("SuperAI 当前已被管理员关闭。")
            return
        if not cfg.group_allowed(event.get_group_id(), is_private=event.is_private_chat()):
            yield event.plain_result("本群未启用 SuperAI。")
            return

        max_chars = max(1, _as_int(cfg.commands.get("max_input_chars"), 4000))
        text = truncate(normalize_space(prompt), max_chars, suffix="")

        # 图片问答：文本可能为空，但只要有图就应该继续
        images = self._event_image_urls(event)
        if not text and not images:
            yield event.plain_result(self._help_text())
            return
        if not self._cooldown_ok(event.unified_msg_origin):
            yield event.plain_result("请求太快啦，请稍后再试。")
            return

        started = time.time()
        try:
            result = await self._run_agent(event, text or "请描述这张图片。", images=images)
        except SuperAIError as exc:
            yield event.plain_result(f"⚠️ {exc.friendly()}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] /ai 执行失败：{exc}", exc_info=True)
            yield event.plain_result(f"⚠️ 执行失败：{exc}")
            return

        latency = int((time.time() - started) * 1000)
        # 抹掉模型可能回显的内部标记（<think> / <superai_context> 等）
        yield event.plain_result(strip_markup(result) or "（模型没有返回内容）")
        logger.info(f"[SuperAI] /ai 完成，耗时 {latency}ms")

    def _event_image_urls(self, event: AstrMessageEvent) -> list[str]:
        """从消息链中取出图片（指令路径用）。

        ``ProviderRequest`` 只在走 AstrBot 默认管线时存在；``/ai`` 是自己
        发起的调用，所以必须直接从事件里取图片。
        """
        try:
            import astrbot.api.message_components as Comp
        except ImportError:  # pragma: no cover - 老版本
            return []
        urls: list[str] = []
        for component in event.get_messages():
            if isinstance(component, Comp.Image):
                url = (
                    getattr(component, "url", "")
                    or getattr(component, "file", "")
                    or getattr(component, "path", "")
                )
                if url:
                    urls.append(str(url))
        return list(dict.fromkeys(urls))

    async def _run_agent(
        self, event: AstrMessageEvent, prompt: str, *, images: list[str] | None = None
    ) -> str:
        """执行一次 Agent 调用（带工具、带降级）。"""
        session = event.unified_msg_origin

        # 1) 先按路由策略挑模型；路由关闭时退回会话默认模型
        candidates: list[str] = []
        tier = TIER_DEFAULT
        if self.config.router_enabled:
            decision = self.router.decide(
                prompt=prompt,
                has_image=bool(images),
                context_tokens=estimate_tokens(prompt),
                session_tier=self._session_tier.get(session, ""),
            )
            tier = decision.tier
            available = self._available_provider_ids()
            session_provider = await self._current_provider_id(session)
            with contextlib.suppress(SuperAIError):
                primary = self.router.resolve_provider_id(
                    decision,
                    session_provider_id=session_provider,
                    available_ids=available or None,
                )
                candidates = self.router.ordered_candidates(
                    decision,
                    primary=primary,
                    session_provider_id=session_provider,
                    available_ids=available or None,
                )
        if not candidates:
            fallback = await self._current_provider_id(session)
            if not fallback:
                raise ProviderUnavailableError(
                    "没有可用的模型提供商",
                    hint="请在 AstrBot WebUI 的「服务提供商」中配置并启用一个对话模型。",
                )
            candidates = [fallback]

        # 2) 组装工具
        tools: ToolSet | None = None
        if self.config.agent_enabled and getattr(self, "_tools", None):
            from astrbot.core.agent.tool import ToolSet

            tools = ToolSet(list(self._tools))

        agent_cfg = self.config.agent
        outcome = await self.agent.run(
            event=event,
            prompt=prompt,
            candidates=candidates,
            system_prompt=str(agent_cfg.get("system_prompt") or "") or None,
            image_urls=list(images or []),
            tools=tools,
            use_agent=bool(tools),
        )

        # 3) 记录用量（/ai 走的是我们自己的调用，不会触发 on_llm_response）。
        #    必须带上真实 token：否则「按 token 计的日配额」在指令路径上形同虚设。
        if self.config.metrics_enabled:
            self.metrics.record(
                session=session,
                provider_id=outcome.provider_id,
                route=tier,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                cached_tokens=outcome.cached_tokens,
                success=True,
            )
        return outcome.text

    # ------------------------------------------------------------------
    # 指令：/superai 管理
    # ------------------------------------------------------------------
    @filter.command_group("superai")
    def superai_group(self):
        """SuperAI 管理指令组。"""
        pass

    @superai_group.command("help")
    async def superai_help(self, event: AstrMessageEvent):
        """查看 SuperAI 帮助。"""
        yield event.plain_result(self._help_text())

    @superai_group.command("status")
    async def superai_status(self, event: AstrMessageEvent):
        """查看 SuperAI 运行状态。"""
        yield event.plain_result(self._status_text())

    @superai_group.command("stats")
    async def superai_stats(self, event: AstrMessageEvent, days: int = 7):
        """查看用量统计：/superai stats [天数]"""
        days = max(1, min(90, _as_int(days, 7)))
        self.metrics.flush()
        data = self.metrics.summary(days)
        lines = [
            f"📊 SuperAI 用量统计（最近 {days} 天）",
            f"请求数：{data['requests']}（失败 {data['failures']}）",
            f"Token：{data['total_tokens']}"
            f"（输入 {data['input_total']}，其中命中缓存 {data['cached_tokens']}"
            f" / 输出 {data['output_tokens']}）",
            f"平均耗时：{data['avg_latency_ms']} ms",
        ]
        budget = self._budget_line()
        if budget:
            lines.append(budget)
        providers = _top_pairs(data.get("providers"))
        if providers:
            lines.append("模型调用分布：" + providers)
        routes = _top_pairs(data.get("routes"))
        if routes:
            lines.append("路由分布：" + routes)
        yield event.plain_result("\n".join(lines))

    def _budget_line(self) -> str:
        """返回今日配额使用情况的一行文本。"""
        token_budget = _as_int(self.config.metrics.get("daily_token_budget"), 0)
        request_budget = _as_int(self.config.metrics.get("daily_request_budget"), 0)
        if token_budget <= 0 and request_budget <= 0:
            return ""
        stats = self.metrics.today_stats()
        parts = [f"今日已用 {stats.requests} 次请求 / {stats.total_tokens} tokens"]
        if token_budget > 0:
            parts.append(f"token 配额 {stats.total_tokens}/{token_budget}")
        if request_budget > 0:
            parts.append(f"请求配额 {stats.requests}/{request_budget}")
        return "配额：" + "，".join(parts)

    @superai_group.group("memory")
    def superai_memory_group(self):
        """记忆子指令组，支持 /superai memory list 等写法。

        注意：这里必须挂在 ``superai_group`` 之下（``@superai_group.group``），
        不能用 ``@filter.command_group("superai.memory")``。后者会把组名注册成
        **字面指令** ``superai.memory``，与 AstrBot 的 ``CommandGroupFilter`` 用
        ``message_str.startswith(...)`` 匹配「superai memory ...」的语义对不上 ——
        一旦再给 ``superai_group`` 加一个同名的 ``@command("memory")``，
        两者会在唤醒阶段同时命中，导致
        ``/superai memory list`` 被旧的兼容指令抢走，
        子指令组里的 list / search / clear / stats 永远执行不到。
        """
        pass

    @superai_memory_group.command("list")
    async def superai_memory_list(self, event: AstrMessageEvent, limit: int = 10):
        """查看本会话记忆：/superai memory list [条数]"""
        session = event.unified_msg_origin
        limit = max(1, min(50, _as_int(limit, 10)))
        entries = self.memory.list(session, limit=limit)
        summary = self.summaries.get(session)
        lines = [f"🧠 本会话长期记忆：{self.memory.count(session)} 条"]
        if summary.summary:
            lines.append(f"📝 当前摘要（覆盖 {summary.covered_rounds} 条历史）：")
            lines.append(truncate(summary.summary, 300))
        if not entries:
            lines.append("（暂无，用户说「记住……」时会自动写入）")
        else:
            lines.extend(f"- [{entry.kind}] {entry.content}" for entry in entries)
        yield event.plain_result("\n".join(lines))

    @superai_memory_group.command("search")
    async def superai_memory_search(self, event: AstrMessageEvent, query: str = ""):
        """检索记忆：/superai memory search <关键词>"""
        session = event.unified_msg_origin
        if not query.strip():
            yield event.plain_result("请提供检索关键词，例如：/superai memory search 喜欢的语言")
            return
        entries = self.memory.search(session, query, top_k=5)
        if not entries:
            yield event.plain_result("没有找到相关记忆。")
            return
        lines = ["🔍 检索结果："]
        lines.extend(f"- [{entry.kind}] {entry.content}" for entry in entries)
        yield event.plain_result("\n".join(lines))

    @superai_memory_group.command("clear")
    async def superai_memory_clear_cmd(self, event: AstrMessageEvent):
        """清空本会话记忆（仅管理员）：/superai memory clear"""
        session = event.unified_msg_origin
        try:
            is_admin = bool(event.is_admin())
        except Exception:  # noqa: BLE001
            is_admin = False
        if not is_admin:
            yield event.plain_result("只有管理员可以清空记忆。")
            return
        removed = self.memory.clear(session)
        self.summaries.clear(session)
        yield event.plain_result(f"已清空本会话记忆（{removed} 条）与摘要。")

    @superai_memory_group.command("stats")
    async def superai_memory_stats(self, event: AstrMessageEvent):
        """查看记忆总量：/superai memory stats"""
        overview = self.memory.stats()
        kinds = overview["kinds"]
        yield event.plain_result(
            "🧠 记忆总览\n"
            f"会话数：{overview['sessions']}\n"
            f"记忆总数：{overview['total']}"
            f"（事实 {kinds.get('fact', 0)} / 偏好 {kinds.get('preference', 0)}"
            f" / 事件 {kinds.get('event', 0)}）\n"
            f"摘要数：{len(self.summaries.list_sessions())}"
        )

    @superai_group.command("route")
    async def superai_route(self, event: AstrMessageEvent, tier: str = ""):
        """查看或指定路由档位：/superai route [cheap|strong|reasoning|vision|long_context|auto]"""
        session = event.unified_msg_origin
        if not tier:
            current = self._session_tier.get(session, TIER_DEFAULT)
            provider_map = self.config.provider_map()
            lines = [
                "🧭 SuperRouter 状态",
                f"当前档位：{current}",
                f"策略：{self.config.router.get('strategy')}",
                "档位 -> 模型：",
            ]
            lines.extend(f"- {key}：{value or '（未配置）'}" for key, value in provider_map.items())
            failures = self.router.health()
            if failures:
                lines.append("失败计数：" + "，".join(f"{k}×{v}" for k, v in failures.items()))
            # 降级链的主模型要跟「当前档位」一致。早前这里硬编码用 strong 档的
            # 模型当 primary，会话固定为 cheap / reasoning 时展示出来的链
            # 与真实路由结果不一致，用户会以为自己配错了。
            active_tier = self._session_tier.get(session, "")
            decision = self.router.decide(prompt="", session_tier=active_tier)
            primary = decision.primary or self.config.tier_provider(decision.tier)
            if not primary:
                primary = await self._current_provider_id(session)
            chain = self.router.ordered_candidates(
                decision,
                primary=primary,
                session_provider_id=await self._current_provider_id(session),
                available_ids=self._available_provider_ids() or None,
            )
            if chain:
                lines.append("当前降级链：" + " → ".join(chain))
            yield event.plain_result("\n".join(lines))
            return

        tier = tier.strip().lower()
        if tier in {"auto", "reset", "默认"}:
            self._session_tier.pop(session, None)
            yield event.plain_result("已恢复自动路由。")
            return
        if tier not in SELECTABLE_TIERS:
            yield event.plain_result(f"未知档位：{tier}\n可选：{'、'.join(SELECTABLE_TIERS)}、auto")
            return
        self._session_tier[session] = tier
        yield event.plain_result(
            f"已把本会话路由档位固定为 {tier}。发送 /superai route auto 恢复自动。"
        )

    @superai_group.command("tools")
    async def superai_tools(self, event: AstrMessageEvent):
        """查看已注册的工具。"""
        from superai.tools.registry import TOOL_LABELS

        names = self.tool_names
        if not names:
            yield event.plain_result("当前没有注册任何工具，请在插件配置中开启对应能力。")
            return
        lines = ["🧰 已注册工具："]
        lines.extend(f"- {name}：{TOOL_LABELS.get(name, '')}" for name in names)
        yield event.plain_result("\n".join(lines))

    @superai_group.command("workflow")
    async def superai_workflow(self, event: AstrMessageEvent, name: str = "", input_text: str = ""):
        """运行工作流：/superai workflow <名称> [输入]"""
        if not self.config.workflow_enabled:
            yield event.plain_result("工作流功能未启用。")
            return
        workflows = self.workflows.load(self.config.workflow.get("workflows"))
        if not name:
            if not workflows:
                yield event.plain_result("当前没有配置任何工作流。")
                return
            lines = ["📋 可用工作流："]
            for key, wf in workflows.items():
                desc = f" - {wf.description}" if wf.description else ""
                lines.append(f"- {key}（{len(wf.steps)} 步）{desc}")
            yield event.plain_result("\n".join(lines))
            return
        workflow = workflows.get(name)
        if workflow is None:
            yield event.plain_result(f"未找到工作流「{name}」。")
            return
        try:
            result = await self.run_workflow(
                workflow, user_input=input_text, session=event.unified_msg_origin
            )
        except SuperAIError as exc:
            yield event.plain_result(f"⚠️ {exc.friendly()}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] 工作流执行失败：{exc}", exc_info=True)
            yield event.plain_result(f"⚠️ 工作流执行失败：{exc}")
            return
        yield event.plain_result(result)

    @superai_group.command("maintain")
    async def superai_maintain(self, event: AstrMessageEvent):
        """立即执行一次维护（落盘 + 记忆衰减），仅管理员。"""
        try:
            is_admin = bool(event.is_admin())
        except Exception:  # noqa: BLE001
            is_admin = False
        if not is_admin:
            yield event.plain_result("只有管理员可以执行维护。")
            return
        await self.run_maintenance(force=True)
        removed = self.memory.decay(
            half_life_days=_as_float(self.config.memory.get("decay_half_life_days"), 30.0)
        )
        yield event.plain_result(f"✅ 维护完成：统计数据已落盘，记忆衰减淘汰 {removed} 条。")

    # ------------------------------------------------------------------
    # 工作流执行
    # ------------------------------------------------------------------
    async def run_workflow(self, workflow: Workflow, *, user_input: str, session: str) -> str:
        """按顺序执行工作流，逐步输出中间结果。

        步骤之间的引用：

        - ``{{prev}}``：上一步输出
        - ``{{input}}``：用户输入的初始内容
        - ``{{step}}``：当前步号
        """
        if not workflow.steps:
            raise SuperAIError("该工作流没有配置任何步骤。")

        outputs: list[str] = []
        previous = ""
        max_step_chars = max(200, _as_int(self.config.workflow.get("max_step_chars"), 4000))
        route_tier = workflow.route or ""
        for index, step in enumerate(workflow.steps, 1):
            prompt = step.prompt
            if step.use_history and previous:
                prompt = f"{prompt}\n\n上一步结果：\n{previous}"
            prompt = (
                prompt.replace("{{prev}}", previous)
                .replace("{{input}}", user_input)
                .replace("{{step}}", str(index))
            )
            if not prompt.strip():
                continue

            provider_ids = await self._workflow_candidates(route_tier, session)
            try:
                result = await self.agent.simple(
                    prompt,
                    candidates=provider_ids,
                    system_prompt=str(self.config.agent.get("system_prompt") or ""),
                )
            except SuperAIError as exc:
                self.workflows.record_run(
                    workflow.name, success=False, detail=f"第 {index} 步失败：{exc.message}"
                )
                raise
            previous = result
            outputs.append(f"【第 {index} 步 · {step.name}】\n{truncate(result, max_step_chars)}")

        self.workflows.record_run(workflow.name, success=True, detail=f"{len(outputs)} 步完成")
        return f"🔧 工作流「{workflow.name}」执行完成\n\n" + "\n\n".join(outputs)

    async def _workflow_candidates(self, route_tier: str, session: str) -> list[str]:
        """给工作流步骤挑候选模型（指定档位 → 该档位 + 降级链 → 会话默认模型）。

        最后一定要兜到「会话/全局默认模型」：档位一个都没配是很常见的情形
        （用户只想要工作流、不想配路由），此时如果直接返回空列表，
        工作流会以「没有可用的模型提供商」失败，而其实系统里有可用模型。
        """
        provider_map = self.config.provider_map()
        candidates: list[str] = []
        tier = route_tier.strip().lower() if route_tier else ""
        if tier and provider_map.get(tier):
            candidates.append(provider_map[tier])
        # 摘要专用模型作为次选，最后兜到 strong / cheap
        summary_provider = str(self.config.memory.get("summary_provider_id") or "")
        for pid in (
            summary_provider,
            provider_map.get("strong", ""),
            provider_map.get("cheap", ""),
            provider_map.get("reasoning", ""),
            provider_map.get("long_context", ""),
        ):
            if pid and pid not in candidates:
                candidates.append(pid)

        available = self._available_provider_ids()
        candidates = [pid for pid in candidates if not available or pid in available]
        if not candidates:
            # 兜底：用会话当前（或全局默认）模型，保证工作流仍可运行
            fallback = await self._current_provider_id(session)
            if fallback:
                candidates.append(fallback)
        return candidates

    # ------------------------------------------------------------------
    # Studio 面板 API
    # ------------------------------------------------------------------
    async def api_status(self):
        """GET /status —— 运行状态。"""
        try:
            available = self._available_provider_ids()
            return json_response(
                {
                    "version": __version__,
                    "enabled": self.config.enabled,
                    "hooks_ready": self._installed_hooks,
                    "features": {
                        "router": self.config.router_enabled,
                        "memory": self.config.memory_enabled,
                        "web": self.config.web_enabled,
                        "knowledge_base": self.config.kb_enabled,
                        "agent": self.config.agent_enabled,
                        "workflow": self.config.workflow_enabled,
                        "metrics": self.config.metrics_enabled,
                    },
                    "tools": self.tool_names,
                    "providers": sorted(available),
                    "route_tiers": self.config.provider_map(),
                    "health": self.router.health(),
                    "health_detail": self.router.health_detail(available_ids=available),
                    "memory": self.memory.stats(),
                    "sessions": len(self._session_tier),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] status API 失败：{exc}", exc_info=True)
            return error_response(str(exc), status_code=500)

    async def api_stats(self):
        """GET /stats —— 用量统计。"""
        try:
            days = _as_int(request.query.get("days", 7), 7)
        except Exception:  # noqa: BLE001
            days = 7
        days = max(1, min(90, days))
        try:
            self.metrics.flush()
            return json_response(self.metrics.summary(days))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] stats API 失败：{exc}", exc_info=True)
            return error_response(str(exc), status_code=500)

    async def api_memory(self):
        """GET /memory —— 查询会话记忆。"""
        try:
            session = str(request.query.get("session", "") or "")
            if not session:
                return error_response("缺少 session 参数", status_code=400)
            query = str(request.query.get("q", "") or "")
            limit = max(1, min(100, _as_int(request.query.get("limit", 20), 20)))
            entries = (
                self.memory.search(session, query, top_k=limit)
                if query
                else self.memory.list(session, limit=limit)
            )
            summary = self.summaries.get(session)
            return json_response(
                {
                    "session": session,
                    "total": self.memory.count(session),
                    "summary": summary.summary,
                    "covered_rounds": summary.covered_rounds,
                    "entries": [entry.to_dict() for entry in entries],
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] memory API 失败：{exc}", exc_info=True)
            return error_response(str(exc), status_code=500)

    async def api_memory_clear(self):
        """POST /memory/clear —— 清空会话记忆。"""
        try:
            payload = await request.json(default={})
            if not isinstance(payload, dict):
                return error_response("请求体应为 JSON 对象", status_code=400)
            session = str(payload.get("session") or "")
            if not session:
                return error_response("缺少 session 字段", status_code=400)
            removed = self.memory.clear(session)
            self.summaries.clear(session)
            return json_response({"removed": removed})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] memory clear API 失败：{exc}", exc_info=True)
            return error_response(str(exc), status_code=500)

    async def api_tools(self):
        """GET /tools —— 工具清单（带中文用途说明）。

        同时返回 ``tools``（纯名字，保持向后兼容）与 ``items``（含说明与启用状态），
        面板可以据此展示得更清楚，而不用把说明硬编码在前端。
        """
        try:
            from superai.tools.registry import TOOL_LABELS

            names = self.tool_names
            return json_response(
                {
                    "tools": names,
                    "items": [
                        {
                            "name": name,
                            "description": TOOL_LABELS.get(name, ""),
                            "active": True,
                        }
                        for name in names
                    ],
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] tools API 失败：{exc}", exc_info=True)
            return error_response(str(exc), status_code=500)

    async def api_workflows(self):
        """GET /workflows —— 工作流列表与最近运行记录。"""
        try:
            workflows = self.workflows.load(self.config.workflow.get("workflows"))
            return json_response(
                {
                    "workflows": [wf.to_dict() for wf in workflows.values()],
                    "recent_runs": self.workflows.recent_runs(10),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] workflows API 失败：{exc}", exc_info=True)
            return error_response(str(exc), status_code=500)

    async def api_sessions(self):
        """GET /sessions —— 有记忆或摘要的会话列表。

        面板接口必须自己兜住异常：这里任何一个存储读取失败都会让前端
        整个面板白屏，而 ``api_sessions`` 早前没有 try/except。
        """
        try:
            sessions: dict[str, dict[str, Any]] = {}
            for session in self.summaries.list_sessions():
                sessions.setdefault(session, {})["has_summary"] = True
            for session in self.memory.sessions():
                sessions.setdefault(session, {})["has_memory"] = True
            rows = [
                {
                    "session": session,
                    "memories": self.memory.count(session),
                    "summary_chars": len(self.summaries.get(session).summary),
                }
                for session in sorted(sessions)
            ]
            return json_response({"sessions": rows})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] sessions API 失败：{exc}", exc_info=True)
            return error_response(str(exc), status_code=500)

    # ------------------------------------------------------------------
    # 文本输出
    # ------------------------------------------------------------------
    def _help_text(self) -> str:
        return (
            "🤖 SuperAI 使用帮助\n"
            "核心指令：\n"
            "/ai <问题>             向 SuperAI 提问（自动路由 + 记忆 + 工具，可附图片）\n"
            "/superai status        查看运行状态\n"
            "/superai stats [天数]   查看用量统计\n"
            "/superai memory list    查看本会话记忆与摘要\n"
            "/superai memory search <关键词>  检索记忆\n"
            "/superai memory clear   清空本会话记忆（管理员）\n"
            "/superai memory stats   查看全局记忆概览\n"
            "/superai route [档位]   查看或固定路由档位\n"
            "/superai tools          查看已注册工具\n"
            "/superai workflow [名称] [输入]  查看或运行工作流\n"
            "/superai maintain       立即落盘统计并衰减记忆（管理员）\n"
            "提示：直接说「记住……」即可写入长期记忆。"
        )

    def _status_text(self) -> str:
        features = {
            "路由": self.config.router_enabled,
            "记忆": self.config.memory_enabled,
            "联网": self.config.web_enabled,
            "知识库": self.config.kb_enabled,
            "Agent": self.config.agent_enabled,
            "工作流": self.config.workflow_enabled,
        }
        flags = "  ".join(f"{name}={'✅' if value else '❌'}" for name, value in features.items())
        providers = sorted(self._available_provider_ids())
        memory_stats = self.memory.stats()
        lines = [
            f"⚙️ SuperAI v{__version__}",
            flags,
            f"钩子：{'✅ 已就绪' if self._installed_hooks else '⚠️ 未检测到'}",
            f"可用模型：{len(providers)} 个"
            + (f"（{', '.join(providers[:5])}）" if providers else ""),
            f"工具：{len(self.tool_names)} 个",
            f"记忆总量：{memory_stats['total']} 条（会话 {memory_stats['sessions']} 个）",
            f"会话摘要数：{len(self.summaries.list_sessions())}",
        ]
        failures = self.router.health()
        if failures:
            lines.append("模型失败计数：" + "，".join(f"{k}×{v}" for k, v in failures.items()))
        stats = self.metrics.today_stats()
        lines.append(f"今日用量：{stats.requests} 次请求 / {stats.total_tokens} tokens")
        budget = self._budget_line()
        if budget:
            lines.append(budget)
        return "\n".join(lines)
