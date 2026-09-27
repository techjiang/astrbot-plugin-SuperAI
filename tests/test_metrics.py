"""用量统计与预算测试。"""

from superai.core.metrics import MetricsCollector


def test_record_and_summary(tmp_path):
    collector = MetricsCollector(tmp_path)
    collector.record(
        session="s1",
        provider_id="p1",
        route="cheap",
        input_tokens=100,
        output_tokens=50,
        latency_ms=800,
    )
    collector.record(
        session="s1", provider_id="p2", route="strong", input_tokens=10, output_tokens=5
    )

    stats = collector.today_stats()
    assert stats.requests == 2
    assert stats.total_tokens == 165
    assert stats.avg_latency_ms == 400
    assert stats.providers == {"p1": 1, "p2": 1}

    summary = collector.summary(7)
    assert summary["requests"] == 2
    assert summary["total_tokens"] == 165


def test_failures_counted(tmp_path):
    collector = MetricsCollector(tmp_path)
    collector.record(success=False, error="boom")
    collector.record(success=True)
    stats = collector.today_stats()
    assert stats.requests == 2
    assert stats.failures == 1


def test_budget_check(tmp_path):
    collector = MetricsCollector(tmp_path)
    assert collector.check_budget(token_budget=1000, request_budget=10) == ""
    collector.record(input_tokens=900, output_tokens=200)
    assert "token 用量已达上限" in collector.check_budget(token_budget=1000)
    assert "请求数已达上限" in collector.check_budget(request_budget=1)


def test_persistence_roundtrip(tmp_path):
    collector = MetricsCollector(tmp_path)
    collector.record(provider_id="p1", input_tokens=42)
    collector.flush()

    reloaded = MetricsCollector(tmp_path)
    assert reloaded.today_stats().input_tokens == 42


def test_detail_records_capped(tmp_path):
    collector = MetricsCollector(tmp_path)
    for index in range(40):
        collector.record(input_tokens=index)
    assert len(collector.today_stats().last_records) <= collector.MAX_KEEP_RECORDS


def test_retention_prunes_old_days(tmp_path):
    collector = MetricsCollector(tmp_path, retention_days=2)
    for date in ("2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"):
        bucket = collector._bucket(date)
        bucket.requests = 1
    collector._dirty = True
    collector.flush()
    reloaded = MetricsCollector(tmp_path, retention_days=2)
    assert len(reloaded._days) <= 2


# ---------------------------------------------------------------------------
# 只读路径不得产生副作用
# ---------------------------------------------------------------------------
def test_today_stats_does_not_create_empty_bucket(tmp_path):
    """``today_stats()`` 是只读 API，不得凭空创建当天的桶。

    ``/superai status``、Studio 面板、预算检查都会调用它。早前实现里
    ``_bucket()`` 无条件 ``self._days[key] = stats``，于是只要用户打开过面板，
    即使当天一条消息都没有也会多出一个「0 请求」的日期：

    - 它会挤占 ``retention_days`` 的保留名额，把真正的历史数据挤出裁剪窗口；
    - 它会让「最近 N 天趋势」里出现无意义的 0 值空洞。
    """
    from superai.core.metrics import MetricsCollector

    collector = MetricsCollector(tmp_path)
    collector.today_stats()
    collector.summary(days=7)
    assert collector._days == {}, "只读接口不应该往 _days 里写任何东西"
    # range_stats 现在按**自然日**补全区间，所以返回的是「有数据的桶」+
    # 「区间内的空桶」，但空桶必须是临时对象，绝不能落进 _days。
    assert collector._days == {}, "range_stats 补全空桶时不得写回 _days"
    buckets = collector.range_stats(30)
    assert len(buckets) == 30
    assert all(bucket.requests == 0 for bucket in buckets)
    assert collector._days == {}, "补全后的空桶不得被持久化"


