"""记忆 / 摘要 / 工作流存储测试。"""

from superai.storage.memory import MemoryStore
from superai.storage.store import JsonStore
from superai.storage.summary import SummaryStore
from superai.storage.workflows import WorkflowStore


def make_store(tmp_path, name="memory.json"):
    return JsonStore(tmp_path / name)


def test_memory_add_and_count(tmp_path):
    store = MemoryStore(make_store(tmp_path))
    entry = store.add("s1", "用户喜欢简洁的回答")
    assert entry is not None
    assert store.count("s1") == 1
    assert store.count("s2") == 0


def test_memory_rejects_too_short(tmp_path):
    store = MemoryStore(make_store(tmp_path))
    assert store.add("s1", "hi") is None
    assert store.count("s1") == 0


def test_memory_dedup_increments_weight(tmp_path):
    store = MemoryStore(make_store(tmp_path))
    store.add("s1", "用户在做 AstrBot 插件")
    store.add("s1", "用户在做 AstrBot 插件")
    entries = store.list("s1")
    assert len(entries) == 1
    assert entries[0].weight > 1.0
    assert entries[0].hits == 1


def test_memory_search_returns_relevant(tmp_path):
    store = MemoryStore(make_store(tmp_path))
    store.add("s1", "用户喜欢的编程语言是 Python")
    store.add("s1", "用户住在上海浦东")
    results = store.search("s1", "编程语言", top_k=2)
    assert results
    assert "Python" in results[0].content


def test_memory_search_isolated_by_session(tmp_path):
    store = MemoryStore(make_store(tmp_path))
    store.add("s1", "用户喜欢的编程语言是 Python")
    assert store.search("s2", "编程语言") == []


def test_memory_clear(tmp_path):
    store = MemoryStore(make_store(tmp_path))
    store.add("s1", "用户喜欢简洁的回答")
    assert store.clear("s1") == 1
    assert store.count("s1") == 0


def test_memory_decay_removes_stale(tmp_path):
    store = MemoryStore(make_store(tmp_path))
    entry = store.add("s1", "很久以前记下的一条事实")
    assert entry is not None
    # 把 updated_at 调到 3 年前，权重应被衰减到阈值以下
    data = store._all()
    data["s1"][0]["updated_at"] = entry.updated_at - 3 * 365 * 86400
    store._store.save("long_term")
    removed = store.decay(half_life_days=30)
    assert removed == 1
    assert store.count("s1") == 0


def test_summary_store_roundtrip(tmp_path):
    store = SummaryStore(make_store(tmp_path, "summary.json"))
    store.update(
        "s1", "用户讨论了插件架构。", covered_rounds=12, covered_until=100, total_rounds=12
    )
    summary = store.get("s1")
    assert summary.summary == "用户讨论了插件架构。"
    assert summary.covered_rounds == 12

    store.update("s1", "新摘要", covered_rounds=24, covered_until=200, total_rounds=24)
    summary = store.get("s1")
    assert summary.summary == "新摘要"
    assert summary.history == ["用户讨论了插件架构。"]


def test_summary_bump_rounds(tmp_path):
    store = SummaryStore(make_store(tmp_path, "summary.json"))
    store.bump_rounds("s1", 3)
    assert store.get("s1").total_rounds == 3


def test_workflow_load_from_config(tmp_path):
    store = WorkflowStore(make_store(tmp_path, "workflows.json"))
    raw = {
        "早报": {
            "description": "每日早报",
            "route": "cheap",
            "steps": [
                {"name": "收集", "prompt": "总结今天科技新闻"},
                {"name": "翻译", "prompt": "把 {{prev}} 翻译成英文"},
            ],
        }
    }
    workflows = store.load(raw)
    assert "早报" in workflows
    workflow = workflows["早报"]
    assert workflow.route == "cheap"
    assert len(workflow.steps) == 2
    assert workflow.steps[0].name == "收集"


def test_workflow_max_steps_truncated(tmp_path):
    store = WorkflowStore(make_store(tmp_path, "workflows.json"), max_steps=1)
    workflows = store.load({"长流程": {"steps": [{"prompt": "a"}, {"prompt": "b"}]}})
    assert len(workflows["长流程"].steps) == 1


def test_workflow_skips_empty_definitions(tmp_path):
    store = WorkflowStore(make_store(tmp_path, "workflows.json"))
    workflows = store.load({"空": {"steps": []}, "非法": "not-a-dict"})
    assert workflows == {}


