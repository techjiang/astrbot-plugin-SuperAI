"""记忆处理链路测试 —— 守住「摘要与事实抽取互相踩踏」的静默失效。

背景：这一组断言来自一次真实联调发现的缺陷。``_prepare_memory`` 里曾经写成::

    summary = await self.memory_service.maybe_summarize(session, history)
    if summary:
        return summary
    if self._should_extract_facts(session, history):
        ...  # 后台抽取事实

问题在于 ``maybe_summarize`` 在**未达到摘要阈值**时会把「已有摘要」原样返回
（这是它设计上的行为，用来给调用方提供当前摘要）。而摘要一旦生成过就一直
非空，于是 ``extract_facts`` 分支对任何产生过摘要的会话**永远不会执行** ——
「自动抽取事实」这个主打功能静默失效，日志里没有任何异常。

这一组测试专门覆盖「摘要非空」与「事实抽取应触发」这两个条件同时成立的情形。
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

from superai.core.config import build_config
from superai.memory_service import MemoryService
from superai.storage.store import JsonStore
from superai.storage.summary import SummaryStore


class _Agent:
    """记录调用的假 Agent。"""

    def __init__(self) -> None:
        self.summary_calls = 0
        self.extract_calls = 0

    async def simple(self, prompt: str, *, candidates, attempts: int = 2) -> str:
        if "摘要器" in prompt:
            self.summary_calls += 1
            return "这是摘要"
        self.extract_calls += 1
        return '[{"content": "用户偏好简洁回答", "kind": "preference"}]'


class _Memory:
    """记录写入的假记忆仓库。"""

    def __init__(self) -> None:
        self.added: list[str] = []

    def add(self, session: str, content: str, **kwargs):
        self.added.append(content)
        return types.SimpleNamespace(content=content)


class _Plugin:
    """够 MemoryService 使用的最小插件替身。"""

    def __init__(self, tmp_path: Path, *, trigger: int = 12, router: dict | None = None) -> None:
        self.summaries = SummaryStore(JsonStore(tmp_path / "summary.json"))
        self.agent = _Agent()
        self.memory = _Memory()
        # 用**真实**的 SuperAIConfig，而不是 SimpleNamespace：
        # 配置项是否真的存在、property 是否真的能算出来，本身就是要验证的东西。
        payload: dict = {"memory": {"summary_trigger_rounds": trigger}}
        if router:
            payload["router"] = router
        self.config = build_config(payload)

    async def fallback_provider_id(self, session: str = "") -> str:
        return "p-default"

    def last_active_ts(self) -> int:
        return 0


def test_maybe_summarize_returns_existing_summary_below_threshold(tmp_path):
    """未达阈值时返回**已有摘要**（非空）—— 这正是早退陷阱的成因。"""
    plugin = _Plugin(tmp_path)
    service = MemoryService(plugin)

    # 先造出一条已有摘要，且轮数未达阈值
    plugin.summaries.update("s", "旧摘要", covered_rounds=10, covered_until=0, total_rounds=10)
    summary = asyncio.run(service.maybe_summarize("s", ["消息"]))
    assert summary == "旧摘要", "未达阈值应返回已有摘要而不是空串"
    assert plugin.agent.summary_calls == 0, "未达阈值不应调用模型"


def test_fact_extraction_possible_even_when_summary_exists(tmp_path):
    """摘要非空时，事实抽取仍然必须能够触发。

    这是对 ``if summary: return summary`` 那个致命早退的回归测试：
    直接验证 ``MemoryService.extract_facts`` 在摘要已经存在的情况下仍可工作。
    """
    plugin = _Plugin(tmp_path)
    service = MemoryService(plugin)
    plugin.summaries.update("s", "旧摘要", covered_rounds=10, covered_until=0, total_rounds=10)
    written = asyncio.run(
        service.extract_facts("s", "\n".join(f"用户说了第 {i} 句话" for i in range(6)))
    )
    assert written == ["用户偏好简洁回答"], "摘要存在时事实抽取也必须能写入记忆"
    assert plugin.agent.extract_calls == 1


def test_extract_facts_respects_config_switches(tmp_path):
    """关闭长期记忆或抽取开关时不应调用模型。"""
    plugin = _Plugin(tmp_path)
    plugin.config.memory["extract_facts"] = False
    service = MemoryService(plugin)
    assert asyncio.run(service.extract_facts("s", "用户说了一句足够长的话用来测试")) == []
    assert plugin.agent.extract_calls == 0


def test_extract_facts_skips_short_dialogue(tmp_path):
    plugin = _Plugin(tmp_path)
    service = MemoryService(plugin)
    assert asyncio.run(service.extract_facts("s", "短")) == []
    assert plugin.agent.extract_calls == 0


def test_light_candidates_fall_back_to_default_provider(tmp_path):
    """只配了默认模型的用户（最常见部署），轻量任务必须有候选。"""
    plugin = _Plugin(tmp_path)
    service = MemoryService(plugin)
    assert service.light_candidates() == []
    assert asyncio.run(service.resolve_light_candidates("s")) == ["p-default"]


def test_light_candidates_prefer_configured_tiers(tmp_path):
    """配了档位时应优先用档位，不被会话默认模型抢走。"""
    plugin = _Plugin(
        tmp_path,
        router={"cheap_provider_id": "p-cheap", "strong_provider_id": "p-strong"},
    )
    service = MemoryService(plugin)
    assert service.light_candidates() == ["p-cheap", "p-strong"]
    assert asyncio.run(service.resolve_light_candidates("s")) == ["p-cheap", "p-strong"]


def test_light_candidates_deduplicate(tmp_path):
    """摘要专用模型与档位相同时不应重复。"""
    plugin = _Plugin(tmp_path, router={"cheap_provider_id": "p-cheap"})
    plugin.config.memory["summary_provider_id"] = "p-cheap"
    service = MemoryService(plugin)
    assert service.light_candidates() == ["p-cheap"]


# ---------------------------------------------------------------------------
# 调用方（插件本体）的分支可达性：直接扫源码，防止早退写法被重新引入
# ---------------------------------------------------------------------------
def test_prepare_memory_does_not_early_return_on_summary():
    """``_prepare_memory`` 不得在「摘要非空」时提前 return。

    这是一个**结构性**断言：只要有人把
    ``if summary: return summary`` 重新写回去，这条测试就会失败。
    用源码扫描而不是行为测试，是因为真正的分支在插件类里、
    需要完整的 AstrBot 事件与 Context 才能驱动 —— 而这里要守的是「写法」。
    """
    import re

    root = Path(__file__).resolve().parents[1]
    source = (root / "main.py").read_text(encoding="utf-8")
    match = re.search(
        r"async def _prepare_memory.*?(?=\n    def |\n    async def )",
        source,
        re.S,
    )
    assert match, "未找到 _prepare_memory 方法"
    body = match.group(0)

    assert "_should_extract_facts" in body, "_prepare_memory 必须调用 _should_extract_facts"
    assert not re.search(r"if summary:\s*\n\s*return", body), (
        "_prepare_memory 出现了 `if summary: return` 早退 —— "
        "maybe_summarize 在未达阈值时会返回已有摘要，"
        "这会让事实抽取分支永远不可达（静默失效）"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
