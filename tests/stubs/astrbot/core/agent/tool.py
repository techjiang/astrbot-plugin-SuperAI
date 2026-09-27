"""FunctionTool / ToolSet 替身，仅保留插件用到的接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic

from typing_extensions import TypeVar

TContext = TypeVar("TContext", default=Any)

ToolExecResult = str


@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict


@dataclass
class FunctionTool(ToolSchema, Generic[TContext]):
    handler: Any = None
    active: bool = True

    async def call(self, context, **kwargs) -> ToolExecResult:
        raise NotImplementedError


@dataclass
class ToolSet:
    tools: list = field(default_factory=list)

    def empty(self) -> bool:
        return not self.tools

    def add_tool(self, tool) -> None:
        self.tools.append(tool)

    def get_tool(self, name: str):
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None