def test_workflow_runs_recorded(tmp_path):
    store = WorkflowStore(make_store(tmp_path, "workflows.json"))
    store.record_run("早报", success=True, detail="2 步完成")
    runs = store.recent_runs(5)
    assert runs and runs[0]["name"] == "早报"
    assert runs[0]["success"] is True


# ---------------------------------------------------------------------------
# 记忆衰减：必须与「维护次数」无关
# ---------------------------------------------------------------------------
def test_decay_is_idempotent_across_maintenance_runs(tmp_path):
    """回归：连续跑维护时，衰减量只能取决于真实经过时间，不能逐次叠加。

    旧实现用 ``now - updated_at`` 当衰减区间，而 ``updated_at`` 在衰减时
    不会推进，于是每跑一次维护就把同一个时间差再乘一遍，
    一条 1 天前的记忆会在 30 天半衰期下被反复打折直至清掉。
    """
    import time

    from superai.storage.memory import MemoryStore
    from superai.storage.store import JsonStore

    store = MemoryStore(JsonStore(tmp_path / "memory.json"))
    store.add("s", "这是一条比较长的事实内容")

    raw = store._all()
    raw["s"][0]["updated_at"] = int(time.time()) - 86400  # 1 天前
    store._store.save("long_term")

    weights = []
    for _ in range(4):
        store.decay(half_life_days=30)
        entries = store._all().get("s") or []
        assert entries, "1 天前的记忆不该被淘汰"
        weights.append(entries[0]["weight"])

    assert len(set(weights)) == 1, f"重复衰减不应改变权重，实际 {weights}"
    # 30 天半衰期、1 天 → 0.5^(1/30) ≈ 0.9772
    assert 0.97 <= weights[0] <= 0.98


def test_decay_eventually_removes_stale_memory(tmp_path):
    """真正过期的记忆仍应被淘汰（衰减功能不能被修坏）。"""
    import time

    from superai.storage.memory import MemoryStore
    from superai.storage.store import JsonStore

    store = MemoryStore(JsonStore(tmp_path / "memory.json"))
    store.add("s", "这是一条很久以前的事实")
    raw = store._all()
    raw["s"][0]["updated_at"] = int(time.time()) - 86400 * 400  # 400 天前
    raw["s"][0]["weight"] = 0.5
    store._store.save("long_term")

    removed = store.decay(half_life_days=30)
    assert removed == 1
    assert store._all().get("s") == []


# ---------------------------------------------------------------------------
# min_relevance 必须真正生效（曾经是死参数）
# ---------------------------------------------------------------------------
def _memory_store(tmp_path):
    from superai.storage.memory import MemoryStore
    from superai.storage.store import JsonStore

    return MemoryStore(JsonStore(tmp_path / "long_term.json"))


def test_search_min_relevance_filters_unrelated_entries(tmp_path):
    """``min_relevance > 0`` 时，低于门槛的记忆连兜底都不能返回。

    该参数此前只写在签名与文档里、实现中从未被读取 ——
    调用方以为过滤生效了，实际拿到的是「权重最高的若干条」。
    """
    store = _memory_store(tmp_path)
    store.add("s", "用户喜欢简洁的回答", kind="preference")
    store.add("s", "项目叫 SuperAI", kind="fact")

    # 没有门槛：无关查询也走兜底（模型总有背景可参考）
    fallback = store.search("s", "帮我写一段文案")
    assert {e.content for e in fallback} == {"用户喜欢简洁的回答", "项目叫 SuperAI"}

    # 有门槛且完全没命中：只回落到 preference 类稳定偏好
    strict = store.search("s", "帮我写一段文案", min_relevance=0.5)
    assert [e.content for e in strict] == ["用户喜欢简洁的回答"]


def test_search_min_relevance_keeps_relevant_entries(tmp_path):
    """有门槛时，真正相关的记忆仍必须被返回。"""
    store = _memory_store(tmp_path)
    store.add("s", "项目叫 SuperAI", kind="fact")
    store.add("s", "用户喜欢简洁的回答", kind="preference")

    hits = store.search("s", "SuperAI", min_relevance=0.5)
    assert [e.content for e in hits] == ["项目叫 SuperAI"]


def test_search_without_min_relevance_behaviour_unchanged(tmp_path):
    """默认（门槛 0）行为与此前保持一致。"""
    store = _memory_store(tmp_path)
    store.add("s", "用户喜欢简洁的回答", kind="preference")
    hits = store.search("s", "简洁")
    assert [e.content for e in hits] == ["用户喜欢简洁的回答"]
