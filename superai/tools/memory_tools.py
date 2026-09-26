"""记忆相关工具：让模型能够主动记住 / 回忆信息。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .base import SuperAITool, extract_event, extract_tool_context


@dataclass
class RememberTool(SuperAITool):
    """把用户明确要求记住的信息写入长期记忆。"""

    name: str = "superai_remember"
    description: str = (
        "把一条值得长期记住的信息写入记忆库。"
        "当用户明确表示「记住这个」「以后都这样」或提供了稳定偏好/事实时调用。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的内容，尽量写成一句独立的陈述句。",
                },
                "kind": {
                    "type": "string",
                    "description": "记忆类型：fact（事实）/ preference（偏好）/ event（事件）。",
                    "enum": ["fact", "preference", "event"],
                },
            },
            "required": ["content"],
        }
    )

    async def run_tool(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        tool_ctx = extract_tool_context(context)
        event = extract_event(context)
        if tool_ctx is None or event is None:
            return "记忆功能当前不可用。"

        content = str(kwargs.get("content") or "").strip()
        kind = str(kwargs.get("kind") or "fact")
        if not content:
            return "没有可记住的内容。"

        session = str(getattr(event, "unified_msg_origin", "") or "")
        entry = tool_ctx.memory.add(
            session,
            content,
            kind=kind,
            source=f"uid:{getattr(event, 'get_sender_id', lambda: '')() or 'unknown'}",
        )
        if entry is None:
            return "内容过短，已忽略。"
        return f"已记住：{entry.content}"
