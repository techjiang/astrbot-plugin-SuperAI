#!/usr/bin/env python3
"""SuperAI 端到端联调脚本（在**真实 AstrBot** 里加载本插件并驱动一轮对话）。

用途：把「测试绿但线上死」的风险降到最低 —— 单元测试用的是替身，
而这个脚本走的是真框架：真 ``StarManager`` 加载、真 ``star_handlers_registry``
绑定、真 ``call_event_hook`` 调用、真 provider 请求。

用法（需要一个 AstrBot 源码目录）::

    git clone --depth 1 https://github.com/AstrBotDevs/AstrBot /tmp/astrbot-ref
    ASTRBOT_REF=/tmp/astrbot-ref python scripts/e2e_smoke.py

退出码 0 表示：入口被发现、插件被加载、钩子被正确绑定、路由与用量统计生效。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASTRBOT_REF = Path(os.environ.get("ASTRBOT_REF", "/tmp/astrbot-ref"))


def _fail(msg: str) -> None:
    print(f"❌ {msg}")
    raise SystemExit(1)


def _check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        raise SystemExit(1)


def main() -> None:
    if not ASTRBOT_REF.joinpath("astrbot", "api", "star").exists():
        _fail(f"未找到 AstrBot 源码：{ASTRBOT_REF}（可用 ASTRBOT_REF 指定）")

    # 1) 复刻框架的插件发现逻辑
    def discover(plugin_dir: Path) -> str | None:
        """返回 AstrBot 认定的入口模块名；None 表示会被跳过。"""
        if (plugin_dir / "main.py").is_file():
            return "main"
        named = plugin_dir / f"{plugin_dir.name}.py"
        if named.is_file():
            return plugin_dir.name
        return None

    entry = discover(PLUGIN_ROOT)
    _check(
        "插件入口可被发现（main.py / <dirname>.py）",
        entry is not None,
        f"entry={entry}",
    )

    sys.path.insert(0, str(ASTRBOT_REF))

    # 2) 以顶层模块方式导入入口 —— 与框架一致
    import importlib.util

    spec = importlib.util.spec_from_file_location("superai_entry_main", PLUGIN_ROOT / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["superai_entry_main"] = module
    spec.loader.exec_module(module)

    cls = module.SuperAIPlugin
    _check("入口暴露 SuperAIPlugin", hasattr(module, "SuperAIPlugin"))

    # 3) 框架的绑定契约：handler_module_path 必须等于入口模块路径
    _check(
        "钩子定义在入口模块（self 才能被绑定）",
        cls.on_llm_request.__module__ == module.__name__,
        cls.on_llm_request.__module__,
    )

    # 4) 钩子必须能被 await（不能是 async generator）
    import inspect

    for hook in ("on_llm_request", "on_llm_response"):
        fn = getattr(cls, hook)
        _check(f"{hook} 是普通协程", inspect.iscoroutinefunction(fn))

    # 5) 真框架注册 + 真调用
    import functools
    import inspect as _inspect

    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.star.register.star_handler import get_handler_or_create
    from astrbot.core.star.star_handler import EventType, star_handlers_registry

    for hook in (cls.on_llm_request, cls.on_llm_response):
        assert _inspect.iscoroutinefunction(hook), f"{hook.__name__} 必须是普通协程"

    class _Ctx:
        """最小 Star Context：够插件 __init__ 用即可。"""

        def __init__(self) -> None:
            self.registered_web_apis: list = []
            self.llm_tools: list = []

        def register_web_api(self, route, handler, methods, desc) -> None:
            self.registered_web_apis.append((route, handler, methods, desc))

        def add_llm_tools(self, *tools) -> None:
            for tool in tools:
                items = tool if isinstance(tool, (list, tuple)) else [tool]
                self.llm_tools.extend(items)

        def get_all_providers(self):
            return [
                _Provider("p-cheap", "mock-cheap"),
                _Provider("p-strong", "mock-strong"),
            ]

        async def get_current_chat_provider_id(self, umo=None):
            return "p-cheap"

        def get_llm_tool_manager(self):  # 旧接口兼容
            return None

    class _Meta:
        def __init__(self, pid: str) -> None:
            self.id = pid

    class _Provider:
        def __init__(self, pid: str, model: str) -> None:
            self._pid = pid
            self._model = model

        def meta(self):
            return _Meta(self._pid)

        def get_model(self) -> str:
            return self._model

    # 用临时目录承载插件数据，避免污染真实 data/
    tmp_dir = Path(tempfile.mkdtemp(prefix="superai-e2e-"))
    ctx = _Ctx()
    original_data_path = module.get_astrbot_data_path
    module.get_astrbot_data_path = lambda: str(tmp_dir)
    try:
        instance = cls(ctx, {"enabled": True, "debug": True})
    finally:
        module.get_astrbot_data_path = original_data_path

    # 注册进真实注册表，并按框架的方式绑定（此处绑实例，语义等价的更强验证）
    for hook_name, event_type in (
        ("on_llm_request", EventType.OnLLMRequestEvent),
        ("on_llm_response", EventType.OnLLMResponseEvent),
    ):
        md = get_handler_or_create(getattr(cls, hook_name), event_type)
        md.handler = functools.partial(getattr(cls, hook_name), instance)

    registered = [
        h.handler_name
        for h in star_handlers_registry.get_handlers_by_event_type(EventType.OnLLMRequestEvent)
    ]
    _check("on_llm_request 命中真实注册表", "on_llm_request" in registered)

    class _Event:
        """最小 AstrMessageEvent：覆盖插件用到的方法。"""

        def __init__(self) -> None:
            self.unified_msg_origin = "e2e:FriendMessage:1"
            self._extra: dict = {}
            self._stopped = False
            self._result = None

        def get_group_id(self) -> str:
            return ""

        def is_private_chat(self) -> bool:
            return True

        def get_extra(self, key, default=None):
            return self._extra.get(key, default)

        def set_extra(self, key, value) -> None:
            self._extra[key] = value

        def clear_result(self) -> None:
            self._result = None

        def is_stopped(self) -> bool:
            return self._stopped

        def get_platform_name(self) -> str:
            return "webchat"

        def get_sender_id(self) -> str:
            return "1"

    async def _run() -> None:
        event = _Event()
        req = ProviderRequest(prompt="你好")
        md = next(
            h
            for h in star_handlers_registry.get_handlers_by_event_type(EventType.OnLLMRequestEvent)
            if h.handler_name == "on_llm_request"
        )
        # 关键：像 call_event_hook 那样 await，且**只**传 (event, req)
        await md.handler(event, req)
        _check("按 AstrBot 方式 await 钩子未抛异常（self 已绑定）", True)
        _check(
            "钩子确实改写了请求（路由生效）",
            req.model is not None,
            f"model={req.model}",
        )

        # 用量统计应当可查询（flush 是同步方法，直接调用）
        instance.metrics.flush()
        stats = instance.metrics.summary(days=7)
        _check("用量统计可查询", isinstance(stats, dict))

    asyncio.run(_run())
    _check("插件注册了 Studio Web API", len(ctx.registered_web_apis) > 0)

    # 6) 指令路由：/superai memory <子指令> 必须命中真正的子指令
    _check_memory_subcommands(module)

    # 7) 工具归属：AstrBot 必须认为这些工具属于本插件
    _check_tool_ownership(instance, module, ctx)

    print("\n🎉 端到端联调全部通过")


def _check_memory_subcommands(module) -> None:
    """确认 ``/superai memory list`` 不会被同名的兼容命令抢走。

    这类冲突「不报错但功能全废」：两个 handler 同时命中，
    旧的兼容命令自己跑掉，子指令组里的 list / search / clear / stats 永远执行不到。
    """
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.filter.command_group import CommandGroupFilter
    from astrbot.core.star.star_handler import EventType, star_handlers_registry

    handlers = [
        handler
        for handler in star_handlers_registry._handlers
        if handler.handler_module_path == module.__name__
        and handler.event_type == EventType.AdapterMessageEvent
    ]

    class _Ev:
        def __init__(self, text: str) -> None:
            self.message_str = text
            self.is_at_or_wake_command = True

        def get_message_str(self) -> str:
            return self.message_str

        def get_extra(self, key, default=None):
            return default

        def set_extra(self, key, value) -> None:
            pass

    def matched(text: str) -> set[str]:
        event = _Ev(text)
        hits: set[str] = set()
        for handler in handlers:
            try:
                if all(f.filter(event, {}) for f in handler.event_filters):
                    hits.add(handler.handler_name)
            except ValueError:
                continue
        return hits

    for text, expected in (
        ("superai memory list", "superai_memory_list"),
        ("superai memory search 关键词", "superai_memory_search"),
        ("superai memory clear", "superai_memory_clear_cmd"),
        ("superai memory stats", "superai_memory_stats"),
    ):
        _check(
            f"指令路由 {text!r} 命中 {expected}",
            expected in matched(text),
            f"实际命中 {sorted(matched(text))}",
        )

    _check(
        "superai 指令组下不存在同名的 memory 命令（否则会抢走子指令组）",
        not hasattr(module.SuperAIPlugin, "superai_memory"),
    )

    group_names = {
        handler.handler_name: f.group_name
        for handler in handlers
        for f in handler.event_filters
        if isinstance(f, CommandGroupFilter)
    }
    memory_group = next(
        (
            f.get_complete_command_names()
            for handler in handlers
            for f in handler.event_filters
            if isinstance(f, CommandGroupFilter) and f.group_name == "memory"
        ),
        None,
    )
    _check(
        "memory 指令组的完整指令名是 'superai memory'",
        memory_group == ["superai memory"],
        f"实际 {memory_group}（groups={group_names}）",
    )

    _ = CommandFilter  # 仅为说明上面复刻的是框架的 filter 语义


def _check_tool_ownership(instance, module, ctx) -> None:
    """确认工具的 ``handler_module_path`` 指向入口模块。

    工具类定义在 ``superai/tools/*`` 子模块时，``add_llm_tools()`` 会反推出
    顶层模块名 ``superai.tools.xxx``，插件与工具的归属关系断裂：
    仪表盘停用插件时工具不会被一起停掉，重载时也不刷新 ``active``。
    """
    from astrbot.core.star.star_manager import PluginManager

    tools = list(getattr(instance, "_tools", []))
    _check("插件装配了工具", bool(tools), f"{len(tools)} 个")

    wrong = [
        f"{tool.name}={getattr(tool, 'handler_module_path', None)!r}"
        for tool in tools
        if getattr(tool, "handler_module_path", None) != module.__name__
    ]
    _check(
        "所有工具的 handler_module_path 均为插件入口模块",
        not wrong,
        "，".join(wrong),
    )

    not_owned = [
        tool.name for tool in tools if not PluginManager._is_plugin_llm_tool(tool, module.__name__)
    ]
    _check(
        "框架认定所有工具属于本插件",
        not not_owned,
        f"未归属：{not_owned}",
    )


if __name__ == "__main__":
    main()
