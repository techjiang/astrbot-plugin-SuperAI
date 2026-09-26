"""插件实例化冒烟测试。

使用真实的 AstrBot 框架（若可用），以最小替身驱动插件的关键路径：
实例化 -> 注册工具 -> 指令处理 -> 记忆写入 -> 工作流。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 真实 AstrBot 是否可用：优先使用它，并把本地 stub 从 sys.path 中移除，
# 否则 ``tests/stubs/astrbot`` 会遮蔽真实实现。
REAL_ASTRBOT = False
for candidate in ("/tmp/astrbot-ref",):
    if Path(candidate, "astrbot", "api", "event", "__init__.py").exists():
        stubs = str(ROOT / "tests" / "stubs")
        while stubs in sys.path:
            sys.path.remove(stubs)
        for module in [
            name for name in sys.modules if name == "astrbot" or name.startswith("astrbot.")
        ]:
            del sys.modules[module]
        sys.path.insert(0, candidate)
        REAL_ASTRBOT = True
        break

pytestmark = pytest.mark.skipif(
    not REAL_ASTRBOT, reason="需要本地 AstrBot 源码（/tmp/astrbot-ref）才能执行冒烟测试"
)


def _make_plugin(tmp_path, monkeypatch, **overrides):
    """构造一个使用临时数据目录的插件实例。"""
    from astrbot.core.star.star import StarMetadata  # noqa: F401

    import superai.main as main_mod

    monkeypatch.setattr(main_mod, "get_astrbot_data_path", lambda: str(tmp_path), raising=True)

    class FakeProviderMeta:
        def __init__(self, pid: str) -> None:
            self.id = pid

    class FakeProvider:
        def __init__(self, pid: str) -> None:
            self._pid = pid

        def meta(self):
            return FakeProviderMeta(self._pid)

    class FakeToolManager:
        def __init__(self) -> None:
            self.func_list = []

    class FakeProviderManager:
        def __init__(self) -> None:
            self.llm_tools = FakeToolManager()

    class FakeContext:
        """最小 AstrBot Context 替身。"""

        def __init__(self) -> None:
            self.provider_manager = FakeProviderManager()
            self.kb_manager = None
            self.registered_web_apis: list = []

        def add_llm_tools(self, *tools) -> None:
            self.provider_manager.llm_tools.func_list.extend(tools)

        def get_all_providers(self):
            return [FakeProvider("p-cheap"), FakeProvider("p-strong")]

        async def get_current_chat_provider_id(self, umo: str) -> str:
            return "p-strong"

        async def llm_generate(self, **kwargs):
            class Resp:
                completion_text = "（模型输出）"

            return Resp()

        async def tool_loop_agent(self, **kwargs):
            class Resp:
                completion_text = "（Agent 输出）"

            return Resp()

        def register_web_api(self, route, handler, methods, desc) -> None:
            self.registered_web_apis.append((route, handler, methods, desc))

    config = {
        "enabled": True,
        "router": {
            "enabled": True,
            "strong_provider_id": "p-strong",
            "cheap_provider_id": "p-cheap",
        },
        "memory": {"enabled": True},
        **overrides,
    }
    return main_mod.SuperAIPlugin(FakeContext(), config)


def test_plugin_instantiates_and_registers_tools(tmp_path, monkeypatch):
    plugin = _make_plugin(tmp_path, monkeypatch)
    assert plugin.config.enabled
    assert "superai_remember" in plugin.tool_names
    assert plugin.data_dir.exists()


def test_plugin_registers_web_apis(tmp_path, monkeypatch):
    from superai.main import PLUGIN_NAME

    plugin = _make_plugin(tmp_path, monkeypatch)
    routes = list(getattr(plugin.context, "registered_web_apis", []))
    assert routes, "插件应至少注册一个 Web API"
    assert any(PLUGIN_NAME in route for route, *_ in routes)


def test_status_text_contains_flags(tmp_path, monkeypatch):
    plugin = _make_plugin(tmp_path, monkeypatch)
    text = plugin._status_text()
    assert "SuperAI" in text
    assert "路由=✅" in text
    assert "工具：" in text


def test_memory_roundtrip_through_plugin(tmp_path, monkeypatch):
    plugin = _make_plugin(tmp_path, monkeypatch)
    plugin.memory.add("umo:1", "用户喜欢 Python")
    entries = plugin.memory.search("umo:1", "Python")
    assert entries and "Python" in entries[0].content


@pytest.mark.asyncio
async def test_run_workflow(tmp_path, monkeypatch):
    from superai.storage.workflows import Workflow, WorkflowStep

    plugin = _make_plugin(tmp_path, monkeypatch)
    workflow = Workflow(
        name="测试流程",
        steps=[
            WorkflowStep(name="第一步", prompt="总结 {{input}}"),
            WorkflowStep(name="第二步", prompt="翻译 {{prev}}"),
        ],
    )
    result = await plugin.run_workflow(workflow, user_input="你好", session="umo:1")
    assert "测试流程" in result
    assert "第一步" in result
    assert "第二步" in result


@pytest.mark.asyncio
async def test_api_status_shape(tmp_path, monkeypatch):
    plugin = _make_plugin(tmp_path, monkeypatch)
    response = await plugin.api_status()
    payload = response.body if hasattr(response, "body") else response
    assert payload is not None
