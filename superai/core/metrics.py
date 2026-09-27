"""用量统计与预算控制。

SuperAI 会在每次 LLM 调用后记录 token 用量与耗时，用于：

- ``/superai stats`` 指令展示
- WebUI Studio 面板展示
- 每日预算（token / 请求数）拦截

统计以「天」为粒度落盘（JSON），超过保留天数的数据会被裁剪。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import logger

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(slots=True)
class TokenUsageRecord:
    """单次请求的用量记录。"""

    ts: int
    """时间戳（秒）"""

    session: str = ""
    """会话标识（unified_msg_origin）"""

    provider_id: str = ""
    """实际使用的提供商 ID"""

    route: str = ""
    """命中的路由档位"""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    success: bool = True
    error: str = ""

    @property
    def input_total(self) -> int:
        """输入 token 总量（含命中缓存的部分）。

        provider 上报的 ``input_other`` 是**不含**缓存命中的输入量，
        ``input_cached`` 才是命中部分；两者相加才是真实的输入规模。
        """
        return self.input_tokens + self.cached_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_total + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DailyStats:
    """按天聚合的统计。"""

    date: str
    requests: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms_sum: int = 0
    providers: dict[str, int] = field(default_factory=dict)
    routes: dict[str, int] = field(default_factory=dict)
    last_records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def input_total(self) -> int:
        """输入 token 总量（含缓存命中部分）。"""
        return self.input_tokens + self.cached_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_total + self.output_tokens

    @property
    def avg_latency_ms(self) -> int:
        if not self.requests:
            return 0
        return int(self.latency_ms_sum / self.requests)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可持久化的字典（只包含原始字段）。"""
        return asdict(self)

    def view(self) -> dict[str, Any]:
        """序列化为面向展示的字典（含派生字段）。"""
        return {
            **asdict(self),
            "input_total": self.input_total,
            "total_tokens": self.total_tokens,
            "avg_latency_ms": self.avg_latency_ms,
        }


#: DailyStats 中所有整型计数字段，用于安全地从磁盘恢复
_INT_FIELDS = {
    "requests",
    "failures",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "latency_ms_sum",
}


