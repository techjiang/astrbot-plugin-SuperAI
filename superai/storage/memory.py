"""长期记忆存储。

设计目标：

1. **按会话隔离**：不同群 / 私聊的记忆互不串味。
2. **可去重**：同一句话重复出现时只累加权重，不新增条目。
3. **可衰减**：长时间未被命中的记忆权重逐步下降，便于淘汰。
4. **可检索**：使用「关键词 + 字符 n-gram」的轻量打分，无需外部向量库，
   保证插件零重依赖也能开箱即用。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.utils import fingerprint, normalize_space
from .store import JsonStore


@dataclass
class MemoryEntry:
    """一条长期记忆。"""

    memory_id: str
    """记忆 ID（内容指纹）"""

    session: str
    """所属会话（unified_msg_origin）"""

    content: str
    """记忆正文"""

    kind: str = "fact"
    """类型：fact（事实）/ preference（偏好）/ event（事件）"""

    weight: float = 1.0
    """权重，命中一次 +0.2，上限 5.0"""

    hits: int = 0
    """被检索命中次数"""

    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))
    source: str = ""
    """来源标记，例如 uid:12345"""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        return cls(
            memory_id=str(data.get("memory_id") or ""),
            session=str(data.get("session") or ""),
            content=str(data.get("content") or ""),
            kind=str(data.get("kind") or "fact"),
            weight=float(data.get("weight", 1.0)),
            hits=int(data.get("hits", 0)),
            created_at=int(data.get("created_at", 0) or 0),
            updated_at=int(data.get("updated_at", 0) or 0),
            source=str(data.get("source") or ""),
        )


def _grams(text: str, size: int = 2) -> set[str]:
    """生成字符 n-gram 集合，用于中英文混排的轻量相似度。"""
    normalized = normalize_space(text).lower()
    if not normalized:
        return set()
    if len(normalized) <= size:
        return {normalized}
    return {normalized[i : i + size] for i in range(len(normalized) - size + 1)}


class MemoryStore:
    """长期记忆仓库。

    数据结构::

        {
          "<session>": [MemoryEntry, ...]
        }
    """

    MAX_PER_SESSION = 400
    """单会话最多保留的记忆条数，超出后按权重淘汰。"""

    def __init__(self, store: JsonStore) -> None:
        self._store = store

    # -- 内部 -------------------------------------------------------------
    def _all(self) -> dict[str, list[dict[str, Any]]]:
        data = self._store.get("long_term")
        if not isinstance(data, dict):
            data = {}
            self._store.set(data, "long_term")
        return data

    def _entries(self, session: str) -> list[MemoryEntry]:
        raw = self._all().get(session) or []
        return [MemoryEntry.from_dict(item) for item in raw if isinstance(item, dict)]

    # -- 写入 -------------------------------------------------------------
    def add(
        self,
        session: str,
        content: str,
        *,
        kind: str = "fact",
        source: str = "",
        weight: float = 1.0,
    ) -> MemoryEntry | None:
        """新增一条记忆；内容为空或不满足最小长度时返回 ``None``。"""
        text = normalize_space(content)
        if len(text) < 4:
            return None
        memory_id = fingerprint(text, length=20)
        data = self._all()
        bucket = data.setdefault(session, [])
        now = int(time.time())
        for item in bucket:
            if item.get("memory_id") == memory_id:
                item["hits"] = int(item.get("hits", 0)) + 1
                item["weight"] = min(5.0, float(item.get("weight", 1.0)) + 0.2)
                item["updated_at"] = now
                self._store.save("long_term")
                return MemoryEntry.from_dict(item)
        entry = MemoryEntry(
            memory_id=memory_id,
            session=session,
            content=text,
            kind=kind,
            weight=max(0.1, float(weight)),
            source=source,
        )
        bucket.append(entry.to_dict())
        if len(bucket) > self.MAX_PER_SESSION:
            bucket.sort(
                key=lambda item: (
                    float(item.get("weight", 1.0)),
                    int(item.get("updated_at", 0)),
                ),
                reverse=True,
            )
            del bucket[self.MAX_PER_SESSION :]
        self._store.save("long_term")
        return entry

    def add_many(
        self,
        session: str,
        contents: list[str],
        *,
        kind: str = "fact",
        source: str = "",
    ) -> int:
        """批量写入，返回成功新增/更新的条数。"""
        count = 0
        for content in contents:
            if self.add(session, content, kind=kind, source=source):
                count += 1
        return count

    # -- 检索 -------------------------------------------------------------
    def search(self, session: str, query: str, *, top_k: int = 5) -> list[MemoryEntry]:
        """按相似度检索会话记忆。"""
        entries = self._entries(session)
        if not entries:
            return []
        query_grams = _grams(query)
        query_norm = normalize_space(query).lower()
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in entries:
            score = 0.0
            if query_norm and query_norm in entry.content.lower():
                score += 3.0
            if query_grams:
                entry_grams = _grams(entry.content)
                if entry_grams:
                    overlap = len(query_grams & entry_grams)
                    score += 2.0 * overlap / max(1, len(query_grams))
            if score <= 0:
                continue
            score *= 1.0 + min(entry.weight, 5.0) * 0.1
            scored.append((score, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected = [entry for _, entry in scored[: max(1, top_k)]]

        # 命中即加权，让「常用记忆」更稳
        if selected:
            touched = {entry.memory_id for entry in selected}
            data = self._all()
            now = int(time.time())
            for item in data.get(session, []):
                if item.get("memory_id") in touched:
                    item["hits"] = int(item.get("hits", 0)) + 1
                    item["weight"] = min(5.0, float(item.get("weight", 1.0)) + 0.2)
                    item["updated_at"] = now
            self._store.save("long_term")
        return selected

    def list(self, session: str, *, limit: int = 20) -> list[MemoryEntry]:
        """按更新时间倒序列出会话记忆。"""
        entries = self._entries(session)
        entries.sort(key=lambda entry: entry.updated_at, reverse=True)
        return entries[: max(1, limit)]

    # -- 清理 -------------------------------------------------------------
    def remove(self, session: str, memory_id: str) -> bool:
        data = self._all()
        bucket = data.get(session)
        if not isinstance(bucket, list):
            return False
        before = len(bucket)
        data[session] = [item for item in bucket if item.get("memory_id") != memory_id]
        self._store.save("long_term")
        return len(data[session]) < before

    def clear(self, session: str) -> int:
        """清空某个会话的记忆，返回删除条数。"""
        data = self._all()
        bucket = data.pop(session, [])
        self._store.save("long_term")
        return len(bucket) if isinstance(bucket, list) else 0

    def clear_all(self) -> None:
        self._store.set({}, "long_term")

    def count(self, session: str | None = None) -> int:
        data = self._all()
        if session is None:
            return sum(len(v) for v in data.values() if isinstance(v, list))
        bucket = data.get(session) or []
        return len(bucket)

    def decay(self, *, half_life_days: float = 30.0) -> int:
        """按半衰期衰减权重，返回被淘汰的条数。

        未被命中且权重低于 0.2 的记忆会被删除。
        """
        data = self._all()
        now = int(time.time())
        removed = 0
        half_life = max(1.0, float(half_life_days)) * 86400
        for session, bucket in list(data.items()):
            if not isinstance(bucket, list):
                continue
            kept: list[dict[str, Any]] = []
            for item in bucket:
                updated = int(item.get("updated_at", now) or now)
                age = max(0, now - updated)
                factor = 0.5 ** (age / half_life)
                item["weight"] = round(float(item.get("weight", 1.0)) * factor, 4)
                if item["weight"] < 0.2:
                    removed += 1
                    continue
                kept.append(item)
            data[session] = kept
        self._store.save("long_term")
        return removed
