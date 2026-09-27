"""astrbot.api.star 替身。"""

from __future__ import annotations

from typing import Any


class Context:
    """插件上下文替身。"""

    def __init__(self) -> None:
        self.registered_web_apis: list = []

    def register_web_api(self, route, handler, methods, desc) -> None:  # noqa: ANN001
        self.registered_web_apis.append((route, handler, methods, desc))


class Star:
    """插件基类替身。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        self.context = context
        self._config = config

    async def initialize(self) -> None: ...

    async def terminate(self) -> None: ...


class StarMetadata:  # noqa: D101
    pass


__all__ = ["Context", "Star", "StarMetadata"]


def __getattr__(name: str) -> Any:  # pragma: no cover
    raise AttributeError(name)
