"""astrbot.api.event 替身：只保留插件用到的装饰器与事件类型。"""

from __future__ import annotations

from typing import Any


class AstrMessageEvent:
    """消息事件替身。"""

    def __init__(self, umo: str = "stub:GroupMessage:1") -> None:
        self.unified_msg_origin = umo

    def plain_result(self, text: str):  # noqa: ANN201
        return ("plain", text)


def _passthrough(*_args: Any, **_kwargs: Any):
    """返回「原样返回被装饰对象」的装饰器。"""

    def decorator(func):  # noqa: ANN001
        return func

    return decorator


class _Commandable:
    """``RegisteringCommandable`` 替身：支持 ``.command(...)`` 级联注册。"""

    def command(self, *_args: Any, **_kwargs: Any):  # noqa: ANN201
        return _passthrough()

    def group(self, *_args: Any, **_kwargs: Any):  # noqa: ANN201
        return _passthrough()

    def custom_filter(self, *_args: Any, **_kwargs: Any):  # noqa: ANN201
        return _passthrough()


def _command_group(*_args: Any, **_kwargs: Any):
    """``@filter.command_group`` 替身：被装饰函数会变成可级联注册的对象。"""

    def decorator(func):  # noqa: ANN001
        return _Commandable()

    return decorator


class _Filter:
    """``astrbot.api.event.filter`` 替身。"""

    command = staticmethod(_passthrough)
    command_group = staticmethod(_command_group)
    on_llm_request = staticmethod(_passthrough)
    on_llm_response = staticmethod(_passthrough)
    on_agent_begin = staticmethod(_passthrough)
    on_agent_done = staticmethod(_passthrough)
    event_message_type = staticmethod(_passthrough)
    regex = staticmethod(_passthrough)
    permission_type = staticmethod(_passthrough)


filter = _Filter()

__all__ = ["AstrMessageEvent", "filter"]
