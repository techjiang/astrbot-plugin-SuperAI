"""SuperAI 插件入口。

SuperAI 是面向 AstrBot 的 AI 增强层，核心能力：

1. **SuperRouter**：多模型智能路由 + 失败降级，让每次请求都落到合适的模型上。
2. **SuperMemory**：滚动摘要 + 长期记忆自动抽取与注入，长对话不失忆、成本可控。
3. **SuperAgent**：注册联网搜索、记忆、知识库、工作流等 function calling 工具。
4. **知识库增强**：可选的 KB 自动检索注入。
5. **AI 快捷指令**：``/ai`` 系列指令与可配置的工具前缀。
6. **工作流编排**：把多步 AI 任务配置化。
7. **用量统计与成本控制**：token / 请求数预算拦截 + Studio 面板。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.agent.tool import ToolSet
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.config import SuperAIConfig, build_config
from .core.errors import ProviderUnavailableError, SuperAIError
from .core.metrics import MetricsCollector
from .core.utils import estimate_tokens, normalize_space, truncate
from .memory_service import MemoryService
from .prompt import append_dynamic, apply_stable_prompt, build_dynamic_block
from .router.router import TIER_DEFAULT, SuperRouter
from .storage.memory import MemoryStore
from .storage.store import JsonStore
from .storage.summary import SummaryStore
from .storage.workflows import Workflow, WorkflowStore
from .tools.registry import build_toolset
from .version import __version__

PLUGIN_NAME = "astrbot_plugin_superai"
DATA_DIR_NAME = PLUGIN_NAME

#: 会话级路由档位缓存的键前缀（写在 event extra 中）
SESSION_TIER_KEY = "superai_tier"


class SuperAIPlugin(Star):
    """SuperAI 主插件类。

    插件元数据以 ``metadata.yaml`` 为准（展示名、描述、版本、支持的平台等），
    因此不再使用已弃用的 ``@register`` 装饰器。
    """

    """SuperAI 主插件类。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.context = context
        self._raw_config = config
        self.config: SuperAIConfig = build_config(config)

        # --- 持久化 -----------------------------------------------------
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / DATA_DIR_NAME
        data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        self.memory = MemoryStore(JsonStore(data_dir / "memory.json"))
        self.summaries = SummaryStore(JsonStore(data_dir / "summary.json"))
        self.workflows = WorkflowStore(
            JsonStore(data_dir / "workflows.json"),
            max_steps=int(self.config.workflow.get("max_steps") or 8),
        )
        self.metrics = MetricsCollector(
            data_dir,
            retention_days=int(self.config.metrics.get("retention_days") or 30),
            debug=self.config.debug,
        )

        # --- 运行时状态 -------------------------------------------------
        self.router = SuperRouter(self.config)
        self.memory_service = MemoryService(self)
        self._session_cooldown: dict[str, float] = {}
        self._session_tier: dict[str, str] = {}
        self._maintenance_task: asyncio.Task | None = None
        self._last_active_ts: int = int(time.time())
        self._request_started: dict[str, float] = {}
        """会话 -> 本轮请求起始时间，用于统计耗时"""

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
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        logger.info(
            f"[SuperAI] v{__version__} 已加载 | 路由={'开' if self.config.router_enabled else '关'} "
            f"记忆={'开' if self.config.memory_enabled else '关'} "
            f"联网={'开' if self.config.web_enabled else '关'} "
            f"知识库={'开' if self.config.kb_enabled else '关'} "
            f"Agent={'开' if self.config.agent_enabled else '关'}"
        )

    async def terminate(self) -> None:
        """插件卸载 / 停用时清理资源。"""
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            # 取消后台任务；这里吞掉取消异常与任务内残留异常，避免卸载报错
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._maintenance_task
            self._maintenance_task = None
        self.metrics.flush()
        logger.info("[SuperAI] 已卸载，统计数据已落盘。")

    async def _maintenance_loop(self) -> None:
        """后台维护：定期落盘统计、衰减记忆。"""
        while True:
            try:
                await asyncio.sleep(600)
                self.metrics.flush()
                self.memory.decay(
                    half_life_days=float(self.config.memory.get("decay_half_life_days") or 30)
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 后台任务不可崩溃
                logger.warning(f"[SuperAI] 后台维护任务异常：{exc}")

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _register_tools(self) -> None:
        """把 SuperAI 工具注册到 AstrBot。"""
        try:
            toolset = build_toolset(self.config)
            tools = list(toolset.tools)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] 工具装配失败：{exc}", exc_info=True)
            tools = []
        if tools:
            self.context.add_llm_tools(*tools)
        self._tools = tools

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
        ]
        for route, handler, methods, desc in routes:
            try:
                self.context.register_web_api(route, handler, methods, desc)
            except Exception as exc:  # noqa: BLE001 - 旧版本可能不支持
                logger.debug(f"[SuperAI] 注册 Web API {route} 失败：{exc}")

    def last_active_ts(self) -> int:
        """返回最近一次活跃时间戳。"""
        return self._last_active_ts

    async def llm_simple(
        self,
        prompt: str,
        *,
        provider_id: str = "",
        session: str = "",
        system_prompt: str = "",
    ) -> str:
        """内部使用的轻量 LLM 调用（摘要 / 抽取 / 工作流步骤）。

        优先使用指定 provider，其次使用会话当前 provider。
        """
        target = provider_id or await self._current_provider_id(session)
        if not target:
            raise ProviderUnavailableError("没有可用的模型提供商")
        resp = await self.context.llm_generate(
            chat_provider_id=target,
            prompt=prompt,
            system_prompt=system_prompt or None,
        )
        return resp.completion_text or ""

    async def _current_provider_id(self, session: str) -> str:
        """获取会话当前使用（或全局默认）的 provider id。"""
        try:
            if session:
                return await self.context.get_current_chat_provider_id(umo=session)
        except Exception:  # noqa: BLE001 - 会话可能还没有绑定模型
            logger.debug("[SuperAI] 未找到会话级默认模型，尝试用全局默认模型")
        providers = self.context.get_all_providers()
        if providers:
            return providers[0].meta().id
        return ""

    def _available_provider_ids(self) -> set[str]:
        return {provider.meta().id for provider in self.context.get_all_providers()}

    def _cooldown_ok(self, session: str) -> bool:
        """指令冷却检查。"""
        cooldown = int(self.config.commands.get("cooldown_seconds") or 0)
        if cooldown <= 0:
            return True
        now = time.time()
        last = self._session_cooldown.get(session, 0)
        if now - last < cooldown:
            return False
        self._session_cooldown[session] = now
        return True

    def _extract_image_urls(self, event: AstrMessageEvent) -> list[str]:
        """从消息链里取出图片地址。"""
        import astrbot.api.message_components as Comp

        urls: list[str] = []
        for component in event.get_messages():
            if isinstance(component, Comp.Image):
                url = getattr(component, "url", "") or getattr(component, "file", "")
                if url:
                    urls.append(str(url))
        return urls

    async def _history_texts(self, event: AstrMessageEvent, limit: int = 40) -> list[str]:
        """异步取回会话历史（人类可读文本）。"""
        session = event.unified_msg_origin
        manager = self.context.conversation_manager
        try:
            conversation_id = await manager.get_curr_conversation_id(session)
            if not conversation_id:
                return []
            texts, _ = await manager.get_human_readable_context(
                session, conversation_id, page=1, page_size=limit
            )
            return [normalize_space(text) for text in texts if text]
        except Exception as exc:  # noqa: BLE001 - 历史读取失败不影响对话
            logger.debug(f"[SuperAI] 读取会话历史失败：{exc}")
            return []

    # ------------------------------------------------------------------
    # LLM 钩子：路由 + 记忆注入
    # ------------------------------------------------------------------
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """在请求 LLM 前完成：预算检查 → 记忆/摘要注入 → 模型路由。"""
        if not self.config.enabled:
            return
        self._last_active_ts = int(time.time())

        session = event.unified_msg_origin
        self._request_started[session] = time.time()
        if not self.config.group_allowed(event.get_group_id(), is_private=event.is_private_chat()):
            return

        # 1) 预算检查
        self.metrics.flush()
        over_budget = self.metrics.check_budget(
            token_budget=int(self.config.metrics.get("daily_token_budget") or 0),
            request_budget=int(self.config.metrics.get("daily_request_budget") or 0),
        )
        if over_budget:
            logger.warning(f"[SuperAI] {over_budget}")
            await event.send(event.plain_result(f"⚠️ {over_budget}，请联系管理员调整配额。"))
            event.stop_event()
            return

        # 2) 稳定指令注入（不随轮次变化，保护缓存）
        apply_stable_prompt(req, extra=str(self.config.agent.get("system_prompt") or ""))

        # 3) 动态上下文：摘要 + 长期记忆
        memories: list[str] = []
        summary = ""
        if self.config.memory_enabled:
            try:
                history = await self._history_texts(event)
                if history:
                    self.summaries.bump_rounds(session, 1)
                    summary = await self.memory_service.maybe_summarize(session, history)
                    if not summary:
                        summary = self.summaries.get(session).summary
                    # 异步抽取长期记忆（不阻塞本轮回复）
                    asyncio.create_task(self._extract_facts_bg(session, "\n".join(history[-10:])))
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[SuperAI] 会话记忆处理失败：{exc}")

            if self.config.memory.get("long_term_enabled", True):
                entries = self.memory.search(
                    session,
                    req.prompt or "",
                    top_k=int(self.config.memory.get("long_term_top_k") or 5),
                )
                memories = [entry.content for entry in entries]
                if memories:
                    self._push_injected_memories(event, memories)

        # 4) 路由决策
        if self.config.router_enabled:
            await self._apply_routing(event, req)

        # 5) 组装动态块
        block = build_dynamic_block(
            summary=summary,
            memories=memories if self.config.memory.get("inject_into_prompt", True) else None,
            route_tier=self._session_tier.get(session, ""),
        )
        if block:
            append_dynamic(req, block)

    async def _extract_facts_bg(self, session: str, dialogue: str) -> None:
        try:
            await self.memory_service.extract_facts(session, dialogue)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[SuperAI] 后台记忆抽取失败：{exc}")

    def _push_injected_memories(self, event: AstrMessageEvent, memories: list[str]) -> None:
        """记录本轮注入的记忆（供 Studio 面板展示）。"""
        try:
            injected = event.get_extra("superai_injected_memories") or []
            injected.extend(memories)
            event.set_extra("superai_injected_memories", injected[-10:])
        except Exception:  # noqa: BLE001
            pass

    async def _apply_routing(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """根据路由决策切换本轮使用的模型。"""
        session = event.unified_msg_origin
        prompt = req.prompt or ""
        image_urls = getattr(req, "image_urls", None) or []
        context_tokens = estimate_tokens(prompt) + sum(
            estimate_tokens(str(item)) for item in (req.contexts or [])[:10]
        )
        decision = self.router.decide(
            prompt=prompt,
            has_image=bool(image_urls),
            context_tokens=context_tokens,
            session_tier=self._session_tier.get(session, ""),
        )
        available = self._available_provider_ids()
        try:
            session_provider = await self._current_provider_id(session)
        except Exception:  # noqa: BLE001
            session_provider = ""
        try:
            primary = self.router.resolve_provider_id(
                decision,
                session_provider_id=session_provider,
                available_ids=available or None,
            )
        except SuperAIError as exc:
            logger.warning(f"[SuperAI] 路由失败：{exc.friendly()}")
            return

        chain = self.router.fallback_chain(
            decision,
            primary=primary,
            session_provider_id=session_provider,
            available_ids=available or None,
        )
        # 把降级链交给 AstrBot 的运行器
        if hasattr(req, "model") and primary:
            req.model = primary
        try:
            event.set_extra("superai_route", decision.to_dict())
            event.set_extra("superai_route_chain", chain)
        except Exception:  # noqa: BLE001
            pass
        self._session_tier[session] = decision.tier
        logger.debug(
            f"[SuperAI] 路由档位={decision.tier} 主模型={primary} "
            f"原因={decision.reason} 降级链={chain}"
        )

    # ------------------------------------------------------------------
    # LLM 响应钩子：统计用量
    # ------------------------------------------------------------------
    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        """记录本轮用量。"""
        if not self.config.metrics_enabled:
            return
        try:
            route = event.get_extra("superai_route") or {}
            usage = getattr(resp, "usage", None)
            started = self._request_started.pop(event.unified_msg_origin, time.time())
            latency_ms = max(0, int((time.time() - started) * 1000))
            provider_ids = list(route.get("provider_ids") or []) if route else []
            recorded = self.metrics.record(
                session=event.unified_msg_origin,
                provider_id=provider_ids[0] if provider_ids else "",
                route=str(route.get("tier") or ""),
                input_tokens=int(getattr(usage, "input_other", 0) or 0),
                output_tokens=int(getattr(usage, "output", 0) or 0),
                cached_tokens=int(getattr(usage, "input_cached", 0) or 0),
                latency_ms=latency_ms,
                success=True,
            )
            logger.debug(
                f"[SuperAI] 记录用量：provider={recorded.provider_id or '-'} "
                f"route={recorded.route or '-'} tokens={recorded.total_tokens}"
            )
        except Exception as exc:  # noqa: BLE001 - 统计失败不影响对话
            logger.debug(f"[SuperAI] 记录用量失败：{exc}")

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
        if not self._cooldown_ok(event.unified_msg_origin):
            yield event.plain_result("请求太快啦，请稍后再试。")
            return

        max_chars = int(cfg.commands.get("max_input_chars") or 4000)
        text = truncate(normalize_space(prompt), max_chars, suffix="")
        if not text:
            yield event.plain_result(self._help_text())
            return

        started = time.time()
        try:
            result = await self._run_agent(event, text)
        except SuperAIError as exc:
            yield event.plain_result(f"⚠️ {exc.friendly()}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] /ai 执行失败：{exc}", exc_info=True)
            yield event.plain_result(f"⚠️ 执行失败：{exc}")
            return

        latency = int((time.time() - started) * 1000)
        yield event.plain_result(result or "（模型没有返回内容）")
        logger.info(f"[SuperAI] /ai 完成，耗时 {latency}ms")

    async def _run_agent(self, event: AstrMessageEvent, prompt: str) -> str:
        """执行一次 Agent 调用（带工具）。"""
        session = event.unified_msg_origin
        provider_id = await self._current_provider_id(session)
        if not provider_id:
            raise ProviderUnavailableError(
                "没有可用的模型提供商",
                hint="请在 AstrBot WebUI 的「服务提供商」中配置并启用一个对话模型。",
            )

        agent_cfg = self.config.agent
        max_steps = int(agent_cfg.get("max_steps") or 12)
        tool_timeout = int(agent_cfg.get("tool_call_timeout") or 60)

        tools: ToolSet | None = None
        if self.config.agent_enabled and getattr(self, "_tools", None):
            tools = ToolSet(list(self._tools))

        image_urls = self._extract_image_urls(event)
        if not tools:
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                image_urls=image_urls or None,
                system_prompt=agent_cfg.get("system_prompt") or None,
            )
            return resp.completion_text or ""

        resp = await self.context.tool_loop_agent(
            event=event,
            chat_provider_id=provider_id,
            prompt=prompt,
            image_urls=image_urls or None,
            tools=tools,
            max_steps=max_steps,
            tool_call_timeout=tool_timeout,
            system_prompt=agent_cfg.get("system_prompt") or None,
        )
        return resp.completion_text or ""

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
        days = max(1, min(90, int(days or 7)))
        self.metrics.flush()
        data = self.metrics.summary(days)
        lines = [
            f"📊 SuperAI 用量统计（最近 {days} 天）",
            f"请求数：{data['requests']}（失败 {data['failures']}）",
            f"Token：{data['total_tokens']}（输入 {data['input_tokens']} / 输出 {data['output_tokens']} / 命中缓存 {data['cached_tokens']}）",
            f"平均耗时：{data['avg_latency_ms']} ms",
        ]
        if data.get("providers"):
            top = sorted(data["providers"].items(), key=lambda item: item[1], reverse=True)[:5]
            lines.append("模型调用分布：" + "，".join(f"{k}×{v}" for k, v in top))
        if data.get("routes"):
            top = sorted(data["routes"].items(), key=lambda item: item[1], reverse=True)[:5]
            lines.append("路由分布：" + "，".join(f"{k or 'default'}×{v}" for k, v in top))
        yield event.plain_result("\n".join(lines))

    @superai_group.command("memory")
    async def superai_memory(self, event: AstrMessageEvent, action: str = "list", query: str = ""):
        """记忆管理：/superai memory [list|search|clear] [关键词]"""
        session = event.unified_msg_origin
        action = (action or "list").lower()
        if action in {"clear", "reset"}:
            if not event.is_admin():
                yield event.plain_result("只有管理员可以清空记忆。")
                return
            removed = self.memory.clear(session)
            self.summaries.clear(session)
            yield event.plain_result(f"已清空本会话记忆（{removed} 条）与摘要。")
            return
        if action in {"search", "find"}:
            if not query:
                yield event.plain_result(
                    "请提供检索关键词，例如：/superai memory search 喜欢的语言"
                )
                return
            entries = self.memory.search(session, query, top_k=5)
            if not entries:
                yield event.plain_result("没有找到相关记忆。")
                return
            lines = ["🔍 检索结果："] + [f"- [{entry.kind}] {entry.content}" for entry in entries]
            yield event.plain_result("\n".join(lines))
            return

        entries = self.memory.list(session, limit=10)
        summary = self.summaries.get(session)
        lines = [f"🧠 本会话长期记忆：{self.memory.count(session)} 条"]
        if summary.summary:
            lines.append(f"📝 当前摘要：{truncate(summary.summary, 300)}")
        if not entries:
            lines.append("（暂无，用户说「记住……」时会自动写入）")
        else:
            lines.extend(f"- [{entry.kind}] {entry.content}" for entry in entries)
        yield event.plain_result("\n".join(lines))

    @superai_group.command("route")
    async def superai_route(self, event: AstrMessageEvent, tier: str = ""):
        """查看或指定路由档位：/superai route [cheap|strong|reasoning|vision|long_context|auto]"""
        session = event.unified_msg_origin
        if not tier:
            current = self._session_tier.get(session, TIER_DEFAULT)
            pm = self.config.provider_map()
            lines = [
                "🧭 SuperRouter 状态",
                f"当前档位：{current}",
                f"策略：{self.config.router.get('strategy')}",
                "档位 -> 模型：",
            ]
            lines.extend(f"- {key}：{value or '（未配置）'}" for key, value in pm.items())
            yields_chain = self.router.health()
            if yields_chain:
                lines.append("失败计数：" + "，".join(f"{k}×{v}" for k, v in yields_chain.items()))
            yield event.plain_result("\n".join(lines))
            return

        tier = tier.strip().lower()
        if tier in {"auto", "reset", "默认"}:
            self._session_tier.pop(session, None)
            yield event.plain_result("已恢复自动路由。")
            return
        allowed = {"cheap", "strong", "reasoning", "vision", "long_context"}
        if tier not in allowed:
            yield event.plain_result(f"未知档位：{tier}\n可选：{'、'.join(sorted(allowed))}、auto")
            return
        self._session_tier[session] = tier
        yield event.plain_result(
            f"已把本会话路由档位固定为 {tier}。发送 /superai route auto 恢复自动。"
        )

    @superai_group.command("tools")
    async def superai_tools(self, event: AstrMessageEvent):
        """查看已注册的工具。"""
        names = self.tool_names
        if not names:
            yield event.plain_result("当前没有注册任何工具，请在插件配置中开启对应能力。")
            return
        yield event.plain_result("🧰 已注册工具：\n" + "\n".join(f"- {name}" for name in names))

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
        yield event.plain_result(result)

    # ------------------------------------------------------------------
    # 工作流执行
    # ------------------------------------------------------------------
    async def run_workflow(self, workflow: Workflow, *, user_input: str, session: str) -> str:
        """按顺序执行工作流，逐步输出中间结果。

        步骤之间的引用：

        - ``{{prev}}``：上一步输出
        - ``{{input}}``：用户输入的初始内容
        """
        if not workflow.steps:
            raise SuperAIError("该工作流没有配置任何步骤。")

        outputs: list[str] = []
        previous = ""
        route_tier = workflow.route or ""
        for index, step in enumerate(workflow.steps, 1):
            prompt = step.prompt
            if step.use_history and previous:
                prompt = f"{prompt}\n\n上一步结果：\n{previous}"
            prompt = prompt.replace("{{prev}}", previous).replace("{{input}}", user_input)
            prompt = prompt.replace("{{step}}", str(index))
            if not prompt.strip():
                continue
            provider_id = self.config.provider_map().get(route_tier, "")
            try:
                result = await self.llm_simple(prompt, provider_id=provider_id, session=session)
            except SuperAIError:
                self.workflows.record_run(workflow.name, success=False, detail=f"第 {index} 步失败")
                raise
            previous = result
            outputs.append(f"【第 {index} 步 · {step.name}】\n{result}")

        self.workflows.record_run(workflow.name, success=True, detail=f"{len(outputs)} 步完成")
        header = f"🔧 工作流「{workflow.name}」执行完成\n\n"
        return header + "\n\n".join(outputs)

    # ------------------------------------------------------------------
    # Studio 面板 API
    # ------------------------------------------------------------------
    async def api_status(self):
        """GET /status —— 运行状态。"""
        try:
            return json_response(
                {
                    "version": __version__,
                    "enabled": self.config.enabled,
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
                    "providers": sorted(self._available_provider_ids()),
                    "route_tiers": self.config.provider_map(),
                    "health": self.router.health(),
                    "uptime_sessions": len(self._session_tier),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[SuperAI] status API 失败：{exc}", exc_info=True)
            return error_response(str(exc), status_code=500)

    async def api_stats(self):
        """GET /stats —— 用量统计。"""
        try:
            days = int(request.query.get("days", 7) or 7)
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(90, days))
        self.metrics.flush()
        return json_response(self.metrics.summary(days))

    async def api_memory(self):
        """GET /memory —— 查询会话记忆。"""
        session = str(request.query.get("session", "") or "")
        if not session:
            return error_response("缺少 session 参数", status_code=400)
        query = str(request.query.get("q", "") or "")
        try:
            limit = int(request.query.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
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

    async def api_memory_clear(self):
        """POST /memory/clear —— 清空会话记忆。"""
        payload = await request.json(default={})
        session = str(payload.get("session") or "")
        if not session:
            return error_response("缺少 session 字段", status_code=400)
        removed = self.memory.clear(session)
        self.summaries.clear(session)
        return json_response({"removed": removed})

    async def api_tools(self):
        """GET /tools —— 工具列表。"""
        return json_response({"tools": self.tool_names})

    async def api_workflows(self):
        """GET /workflows —— 工作流列表与最近运行记录。"""
        workflows = self.workflows.load(self.config.workflow.get("workflows"))
        return json_response(
            {
                "workflows": [wf.to_dict() for wf in workflows.values()],
                "recent_runs": self.workflows.recent_runs(10),
            }
        )

    # ------------------------------------------------------------------
    # 文本输出
    # ------------------------------------------------------------------
    def _help_text(self) -> str:
        return (
            "🤖 SuperAI 使用帮助\n"
            "核心指令：\n"
            "/ai <问题>            向 SuperAI 提问（自动路由 + 记忆 + 工具）\n"
            "/superai status       查看运行状态\n"
            "/superai stats [天数]  查看用量统计\n"
            "/superai memory ...   记忆管理（list / search / clear）\n"
            "/superai route ...    查看或固定路由档位\n"
            "/superai tools        查看已注册工具\n"
            "/superai workflow ... 查看或运行工作流\n"
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
        lines = [
            f"⚙️ SuperAI v{__version__}",
            flags,
            f"可用模型：{len(providers)} 个"
            + (f"（{', '.join(providers[:5])}）" if providers else ""),
            f"工具：{len(self.tool_names)} 个",
            f"记忆总量：{self.memory.count()} 条",
            f"会话摘要数：{len(self.summaries.list_sessions())}",
        ]
        stats = self.metrics.today_stats()
        lines.append(f"今日用量：{stats.requests} 次请求 / {stats.total_tokens} tokens")
        return "\n".join(lines)
