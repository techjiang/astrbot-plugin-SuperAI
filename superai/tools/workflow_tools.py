"""工作流工具：让模型按名字触发预编排的多步任务。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .base import SuperAITool, extract_event, extract_tool_context


@dataclass
class RunWorkflowTool(SuperAITool):
    """执行一个已配置的工作流。"""

    name: str = "superai_run_workflow"
    description: str = (
        "执行插件中预定义的多步工作流。当用户请求的任务与某个工作流名称或描述高度匹配时调用。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工作流名称（配置中的 key）。"},
                "input": {
                    "type": "string",
                    "description": "传给工作流的输入文本，可为空。",
                },
            },
            "required": ["name"],
        }
    )

    async def run_tool(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        tool_ctx = extract_tool_context(context)
        event = extract_event(context)
        if tool_ctx is None or event is None:
            return "工作流当前不可用。"
        if not tool_ctx.config.workflow_enabled:
            return "工作流功能未启用。"

        name = str(kwargs.get("name") or "").strip()
        if not name:
            return "缺少工作流名称。"

        workflows = tool_ctx.plugin.workflows.load(tool_ctx.config.workflow.get("workflows"))
        workflow = workflows.get(name)
        if workflow is None:
            available = "、".join(workflows.keys()) or "（无）"
            return f"未找到工作流「{name}」。可用工作流：{available}"

        input_text = str(kwargs.get("input") or "").strip()
        result = await tool_ctx.plugin.run_workflow(
            workflow,
            user_input=input_text,
            session=str(getattr(event, "unified_msg_origin", "") or ""),
        )
        return result
