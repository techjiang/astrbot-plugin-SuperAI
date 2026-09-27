"""astrbot.core.provider 替身：转发到 astrbot.api.provider。"""

from astrbot.api.provider import LLMResponse, ProviderRequest, TokenUsage  # noqa: F401

__all__ = ["LLMResponse", "ProviderRequest", "TokenUsage"]
