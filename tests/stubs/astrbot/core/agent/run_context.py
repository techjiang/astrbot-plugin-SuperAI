"""ContextWrapper 替身。"""

from dataclasses import dataclass, field
from typing import Any, Generic

from typing_extensions import TypeVar

TContext = TypeVar("TContext", default=Any)


@dataclass
class ContextWrapper(Generic[TContext]):
    context: Any
    messages: list = field(default_factory=list)
    tool_call_timeout: int = 120
