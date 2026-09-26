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
