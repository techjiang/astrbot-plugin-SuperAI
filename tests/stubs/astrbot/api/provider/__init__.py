"""astrbot.api.provider 替身：ProviderRequest / LLMResponse / TokenUsage。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TokenUsage:
    input_other: int = 0
    input_cached: int = 0
    output: int = 0

    @property
    def total(self) -> int:
        return self.input_other + self.input_cached + self.output


@dataclass
class ProviderRequest:
    prompt: str | None = None
    session_id: str | None = ""
    image_urls: list[str] = field(default_factory=list)
    audio_urls: list[str] = field(default_factory=list)
    extra_user_content_parts: list = field(default_factory=list)
    func_tool: Any = None
    contexts: list[dict] = field(default_factory=list)
    system_prompt: str = ""
    model: str | None = None


class LLMResponse:
    """``LLMResponse`` 替身。

    真实实现是**手写 ``__init__``** 而非 dataclass，签名形如
    ``LLMResponse(role, completion_text=None, result_chain=None, ...)``。
    替身如果写成 dataclass，字段名会变成 ``_completion_text``，
    任何 ``LLMResponse(role=..., completion_text=...)`` 的调用都会抛
    ``TypeError`` —— 测试代码在无 AstrBot 环境下就会以与真实行为无关的
    方式失败。这里按真实签名实现，保证两套环境行为一致。
    """

    def __init__(
        self,
        role: str,
        completion_text: str | None = None,
        result_chain: Any = None,
        usage: TokenUsage | None = None,
        **kwargs: Any,
    ) -> None:
        self.role = role
        self.result_chain = result_chain
        self.usage = usage
        self._completion_text = completion_text or ""
        self.tools_call_args = kwargs.get("tools_call_args") or []
        self.tools_call_name = kwargs.get("tools_call_name") or []
        self.tools_call_ids = kwargs.get("tools_call_ids") or []
        self.reasoning_content = kwargs.get("reasoning_content")

    @property
    def completion_text(self) -> str:
        if self._completion_text:
            return self._completion_text
        if self.result_chain is not None:
            getter = getattr(self.result_chain, "get_plain_text", None)
            if callable(getter):
                return str(getter() or "")
        return ""

    @completion_text.setter
    def completion_text(self, value: str) -> None:
        self._completion_text = value or ""


__all__ = ["LLMResponse", "ProviderRequest", "TokenUsage"]
