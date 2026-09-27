"""``MessageType`` 替身。"""

from __future__ import annotations

import enum


class MessageType(enum.Enum):
    GROUP_MESSAGE = "GroupMessage"
    FRIEND_MESSAGE = "FriendMessage"
    OTHER_MESSAGE = "OtherMessage"


__all__ = ["MessageType"]