class MetricsCollector:
    """用量统计收集器（内存聚合 + 按天落盘）。"""

    MAX_KEEP_RECORDS = 20
    """每天最多保留的明细条数，避免文件无限膨胀。"""

    def __init__(
        self,
        data_dir: Path,
        *,
        retention_days: int = 30,
        debug: bool = False,
    ) -> None:
        self._dir = Path(data_dir)
        self._file = self._dir / "metrics.json"
        self._retention_days = max(1, int(retention_days or 30))
        self._debug = debug
        self._days: dict[str, DailyStats] = {}
        self._dirty = False
        self._load()

    # -- 存取 -------------------------------------------------------------
    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            payload = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"[SuperAI] 读取统计数据失败，将重新开始统计：{exc}")
            return
        days = payload.get("days") if isinstance(payload, dict) else None
        if not isinstance(days, dict):
            return
        for date, raw in days.items():
            if not isinstance(raw, dict):
                continue
            if not _DATE_RE.fullmatch(str(date)):
                # 历史版本写入的聚合键（如 last_7_days），直接丢弃
                continue
            stats = DailyStats(date=date)
            for key, value in raw.items():
                if key == "date":
                    continue
                if key in {"providers", "routes"}:
                    if isinstance(value, dict):
                        setattr(stats, key, {str(k): int(v) for k, v in value.items()})
                    continue
                if key == "last_records":
                    setattr(stats, key, list(value) if isinstance(value, list) else [])
                    continue
                if key not in _INT_FIELDS:
                    continue  # 忽略派生字段 / 未知字段，保证向前兼容
                try:
                    setattr(stats, key, int(value))
                except (TypeError, ValueError):
                    continue
            self._days[date] = stats

    def flush(self) -> None:
        """把内存中的统计写入磁盘。"""
        if not self._dirty:
            return
        self._prune()
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": int(time.time()),
                "days": {date: stats.to_dict() for date, stats in self._days.items()},
            }
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._file)
            self._dirty = False
        except OSError as exc:  # pragma: no cover - 磁盘异常难以模拟
            logger.warning(f"[SuperAI] 写入统计数据失败：{exc}")

    def _prune(self) -> None:
        if len(self._days) <= self._retention_days:
            return
        for date in sorted(self._days)[: -self._retention_days]:
            self._days.pop(date, None)

    def prune_legacy_keys(self) -> int:
        """清理历史版本遗留的非日期键（如 ``last_7_days``）。

        返回被清理的键数量。老版本会把聚合结果误写入按天字典，
        升级后需要把这些脏数据移除，否则会一直被当作「历史日期」参与排序。
        """
        removed = 0
        for date in list(self._days):
            if not _DATE_RE.fullmatch(date):
                self._days.pop(date, None)
                removed += 1
        if removed:
            self._dirty = True
        return removed

    # -- 记录 -------------------------------------------------------------
    @staticmethod
    def today() -> str:
        return time.strftime("%Y-%m-%d", time.localtime())

    def _bucket(self, date: str | None = None, *, create: bool = True) -> DailyStats:
        """取某个日期（默认今天）的聚合桶。

        参数:
            create: 为 ``True`` 时不存在就新建（写入路径）；
                为 ``False`` 时只读，不存在的日期返回一个临时的空桶、
                **不会**写进 ``self._days``。

        「只读也要新建桶」看起来无害，实际会污染统计：
        ``/superai status``、Studio 面板、预算检查都会调用 ``today_stats()``，
        只要用户打开过面板，即使当天一条消息都没有，也会凭空多出一个
        「0 请求」的日期。它会挤占 ``retention_days`` 的保留名额、把真正的
        历史数据挤出裁剪窗口，并让「最近 N 天趋势」里出现无意义的 0 值空洞。
        """
        key = date or self.today()
        stats = self._days.get(key)
        if stats is None:
            stats = DailyStats(date=key)
            if create:
                self._days[key] = stats
        return stats

    def record(
        self,
        *,
        session: str = "",
        provider_id: str = "",
        route: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        latency_ms: int = 0,
        success: bool = True,
        error: str = "",
    ) -> TokenUsageRecord:
        """记录一次请求，返回明细对象。"""
        record = TokenUsageRecord(
            ts=int(time.time()),
            session=session,
            provider_id=provider_id,
            route=route,
            input_tokens=max(0, int(input_tokens or 0)),
            output_tokens=max(0, int(output_tokens or 0)),
            cached_tokens=max(0, int(cached_tokens or 0)),
            latency_ms=max(0, int(latency_ms or 0)),
            success=success,
            error=error[:200],
        )
        bucket = self._bucket()
        bucket.requests += 1
        if not success:
            bucket.failures += 1
        bucket.input_tokens += record.input_tokens
        bucket.output_tokens += record.output_tokens
        bucket.cached_tokens += record.cached_tokens
        bucket.latency_ms_sum += record.latency_ms
        if provider_id:
            bucket.providers[provider_id] = bucket.providers.get(provider_id, 0) + 1
        if route:
            bucket.routes[route] = bucket.routes.get(route, 0) + 1
        bucket.last_records.append(record.to_dict())
        del bucket.last_records[: -self.MAX_KEEP_RECORDS]
        self._dirty = True
        return record

    def record_failure(
        self,
        *,
        session: str = "",
        provider_id: str = "",
        route: str = "",
        latency_ms: int = 0,
        error: str = "",
    ) -> TokenUsageRecord:
        """记录一次失败请求（不计 token，但计入失败数）。"""
        return self.record(
            session=session,
            provider_id=provider_id,
            route=route,
            latency_ms=latency_ms,
            success=False,
            error=error,
        )

    # -- 查询 -------------------------------------------------------------
    def today_stats(self) -> DailyStats:
        """今日统计（只读，不会凭空创建当天的桶）。"""
        return self._bucket(create=False)

    def range_stats(self, days: int = 7) -> list[DailyStats]:
        """返回最近 N 天的统计（含今天），按日期升序。"""
        keys = sorted(self._days)[-max(1, days) :]
        return [self._days[key] for key in keys]

    def summary(self, days: int = 7) -> dict[str, Any]:
        """聚合最近 N 天的总览数据。

        注意：这里使用独立的临时 ``DailyStats`` 做累加，**不会**把它写进
        ``self._days``。早前的实现把聚合结果存进了按天字典，flush 后会在统计
        文件里留下 ``last_7_days`` 这类假日期，既污染 retention 裁剪，也会让
        ``/superai stats`` 的数字随调用次数不断翻倍。
        """
        buckets = self.range_stats(days)
        total = DailyStats(date=f"last_{max(1, int(days))}_days")
        for bucket in buckets:
            total.requests += bucket.requests
            total.failures += bucket.failures
            total.input_tokens += bucket.input_tokens
            total.output_tokens += bucket.output_tokens
            total.cached_tokens += bucket.cached_tokens
            total.latency_ms_sum += bucket.latency_ms_sum
            for key, value in bucket.providers.items():
                total.providers[key] = total.providers.get(key, 0) + value
            for key, value in bucket.routes.items():
                total.routes[key] = total.routes.get(key, 0) + value
        data = total.view()
        data["days"] = [bucket.view() for bucket in buckets]
        return data

    # -- 预算 -------------------------------------------------------------
    def check_budget(self, *, token_budget: int = 0, request_budget: int = 0) -> str:
        """检查今日预算，返回超限原因；未超限返回空字符串。"""
        stats = self.today_stats()
        if token_budget > 0 and stats.total_tokens >= token_budget:
            return f"今日 token 用量已达上限（{stats.total_tokens}/{token_budget}）"
        if request_budget > 0 and stats.requests >= request_budget:
            return f"今日请求数已达上限（{stats.requests}/{request_budget}）"
        return ""