def test_read_only_queries_do_not_persist_phantom_day(tmp_path):
    """只读后再正常记录，落盘内容里不应出现空桶。"""
    import json

    from superai.core.metrics import DailyStats, MetricsCollector

    collector = MetricsCollector(tmp_path)
    collector._days["2026-09-20"] = DailyStats(date="2026-09-20", requests=5)
    collector.today_stats()  # 模拟面板 / 状态查询
    collector.record(input_tokens=3)
    collector.flush()

    saved = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))["days"]
    phantom = [date for date, item in saved.items() if item["requests"] == 0]
    assert not phantom, f"落盘出现幽灵空桶：{phantom}"
    # 落盘里只有「真有数据」的日期（历史那条 + 今天那条）
    assert len(saved) == 2
    assert len(collector._days) == 2


def test_record_still_creates_bucket(tmp_path):
    """写入路径必须照旧创建当天的桶（否则统计会丢数据）。"""
    from superai.core.metrics import MetricsCollector

    collector = MetricsCollector(tmp_path)
    collector.record(input_tokens=10, output_tokens=5)
    assert collector.today_stats().requests == 1
    assert collector.today_stats().total_tokens == 15


# ---------------------------------------------------------------------------
# 「最近 N 天」必须是自然日区间，而不是「最近 N 个有数据的日期」
# ---------------------------------------------------------------------------
def test_range_stats_spans_calendar_days(tmp_path):
    """``range_stats`` 必须按自然日补全区间。

    早前实现是 ``sorted(self._days)[-N:]``，只取「最近 N 个有数据的日期」。
    只要有日期断档（用户停用几天 / 机器关机 / retention 调大后重新统计），
    就会把**跨月甚至跨季度**的桶算进「最近 7 天」：

        range_stats(7) -> ['2026-03-11', '2026-09-27']

    于是 ``/superai stats 7`` 与 Studio 面板的数字凭空翻倍，
    而界面上完全看不出异常。
    """
    import time

    from superai.core.metrics import DailyStats, MetricsCollector

    collector = MetricsCollector(tmp_path)
    stale = time.strftime("%Y-%m-%d", time.localtime(time.time() - 200 * 86400))
    collector._days[stale] = DailyStats(date=stale, requests=5, input_tokens=1000)
    collector.record(input_tokens=1)  # 触发今天的桶

    buckets = collector.range_stats(7)
    assert len(buckets) == 7, "最近 7 天必须返回 7 个自然日的桶"
    dates = [bucket.date for bucket in buckets]
    assert stale not in dates, "200 天前的数据不得出现在「最近 7 天」里"
    # 日期必须连续且以今天结尾
    today = time.strftime("%Y-%m-%d", time.localtime())
    assert dates[-1] == today
    assert dates == sorted(dates)


def test_summary_excludes_data_outside_the_window(tmp_path):
    """区间外的历史数据不得被算进「最近 N 天」的总览。"""
    import time

    from superai.core.metrics import DailyStats, MetricsCollector

    collector = MetricsCollector(tmp_path)
    stale = time.strftime("%Y-%m-%d", time.localtime(time.time() - 200 * 86400))
    collector._days[stale] = DailyStats(date=stale, requests=5, input_tokens=1000)
    collector.record(input_tokens=300)

    summary = collector.summary(7)
    assert summary["requests"] == 1, f"只应统计区间内的 1 次请求，实际 {summary['requests']}"
    assert summary["input_total"] == 300
    assert len(summary["days"]) == 7, "趋势数据应覆盖完整区间（含空桶）"


def test_range_stats_does_not_persist_padding_buckets(tmp_path):
    """补全空桶必须是只读行为，不得写进 ``_days``（否则会挤占 retention）。"""
    from superai.core.metrics import MetricsCollector

    collector = MetricsCollector(tmp_path)
    collector.range_stats(90)
    collector.summary(90)
    assert collector._days == {}, "补全空桶不得产生幽灵日期"
