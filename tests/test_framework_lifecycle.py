"""全生命周期「真框架」联调测试 —— 把整条启动链路完整跑一遍。

与现有契约测试的区别：那些测试各自验证**一个假设**（入口名、钩子签名、
``handler_module_path`` 归属……），用的是替身或局部注册表；
本文件用真实的 ``PluginManager`` 走完 AstrBot 的**完整加载流程**：

    _get_modules 发现插件
      → __import__("data.plugins.<dir>.main")
      → _load_plugin_metadata 读 metadata.yaml
      → add_llm_tools 注册工具
      → functools.partial 绑定 self
      → initialize() 钩子
      → 指令 filter 真实匹配

然后按框架的方式驱动一轮：``on_llm_request`` / ``on_llm_response`` 直接 await，
指令 handler 用 ``call_handler`` 的 ``async for`` 语义执行。

**为什么值得单独建一个文件**：上述每一步都用真实的框架代码，
因此能拦住「每个局部单测都过、但整体集成后失效」的问题 ——
例如入口能发现但 metadata 版本校验不过、指令注册了但 filter 匹配不到、
钩子绑定了但 initialize() 抛异常导致插件被回滚。

缺少真实 AstrBot 源码时整体 skip，不影响纯逻辑测试。
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASTRBOT_REF = Path(os.environ.get("ASTRBOT_REF", "/tmp/astrbot-ref"))

#: 真实 AstrBot 是否可用（与 conftest 的判定保持一致）
_REAL = ASTRBOT_REF.joinpath("astrbot", "api", "event", "__init__.py").exists()

pytestmark = pytest.mark.skipif(
    not _REAL,
    reason="需要真实 AstrBot 源码（ASTRBOT_REF 指向 AstrBot 仓库）",
)

PLUGIN_DIR_NAME = "astrbot_plugin_superai"
ENTRY_MODULE = f"data.plugins.{PLUGIN_DIR_NAME}.main"

_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", ".ruff_cache", ".pytest_cache", "data", "shots", "*.pyc"
)


class _MinimalContext:
    """只实现插件装配期用到的那几个 Context API。

    刻意**不**用 MagicMock：Mock 会吞掉「框架新增了一个必需方法」这类变化，
    而这正是本测试要观察的信号。
    """

    def __init__(self) -> None:
        self.registered_web_apis: list[tuple] = []
        self.llm_tools: list = []
        self.astrbot_config_mgr = None

    def register_web_api(self, route, handler, methods, desc) -> None:
        self.registered_web_apis.append((route, handler, methods, desc))

    def add_llm_tools(self, *tools) -> None:
        for tool in tools:
            items = tool if isinstance(tool, (list, tuple)) else [tool]
            self.llm_tools.extend(items)

    def get_all_providers(self):
        return []

    async def get_current_chat_provider_id(self, umo=None):
        return None

    def get_llm_tool_manager(self):
        return None


@pytest.fixture(scope="module")
def loaded_plugin():
    """用真实 PluginManager 完整加载插件，返回加载结果上下文。"""

    root = Path(tempfile.mkdtemp(prefix="superai-lifecycle-"))
    os.environ["ASTRBOT_ROOT"] = str(root)
    (root / "data" / "plugins").mkdir(parents=True, exist_ok=True)
    (root / "data" / "config").mkdir(parents=True, exist_ok=True)

    # 框架在 check_env() 里把 ASTRBOT_ROOT 加入 sys.path，让 "data.plugins.*" 可导入
    for path in (str(ASTRBOT_REF), str(root)):
        if path not in sys.path:
            sys.path.insert(0, path)

    target = root / "data" / "plugins" / PLUGIN_DIR_NAME
    shutil.copytree(ROOT, target, ignore=_IGNORE)

    # 清掉可能残留的旧模块，保证每次都是全新的导入
    for name in [m for m in sys.modules if m.startswith("data.plugins") or m == "data"]:
        sys.modules.pop(name, None)

    from astrbot.core.config.astrbot_config import AstrBotConfig
    from astrbot.core.star.star import star_map, star_registry
    from astrbot.core.star.star_manager import PluginManager

    config = AstrBotConfig(config_path=str(root / "data" / "cmd_config.json"))
    ctx = _MinimalContext()
    manager = PluginManager(ctx, config)

    ok, error = asyncio.run(manager.reload())
    assert ok, f"真实 PluginManager 加载插件失败：{error}"
    assert not manager.failed_plugin_dict, f"存在加载失败的插件：{manager.failed_plugin_dict}"

    metadata = next(
        (m for m in star_registry if m.root_dir_name == PLUGIN_DIR_NAME),
        None,
    )
    assert metadata is not None, "插件没有被注册进 star_registry"
    assert metadata.activated, "插件加载后应当处于已激活状态"
    assert metadata.module_path == ENTRY_MODULE

    yield {
        "manager": manager,
        "context": ctx,
        "metadata": metadata,
        "instance": metadata.star_cls,
        "star_map": star_map,
        "star_registry": star_registry,
    }

    # 清理：把插件相关模块从 sys.modules 移除，避免污染其它测试
    for name in [m for m in list(sys.modules) if m.startswith("data.plugins") or m == "data"]:
        sys.modules.pop(name, None)
    os.environ.pop("ASTRBOT_ROOT", None)
    shutil.rmtree(root, ignore_errors=True)


def _handlers_for(metadata, event_type):
    from astrbot.core.star.star_handler import star_handlers_registry

    return [
        h
        for h in star_handlers_registry.get_handlers_by_event_type(event_type)
        if h.handler_module_path == metadata.module_path
    ]


# ---------------------------------------------------------------------------
# 1. 元数据与装配结果
# ---------------------------------------------------------------------------
def test_metadata_loaded_from_yaml(loaded_plugin):
    """``metadata.yaml`` 的字段必须真正进入框架的 StarMetadata。"""
    md = loaded_plugin["metadata"]

    assert md.name == "astrbot_plugin_superai"
    assert md.display_name == "SuperAI"
    assert md.short_desc, "short_desc 会用于插件市场卡片，必须存在"
    # 商店卡片上的「作者」就是 md.author。它必须是插件作者而不是平台账号 ——
    # 回归（Issue #1）：这里一度是平台账号 cosc，导致市场显示错误作者。
    assert md.author == "TechSauce"
    assert md.repo, "repo 用于来源归属与更新检测，不能为空"
    assert md.astrbot_version == ">=4.5.7"
    assert md.pages and md.pages[0]["name"] == "studio"
    assert md.i18n, "插件自带 i18n 应被框架读取"

    # 支持平台必须是官方 ADAPTER_NAME_2_TYPE 的 key，否则 WebUI 展示会失效
    from astrbot.core.star.filter.platform_adapter_type import ADAPTER_NAME_2_TYPE

    unknown = [p for p in md.support_platforms if p not in ADAPTER_NAME_2_TYPE]
    assert not unknown, f"support_platforms 含非官方平台 key：{unknown}"
    assert "aiocqhttp" in md.support_platforms


def test_plugin_logo_is_picked_up(loaded_plugin):
    """框架只在插件目录根下找 ``logo.png``，放错位置插件列表就没有图标。"""
    md = loaded_plugin["metadata"]
    assert md.logo_path, "框架没有识别到插件 Logo"
    assert Path(md.logo_path).name == "logo.png"
    assert Path(md.logo_path).is_file()


def test_all_handlers_registered(loaded_plugin):
    """钩子与指令都必须登记在**插件入口模块**下。"""
    from astrbot.core.star.star_handler import EventType

    md = loaded_plugin["metadata"]
    llm_hooks = _handlers_for(md, EventType.OnLLMRequestEvent)
    assert [h.handler_name for h in llm_hooks] == ["on_llm_request"]

    response_hooks = _handlers_for(md, EventType.OnLLMResponseEvent)
    assert [h.handler_name for h in response_hooks] == ["on_llm_response"]

    commands = {h.handler_name for h in _handlers_for(md, EventType.AdapterMessageEvent)}
    for expected in (
        "ai_command",
        "superai_group",
        "superai_status",
        "superai_stats",
        "superai_memory_list",
        "superai_memory_search",
        "superai_memory_stats",
        "superai_route",
        "superai_tools",
        "superai_workflow",
    ):
        assert expected in commands, f"指令 {expected} 未注册"


def test_llm_hook_self_is_bound(loaded_plugin):
    """框架必须用 ``functools.partial`` 把插件实例绑定到钩子上。

    这正是 v0.2.2 修掉的致命问题：绑定没发生时调用会抛 TypeError，
    被 ``call_event_hook`` 吞掉，插件「加载成功」但什么都不做。
    """
    from astrbot.core.star.star_handler import EventType

    md = loaded_plugin["metadata"]
    for handler in _handlers_for(md, EventType.OnLLMRequestEvent):
        assert isinstance(handler.handler, functools.partial), (
            "钩子没有被 functools.partial 绑定 self —— "
            "框架的绑定条件（handler_module_path == metadata.module_path）未满足"
        )
        assert isinstance(handler.handler.args[0], type(loaded_plugin["instance"]))


def test_tools_belong_to_plugin(loaded_plugin):
    """工具的 ``handler_module_path`` 必须指向入口模块，才能随插件一起停用。"""
    from astrbot.core.star.star_manager import PluginManager

    md = loaded_plugin["metadata"]
    tools = list(loaded_plugin["context"].llm_tools)
    assert tools, "插件没有注册任何 LLM 工具"

    for tool in tools:
        assert tool.handler_module_path == md.module_path, (
            f"工具 {tool.name} 归属错误：{tool.handler_module_path}"
        )
        assert PluginManager._is_plugin_llm_tool(tool, md.module_path), (
            f"框架不认为 {tool.name} 属于本插件 —— 停用插件时它不会被一起停用"
        )


def test_web_apis_registered(loaded_plugin):
    """Studio 面板 API 必须按 ``/<plugin_name>/<路径>`` 注册。

    框架的 ``_match_registered_web_api`` 是把请求路径与注册路由**整段**匹配的
    （见 ``astrbot/dashboard/api/plugins.py``），所以路由必须自带插件名前缀，
    否则面板请求会 404。
    """
    apis = loaded_plugin["context"].registered_web_apis
    assert len(apis) >= 6, f"Studio 面板 API 数量不足：{len(apis)}"
    routes = {item[0] for item in apis}
    for suffix in ("status", "stats", "memory", "sessions", "tools"):
        expected = f"/{PLUGIN_DIR_NAME}/{suffix}"
        assert expected in routes, f"缺少 Studio API：{expected}（实际 {sorted(routes)}）"
    for _, _, methods, _ in apis:
        assert methods and all(m.upper() in {"GET", "POST"} for m in methods)


# ---------------------------------------------------------------------------
# 2. 真实驱动一轮请求
# ---------------------------------------------------------------------------
def _make_event(text: str):
    """按框架的方式构造一个已通过 WakingCheck 的事件。"""
    from astrbot.core.platform.astr_message_event import AstrMessageEvent

    class _PlatformMeta:
        id = "webchat"
        name = "webchat"

    class _Message:
        type = "FriendMessage"

    class _Event(AstrMessageEvent):
        def __init__(self, message: str) -> None:
            super().__init__(message, _Message(), _PlatformMeta(), "1")
            self.unified_msg_origin = "webchat:FriendMessage:1"
            # WakingCheck stage 在私聊 / 唤醒场景下会置为 True
            self.is_at_or_wake_command = True
            self.role = "admin"

        def get_group_id(self) -> str:
            return ""

        def is_private_chat(self) -> bool:
            return True

        def get_platform_name(self) -> str:
            return "webchat"

        def get_sender_id(self) -> str:
            return "1"

    return _Event(text)


def test_llm_hooks_run_without_error(loaded_plugin):
    """按框架的方式 await 两个钩子，必须不抛异常且真正改写了请求。"""
    from astrbot.core.provider.entities import LLMResponse, ProviderRequest
    from astrbot.core.star.star_handler import EventType

    md = loaded_plugin["metadata"]
    request_hook = _handlers_for(md, EventType.OnLLMRequestEvent)[0].handler
    response_hook = _handlers_for(md, EventType.OnLLMResponseEvent)[0].handler

    event = _make_event("你好")
    req = ProviderRequest(prompt="你好")
    asyncio.run(request_hook(event, req))

    assert "SuperAI" in (req.system_prompt or ""), "稳定指令没有被注入"

    event2 = _make_event("你好")
    asyncio.run(request_hook(event2, ProviderRequest(prompt="你好")))
    asyncio.run(response_hook(event2, LLMResponse(role="assistant", completion_text="你好呀")))


def _command_hits(metadata, text: str) -> set[str]:
    """用**真实** filter 语义判断某条消息会命中哪些指令 handler。"""
    from astrbot.core.star.star_handler import EventType

    event = _make_event(text)
    hits: set[str] = set()
    for handler in _handlers_for(metadata, EventType.AdapterMessageEvent):
        try:
            if all(f.filter(event, {}) for f in handler.event_filters):
                hits.add(handler.handler_name)
        except ValueError:
            # 指令组在参数不足时会抛 ValueError（框架用来提示用法），属于命中
            hits.add(handler.handler_name)
    return hits


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ai 你好", "ai_command"),
        ("superai status", "superai_status"),
        ("superai stats", "superai_stats"),
        ("superai help", "superai_help"),
        ("superai route", "superai_route"),
        ("superai tools", "superai_tools"),
        ("superai workflow", "superai_workflow"),
        ("superai maintain", "superai_maintain"),
        ("superai memory list", "superai_memory_list"),
        ("superai memory search 关键词", "superai_memory_search"),
        ("superai memory clear", "superai_memory_clear_cmd"),
        ("superai memory stats", "superai_memory_stats"),
    ],
)
def test_commands_are_reachable(loaded_plugin, text, expected):
    """每条指令都必须真实可达（子指令不能被同名命令抢走）。"""
    hits = _command_hits(loaded_plugin["metadata"], text)
    assert expected in hits, f"{text!r} 未命中 {expected}，实际命中 {sorted(hits)}"


def test_commands_execute_without_error(loaded_plugin):
    """真实执行每条指令，必须不抛异常并产出文本。"""
    from astrbot.core.pipeline.context_utils import call_handler
    from astrbot.core.star.star_handler import EventType

    md = loaded_plugin["metadata"]
    handlers = {h.handler_name: h for h in _handlers_for(md, EventType.AdapterMessageEvent)}
    cases = [
        ("superai_status", "superai status", {}),
        ("superai_stats", "superai stats", {}),
        ("superai_help", "superai help", {}),
        ("superai_route", "superai route", {}),
        ("superai_tools", "superai tools", {}),
        ("superai_memory_stats", "superai memory stats", {}),
        ("superai_workflow", "superai workflow", {}),
        ("superai_maintain", "superai maintain", {}),
    ]

    async def _run(name: str, text: str, params: dict) -> str:
        event = _make_event(text)
        async for _ in call_handler(event, handlers[name].handler, **params):
            pass
        result = event._result
        getter = getattr(result, "get_plain_text", None)
        return str(getter() or "") if callable(getter) else ""

    for name, text, params in cases:
        output = asyncio.run(_run(name, text, params))
        assert output.strip(), f"{text} 没有产出任何文本"


def test_handlers_are_awaitable_or_async_generator(loaded_plugin):
    """指令 handler 必须是协程或异步生成器 —— 两者框架都支持。

    钩子则**必须**是普通协程：``call_event_hook`` 走 ``await``，
    异步生成器会抛 ``TypeError: object async_generator can't be used in 'await'``。
    """
    from astrbot.core.star.filter.command_group import CommandGroupFilter
    from astrbot.core.star.star_handler import EventType

    md = loaded_plugin["metadata"]
    for handler in _handlers_for(md, EventType.AdapterMessageEvent):
        # 指令**组**本身不是可执行体（它只做参数不足提示与路由前缀匹配），
        # 框架不会 await 它，因此不要求它是协程。
        is_group = any(isinstance(f, CommandGroupFilter) for f in handler.event_filters)
        if is_group:
            continue
        assert inspect.iscoroutinefunction(handler.handler) or inspect.isasyncgenfunction(
            handler.handler
        ), f"{handler.handler_name} 既不是协程也不是异步生成器"

    for event_type, name in (
        (EventType.OnLLMRequestEvent, "on_llm_request"),
        (EventType.OnLLMResponseEvent, "on_llm_response"),
    ):
        for handler in _handlers_for(md, event_type):
            assert inspect.iscoroutinefunction(handler.handler), (
                f"{name} 必须是普通协程，async generator 会被 await 抛 TypeError"
            )
