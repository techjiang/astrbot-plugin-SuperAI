"""记忆检索工具：让模型按需回忆历史。"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..core.utils import as_int, truncate
from .base import SuperAITool, extract_event, extract_tool_context


@dataclass
class RecallTool(SuperAITool):
    """按关键词回忆会话记忆。"""

    name: str = "superai_recall"
    description: str = (
        "按关键词检索本会话的长期记忆。当用户提到「之前说过」「你还记得吗」或需要历史偏好时调用。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词或一句话描述。",
                },
                "top_k": {
                    "type": "number",
                    "description": "返回条数，默认 5。",
                },
            },
            "required": ["query"],
        }
    )

    async def run_tool(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        tool_ctx = extract_tool_context(context)
        event = extract_event(context)
        if tool_ctx is None or event is None:
            return "记忆检索当前不可用。"

        query = str(kwargs.get("query") or "").strip()
        if not query:
            return "缺少检索关键词。"
        top_k = as_int(kwargs.get("top_k"), 5, minimum=1, maximum=20)

        session = str(getattr(event, "unified_msg_origin", "") or "")
        entries = tool_ctx.memory.search(session, query, top_k=top_k)
        lines = [f"- [{entry.kind}] {entry.content}" for entry in entries]
        if not lines:
            # 检索不中时把本轮已注入的记忆也回给模型，避免它反复调用工具
            injected: list[str] = []
            with contextlib.suppress(Exception):
                injected = list(event.get_extra("superai_injected_memories") or [])
            if injected:
                return "没有精确匹配，但本轮已提供这些记忆：\n" + "\n".join(
                    f"- {item}" for item in injected
                )
            return "没有找到相关记忆。"
        return "检索到的记忆：\n" + "\n".join(lines)


@dataclass
class HistorySummaryTool(SuperAITool):
    """让模型主动读取本会话的历史摘要。"""

    name: str = "superai_history_summary"
    description: str = (
        "读取本会话的历史对话摘要（长对话被压缩后的要点）。"
        "当用户询问「我们之前聊了什么」「前面讨论过什么」时调用。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def run_tool(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        tool_ctx = extract_tool_context(context)
        event = extract_event(context)
        if tool_ctx is None or event is None:
            return "摘要读取当前不可用。"
        session = str(getattr(event, "unified_msg_origin", "") or "")
        summary = tool_ctx.plugin.summaries.get(session)
        if not summary.summary:
            return "本会话还没有生成历史摘要。"
        return (
            f"历史摘要（累计 {summary.total_rounds} 轮，已覆盖 "
            f"{summary.covered_rounds} 轮）：\n{truncate(summary.summary, 2000, suffix='')}"
        )
