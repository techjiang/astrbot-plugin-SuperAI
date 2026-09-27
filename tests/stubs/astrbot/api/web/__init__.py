"""astrbot.api.web 替身。

真实实现把请求绑定到 contextvar；测试里用同一个机制，
让 ``api_*`` 方法可以在不启动 HTTP 服务的情况下被直接调用。
"""

from __future__ import annotations

import contextlib
import json
from contextvars import ContextVar
from typing import Any


@contextlib.contextmanager
def _noop():  # pragma: no cover
    yield None


class _MultiDict(dict):
    """极简 MultiDict：``get(key, default)`` 返回第一个值。"""

    def __init__(self, pairs: list[tuple[str, str]] | None = None) -> None:
        super().__init__()
        self._pairs = list(pairs or [])
        for key, value in self._pairs:
            super().__setitem__(key, value)

    def get(self, key, default=None):  # noqa: ANN001, ANN201
        return super().get(key, default)


class PluginRequest:
    """插件 Web 请求替身。"""

    def __init__(
        self,
        query: list[tuple[str, str]] | None = None,
        body: Any = None,
        *,
        method: str = "GET",
        plugin_name: str | None = None,
        path_params: dict | None = None,
    ) -> None:
        self.method = method
        self.path = "/api"
        self.plugin_name = plugin_name
        self.path_params = path_params or {}
        self.query = _MultiDict(list(query or []))
        self.body_data = body

    async def json(self, default: Any = None) -> Any:
        return self.body_data if self.body_data is not None else default


_request_var: ContextVar[PluginRequest] = ContextVar("stub_plugin_request")


class _RequestProxy:
    def _current(self) -> PluginRequest:
        try:
            return _request_var.get()
        except LookupError as exc:  # pragma: no cover
            raise RuntimeError(
                "astrbot.api.web.request 仅在插件 Web 处理器内可用"
            ) from exc

    @property
    def query(self) -> _MultiDict:
        return self._current().query

    @property
    def method(self) -> str:
        return self._current().method

    async def json(self, default: Any = None) -> Any:
        return await self._current().json(default=default)


request = _RequestProxy()


@contextlib.contextmanager
def bind_request_context(plugin_request: PluginRequest):
    token = _request_var.set(plugin_request)
    try:
        yield plugin_request
    finally:
        _request_var.reset(token)


class _Response:
    """极简响应对象：保留 data / status_code 供断言。"""

    def __init__(self, data: Any, status_code: int = 200) -> None:
        self.data = data
        self.status_code = status_code
        self.body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")


def json_response(data: Any = None, *, status_code: int = 200, headers=None) -> _Response:  # noqa: ANN001
    return _Response({} if data is None else data, status_code)


def error_response(
    message: str, *, status_code: int = 400, data: Any = None, headers=None
) -> _Response:  # noqa: ANN001
    return _Response(
        {"status": "error", "message": message, "data": data}, status_code
    )


__all__ = [
    "PluginRequest",
    "bind_request_context",
    "error_response",
    "json_response",
    "request",
]
