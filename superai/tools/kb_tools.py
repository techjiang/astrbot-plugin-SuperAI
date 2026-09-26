"""知识库检索工具：把 AstrBot 知识库暴露给模型按需检索。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .base import SuperAITool, extract_tool_context


@dataclass
class KnowledgeSearchTool(SuperAITool):
    """检索 AstrBot 知识库。"""

    name: str = "superai_knowledge_search"
    description: str = (
        "在已配置的知识库中检索资料。当用户询问产品文档、内部资料、专有领域知识时调用。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题或关键词。"},
                "kb_names": {
                    "type": "array",
                    "description": "要检索的知识库名称列表，留空使用插件配置的默认知识库。",
                    "items": {"type": "string"},
                },
                "top_k": {"type": "number", "description": "返回条数，默认 5。"},
            },
            "required": ["query"],
        }
    )

    async def run_tool(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        tool_ctx = extract_tool_context(context)
        if tool_ctx is None or not tool_ctx.config.kb_enabled:
            return "知识库检索未启用。"

        query = str(kwargs.get("query") or "").strip()
        if not query:
            return "缺少检索问题。"

        kb_names = kwargs.get("kb_names")
        if not isinstance(kb_names, list) or not kb_names:
            kb_names = list(tool_ctx.config.knowledge_base.get("kb_names") or [])
        kb_names = [str(name).strip() for name in kb_names if str(name).strip()]
        if not kb_names:
            return "没有配置可检索的知识库。"

        try:
            top_k = int(kwargs.get("top_k") or tool_ctx.config.knowledge_base.get("top_k") or 5)
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(20, top_k))

        kb_manager = getattr(tool_ctx.plugin.context, "kb_manager", None)
        if kb_manager is None:
            return "当前 AstrBot 版本不支持知识库。"

        try:
            result = await kb_manager.retrieve(query, kb_names, top_m_final=top_k)
        except Exception as exc:  # noqa: BLE001 - 知识库异常不应中断对话
            logger.warning(f"[SuperAI] 知识库检索失败：{exc}")
            return f"知识库检索失败：{exc}"

        if not result:
            return "知识库中没有找到相关内容。"

        results = result.get("results") if isinstance(result, dict) else None
        if not results:
            context_text = result.get("context_text") if isinstance(result, dict) else ""
            return context_text or "知识库中没有找到相关内容。"

        lines = ["知识库检索结果："]
        for index, item in enumerate(results, 1):
            lines.append(
                f"{index}. 来源：{item.get('kb_name')} / {item.get('doc_name')}"
                f"（相关度 {float(item.get('score') or 0):.2f}）\n   {item.get('content', '')[:500]}"
            )
        return "\n".join(lines)
