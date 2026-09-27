"""带降级能力的 Agent 执行器。

AstrBot 自带的 ``tool_loop_agent`` 只会用**一个** provider。当它超时、限流或
报错时，整轮对话就失败了 —— 用户只看到一片沉默。

SuperAI 在这里补一层「换模型重试」：按路由降级链依次尝试，成功即返回，
并把 provider 的健康度反馈给 :class:`~superai.router.router.SuperRouter`，
让不稳定的模型后续自动排到后面。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from .core.errors import ProviderUnavailableError
from .core.utils import retry_async

if TYPE_CHECKING:  # pragma: no cover
    from astrbot.core.agent.tool import ToolSet

    from .main import SuperAIPlugin


@dataclass
class AgentOutcome:
    """一次 Agent 执行的结果。"""

    text: str = ""
    provider_id: str = ""
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    """真实的 token 用量。原先 ``/ai`` 只记「1 次请求」而不记 token，
    导致统计与配额（按 token 拦截）在指令路径上完全失真。"""

    @property
    def ok(self) -> bool:
        return bool(self.text)


class AgentExecutor:
    """把「多个候选模型 + 重试」封装成一次调用。"""

    def __init__(self, plugin: SuperAIPlugin) -> None:
        self.plugin = plugin

    async def run(
        self,
        *,
        event: Any,
        prompt: str,
        candidates: list[str],
        system_prompt: str | None = None,
        image_urls: list[str] | None = None,
        tools: ToolSet | None = None,
        use_agent: bool = True,
    ) -> AgentOutcome:
        """依次尝试候选 provider，返回第一个成功的结果。

        参数:
            event: AstrBot 消息事件（``tool_loop_agent`` 需要）。
            prompt: 用户输入。
            candidates: 候选 provider id（已按健康度排好序）。
            system_prompt: 系统提示词。
            image_urls: 随消息附带的图片。
            tools: 可用的工具集；``None`` 表示纯文本生成。
            use_agent: 是否使用 ``tool_loop_agent``（支持工具调用）。
        """
        candidates = [pid for pid in dict.fromkeys(candidates) if pid]
        if not candidates:
            raise ProviderUnavailableError(
                "没有可用的模型提供商",
                hint="请在 AstrBot WebUI 的「服务提供商」中配置并启用一个对话模型。",
            )

        agent_cfg = self.plugin.config.agent
        max_steps = max(1, int(agent_cfg.get("max_steps") or 12))
        tool_timeout = max(5, int(agent_cfg.get("tool_call_timeout") or 60))
        # 因为外层已经在做降级重试，内层不再交给 provider 自己无限重试
        per_attempt_timeout = max(10.0, float(self.plugin.config.router.get("timeout") or 120))

        outcome = AgentOutcome()
        usage_box: list[Any] = []
        for index, provider_id in enumerate(candidates, 1):
            outcome.attempts = index
            usage_box.clear()
            try:
                text = await asyncio.wait_for(
                    self._invoke(
                        event=event,
                        provider_id=provider_id,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        image_urls=image_urls,
                        tools=tools if use_agent else None,
                        max_steps=max_steps,
                        tool_timeout=tool_timeout,
                        usage_box=usage_box,
                    ),
                    timeout=per_attempt_timeout,
                )
            except asyncio.TimeoutError:
                reason = f"{provider_id} 超时（>{int(per_attempt_timeout)}s）"
                outcome.errors.append(reason)
                self.plugin.router.mark_failure(provider_id)
                logger.warning(f"[SuperAI] {reason}，尝试下一个模型")
                continue
            except Exception as exc:  # noqa: BLE001 - 任何失败都应触发降级
                reason = f"{provider_id} 调用失败：{exc}"
                outcome.errors.append(reason)
                self.plugin.router.mark_failure(provider_id)
                logger.warning(f"[SuperAI] {reason}，尝试下一个模型")
                continue

            if not (text or "").strip():
                # 空回复同样视为失败，否则用户会收到一条空白消息
                reason = f"{provider_id} 返回了空内容"
                outcome.errors.append(reason)
                self.plugin.router.mark_failure(provider_id)
                continue

            self.plugin.router.mark_success(provider_id)
            outcome.text = text
            outcome.provider_id = provider_id
            if usage_box:
                outcome.input_tokens, outcome.cached_tokens, outcome.output_tokens = usage_box[0]
            return outcome

        raise ProviderUnavailableError(
            "所有候选模型都调用失败",
            hint="最近一次错误：" + (outcome.errors[-1] if outcome.errors else "未知"),
        )

    async def _invoke(
        self,
        *,
        event: Any,
        provider_id: str,
        prompt: str,
        system_prompt: str | None,
        image_urls: list[str] | None,
        tools: ToolSet | None,
        max_steps: int,
        tool_timeout: int,
        usage_box: list[Any] | None = None,
    ) -> str:
        """真正发起一次 LLM 调用。"""
        context = self.plugin.context
        if tools is not None and not _toolset_empty(tools):
            resp = await context.tool_loop_agent(
                event=event,
                chat_provider_id=provider_id,
                prompt=prompt,
                image_urls=image_urls or None,
                tools=tools,
                max_steps=max_steps,
                tool_call_timeout=tool_timeout,
                system_prompt=system_prompt or None,
            )
        else:
            resp = await context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                image_urls=image_urls or None,
                system_prompt=system_prompt or None,
            )
        if usage_box is not None:
            usage_box.append(_usage_tuple(resp))
        return _response_text(resp)

    async def simple(
        self,
        prompt: str,
        *,
        candidates: list[str],
        system_prompt: str = "",
        attempts: int = 2,
    ) -> str:
        """轻量调用（摘要 / 事实抽取 / 工作流步骤），带一次重试。

        与 :meth:`run` 的区别：不使用工具、不需要 event，失败时按候选顺序
        重试，永不向调用方抛异常。
        """
        candidates = [pid for pid in dict.fromkeys(candidates) if pid]
        if not candidates:
            raise ProviderUnavailableError("没有可用的模型提供商")

        last_error: Exception | None = None
        for provider_id in candidates[: max(1, attempts)]:
            try:
                text = await retry_async(
                    lambda pid=provider_id: self.plugin.context.llm_generate(
                        chat_provider_id=pid,
                        prompt=prompt,
                        system_prompt=system_prompt or None,
                    ),
                    attempts=2,
                    base_delay=0.5,
                )
            except Exception as exc:  # noqa: BLE001 - 换下一个模型
                last_error = exc
                self.plugin.router.mark_failure(provider_id)
                logger.debug(f"[SuperAI] 轻量调用 {provider_id} 失败：{exc}")
                continue
            self.plugin.router.mark_success(provider_id)
            return _response_text(text)
        raise ProviderUnavailableError(
            "轻量模型调用失败",
            hint=str(last_error) if last_error else "",
        )


def _toolset_empty(tools: Any) -> bool:
    """判断 ToolSet 是否为空（兼容不同版本的 API）。"""
    if tools is None:
        return True
    tools_list = getattr(tools, "tools", None)
    if isinstance(tools_list, list):
        return not tools_list
    empty = getattr(tools, "empty", None)
    if callable(empty):
        try:
            return bool(empty())
        except Exception:  # noqa: BLE001
            return False
    return False


def _usage_tuple(resp: Any) -> tuple[int, int, int]:
    """从 ``LLMResponse`` 里取出 ``(input_other, input_cached, output)``。

    取不到时返回全 0；统计口径与 ``on_llm_response`` 保持一致 ——
    ``input_other`` 不含缓存命中，必须单独累加 ``input_cached``。
    """
    usage = getattr(resp, "usage", None)
    if usage is None:
        return (0, 0, 0)

    def _pick(name: str) -> int:
        try:
            return max(0, int(getattr(usage, name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    return (_pick("input_other"), _pick("input_cached"), _pick("output"))


def _response_text(resp: Any) -> str:
    """从 LLMResponse 里取纯文本。

    ``resp.completion_text`` 走的是 ``MessageChain.get_plain_text()``，会把
    Thinking 等组件过滤掉 —— 对「用户可见回复」来说这是对的；但对内部用途
    （摘要、事实抽取）也够用。图片组件不参与，因此这里直接用 ``result_chain``
    兜底。
    """
    if resp is None:
        return ""
    text = getattr(resp, "completion_text", "") or ""
    if text:
        return str(text)
    # 极少数 provider 只填了 result_chain
    chain = getattr(resp, "result_chain", None)
    getter = getattr(chain, "get_plain_text", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:  # noqa: BLE001 - tool_calls 等特殊链会抛错
            return ""
    return ""
