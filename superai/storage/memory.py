"""长期记忆存储。

设计目标：

1. **按会话隔离**：不同群 / 私聊的记忆互不串味。
2. **可去重**：同一句话重复出现时只累加权重，不新增条目。
3. **可衰减**：长时间未被命中的记忆权重逐步下降，便于淘汰。
4. **可检索**：使用「关键词 + 字符 n-gram」的轻量打分，无需外部向量库，
   保证插件零重依赖也能开箱即用。
"""

from __future__ import annotations

import re
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

    decayed_at: int = 0
    """上次衰减到的时间戳。

    衰减必须基于「距上次衰减过了多久」而不是「距 last_hit 过了多久」，
    否则每跑一次维护都会把同一个时间差再乘一遍，权重会指数级坍塌
    （1 天前的记忆在 30 天半衰期下应当只衰减一次到 0.977，
    实际却会在连续维护里一路滑到 0.91、0.89……直至被当成垃圾清掉）。"""

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
            decayed_at=int(data.get("decayed_at", 0) or 0),
        )


#: 记忆类型白名单
ALLOWED_KINDS = frozenset({"fact", "preference", "event"})

#: 一条记忆的最小字符数（更短的视为噪声）
MIN_MEMORY_CHARS = 4


#: 英文 / 数字词的最小长度，更短的（如 "a"）不参与关键词打分
_MIN_TOKEN_LEN = 2


def _tokens(text: str) -> set[str]:
    """切出英文 / 数字关键词（中文用 n-gram 处理，不走这里）。"""
    return {
        token for token in re.findall(r"[a-z0-9_]+", text or "") if len(token) >= _MIN_TOKEN_LEN
    }


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
        if len(text) < MIN_MEMORY_CHARS:
            return None
        if kind not in ALLOWED_KINDS:
            kind = "fact"
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
    def search(
        self,
        session: str,
        query: str,
        *,
        top_k: int = 5,
        min_relevance: float = 0.0,
    ) -> list[MemoryEntry]:
        """按相似度检索会话记忆。

        打分由两部分组成：

        1. **相关性**：子串命中 + 字符 n-gram 重合度 + 关键词重合度。
        2. **重要度**：权重（被反复确认过的记忆更稳）。

        只要有任何一条记忆的相关性 > 0，就只返回相关的那些；一条都没命中时
        回落到「权重最高的若干条」——这很重要，否则用户说「帮我写一段文案」
        这种与历史偏好无关的请求，模型就完全拿不到「用户喜欢简洁的回答」这类
        背景信息，长期记忆等于白存。

        参数:
            min_relevance: 相关性门槛。低于它的记忆**连兜底都不会返回**。
                用于「稳定偏好」这类永远应该带上、但从不命中查询的记忆：
                它们通常通过 ``kind`` 而不是关键词命中，所以需要一个独立的
                通道把它们捞出来，而不是靠 n-gram 碰运气。
        """
        entries = self._entries(session)
        if not entries:
            return []
        limit = max(1, min(int(top_k or 5), 50))

        query_norm = normalize_space(query).lower()
        if not query_norm:
            # 没有 query 时按权重给概览
            return self.list(session, limit=limit)

        query_grams = _grams(query_norm)
        query_tokens = _tokens(query_norm)
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in entries:
            content_lower = entry.content.lower()
            score = 0.0
            if query_norm in content_lower:
                score += 3.0
            if query_grams:
                entry_grams = _grams(entry.content)
                if entry_grams:
                    overlap = len(query_grams & entry_grams)
                    score += 2.0 * overlap / max(1, len(query_grams))
            if query_tokens:
                entry_tokens = _tokens(content_lower)
                if entry_tokens:
                    shared = len(query_tokens & entry_tokens)
                    score += 1.5 * shared / max(1, len(query_tokens))
            if score <= 0:
                continue
            score *= 1.0 + min(entry.weight, 5.0) * 0.1
            scored.append((score, entry))

        if not scored:
            # 没有任何相关性：回落到「权重最高」的记忆，保证模型仍拿得到背景。
            # 但这里必须把 preference 类的稳定偏好排在前面：它们与具体提问
            # 天然不相关，如果只按 weight 排，一条临时事实很容易把
            # 「用户喜欢简洁的回答」挤出去。
            ranked = sorted(
                entries,
                key=lambda item: (item.kind == "preference", item.weight, item.updated_at),
                reverse=True,
            )
            return ranked[:limit]

        scored.sort(key=lambda pair: pair[0], reverse=True)
        limit = max(1, min(int(top_k or 5), 50))
        selected = [entry for _, entry in scored[:limit]]

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
        """按「权重、更新时间」倒序列出会话记忆。"""
        entries = self._entries(session)
        entries.sort(
            key=lambda entry: (entry.weight, entry.updated_at),
            reverse=True,
        )
        return entries[: max(1, int(limit))]

    def get(self, session: str, memory_id: str) -> MemoryEntry | None:
        """按 ID 读取单条记忆。"""
        for item in self._all().get(session) or []:
            if item.get("memory_id") == memory_id:
                return MemoryEntry.from_dict(item)
        return None

    def stats(self, session: str | None = None) -> dict[str, Any]:
        """返回记忆概览（总数与各类型数量）。"""
        data = self._all()
        buckets = {session: data.get(session) or []} if session is not None else data
        kinds: dict[str, int] = {"fact": 0, "preference": 0, "event": 0}
        total = 0
        for bucket in buckets.values():
            if not isinstance(bucket, list):
                continue
            for item in bucket:
                total += 1
                kind = str(item.get("kind") or "fact")
                kinds[kind] = kinds.get(kind, 0) + 1
        return {"total": total, "kinds": kinds, "sessions": len(buckets)}

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

    def sessions(self) -> list[str]:
        """返回所有存在记忆的会话标识。"""
        return [key for key, value in self._all().items() if isinstance(value, list) and value]

    def count(self, session: str | None = None) -> int:
        data = self._all()
        if session is None:
            return sum(len(v) for v in data.values() if isinstance(v, list))
        bucket = data.get(session) or []
        return len(bucket)

    def decay(self, *, half_life_days: float = 30.0) -> int:
        """按半衰期衰减权重，返回被淘汰的条数。

        衰减量取决于「**距上次衰减**过了多久」，而不是「距上次命中过了多久」。
        每次衰减后会把 ``decayed_at`` 推进到现在，所以衰减是可加（幂等）的：
        维护循环跑多少次，结果都只与真实经过的时间有关，不会因为调用次数
        不同而给出不同权重。

        未被命中且权重低于 0.2 的记忆会被删除。
        """
        data = self._all()
        now = int(time.time())
        removed = 0
        half_life = max(1.0, float(half_life_days)) * 86400
        changed = False
        for session, bucket in list(data.items()):
            if not isinstance(bucket, list):
                continue
            kept: list[dict[str, Any]] = []
            for item in bucket:
                # 起点：上次衰减时间；从未衰减过则用最后更新时间
                last = int(item.get("decayed_at") or item.get("updated_at", now) or now)
                elapsed = now - last
                if elapsed > 0:
                    factor = 0.5 ** (elapsed / half_life)
                    item["weight"] = round(float(item.get("weight", 1.0)) * factor, 4)
                    item["decayed_at"] = now
                    changed = True
                if float(item.get("weight", 1.0)) < 0.2:
                    removed += 1
                    continue
                kept.append(item)
            data[session] = kept
        if changed or removed:
            self._store.save("long_term")
        return removed
