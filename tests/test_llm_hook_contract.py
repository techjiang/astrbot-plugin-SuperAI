"""LLM 钩子的「契约」测试。

这一组测试专门守住一类**测试通过、线上失效**的陷阱：钩子的调用约定。

AstrBot 的 ``call_event_hook()`` 对 LLM 钩子只做::

    await handler.handler(event, *args, **kwargs)

它**不会**像普通事件管线（``call_handler``）那样 ``async for`` 迭代异步生成器。
因此 ``on_llm_request`` 一旦出现 ``yield`` 就会退化成 async generator，
``await`` 它直接抛 ``TypeError: object async_generator can't be used in
'await' expression``——而 ``call_event_hook`` 会把异常吞掉只记一行 error，
结果是**路由 / 记忆注入 / 图片保护 / 预算拦截全部静默失效**。

早前的测试用 ``async for _ in plugin.on_llm_request(...)`` 驱动钩子，
刚好把这个错误「喂」进了测试，所以 CI 全绿而功能是死的。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from conftest import load_superai_entry

SuperAIPlugin = load_superai_entry().SuperAIPlugin

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. 钩子必须能以 AstrBot 的方式调用
# ---------------------------------------------------------------------------
def test_on_llm_request_is_a_plain_coroutine():
    """回归：``on_llm_request`` 必须是普通协程，不能是 async generator。"""
    assert inspect.iscoroutinefunction(SuperAIPlugin.on_llm_request), (
        "on_llm_request 必须是普通协程：AstrBot 用 `await handler(event, req)` 调用它，"
        "async generator 无法被 await，会被 call_event_hook 静默吞掉"
    )
    assert not inspect.isasyncgenfunction(SuperAIPlugin.on_llm_request)


def test_on_llm_response_is_a_plain_coroutine():
    assert inspect.iscoroutinefunction(SuperAIPlugin.on_llm_response)
    assert not inspect.isasyncgenfunction(SuperAIPlugin.on_llm_response)


def test_on_llm_request_never_yields_in_source():
    """用 AST 扫真正的语句，防止以后有人为了「返回结果」重新引入 yield。

    注意不能直接对源码做字符串匹配：文档字符串里就写了 "yield" 这个词，
    会把说明文字误判成代码。
    """
    import ast

    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_llm_request":
            target = node
            break
    assert target is not None, "未找到 on_llm_request"

    bad = [node for node in ast.walk(target) if isinstance(node, (ast.Yield, ast.YieldFrom))]
    assert not bad, (
        "on_llm_request 内部出现了 yield —— 会把协程变成 async generator，"
        "AstrBot 无法 await 它（见本模块文档）"
    )


@pytest.mark.asyncio
async def test_hook_is_awaitable_the_way_astrbot_calls_it(tmp_path, monkeypatch):
    """按 AstrBot 的调用方式（await，不迭代）驱动钩子，必须不抛异常。"""
    from tests.test_plugin_smoke import FakeEvent, _make_plugin, _make_request

    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    event = FakeEvent()
    req = _make_request("你好")

    handler = plugin.on_llm_request(event, req)
    assert inspect.isawaitable(handler), "AstrBot 会 await 它，必须返回可等待对象"
    await handler

    assert "SuperAI" in (req.system_prompt or ""), "稳定指令注入必须生效"


# ---------------------------------------------------------------------------
# 2. 预算拦截必须真的能终止事件
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_budget_stop_uses_set_result(tmp_path, monkeypatch):
    """``plain_result()`` 只是「构造」结果，必须 ``set_result()`` 才会发给用户。"""
    from tests.test_plugin_smoke import FakeEvent, _make_plugin, _make_request

    plugin, _ = _make_plugin(
        tmp_path, monkeypatch, metrics={"enabled": True, "daily_request_budget": 1}
    )
    plugin.metrics.record(input_tokens=1)
    event = FakeEvent()
    req = _make_request("你好")

    await plugin.on_llm_request(event, req)

    assert event.get_result() is not None, "超预算时必须 set_result，否则用户收不到提示"
    assert "上限" in event.result_text()
    assert event.is_stopped(), "超预算时必须 stop_event，否则仍会去调用模型"
    assert req.model is None, "被拦截的请求不应再被改写模型"


# ---------------------------------------------------------------------------
# 3. enabled_tasks 必须真的按任务类型放行 / 拦截
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_enabled_tasks_gates_chat(tmp_path, monkeypatch):
    """只勾选 long_context 时，普通对话不应被接管。"""
    from tests.test_plugin_smoke import FakeEvent, _make_plugin, _make_request

    plugin, _ = _make_plugin(tmp_path, monkeypatch, enabled_tasks=["long_context"])
    event = FakeEvent()
    req = _make_request("你好")

    await plugin.on_llm_request(event, req)
    assert req.model is None, "未勾选 chat 时不应改写模型"

    assert plugin.config.is_task_enabled("long_context")
    assert not plugin.config.is_task_enabled("chat")


@pytest.mark.asyncio
async def test_enabled_tasks_default_covers_image(tmp_path, monkeypatch):
    """默认任务列表必须包含 image，否则「图片走视觉档」默认就是关闭的。"""
    from superai.core.config import DEFAULT_ENABLED_TASKS

    assert "image" in DEFAULT_ENABLED_TASKS

    from tests.test_plugin_smoke import FakeEvent, _make_plugin, _make_request

    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    event = FakeEvent()
    req = _make_request("这是什么")
    req.image_urls = ["/tmp/cat.png"]

    await plugin.on_llm_request(event, req)
    route = event.get_extra("superai_route") or {}
    assert route.get("tier") == "vision"
    assert req.image_urls == ["/tmp/cat.png"], "图片必须被保住"


# ---------------------------------------------------------------------------
# 4. 事实抽取必须节流
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fact_extraction_is_throttled(tmp_path, monkeypatch):
    """连续多轮对话不应每轮都调用一次模型做事实抽取。"""
    from tests.test_plugin_smoke import FakeEvent, _make_plugin, _make_request

    history = ["User: 我在做 AstrBot 插件", "Assistant: 好的", "User: 继续", "Assistant: 嗯"]
    plugin, context = _make_plugin(tmp_path, monkeypatch, history=history, pages=1)
    event = FakeEvent()

    for index in range(6):
        await plugin.on_llm_request(event, _make_request(f"第{index}轮"))

    # 让后台任务跑完
    import asyncio

    await asyncio.sleep(0.3)

    calls = list(context.generate_calls)
    assert len(calls) <= 2, f"6 轮对话最多允许 1 次摘要 + 1 次抽取，实际 {len(calls)} 次模型调用"
