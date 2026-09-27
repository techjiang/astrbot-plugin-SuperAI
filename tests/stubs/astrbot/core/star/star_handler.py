"""astrbot.core.star.star_handler 替身。"""

from __future__ import annotations

import enum


class EventType(enum.Enum):
    OnLLMRequestEvent = enum.auto()
    OnLLMResponseEvent = enum.auto()


class _Registry:
    def get_handlers_by_event_type(self, event_type, plugins_name=None):  # noqa: ANN001, ANN201
        return []


star_handlers_registry = _Registry()

__all__ = ["EventType", "star_handlers_registry"]
