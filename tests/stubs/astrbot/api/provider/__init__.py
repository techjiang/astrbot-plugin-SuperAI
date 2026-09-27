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


@dataclass
class LLMResponse:
    role: str = "assistant"
    _completion_text: str = ""
    result_chain: Any = None
    usage: TokenUsage | None = None

    @property
    def completion_text(self) -> str:
        if self.result_chain is not None:
            getter = getattr(self.result_chain, "get_plain_text", None)
            if callable(getter):
                return str(getter() or "")
        return self._completion_text

    @completion_text.setter
    def completion_text(self, value: str) -> None:
        self._completion_text = value


__all__ = ["LLMResponse", "ProviderRequest", "TokenUsage"]
