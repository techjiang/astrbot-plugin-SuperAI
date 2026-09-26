"""TextPart / Message 替身。"""

from dataclasses import dataclass


@dataclass
class TextPart:
    text: str

    def mark_as_temp(self):
        return self
