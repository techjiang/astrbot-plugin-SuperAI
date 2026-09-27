"""SuperAI 统一异常定义。

所有对外抛出的异常都继承自 :class:`SuperAIError`，方便上层统一捕获，
避免插件内部异常直接冒泡导致消息处理链路中断。
"""

from __future__ import annotations


class SuperAIError(Exception):
    """SuperAI 基础异常。"""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        #: 面向用户的补充提示（例如「请在 WebUI 配置模型提供商」）
        self.hint = hint

    def friendly(self) -> str:
        """返回适合直接发给用户的可读文案。"""
        if self.hint:
            return f"{self.message}\n提示：{self.hint}"
        return self.message


class ConfigError(SuperAIError):
    """配置缺失或非法。"""


class ProviderUnavailableError(SuperAIError):
    """没有任何可用的模型提供商。"""


class RouteNotFoundError(SuperAIError):
    """路由规则未命中且没有可用的兜底提供商。"""


class StorageError(SuperAIError):
    """持久化读写失败。"""


class BudgetExceededError(SuperAIError):
    """超出预算 / 配额限制。"""


class ToolExecutionError(SuperAIError):
    """工具执行失败。"""
