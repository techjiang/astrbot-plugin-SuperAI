"""工具装配：根据配置决定注册哪些工具。"""

from __future__ import annotations

from dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.tool import ToolSet

from ..core.config import SuperAIConfig, _as_bool
from .kb_tools import KnowledgeSearchTool
from .memory_tools import RememberTool
from .recall_tools import HistorySummaryTool, RecallTool
from .web_tools import FetchUrlTool, WebSearchTool
from .workflow_tools import RunWorkflowTool


def build_toolset(config: SuperAIConfig, *, include_workflow: bool = True) -> ToolSet:
    """按配置构建 SuperAgent 的工具集。

    参数:
        config: 插件配置。
        include_workflow: 是否包含工作流工具（子 Agent 场景可关闭以避免递归）。
    """
    tools: list = []

    if config.long_term_enabled:
        tools.append(RememberTool())
        tools.append(RecallTool())
        # 只有开了滚动摘要，让模型读摘要才有意义
        if config.summary_enabled:
            tools.append(HistorySummaryTool())

    if config.web_enabled:
        tools.append(WebSearchTool())
        tools.append(FetchUrlTool())

    if config.kb_enabled and list(config.knowledge_base.get("kb_names") or []):
        tools.append(KnowledgeSearchTool())

    if include_workflow and config.workflow_enabled and config.workflow.get("workflows"):
        tools.append(RunWorkflowTool())

    toolset = ToolSet(tools=tools)
    logger.debug(f"[SuperAI] 已装配 {len(tools)} 个工具：{[tool.name for tool in tools]}")
    return toolset


@dataclass
class ToolSummary:
    """工具的中文说明（用于 /superai tools 与 Studio 面板）。"""

    name: str
    description: str = ""
    enabled: bool = True


#: 工具名 -> 中文用途说明
TOOL_LABELS: dict[str, str] = {
    "superai_web_search": "联网搜索实时信息",
    "superai_fetch_url": "抓取网页正文",
    "superai_knowledge_search": "检索 AstrBot 知识库",
    "superai_remember": "写入长期记忆",
    "superai_recall": "检索长期记忆",
    "superai_history_summary": "读取历史对话摘要",
    "superai_run_workflow": "触发预编排工作流",
}


def describe_tools(config: SuperAIConfig) -> list[ToolSummary]:
    """返回「当前配置下会注册哪些工具」的可读清单。"""
    try:
        tools = list(build_toolset(config).tools)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[SuperAI] 工具清单生成失败：{exc}")
        return []
    return [
        ToolSummary(
            name=tool.name,
            description=TOOL_LABELS.get(tool.name, getattr(tool, "description", "") or ""),
            enabled=_as_bool(getattr(tool, "active", True), True),
        )
        for tool in tools
    ]


__all__ = ["ToolSummary", "build_toolset", "describe_tools", "TOOL_LABELS"]
