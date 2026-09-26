"""工具基类与依赖注入容器。

SuperAI 的工具需要访问 AstrBot 上下文、配置、记忆库等对象。
为了避免在每个工具里重复取依赖，这里用一个轻量的 ``ToolContext`` 承载它们，
并以 ``FunctionTool`` 子类的形式定义工具（AstrBot >= 4.5.7 推荐方式）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

if TYPE_CHECKING:  # pragma: no cover
    from ..main import SuperAIPlugin


@dataclass
class ToolContext:
    """工具运行时依赖。"""

    plugin: SuperAIPlugin

    @property
    def config(self):  # noqa: ANN201 - 避免循环导入
        return self.plugin.config

    @property
    def memory(self):  # noqa: ANN201
        return self.plugin.memory

    @property
    def metrics(self):  # noqa: ANN201
        return self.plugin.metrics


def extract_event(context: ContextWrapper[AstrAgentContext]) -> Any:
    """从运行上下文里取出消息事件。"""
    try:
        return context.context.event
    except AttributeError:  # pragma: no cover - 防御式
        return None


def extract_tool_context(context: ContextWrapper[AstrAgentContext]) -> ToolContext | None:
    """从运行上下文里取出插件实例并包装成 :class:`ToolContext`。"""
    try:
        astr_context = context.context.context
        plugin = getattr(astr_context, "_superai_plugin", None)
    except AttributeError:  # pragma: no cover
        plugin = None
    if plugin is None:
        logger.debug("[SuperAI] 工具运行时未找到插件实例，部分能力将不可用")
        return None
    return ToolContext(plugin=plugin)


class SuperAITool(FunctionTool[AstrAgentContext]):
    """SuperAI 工具基类，统一异常兜底。"""

    active: bool = True

    async def call(  # type: ignore[override]
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        try:
            return await self.run_tool(context, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 工具不允许把异常抛回给模型
            logger.error(f"[SuperAI] 工具 {self.name} 执行失败：{exc}", exc_info=True)
            return f"工具 {self.name} 执行失败：{exc}"

    async def run_tool(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:  # pragma: no cover - 抽象
        raise NotImplementedError


@dataclass
class PlaceholderTool(SuperAITool):
    """占位工具，用于在不具备依赖时保持工具集结构稳定。"""

    name: str = "placeholder"
    description: str = "占位工具，不应被调用。"
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})

    async def run_tool(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        return "该能力当前不可用。"
