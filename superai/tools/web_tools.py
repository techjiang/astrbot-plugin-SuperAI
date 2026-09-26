"""联网搜索工具。

默认使用免 key 的 DuckDuckGo HTML 端点，也可切换到自建 SearXNG。
只依赖 ``aiohttp``（AstrBot 内置），不引入额外依赖。
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

import aiohttp
from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .base import SuperAITool, extract_tool_context

_TAG_RE = re.compile(r"<[^>]+>")
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL,
)
_DDG_SNIPPET_RE = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.DOTALL)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def strip_html(raw: str) -> str:
    """去除 HTML 标签并反转义实体。"""
    return html.unescape(_TAG_RE.sub("", raw or "")).strip()


def unwrap_ddg_url(url: str) -> str:
    """DuckDuckGo 会包装跳转链接，这里还原真实地址。"""
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        query = parse_qs(parsed.query).get("uddg")
        if query:
            return query[0]
    return url


@dataclass
class WebSearchTool(SuperAITool):
    """搜索互联网获取实时信息。"""

    name: str = "superai_web_search"
    description: str = (
        "搜索互联网获取实时信息（新闻、最新动态、天气、价格等）。"
        "当问题涉及训练数据之后的变化或用户明确要求联网时调用。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，尽量精炼。",
                },
                "max_results": {
                    "type": "number",
                    "description": "返回结果条数，默认 5，最大 10。",
                },
            },
            "required": ["query"],
        }
    )

    async def run_tool(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        tool_ctx = extract_tool_context(context)
        if tool_ctx is None or not tool_ctx.config.web_enabled:
            return "联网搜索未启用。"

        query = str(kwargs.get("query") or "").strip()
        if not query:
            return "缺少搜索关键词。"

        cfg = tool_ctx.config.web
        try:
            max_results = int(kwargs.get("max_results") or cfg.get("max_results") or 5)
        except (TypeError, ValueError):
            max_results = 5
        max_results = max(1, min(10, max_results))
        timeout = float(cfg.get("timeout") or 15)
        engine = str(cfg.get("engine") or "duckduckgo")

        try:
            if engine == "searxng":
                items = await self._search_searxng(cfg, query, max_results, timeout)
            else:
                items = await self._search_duckduckgo(query, max_results, timeout)
        except aiohttp.ClientError as exc:
            logger.warning(f"[SuperAI] 搜索请求失败：{exc}")
            return f"搜索失败：{exc}"
        except TimeoutError:
            return "搜索超时，请稍后再试。"

        if not items:
            return f"没有找到与「{query}」相关的结果。"

        lines = [f"关于「{query}」的搜索结果："]
        for index, item in enumerate(items, 1):
            lines.append(f"{index}. {item['title']}\n   {item['snippet']}\n   来源：{item['url']}")
        return "\n".join(lines)

    async def _search_duckduckgo(
        self, query: str, max_results: int, timeout: float
    ) -> list[dict[str, str]]:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        async with (
            aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as session,
            session.get(url) as response,
        ):
            if response.status != 200:
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message="DuckDuckGo 返回异常状态码",
                )
            body = await response.text()

        titles = list(_DDG_RESULT_RE.finditer(body))
        snippets = [strip_html(match.group("snippet")) for match in _DDG_SNIPPET_RE.finditer(body)]
        items: list[dict[str, str]] = []
        for index, match in enumerate(titles[:max_results]):
            title = strip_html(match.group("title"))
            link = unwrap_ddg_url(match.group("href"))
            snippet = snippets[index] if index < len(snippets) else ""
            if not title or not link:
                continue
            items.append({"title": title, "url": link, "snippet": snippet[:400]})
        return items

    async def _search_searxng(
        self, cfg: dict[str, Any], query: str, max_results: int, timeout: float
    ) -> list[dict[str, str]]:
        base = str(cfg.get("searxng_base") or "").rstrip("/")
        if not base:
            raise ValueError("未配置 SearXNG 地址（web.searxng_base）")
        url = f"{base}/search"
        params = {"q": query, "format": "json", "language": "auto"}
        headers = {"User-Agent": USER_AGENT}
        api_key = str(cfg.get("api_key") or "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        async with (
            aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as session,
            session.get(url, params=params) as response,
        ):
            if response.status != 200:
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message="SearXNG 返回异常状态码",
                )
            payload = await response.json(content_type=None)

        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return []
        items: list[dict[str, str]] = []
        for entry in results[:max_results]:
            if not isinstance(entry, dict):
                continue
            items.append(
                {
                    "title": str(entry.get("title") or "").strip(),
                    "url": str(entry.get("url") or "").strip(),
                    "snippet": str(entry.get("content") or "").strip()[:400],
                }
            )
        return [item for item in items if item["title"] and item["url"]]


@dataclass
class FetchUrlTool(SuperAITool):
    """抓取网页正文（简易正文提取）。"""

    name: str = "superai_fetch_url"
    description: str = (
        "抓取指定网页的正文文本，用于在搜索后深入阅读某个链接。仅在需要链接详情时调用。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的完整 URL（http/https）。"},
                "max_chars": {
                    "type": "number",
                    "description": "返回正文的最大字符数，默认 3000。",
                },
            },
            "required": ["url"],
        }
    )

    async def run_tool(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        tool_ctx = extract_tool_context(context)
        if tool_ctx is None or not tool_ctx.config.web_enabled:
            return "网页抓取未启用。"

        url = str(kwargs.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "URL 不合法，只支持 http/https。"

        try:
            max_chars = int(kwargs.get("max_chars") or 3000)
        except (TypeError, ValueError):
            max_chars = 3000
        max_chars = max(200, min(20000, max_chars))
        timeout = float(tool_ctx.config.web.get("timeout") or 15)

        headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as session,
                session.get(url) as response,
            ):
                if response.status != 200:
                    return f"抓取失败：HTTP {response.status}"
                body = await response.text(errors="ignore")
        except (aiohttp.ClientError, TimeoutError) as exc:
            return f"抓取失败：{exc}"

        text = _extract_main_text(body)
        if not text:
            return "未能从页面中提取到正文。"
        return text[:max_chars]


_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.I)
_BLOCK_RE = re.compile(r"</(p|div|h[1-6]|li|tr|section|article)>", re.I)


def _extract_main_text(body: str) -> str:
    """极简正文提取：去脚本样式 + 去标签 + 压空白。"""
    text = _SCRIPT_STYLE_RE.sub(" ", body or "")
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if len(line) > 1]
    # 合并连续短行，去掉导航噪声
    merged: list[str] = []
    buffer = ""
    for line in lines:
        if len(line) <= 20 and not line.endswith(("。", ".", "！", "!", "？", "?")):
            buffer += line
            if len(buffer) > 200:
                merged.append(buffer)
                buffer = ""
            continue
        if buffer:
            line = buffer + line
            buffer = ""
        merged.append(line)
    if buffer:
        merged.append(buffer)
    return "\n".join(merged)[:50000]


def dumps(data: Any) -> str:
    """调试用：稳定 JSON 序列化。"""
    return json.dumps(data, ensure_ascii=False)
