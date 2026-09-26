"""记忆检索工具：让模型按需回忆历史。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

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
        try:
            top_k = int(kwargs.get("top_k") or 5)
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(20, top_k))

        session = str(getattr(event, "unified_msg_origin", "") or "")
        entries = tool_ctx.memory.search(session, query, top_k=top_k)
        if not entries:
            return "没有找到相关记忆。"
        lines = [f"- [{entry.kind}] {entry.content}" for entry in entries]
        return "检索到的记忆：\n" + "\n".join(lines)
