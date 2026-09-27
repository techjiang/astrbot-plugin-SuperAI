"""指令路由与工具归属契约测试 —— 守住两个「不报错但功能全废」的陷阱。

两个问题都在真实 AstrBot 4.28.1 上复现过，且**都不会抛异常**，
只能靠与框架语义对齐的契约测试拦住。

### 1. 指令组被同名命令抢走

AstrBot 的 ``CommandGroupFilter`` 用 ``message_str.startswith(...)`` 匹配，
而 ``CommandFilter`` 用 ``message_str.startswith(f"{full_cmd} ")`` 匹配。

原先插件里同时存在：

- ``superai.memory`` 指令**组**（用 ``@filter.command_group("superai.memory")`` 注册）
- ``memory`` 子**命令**（``@superai_group.command("memory")``）

于是 ``/superai memory list`` 会**同时**命中这两个 handler：旧的兼容命令
拿到 ``action="list" query=""`` 自己跑掉，``superai memory list`` 里真正的
``list / search / clear / stats`` 子指令永远执行不到，而整条链路一句错都不报。

修法：把 ``memory`` 改为挂在 ``superai_group`` 下的**真子组**
（``@superai_group.group("memory")``），并删掉那个同名兼容命令。

### 2. 工具的 ``handler_module_path`` 指向错误的模块

``Context.add_llm_tools()`` 内部用
``_resolve_tool_handler_module_path()`` 从工具类 ``__module__`` 反推归属。
SuperAI 的工具类定义在 ``superai/tools/*`` 里，反推得到的是**顶层模块名**
``superai.tools.memory_tools`` —— 既不在 ``star_map`` 中，
也不等于插件入口模块路径，于是：

- ``PluginManager._is_plugin_llm_tool()`` 一律返回 ``False``：
  在仪表盘停用 / 卸载插件时，这些工具不会被一起停用，重载时也不刷新 ``active``；
- ``_plugin_tool_fix()`` 查不到归属，只能保守保留，插件工具清单与实际注册状态脱节。

修法：注册后用 :meth:`SuperAIPlugin._claim_tools` 把 ``handler_module_path``
统一改写成插件入口模块路径（必须在 ``add_llm_tools`` **之后**做）。
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# 运行前提：本文件的契约全部依赖**真实 AstrBot 框架**的语义
# ---------------------------------------------------------------------------
#: ``tests/stubs`` 只提供「够插件 import」的最小替身，没有真实的
#: 指令注册表、filter 匹配逻辑与工具执行器；在没有真实 AstrBot 的环境里
#: 这些契约无法验证，必须整体跳过，否则会给出「测试绿但没测到」的假象。
# 没有真实 AstrBot 时整个模块跳过（conftest 已优先把真实源码放进 sys.path）。
pytest.importorskip("astrbot.core.star.star_manager", reason="需要真实 AstrBot 框架")


def _entry():
    from conftest import load_superai_entry

    return load_superai_entry()


class _FakeEvent:
    """只实现事件过滤用到的接口。"""

    def __init__(self, message_str: str) -> None:
        self.message_str = message_str
        self.is_at_or_wake_command = True
        self._extras: dict = {}

    def get_message_str(self) -> str:
        return self.message_str

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value) -> None:
        self._extras[key] = value


def _command_handlers():
    """取出本插件入口模块注册的所有「消息事件」处理器。"""
    from astrbot.core.star.star_handler import EventType, star_handlers_registry

    module = _entry()
    return [
        handler
        for handler in star_handlers_registry._handlers
        if handler.handler_module_path == module.__name__
        and handler.event_type == EventType.AdapterMessageEvent
    ]


def _matched_handlers(text: str) -> list[str]:
    """按框架的规则算出这条消息会激活哪些处理器。

    ``CommandGroupFilter.filter()`` 在「消息恰好等于组名」时会抛
    ``ValueError``（框架用它来输出子指令树），这里把它记成
    ``<name:tree>`` 而不是当成失败。
    """
    event = _FakeEvent(text)
    hits: list[str] = []
    for handler in _command_handlers():
        try:
            if all(f.filter(event, {}) for f in handler.event_filters):
                hits.append(handler.handler_name)
        except ValueError:
            continue
    return hits


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("superai memory list", "superai_memory_list"),
        ("superai memory list 5", "superai_memory_list"),
        ("superai memory search Rust", "superai_memory_search"),
        ("superai memory clear", "superai_memory_clear_cmd"),
        ("superai memory stats", "superai_memory_stats"),
    ],
)
def test_memory_subcommands_are_reachable(text, expected):
    """``/superai memory <子指令>`` 必须命中真正的子指令，而不是某个同名命令。"""
    hits = _matched_handlers(text)
    assert expected in hits, (
        f"{text!r} 没有命中 {expected}，实际命中 {hits}；"
        "通常说明 superai 指令组下又注册了同名的 memory 命令，把子指令组抢走了"
    )


def test_no_command_shadows_memory_subgroup():
    """插件不应在 ``superai`` 组下再注册一个名为 ``memory`` 的命令。

    它会在唤醒阶段与 ``superai.memory`` 指令组同时命中，
    让 ``/superai memory list`` 被抢走（详见模块文档第 1 条）。
    """
    module = _entry()
    assert not hasattr(module.SuperAIPlugin, "superai_memory"), (
        "检测到 superai 组下存在名为 memory 的命令处理器；"
        "它会与 superai memory 子指令组冲突，请删除它（子指令组已覆盖同等能力）"
    )


def test_memory_group_is_nested_under_superai():
    """``memory`` 必须是 ``superai`` 的真子组，完整指令名为 ``superai memory``。"""
    from astrbot.core.star.filter.command_group import CommandGroupFilter

    names: dict[str, list[str]] = {}
    for handler in _command_handlers():
        for f in handler.event_filters:
            if isinstance(f, CommandGroupFilter):
                names[handler.handler_name] = f.get_complete_command_names()
    assert names.get("superai_memory_group") == ["superai memory"], (
        f"memory 指令组的完整指令名应为 ['superai memory']，实际 {names.get('superai_memory_group')}；"
        '用 @filter.command_group("superai.memory") 会把组名注册成字面指令，与框架的匹配语义不符'
    )


# ---------------------------------------------------------------------------
# 工具归属
# ---------------------------------------------------------------------------
def _make_plugin(tmp_path, monkeypatch):
    from conftest import load_superai_entry

    module = load_superai_entry()
    monkeypatch.setattr(module, "get_astrbot_data_path", lambda: str(tmp_path))

    class _Ctx:
        def __init__(self) -> None:
            self.registered_web_apis: list = []
            self.llm_tools: list = []

        def register_web_api(self, route, handler, methods, desc) -> None:
            self.registered_web_apis.append((route, handler, methods, desc))

        def add_llm_tools(self, *tools) -> None:
            # 复刻真实实现：用工具类 __module__ 反推归属模块
            from astrbot.core.star.context import _resolve_tool_handler_module_path

            for tool in tools:
                tool.handler_module_path = _resolve_tool_handler_module_path(tool)
                self.llm_tools.append(tool)

        def get_all_providers(self):
            return []

        async def get_current_chat_provider_id(self, umo=None):
            return ""

    ctx = _Ctx()
    plugin = module.SuperAIPlugin(
        ctx, {"enabled": True, "memory": {"enabled": True, "long_term_enabled": True}}
    )
    return module, plugin


def test_tools_claim_entry_module(tmp_path, monkeypatch):
    """工具注册后 ``handler_module_path`` 必须等于插件入口模块路径。

    否则 AstrBot 不认为这些工具属于本插件：
    仪表盘停用插件时工具不会被一起停掉，重载时也不会刷新 active。
    """
    module, plugin = _make_plugin(tmp_path, monkeypatch)
    assert plugin._tools, "配置下应当至少注册了记忆相关工具"
    for tool in plugin._tools:
        assert tool.handler_module_path == module.__name__, (
            f"工具 {tool.name} 的 handler_module_path 是 {tool.handler_module_path!r}，"
            f"应为插件入口模块 {module.__name__}；"
            "否则 PluginManager._is_plugin_llm_tool() 判定它不属于任何插件"
        )


def test_tools_are_recognized_as_plugin_owned(tmp_path, monkeypatch):
    """直接调用框架的归属判定函数，确认返回 True。"""
    from astrbot.core.star.star_manager import PluginManager

    module, plugin = _make_plugin(tmp_path, monkeypatch)
    for tool in plugin._tools:
        assert PluginManager._is_plugin_llm_tool(tool, module.__name__), (
            f"框架不认为 {tool.name} 属于本插件，插件停用后该工具仍会保持激活状态"
        )


def test_claim_tools_is_idempotent(tmp_path, monkeypatch):
    """``_claim_tools`` 会被调用多次（注册 + initialize + 框架重绑），必须幂等。"""
    module, plugin = _make_plugin(tmp_path, monkeypatch)
    for _ in range(3):
        plugin._claim_tools(plugin._tools)
    for tool in plugin._tools:
        assert tool.handler_module_path == module.__name__


def test_tools_still_execute_after_claiming(tmp_path, monkeypatch):
    """改写归属不能破坏工具执行 —— 走一遍真实的 ``FunctionToolExecutor``。"""
    import asyncio

    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
    from astrbot.core.provider.func_tool_manager import (
        FunctionToolManager,
        _PermissionGuardedTool,
    )

    module, plugin = _make_plugin(tmp_path, monkeypatch)
    manager = FunctionToolManager()
    for tool in plugin._tools:
        manager.func_list.append(tool)

    class _Event:
        unified_msg_origin = "webchat:FriendMessage:1"

        def __init__(self) -> None:
            self._extras: dict = {}

        def get_sender_id(self) -> str:
            return "tester"

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value) -> None:
            self._extras[key] = value

    class _Namespace:
        def __init__(self) -> None:
            self.context = plugin.context
            self.event = _Event()

    class _Wrapper:
        def __init__(self) -> None:
            self.context = _Namespace()
            self.tool_call_timeout = 10
            self.messages: list = []

    remember = next(t for t in plugin._tools if t.name == "superai_remember")
    guarded = _PermissionGuardedTool(remember, manager)

    async def _run() -> str:
        result = ""
        async for item in FunctionToolExecutor.execute(
            tool=guarded, run_context=_Wrapper(), content="用户喜欢简洁的回答"
        ):
            # 只取文本内容，不直接 import mcp（那是 AstrBot 的运行依赖，
            # 不属于本仓库的 dev 依赖，引用会让 CI 依赖自检失败）
            content = getattr(item, "content", None)
            if content:
                result = "".join(part.text for part in content if hasattr(part, "text"))
        return result

    text = asyncio.run(_run())
    assert "已记住" in text, f"工具执行结果异常：{text!r}"
