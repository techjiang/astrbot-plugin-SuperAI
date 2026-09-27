"""会话摘要（滚动压缩）存储。

当一段对话轮数超过阈值时，SuperAI 会用「便宜档」模型把它压缩成一段摘要，
并在后续请求中用摘要替换掉过长的原始历史，从而：

- 控制 token 成本；
- 保留早期关键信息；
- 让长对话不至于「失忆」。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .store import JsonStore


@dataclass
class SessionSummary:
    """某个会话的滚动摘要。"""

    session: str
    summary: str = ""
    """摘要正文"""

    covered_rounds: int = 0
    """已覆盖的对话轮数"""

    covered_until: int = 0
    """已覆盖到的最后一条消息时间戳"""

    total_rounds: int = 0
    """累计对话轮数"""

    updated_at: int = field(default_factory=lambda: int(time.time()))
    history: list[str] = field(default_factory=list)
    """历史摘要存档（最近 N 条），便于回溯"""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, session: str, data: dict[str, Any]) -> SessionSummary:
        return cls(
            session=session,
            summary=str(data.get("summary") or ""),
            covered_rounds=int(data.get("covered_rounds", 0) or 0),
            covered_until=int(data.get("covered_until", 0) or 0),
            total_rounds=int(data.get("total_rounds", 0) or 0),
            updated_at=int(data.get("updated_at", 0) or 0),
            history=[str(item) for item in (data.get("history") or [])],
        )


class SummaryStore:
    """摘要仓库（会话 -> SessionSummary）。"""

    MAX_HISTORY = 5

    def __init__(self, store: JsonStore) -> None:
        self._store = store

    def _all(self) -> dict[str, dict[str, Any]]:
        data = self._store.get("summary")
        if not isinstance(data, dict):
            data = {}
            self._store.set(data, "summary")
        return data

    def get(self, session: str) -> SessionSummary:
        raw = self._all().get(session) or {}
        return SessionSummary.from_dict(session, raw if isinstance(raw, dict) else {})

    def update(
        self,
        session: str,
        summary: str,
        *,
        covered_rounds: int,
        covered_until: int,
        total_rounds: int,
    ) -> SessionSummary:
        data = self._all()
        previous = self.get(session)
        history = list(previous.history)
        if previous.summary:
            history.append(previous.summary)
        del history[: -self.MAX_HISTORY]
        payload = SessionSummary(
            session=session,
            summary=summary.strip(),
            covered_rounds=int(covered_rounds),
            covered_until=int(covered_until),
            total_rounds=int(total_rounds),
            updated_at=int(time.time()),
            history=history,
        ).to_dict()
        data[session] = payload
        self._store.save("summary")
        return SessionSummary.from_dict(session, payload)

    def bump_rounds(self, session: str, rounds: int = 1) -> SessionSummary:
        """累加轮数（不触发摘要）。"""
        data = self._all()
        summary = self.get(session)
        total = summary.total_rounds + max(0, rounds)
        data[session] = {
            **summary.to_dict(),
            "total_rounds": total,
            "updated_at": int(time.time()),
        }
        self._store.save("summary")
        return SessionSummary.from_dict(session, data[session])

    def clear(self, session: str) -> None:
        data = self._all()
        data.pop(session, None)
        self._store.save("summary")

    def list_sessions(self) -> list[str]:
        return list(self._all().keys())
