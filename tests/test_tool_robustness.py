"""工具容错回归测试 —— 上游返回意外形状时不得把整轮对话打断。

为什么要单独设防：工具里抛出的异常会被框架的 ``FunctionToolExecutor``
包装后向上冒泡（见 ``astrbot/core/astr_agent_tool_exec.py``），
结果是**整轮对话中断**，用户只看到一句报错。工具本该优雅降级成
「没找到内容 / 搜索失败」这样的文本，让模型继续作答。

本文件覆盖此前两处会抛异常的真实缺口：

1. ``retrieve_kb`` 里对相关度做了两次解析，第二次没保护 ——
   上游给出非数字 score 时抛 ``ValueError``；
2. ``WebSearchTool.run_tool`` 只捕获 ``aiohttp.ClientError`` / ``TimeoutError``，
   配置类错误（选了 searxng 却没填地址）与 JSON 解析失败都会穿透。
"""

from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# 知识库检索
# ---------------------------------------------------------------------------
class _FakeKbManager:
    def __init__(self, result):
        self._result = result

    async def retrieve(self, query, kb_names, top_m_final=5):  # noqa: ANN001
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeContext:
    def __init__(self, result):
        self.kb_manager = _FakeKbManager(result)


def _kb_result(score):
    return {
        "results": [
            {
                "kb_name": "kb",
                "doc_name": "doc",
                "content": "一段知识库内容",
                "score": score,
            },
        ]
    }


@pytest.mark.parametrize(
    "score",
    ["high", "", None, "很高", object(), [1]],
)
def test_retrieve_kb_tolerates_non_numeric_score(score):
    """非数字相关度只能降级成 0.00，不得抛异常。

    回归：此前 '' 里第一次解析有 try/except，展示时又写了第二遍
    ``float(item.get("score") or 0)``（无保护），于是抛 ValueError，
    并把整轮工具调用打断。
    """
    from superai.tools.kb_tools import retrieve_kb

    text = asyncio.run(retrieve_kb(_FakeContext(_kb_result(score)), "问题", ["kb"]))
    assert "一段知识库内容" in text
    assert "相关度 0.00" in text


def test_retrieve_kb_formats_numeric_score():
    """正常数字相关度仍按两位小数展示。"""
    from superai.tools.kb_tools import retrieve_kb

    text = asyncio.run(retrieve_kb(_FakeContext(_kb_result(0.876)), "问题", ["kb"]))
    assert "相关度 0.88" in text


def test_retrieve_kb_survives_manager_error():
    """知识库后端异常不得冒泡。"""
    from superai.tools.kb_tools import retrieve_kb

    text = asyncio.run(
        retrieve_kb(_FakeContext(RuntimeError("后端挂了")), "问题", ["kb"]),
    )
    assert text == ""


def test_retrieve_kb_handles_missing_manager():
    """没有 KB 管理器时返回空串。"""

    class _NoKb:
        kb_manager = None

    from superai.tools.kb_tools import retrieve_kb

    assert asyncio.run(retrieve_kb(_NoKb(), "问题", ["kb"])) == ""


# ---------------------------------------------------------------------------
# 联网搜索
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 测试替身：只注入配置与插件实例，不触碰网络
# ---------------------------------------------------------------------------
class _FakeCfg:
    web_enabled = True

    def __init__(self, *, engine="duckduckgo", searxng_base="", timeout=15):
        self.web = {
            "engine": engine,
            "searxng_base": searxng_base,
            "timeout": timeout,
            "max_results": 5,
        }


class _FakePlugin:
    def __init__(self, config):
        self.config = config
        self.memory = None
        self.metrics = None
        self.context = None


class _FakeToolContext:
    def __init__(self, config):
        self.plugin = _FakePlugin(config)


class _FakeRunContext:
    """``extract_tool_context`` 期望的三层结构。"""

    class _Inner:
        def __init__(self, plugin):
            self._superai_plugin = plugin

    class _Mid:
        def __init__(self, plugin):
            self.context = _FakeRunContext._Inner(plugin)

    def __init__(self, plugin=None):
        self.context = _FakeRunContext._Mid(plugin)
        self.event = None


class _bound_tool_ctx:
    """把工具运行时依赖注入到 ``_FakeRunContext`` 的上下文里。"""

    def __init__(self, tool_ctx):
        self._plugin = tool_ctx.plugin

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("timeout", ["abc", "15秒", "", None, object()])
def test_web_search_reports_config_error_instead_of_raising(timeout):
    """选了 searxng 却没填地址时，必须给出可读提示而不是抛异常。

    回归：``run_tool`` 只捕获 ``aiohttp.ClientError`` / ``TimeoutError``，
    配置类 ``ValueError`` 会穿透。虽然在 **LLM 工具路径**上
    ``SuperAITool.call`` 会兜住异常，但结果退化成一句无信息量的
    「工具执行失败：未配置 SearXNG 地址」；而 ``run_tool`` 本身
    仍是公开可调用的入口，必须自洽。
    """
    from superai.tools.web_tools import WebSearchTool

    tool = WebSearchTool()
    cfg = _FakeCfg(engine="searxng", searxng_base="", timeout=timeout)
    ctx = _FakeToolContext(cfg)
    result = asyncio.run(tool.run_tool(_FakeRunContext(ctx.plugin), query="测试"))

    assert isinstance(result, str) and result
    assert "搜索" in result


@pytest.mark.parametrize("timeout", ["abc", "15秒", "", None])
def test_web_fetch_tolerates_bad_timeout(timeout):
    """抓取工具的 timeout 同样来自配置，非数字时不得抛异常。"""
    from superai.tools.web_tools import FetchUrlTool

    tool = FetchUrlTool()
    cfg = _FakeCfg(engine="duckduckgo", searxng_base="", timeout=timeout)
    ctx = _FakeToolContext(cfg)
    # URL 不合法 -> 提前返回，不会真的发网络请求；
    # 这里主要是确认 timeout 解析不抛异常。
    result = asyncio.run(tool.run_tool(_FakeRunContext(ctx.plugin), url="not-a-url"))
    assert "URL 不合法" in result


def test_web_search_timeout_is_resolved_tolerantly():
    """超时字段一律走 as_float：字符串/空值都要能回落默认值。"""
    from superai.core.utils import as_float

    for bad in ("abc", "15秒", "", None, object()):
        assert as_float(bad, 15.0) == 15.0
    assert as_float("30", 15.0) == 30.0
