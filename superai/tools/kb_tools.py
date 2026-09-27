"""知识库检索工具：把 AstrBot 知识库暴露给模型按需检索。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..core.utils import as_int, normalize_space, truncate
from .base import SuperAITool, extract_tool_context


async def retrieve_kb(
    context: Any,
    query: str,
    kb_names: list[str],
    *,
    top_k: int = 5,
    score_threshold: float = 0.0,
) -> str:
    """调用 AstrBot 的 KB 接口，把结果整理成可直接喂给模型的文本。

    统一在这里做「结果格式归一化」，让工具调用与自动注入两条路径共用。
    """
    kb_manager = getattr(context, "kb_manager", None)
    if kb_manager is None:
        return ""

    query = normalize_space(query)
    if not query or not kb_names:
        return ""

    try:
        result = await kb_manager.retrieve(query, kb_names, top_m_final=top_k)
    except Exception as exc:  # noqa: BLE001 - 知识库异常不应中断对话
        logger.warning(f"[SuperAI] 知识库检索失败：{exc}")
        return ""

    if not result:
        return ""

    if isinstance(result, dict):
        results = result.get("results")
        if not results:
            return str(result.get("context_text") or "")
        lines: list[str] = []
        for index, item in enumerate(results, 1):
            if not isinstance(item, dict):
                continue
            try:
                score = float(item.get("score") or 0)
            except (TypeError, ValueError):
                score = 0.0
            # 相关度门槛：低于门槛的片段不进上下文，避免用噪声把提示词撑大
            if score_threshold > 0 and score < score_threshold:
                continue
            content = truncate(normalize_space(item.get("content")), 500, suffix="")
            if not content:
                continue
            lines.append(
                f"{index}. 来源：{item.get('kb_name')} / {item.get('doc_name')}"
                f"（相关度 {float(item.get('score') or 0):.2f}）\n   {content}"
            )
        return "\n".join(lines)

    return str(result)


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

        top_k = as_int(
            kwargs.get("top_k") or tool_ctx.config.knowledge_base.get("top_k"),
            5,
            minimum=1,
            maximum=20,
        )
        text = await retrieve_kb(tool_ctx.plugin.context, query, kb_names, top_k=top_k)
        if not text:
            return "知识库中没有找到相关内容。"
        return "知识库检索结果：\n" + text
