"""工具装配：根据配置决定注册哪些工具。"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.core.agent.tool import ToolSet

from ..core.config import SuperAIConfig
from .kb_tools import KnowledgeSearchTool
from .memory_tools import RememberTool
from .recall_tools import RecallTool
from .web_tools import FetchUrlTool, WebSearchTool
from .workflow_tools import RunWorkflowTool


def build_toolset(config: SuperAIConfig, *, include_workflow: bool = True) -> ToolSet:
    """按配置构建 SuperAgent 的工具集。

    参数:
        config: 插件配置。
        include_workflow: 是否包含工作流工具（子 Agent 场景可关闭以避免递归）。
    """
    tools: list = []

    if config.memory_enabled and config.memory.get("long_term_enabled", True):
        tools.append(RememberTool())
        tools.append(RecallTool())
        if config.memory.get("extract_facts", True) is False:
            # 仅在明确关闭自动提取时，仍保留手动工具
            pass

    if config.web_enabled:
        tools.append(WebSearchTool())
        tools.append(FetchUrlTool())

    if config.kb_enabled and config.knowledge_base.get("kb_names"):
        tools.append(KnowledgeSearchTool())

    if include_workflow and config.workflow_enabled:
        workflows = config.workflow.get("workflows") or {}
        if workflows:
            tools.append(RunWorkflowTool())

    toolset = ToolSet(tools=tools)
    logger.debug(f"[SuperAI] 已装配 {len(tools)} 个工具：{[tool.name for tool in tools]}")
    return toolset
