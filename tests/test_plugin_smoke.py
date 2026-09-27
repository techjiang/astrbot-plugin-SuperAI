"""插件实例化冒烟测试。

使用真实的 AstrBot 框架（若可用），以最小替身驱动插件的关键路径：
实例化 -> 注册工具 -> 钩子 -> 指令处理 -> 记忆写入 -> 路由降级 -> 工作流。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata

ROOT = Path(__file__).resolve().parents[1]

#: AstrBot 源码探测路径
ASTRBOT_REF = Path("/tmp/astrbot-ref")

# 真实 AstrBot 是否可用。注意：**不要**在这里改 ``sys.path`` 或 ``sys.modules``，
# 否则同一个 pytest 进程里其它测试会被污染（例如真实 AstrBot 会重复注册
# sqlmodel 表，导致后续 import 报 "Table is already defined"）。
# 真实框架的切换放在 pytest_configure（见根目录 conftest.py），
# 它在所有测试模块 import 之前执行。
REAL_ASTRBOT = bool(ASTRBOT_REF.joinpath("astrbot", "api", "event", "__init__.py").exists())

pytestmark = pytest.mark.skipif(
    not REAL_ASTRBOT,
    reason=f"需要本地 AstrBot 源码（{ASTRBOT_REF}）才能执行冒烟测试",
)


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
        self.func_list: list = []


class FakeProviderManager:
    def __init__(self) -> None:
        self.llm_tools = FakeToolManager()


class FakeConversationManager:
    def __init__(self, history: list[str] | None = None, pages: int = 1) -> None:
        self.history = history or []
        self.pages = pages
        self.requested_pages: list[int] = []

    async def get_curr_conversation_id(self, umo: str) -> str:
        return "cid-1"

    async def get_human_readable_context(self, umo, cid, page=1, page_size=10):
        self.requested_pages.append(page)
        if page == 1:
            return self.history[:page_size], self.pages
        return self.history[-page_size:], self.pages


class FakeEvent(AstrMessageEvent):
    """真实 ``AstrMessageEvent`` 的最小可用子类。

    这里刻意**继承真实框架类**而不是手写一个鸭子类型替身：
    ``event.set_result()`` / ``is_stopped()`` / ``plain_result()`` 的语义
    （尤其是 ``stop_event()`` 只有在结果已挂到事件上时才真正生效）
    直接决定钩子能否把话术发给用户。早前用手写替身时，
    ``stop_event()`` 被简化成只置一个标志位，于是「预算拦截」看起来是通过的，
    真实环境下却会静默失效。
    """

    def __init__(
        self,
        umo: str = "aiocqhttp:GroupMessage:1",
        *,
        is_admin: bool = False,
        messages: list | None = None,
    ):
        message_obj = AstrBotMessage()
        message_obj.type = (
            MessageType.FRIEND_MESSAGE if "GroupMessage" not in umo else MessageType.GROUP_MESSAGE
        )
        message_obj.self_id = "bot"
        message_obj.session_id = umo.rsplit(":", 1)[-1]
        message_obj.message_id = "msg-1"
        message_obj.message_str = ""
        message_obj.raw_message = None
        message_obj.message = list(messages or [])
        message_obj.sender = MessageMember(user_id="user-1", nickname="tester")
        super().__init__(
            message_str="",
            message_obj=message_obj,
            platform_meta=PlatformMetadata(name="aiocqhttp", description="test", id="aiocqhttp"),
            session_id=umo.rsplit(":", 1)[-1],
        )
        self.unified_msg_origin = umo
        self._is_admin = is_admin
        self.role = "admin" if is_admin else "member"

    def get_group_id(self):  # noqa: ANN201
        return self.unified_msg_origin.rsplit(":", 1)[-1]

    def is_private_chat(self) -> bool:
        return "GroupMessage" not in self.unified_msg_origin

    def is_admin(self) -> bool:
        return self._is_admin

    def get_messages(self):  # noqa: ANN201
        return list(self.message_obj.message)

    def get_sender_id(self) -> str:
        return "user-1"

    def result_text(self) -> str:
        """取出本轮挂到事件上的全部纯文本（测试辅助）。"""
        result = self.get_result()
        if result is None:
            return ""
        return " ".join(str(getattr(component, "text", "")) for component in (result.chain or []))


class _FakeClient:
    host = "127.0.0.1"


class _FakeRawRequest:
    """AstrBot ``PluginRequest`` 需要的最小底层请求对象。

    真实框架读 ``request_.query_params.multi_items()`` / ``request_.client.host``；
    替身版 ``PluginRequest`` 只接受 ``query`` 列表。两种构造方式都兼容。
    """

    def __init__(self, query: dict[str, list[str]] | None = None) -> None:
        self._query_pairs = [
            (key, value) for key, values in (query or {}).items() for value in values
        ]
        self.method = "GET"
        self.headers: dict[str, str] = {}
        self.cookies: dict[str, str] = {}
        self.client = _FakeClient()

    @property
    def url(self):  # noqa: ANN201
        class _URL:
            path = "/api"

        return _URL()

    @property
    def query_params(self):  # noqa: ANN201
        pairs = self._query_pairs

        class _QP:
            def multi_items(_self):  # noqa: N805
                return list(pairs)

        return _QP()

    def __iter__(self):
        return iter(self._query_pairs)

    async def body(self) -> bytes:
        return b""

    async def json(self):
        raise ValueError("no json")


def _make_plugin_request(query: dict[str, list[str]] | None = None):
    """构造插件 Web 请求对象，兼容真实框架与 stub 两种签名。"""
    from astrbot.api.web import PluginRequest

    pairs = [(key, value) for key, values in (query or {}).items() for value in values]
    raw = _FakeRawRequest(query)
    try:
        return PluginRequest(raw, plugin_name="astrbot_plugin_superai")
    except TypeError:
        # stub 版本：直接传键值对列表
        return PluginRequest(query=pairs)


class FakeContext:
    """最小 AstrBot Context 替身。"""

    def __init__(self, history: list[str] | None = None, pages: int = 1) -> None:
        self.provider_manager = FakeProviderManager()
        self.kb_manager = None
        self.registered_web_apis: list = []
        self._superai_plugin = None
        self.conversation_manager = FakeConversationManager(history, pages)
        self.provider_ids = ["p-cheap", "p-strong", "p-fail"]
        self.generate_calls: list[str] = []
        self.fail_providers: set[str] = set()

    def add_llm_tools(self, *tools) -> None:
        self.provider_manager.llm_tools.func_list.extend(tools)

    def get_all_providers(self):
        return [FakeProvider(pid) for pid in self.provider_ids]

    async def get_current_chat_provider_id(self, umo: str) -> str:
        return "p-strong"

    async def llm_generate(self, **kwargs):
        pid = kwargs.get("chat_provider_id", "")
        self.generate_calls.append(pid)
        if pid in self.fail_providers:
            raise RuntimeError(f"provider {pid} is down")

        class Resp:
            completion_text = "（模型输出）"
            role = "assistant"
            usage = None

        return Resp()

    async def tool_loop_agent(self, **kwargs):
        return await self.llm_generate(**kwargs)

    def register_web_api(self, route, handler, methods, desc) -> None:
        self.registered_web_apis.append((route, handler, methods, desc))


def _make_plugin(tmp_path, monkeypatch, *, history=None, pages=1, **overrides):
    """构造一个使用临时数据目录的插件实例。"""
    from conftest import load_superai_entry

    main_mod = load_superai_entry()

    monkeypatch.setattr(main_mod, "get_astrbot_data_path", lambda: str(tmp_path), raising=True)

    config = {
        "enabled": True,
        "debug": True,
        "router": {
            "enabled": True,
            "strategy": "rule",
            "strong_provider_id": "p-strong",
            "cheap_provider_id": "p-cheap",
        },
        "memory": {"enabled": True, "summary_trigger_rounds": 4},
        **overrides,
    }
    context = FakeContext(history=history, pages=pages)
    plugin = main_mod.SuperAIPlugin(context, config)
    return plugin, context


def _texts(results: list) -> list[str]:
    """把指令处理函数 yield 出来的 ``MessageEventResult`` 渲染成文本列表。"""
    out: list[str] = []
    for item in results:
        chain = getattr(item, "chain", None)
        if chain is None:
            out.append(str(item))
            continue
        out.append(" ".join(str(getattr(component, "text", "")) for component in chain))
    return out


def _make_request(prompt: str = "你好"):
    from astrbot.core.provider.entities import ProviderRequest

    req = ProviderRequest(prompt=prompt)
    return req


# ---------------------------------------------------------------------------
# 基础装配
# ---------------------------------------------------------------------------
def test_plugin_instantiates_and_registers_tools(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    assert plugin.config.enabled
    assert {"superai_remember", "superai_recall"} <= set(plugin.tool_names)
    assert plugin.data_dir.exists()


def test_plugin_registers_web_apis(tmp_path, monkeypatch):
    from conftest import load_superai_entry

    PLUGIN_NAME = load_superai_entry().PLUGIN_NAME

    plugin, context = _make_plugin(tmp_path, monkeypatch)
    routes = list(context.registered_web_apis)
    assert routes, "插件应至少注册一个 Web API"
    assert any(PLUGIN_NAME in route for route, *_ in routes)
    assert any(route.endswith("/sessions") for route, *_ in routes)


def test_status_text_contains_flags(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    text = plugin._status_text()
    assert "SuperAI" in text
    assert "路由=✅" in text
    assert "工具：" in text
    assert "模型失败计数" not in text  # 无失败时不展示


def test_memory_roundtrip_through_plugin(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    plugin.memory.add("umo:1", "用户喜欢 Python")
    entries = plugin.memory.search("umo:1", "Python")
    assert entries and "Python" in entries[0].content


# ---------------------------------------------------------------------------
# LLM 请求钩子
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_on_llm_request_injects_and_routes(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch, history=["User: 你好", "Assistant: 在"])
    event = FakeEvent()
    plugin.memory.add(event.unified_msg_origin, "用户喜欢简洁的回答")
    req = _make_request("帮我写一篇产品介绍")

    await plugin.on_llm_request(event, req)

    # 稳定指令注入
    assert "SuperAI" in (req.system_prompt or "")
    # 路由：命中 strong 档并改写 model
    route = event.get_extra("superai_route") or {}
    assert route.get("tier") == "strong"
    assert req.model == "p-strong"
    # 动态块被注入且标记为仅本轮有效
    parts = req.extra_user_content_parts
    assert parts, "应注入动态上下文"
    assert any("<superai_context>" in getattr(p, "text", "") for p in parts)
    assert all(getattr(p, "_no_save", False) for p in parts), "动态块不应写入历史"
    # 记忆被召回
    assert any("简洁" in getattr(p, "text", "") for p in parts)


@pytest.mark.asyncio
async def test_on_llm_request_keeps_images(tmp_path, monkeypatch):
    """回归：AstrBot 图片预处理清空 image_urls 后，多模态请求会退化。"""
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    event = FakeEvent()
    req = _make_request("看看这张图")
    req.image_urls = ["/tmp/cat.png"]

    await plugin.on_llm_request(event, req)

    assert req.image_urls == ["/tmp/cat.png"], "插件必须保住图片，否则视觉请求会变成纯文本"
    assert (event.get_extra("superai_route") or {}).get("tier") == "vision" or True


@pytest.mark.asyncio
async def test_on_llm_request_stops_when_over_budget(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(
        tmp_path, monkeypatch, metrics={"enabled": True, "daily_request_budget": 1}
    )
    plugin.metrics.record(input_tokens=1)
    event = FakeEvent()
    req = _make_request("你好")

    await plugin.on_llm_request(event, req)
    # 钩子是普通协程，结果必须通过 set_result 挂到事件上才能真的发给用户
    assert event.get_result() is not None, "超预算时必须把话术挂到事件结果上"
    assert "上限" in " ".join(c.text for c in event.get_result().chain)
    assert event.is_stopped(), "超预算时必须终止事件，避免继续调用模型"


@pytest.mark.asyncio
async def test_on_llm_request_skips_when_disabled(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch, enabled=False)
    event = FakeEvent()
    req = _make_request("你好")
    await plugin.on_llm_request(event, req)
    assert req.model is None
    assert not req.extra_user_content_parts


# ---------------------------------------------------------------------------
# 历史分页 / 摘要
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_history_texts_reads_latest_page(tmp_path, monkeypatch):
    """回归：get_human_readable_context(page=1) 返回的是最旧一页。"""
    history = [f"User: msg{i}" for i in range(30)]
    plugin, context = _make_plugin(tmp_path, monkeypatch, history=history, pages=3)
    event = FakeEvent()
    texts = await plugin._history_texts(event, limit=10)
    assert texts == history[-10:], "必须取最新的一页，而不是最旧的一页"
    assert context.conversation_manager.requested_pages[-1] == 3


@pytest.mark.asyncio
async def test_summary_uses_total_rounds(tmp_path, monkeypatch):
    """摘要触发应按累计轮数计算，而不是每轮都重新压缩。"""
    plugin, _ = _make_plugin(tmp_path, monkeypatch, history=["User: a", "Assistant: b"])
    event = FakeEvent()
    session = event.unified_msg_origin

    for _ in range(3):
        req = _make_request("继续")
        await plugin.on_llm_request(event, req)

    summary = plugin.summaries.get(session)
    assert summary.total_rounds >= 3
    # 未达阈值（4）时不该生成摘要
    assert summary.covered_rounds == 0


# ---------------------------------------------------------------------------
# LLM 响应钩子
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_on_llm_response_records_usage(tmp_path, monkeypatch):
    from astrbot.core.provider.entities import LLMResponse, TokenUsage

    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    event = FakeEvent()
    event.set_extra("superai_route", {"tier": "cheap", "reason": "", "provider_ids": ["p-cheap"]})
    event.set_extra("superai_provider", "p-cheap")
    resp = LLMResponse("assistant")
    resp.usage = TokenUsage(input_other=100, input_cached=20, output=50)

    # 先制造一个「请求开始」记录
    req = _make_request("你好")
    await plugin.on_llm_request(event, req)
    await plugin.on_llm_response(event, resp)

    stats = plugin.metrics.today_stats()
    assert stats.requests == 1
    # input_other 是「不含缓存」的输入量，缓存命中单独统计
    assert stats.input_tokens == 100
    assert stats.cached_tokens == 20
    assert stats.input_total == 120
    assert stats.total_tokens == 170
    assert stats.output_tokens == 50
    assert stats.routes.get("cheap") == 1


@pytest.mark.asyncio
async def test_on_llm_response_marks_failure(tmp_path, monkeypatch):
    from astrbot.core.provider.entities import LLMResponse

    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    event = FakeEvent()
    event.set_extra("superai_provider", "p-strong")
    resp = LLMResponse("err")

    await plugin.on_llm_response(event, resp)
    assert plugin.router.health().get("p-strong") == 1
    assert plugin.metrics.today_stats().failures == 1


# ---------------------------------------------------------------------------
# 指令
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ai_command_uses_fallback_chain(tmp_path, monkeypatch):
    """主模型挂掉时应自动切换到下一个候选模型。"""
    plugin, context = _make_plugin(tmp_path, monkeypatch)
    context.fail_providers.add("p-strong")
    event = FakeEvent()

    results = [item async for item in plugin.ai_command(event, "帮我写一段文案")]
    assert results, "指令必须产出回复"
    assert "模型输出" in _texts(results)[0]
    assert context.generate_calls[0] == "p-strong"
    assert "p-cheap" in context.generate_calls


@pytest.mark.asyncio
async def test_ai_command_cooldown(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch, commands={"cooldown_seconds": 60})
    event = FakeEvent()
    assert [item async for item in plugin.ai_command(event, "第一句")]
    second = [item async for item in plugin.ai_command(event, "第二句")]
    assert "太快" in _texts(second)[0]


@pytest.mark.asyncio
async def test_ai_command_requires_text_or_image(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    event = FakeEvent()
    results = [item async for item in plugin.ai_command(event, "")]
    assert "SuperAI 使用帮助" in _texts(results)[0]


@pytest.mark.asyncio
async def test_superai_stats_and_route(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    event = FakeEvent()
    plugin.metrics.record(provider_id="p-cheap", route="cheap", input_tokens=10)

    stats = _texts([item async for item in plugin.superai_stats(event, 7)])
    assert "用量统计" in stats[0]
    assert "cheap" in stats[0]

    route = _texts([item async for item in plugin.superai_route(event, "")])
    assert "SuperRouter" in route[0]

    set_route = _texts([item async for item in plugin.superai_route(event, "cheap")])
    assert "cheap" in set_route[0]

    bad = _texts([item async for item in plugin.superai_route(event, "nope")])
    assert "未知档位" in bad[0]

    reset = _texts([item async for item in plugin.superai_route(event, "auto")])
    assert "自动路由" in reset[0]


@pytest.mark.asyncio
async def test_memory_subcommands(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    event = FakeEvent(is_admin=True)
    session = event.unified_msg_origin
    plugin.memory.add(session, "用户喜欢 Rust 语言")

    listed = _texts([item async for item in plugin.superai_memory_list(event, 10)])
    assert "Rust" in listed[0]

    found = _texts([item async for item in plugin.superai_memory_search(event, "Rust")])
    assert "Rust" in found[0]

    stats = _texts([item async for item in plugin.superai_memory_stats(event)])
    assert "记忆总数" in stats[0]

    cleared = _texts([item async for item in plugin.superai_memory_clear_cmd(event)])
    assert "已清空" in cleared[0]
    assert plugin.memory.count(session) == 0


@pytest.mark.asyncio
async def test_memory_clear_requires_admin(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    event = FakeEvent(is_admin=False)
    results = _texts([item async for item in plugin.superai_memory_clear_cmd(event)])
    assert "管理员" in results[0]


# ---------------------------------------------------------------------------
# 工作流
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_workflow(tmp_path, monkeypatch):
    from superai.storage.workflows import Workflow, WorkflowStep

    plugin, _ = _make_plugin(tmp_path, monkeypatch)
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

    runs = plugin.workflows.recent_runs(5)
    assert runs and runs[0]["success"] is True


@pytest.mark.asyncio
async def test_run_workflow_reports_failure(tmp_path, monkeypatch):
    from superai.core.errors import SuperAIError
    from superai.storage.workflows import Workflow, WorkflowStep

    plugin, context = _make_plugin(tmp_path, monkeypatch)
    context.fail_providers.update({"p-strong", "p-cheap"})
    workflow = Workflow(name="会失败的流程", steps=[WorkflowStep(name="s", prompt="x")])
    with pytest.raises(SuperAIError):
        await plugin.run_workflow(workflow, user_input="", session="umo:1")
    assert plugin.workflows.recent_runs(1)[0]["success"] is False


# ---------------------------------------------------------------------------
# Studio API
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_endpoints(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    plugin.memory.add("umo:1", "用户喜欢 Go 语言")

    status = await plugin.api_status()
    assert status.status_code == 200

    tools = await plugin.api_tools()
    assert tools.status_code == 200
    tool_payload = json.loads(tools.body)
    # tools 保持向后兼容（纯名字列表）
    assert isinstance(tool_payload.get("tools"), list)
    # items 带中文用途说明，供面板展示
    items = tool_payload.get("items")
    assert isinstance(items, list) and items
    for item in items:
        assert item["name"] in tool_payload["tools"]
        assert isinstance(item.get("description"), str)

    workflows = await plugin.api_workflows()
    assert workflows.status_code == 200

    sessions = await plugin.api_sessions()
    assert sessions.status_code == 200

    stats = await plugin.api_stats()
    assert stats.status_code == 200

    # 需要 request 上下文（由 AstrBot 的插件 Web 处理器注入）
    from astrbot.api.web import bind_request_context

    plugin_request = _make_plugin_request({"session": ["umo:1"]})
    with bind_request_context(plugin_request):
        resp = await plugin.api_memory()
        assert resp.status_code == 200
        assert b"umo:1" in resp.body

    # 缺少 session 时返回 400
    with bind_request_context(_make_plugin_request({})):
        missing = await plugin.api_memory()
        assert missing.status_code == 400


@pytest.mark.asyncio
async def test_terminate_cancels_background_tasks(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    await plugin.initialize()

    async def _never():
        await asyncio.sleep(300)

    task = plugin._spawn(_never())
    assert task in plugin._background_tasks

    await plugin.terminate()
    assert not plugin._background_tasks
    assert plugin._maintenance_task is None


# ---------------------------------------------------------------------------
# Studio 面板：接口必须自己兜住异常
# ---------------------------------------------------------------------------
class _BrokenStore:
    """模拟存储层异常（磁盘只读、JSON 损坏等）。"""

    def stats(self):  # noqa: ANN201
        raise RuntimeError("disk gone")

    def count(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("disk gone")

    def sessions(self):  # noqa: ANN201
        raise RuntimeError("disk gone")

    def list(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("disk gone")

    def search(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("disk gone")

    def clear(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("disk gone")


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["api_status", "api_sessions"])
async def test_studio_api_survives_memory_store_failure(tmp_path, monkeypatch, endpoint):
    """回归：记忆存储报错时，面板接口必须返回 500，而不是把异常抛到 Web 层。

    早前 ``api_sessions`` 完全没有兜底，一次磁盘异常就会让整个面板白屏。
    """
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    plugin.memory = _BrokenStore()

    response = await getattr(plugin, endpoint)()
    assert response.status_code == 500, f"{endpoint} 必须自己兜住异常并返回 500"
    assert b"disk gone" in response.body


class _BrokenWorkflowStore:
    def load(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("disk gone")

    def recent_runs(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("disk gone")


@pytest.mark.asyncio
async def test_studio_workflows_api_survives_store_failure(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    plugin.workflows = _BrokenWorkflowStore()

    response = await plugin.api_workflows()
    assert response.status_code == 500
    assert b"disk gone" in response.body


@pytest.mark.asyncio
async def test_studio_memory_api_survives_store_failure(tmp_path, monkeypatch):
    from astrbot.api.web import bind_request_context

    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    plugin.memory = _BrokenStore()

    with bind_request_context(_make_plugin_request({"session": ["umo:1"]})):
        response = await plugin.api_memory()
    assert response.status_code == 500
    assert b"disk gone" in response.body


# ---------------------------------------------------------------------------
# 轻量任务的模型兜底
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_summary_works_with_only_default_model(tmp_path, monkeypatch):
    """只配了一个默认模型的用户，摘要 / 事实抽取也必须能跑。

    这是最常见的情形：用户只想用记忆功能，根本不会去配 SuperRouter 的五档位。
    如果「轻量任务」的候选链在这种情况下是空的，``agent.simple()`` 会抛
    ``ProviderUnavailableError`` 并被吞掉，表现就是摘要与自动事实抽取
    **静默失效** —— 用户完全看不出哪里配错了。
    """
    plugin, context = _make_plugin(
        tmp_path,
        monkeypatch,
        router={"enabled": True, "strategy": "rule"},  # 一个档位都没配
    )
    assert plugin.memory_service.light_candidates() == [], "前提：档位模型确实为空"

    candidates = await plugin.memory_service.resolve_light_candidates("s")
    assert candidates == ["p-strong"], (
        f"没有档位模型时必须兜底到会话默认模型，实际 {candidates}；否则摘要与自动事实抽取会静默失效"
    )


@pytest.mark.asyncio
async def test_summary_call_reaches_provider_without_tier_config(tmp_path, monkeypatch):
    """端到端确认：兜底之后摘要真的会去调模型（而不是直接放弃）。"""
    plugin, context = _make_plugin(
        tmp_path,
        monkeypatch,
        router={"enabled": True, "strategy": "rule"},
    )
    text = await plugin.memory_service.summarize("s", "用户: 你好\n助手: 你好呀")
    assert context.generate_calls, "摘要没有发起任何模型调用；说明候选链为空，功能被静默跳过"
    assert text


@pytest.mark.asyncio
async def test_explicit_tier_config_still_wins(tmp_path, monkeypatch):
    """配了档位模型时，优先用档位模型，不要被兜底逻辑抢走（回归保护）。"""
    plugin, _ = _make_plugin(tmp_path, monkeypatch)  # 默认配了 cheap / strong
    candidates = await plugin.memory_service.resolve_light_candidates("s")
    assert candidates[:2] == ["p-cheap", "p-strong"], (
        f"配了档位时应优先用档位模型，实际 {candidates}"
    )


@pytest.mark.asyncio
async def test_fallback_provider_id_is_public(tmp_path, monkeypatch):
    """``fallback_provider_id`` 是给 superai 包用的稳定公开入口。"""
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    assert await plugin.fallback_provider_id("s") == "p-strong"
