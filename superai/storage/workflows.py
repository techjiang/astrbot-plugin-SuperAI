"""工作流（Workflow）定义与执行记录。

一个工作流就是「按顺序执行的一串步骤」，每步是一句自然语言指令，
可选引用上一步的输出（``{{prev}}``）。这让非技术用户也能在配置里
编排多步 AI 任务，例如：

    name: 每日早报
    steps:
      - 用一句话总结今天的科技新闻
      - 把「{{prev}}」翻译成英文
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .store import JsonStore


@dataclass
class WorkflowStep:
    """工作流中的一步。"""

    name: str
    prompt: str
    use_history: bool = False
    """是否把上一步输出拼进 prompt（等价于手动写 {{prev}}）"""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStep:
        if isinstance(data, str):
            return cls(name=data[:20] or "step", prompt=data)
        return cls(
            name=str(data.get("name") or "step"),
            prompt=str(data.get("prompt") or data.get("text") or ""),
            use_history=bool(data.get("use_history", False)),
        )


@dataclass
class Workflow:
    """一个工作流定义。"""

    name: str
    steps: list[WorkflowStep] = field(default_factory=list)
    description: str = ""
    route: str = ""
    """指定使用的路由档位，留空则按默认策略"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "route": self.route,
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> Workflow:
        raw_steps = data.get("steps") or []
        steps = [WorkflowStep.from_dict(item) for item in raw_steps if item]
        return cls(
            name=str(data.get("name") or name),
            description=str(data.get("description") or ""),
            route=str(data.get("route") or ""),
            steps=steps,
        )


class WorkflowStore:
    """工作流读取 + 运行记录。"""

    def __init__(self, store: JsonStore, *, max_steps: int = 8) -> None:
        self._store = store
        self._max_steps = max(1, int(max_steps))

    def load(self, raw: dict[str, Any] | None = None) -> dict[str, Workflow]:
        """从配置或落盘数据载入工作流定义。

        优先使用传入的配置（WebUI 中编辑），为空时回落到落盘数据。
        """
        source = raw if raw else self._store.get("workflows")
        if not isinstance(source, dict):
            return {}
        workflows: dict[str, Workflow] = {}
        for key, value in source.items():
            if not isinstance(value, dict):
                continue
            workflow = Workflow.from_dict(str(key), value)
            if not workflow.name:
                workflow.name = str(key)
            workflow.steps = workflow.steps[: self._max_steps]
            if workflow.steps:
                workflows[str(key)] = workflow
        return workflows

    def save(self, workflows: dict[str, Workflow]) -> None:
        self._store.set({name: wf.to_dict() for name, wf in workflows.items()}, "workflows")

    def record_run(self, name: str, *, success: bool, detail: str = "") -> None:
        """记录一次运行结果，保留最近 50 条。"""
        data = self._store.get("workflow_runs")
        if not isinstance(data, list):
            data = []
        data.append(
            {
                "name": name,
                "ts": int(time.time()),
                "success": bool(success),
                "detail": detail[:500],
            }
        )
        del data[:-50]
        self._store.set(data, "workflow_runs")

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        data = self._store.get("workflow_runs")
        if not isinstance(data, list):
            return []
        return list(reversed(data[-max(1, limit) :]))
