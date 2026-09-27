"""``AstrBotMessage`` / ``MessageMember`` 替身。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Group:
    group_id: str = ""
    group_name: str = ""


@dataclass
class MessageMember:
    user_id: str = "user"
    nickname: str = "user"


@dataclass
class AstrBotMessage:
    """最小 AstrBot 消息对象替身。"""

    type: Any = None
    self_id: str = "bot"
    session_id: str = ""
    message_id: str = ""
    group: Group | None = None
    sender: MessageMember = field(default_factory=MessageMember)
    message: list = field(default_factory=list)
    message_str: str = ""
    raw_message: object = None
    timestamp: int = field(default_factory=lambda: int(time.time()))

    @property
    def group_id(self) -> str:
        return self.group.group_id if self.group else ""


__all__ = ["AstrBotMessage", "Group", "MessageMember"]
