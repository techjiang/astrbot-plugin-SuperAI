"""AstrAgentContext 替身。"""

from dataclasses import dataclass
from typing import Any


@dataclass
class AstrAgentContext:
    context: Any = None
    event: Any = None
