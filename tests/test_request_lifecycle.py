"""请求生命周期（起始时间记账）契约测试。

``on_llm_request`` 会为每轮请求记一个「起始时间」，``on_llm_response``
再把它取出来算耗时。两者必须**成对**，否则：

- 被拒绝的群 / 未启用的任务类型 / 超预算这三条路径都在钩子里提前 ``return``，
  而 ``on_llm_response`` 只在真正跑完 LLM 时才会被调用 ——
  起始时间记录会**只进不出**，每来一条被拒绝的消息就泄漏一条；
- 等到真正需要统计时，``_pop_request_started`` 会取到很久以前那条旧时间戳，
  单次延迟被算成几小时，``/superai stats`` 与 Studio 面板的「平均耗时」彻底失真。

这类问题不会报错、不会被单元测试发现，只能靠针对性的生命周期断言拦住。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from conftest import load_superai_entry


def _make_plugin(tmp_path, monkeypatch, config=None):
    module = load_superai_entry()
    monkeypatch.setattr(module, "get_astrbot_data_path", lambda: str(tmp_path))

    class _Ctx:
        def __init__(self) -> None:
            self.registered_web_apis: list = []
            self.llm_tools: list = []

        def register_web_api(self, route, handler, methods, desc) -> None:
            self.registered_web_apis.append((route, handler, methods, desc))

        def add_llm_tools(self, *tools) -> None:
            self.llm_tools.extend(tools)

        def get_all_providers(self):
            return []

        async def get_current_chat_provider_id(self, umo=None):
            return ""

    plugin = module.SuperAIPlugin(_Ctx(), config or {"enabled": True})
    return module, plugin


class _Event:
    """够钩子用的最小事件替身。"""

    def __init__(self, group_id: str = "") -> None:
        self.unified_msg_origin = "test:GroupMessage:1"
        self._group_id = group_id
        self._extras: dict = {}
        self.result = None

    def get_group_id(self) -> str:
        return self._group_id

    def is_private_chat(self) -> bool:
        return False

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value) -> None:
        self._extras[key] = value

    def get_messages(self) -> list:
        return []

    def plain_result(self, text):
        return ("plain", text)

    def set_result(self, result) -> None:
        self.result = result

    def stop_event(self) -> None:
        self._extras["stopped"] = True


def _request(module, prompt: str = "你好"):
    from astrbot.core.provider.entities import ProviderRequest

    return ProviderRequest(prompt=prompt)


def test_denied_group_does_not_leak_started_record(tmp_path, monkeypatch):
    """被 deny_groups 拒绝的群不应在 ``_request_started`` 里留下记录。"""
    module, plugin = _make_plugin(
        tmp_path, monkeypatch, {"enabled": True, "commands": {"deny_groups": ["blocked"]}}
    )

    async def _run() -> None:
        for _ in range(20):
            await plugin.on_llm_request(_Event("blocked"), _request(module))

    asyncio.run(_run())
    assert len(plugin._request_started) == 0, (
        "被拒绝的群每来一条消息就泄漏一条起始时间记录；"
        "这些记录永远不会被 on_llm_response 弹出，会污染后续的耗时统计"
    )


def test_disabled_task_does_not_leak_started_record(tmp_path, monkeypatch):
    """``enabled_tasks`` 未包含 chat 时，普通对话不应记起始时间。"""
    module, plugin = _make_plugin(
        tmp_path, monkeypatch, {"enabled": True, "enabled_tasks": ["agent"]}
    )

    async def _run() -> None:
        for _ in range(20):
            await plugin.on_llm_request(_Event(), _request(module))

    asyncio.run(_run())
    assert len(plugin._request_started) == 0


def test_budget_rejection_does_not_leak_started_record(tmp_path, monkeypatch):
    """超预算被拦下时也不应留下起始时间记录。"""
    module, plugin = _make_plugin(
        tmp_path, monkeypatch, {"enabled": True, "metrics": {"daily_request_budget": 1}}
    )
    # 先把今日请求数顶到上限
    plugin.metrics.record(session="s", provider_id="p", route="cheap", success=True)
    plugin.metrics.flush()

    event = _Event()

    async def _run() -> None:
        await plugin.on_llm_request(event, _request(module))

    asyncio.run(_run())
    assert len(plugin._request_started) == 0, (
        "超预算提示已经发给用户（请求根本没发出去），对应的起始时间记录必须回收，否则会污染耗时统计"
    )
    assert event.result is not None, "超预算时应当把提示挂到事件上发给用户"


def test_stale_record_is_discarded(tmp_path, monkeypatch):
    """超过有效期的残留记录必须被丢弃，不能让耗时看起来像几小时。"""
    module, plugin = _make_plugin(tmp_path, monkeypatch)
    plugin._request_started.append(("s", time.time() - 7200))

    started = plugin._pop_request_started("s")
    assert started == 0.0, "过期记录必须返回 0.0（调用方据此跳过耗时统计）"
    assert len(plugin._request_started) == 0, "过期记录应当被顺手清理"


def test_fresh_record_returns_real_timestamp(tmp_path, monkeypatch):
    """正常记录必须能取回真实起始时间（回归保护，避免修过头）。"""
    module, plugin = _make_plugin(tmp_path, monkeypatch)
    plugin._request_started.append(("s", time.time() - 0.05))

    started = plugin._pop_request_started("s")
    assert started > 0, "正常路径必须能取到起始时间，否则耗时统计全变 0"
    assert 0.0 < time.time() - started < 5.0
    assert len(plugin._request_started) == 0


def test_pop_only_touches_own_session(tmp_path, monkeypatch):
    """弹出某会话的记录不能动到别的会话。"""
    module, plugin = _make_plugin(tmp_path, monkeypatch)
    plugin._request_started.append(("other", time.time()))
    plugin._request_started.append(("s", time.time()))

    plugin._pop_request_started("s")
    remaining = [key for key, _ in plugin._request_started]
    assert remaining == ["other"]


def test_latency_is_recorded_reasonably(tmp_path, monkeypatch):
    """端到端确认：一轮完整请求记录下来的耗时是「毫秒级」，不是「小时级」。"""
    module, plugin = _make_plugin(tmp_path, monkeypatch)

    async def _run() -> None:
        event = _Event()
        await plugin.on_llm_request(event, _request(module))
        assert len(plugin._request_started) == 1
        # 模拟 on_llm_response
        from astrbot.api.provider import LLMResponse

        response = LLMResponse(role="assistant", completion_text="你好")
        await plugin.on_llm_response(event, response)

    asyncio.run(_run())
    plugin.metrics.flush()
    summary = plugin.metrics.summary(days=1)
    assert summary["requests"] == 1
    assert summary["avg_latency_ms"] < 60_000, (
        f"平均耗时 {summary['avg_latency_ms']}ms 明显异常；通常说明取到了过期的起始时间戳"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
