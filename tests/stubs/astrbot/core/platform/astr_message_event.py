"""``AstrMessageEvent`` 替身。

只实现测试需要的部分：结果（result）语义必须与真实框架一致 ——
``plain_result()`` 负责「构造」，``set_result()`` 负责「挂到事件上」，
``stop_event()`` 只有在结果已存在时才把 ``result_type`` 置为 STOP。
"""

from __future__ import annotations

import enum
from typing import Any, ClassVar


class EventResultType(enum.Enum):
    CONTINUE = 1
    STOP = 2


class MessageEventResult:
    """``MessageEventResult`` 替身。"""

    def __init__(self, text: str = "") -> None:
        self.chain: list[Any] = []
        self.result_type = EventResultType.CONTINUE
        if text:
            self.message(text)

    def message(self, text: str) -> MessageEventResult:
        self.chain.append(type("Plain", (), {"text": text})())
        return self

    def stop_event(self) -> MessageEventResult:
        self.result_type = EventResultType.STOP
        return self

    def continue_event(self) -> MessageEventResult:
        self.result_type = EventResultType.CONTINUE
        return self

    def is_stopped(self) -> bool:
        return self.result_type == EventResultType.STOP


class AstrMessageEvent:
    """消息事件替身。"""

    def __init__(
        self,
        message_str: str = "",
        message_obj: Any = None,
        platform_meta: Any = None,
        session_id: str = "",
    ) -> None:
        self.message_str = message_str
        self.message_obj = message_obj
        self.platform_meta = platform_meta
        self.session_id = session_id
        self.role = "member"
        self.unified_msg_origin = ""
        self._extras: dict[str, Any] = {}
        self._result: MessageEventResult | None = None
        self._force_stopped = False

    # -- AstrBot 常用接口 -------------------------------------------------
    def get_extra(self, key: str, default: Any = None) -> Any:
        return self._extras.get(key, default)

    def set_extra(self, key: str, value: Any) -> None:
        self._extras[key] = value

    def get_messages(self) -> list:
        return list(getattr(self.message_obj, "message", []) or [])

    def get_group_id(self) -> str:
        return str(getattr(self.message_obj, "group_id", "") or "")

    def is_private_chat(self) -> bool:
        return False

    def is_admin(self) -> bool:
        return self.role == "admin"

    def get_sender_id(self) -> str:
        sender = getattr(self.message_obj, "sender", None)
        return str(getattr(sender, "user_id", "") or "")

    # -- 结果 -------------------------------------------------------------
    def set_result(self, result: MessageEventResult | str) -> None:
        if isinstance(result, str):
            result = MessageEventResult(result)
        self._result = result

    def get_result(self) -> MessageEventResult | None:
        return self._result

    def clear_result(self) -> None:
        self._result = None

    def plain_result(self, text: str) -> MessageEventResult:
        return MessageEventResult().message(text)

    def stop_event(self) -> None:
        self._force_stopped = True
        if self._result is None:
            self.set_result(MessageEventResult().stop_event())
        else:
            self._result.stop_event()

    def continue_event(self) -> None:
        self._force_stopped = False

    def is_stopped(self) -> bool:
        if self._force_stopped:
            return True
        if self._result is None:
            return False
        return self._result.is_stopped()

    def should_call_llm(self, call_llm: bool) -> None:  # pragma: no cover - 兼容接口
        self.call_llm = call_llm

    #: 真实框架里这是类属性；保留以便插件做 isinstance 检查
    ClassVar_placeholder: ClassVar[None] = None


__all__ = ["AstrMessageEvent", "EventResultType", "MessageEventResult"]
