"""TextPart / Message 替身。

真实 ``ContentPart`` 用 pydantic 的 ``PrivateAttr`` 记录 ``_no_save``，
这里用普通属性模拟，让「仅本轮有效」的断言在 stub 环境下也成立。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TextPart:
    text: str
    _no_save: bool = field(default=False, repr=False)

    def mark_as_temp(self):
        """标记为「仅本轮有效」，不写入历史。"""
        self._no_save = True
        return self

    def model_dump_for_context(self) -> dict:
        data = {"type": "text", "text": self.text}
        if self._no_save:
            data["_no_save"] = True
        return data
