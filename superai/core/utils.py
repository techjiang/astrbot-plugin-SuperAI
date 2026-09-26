"""通用小工具：文本裁剪、token 估算、异步重试等。"""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_WS_RE = re.compile(r"[ \t\u3000]+")


def estimate_tokens(text: str) -> int:
    """粗略估算文本 token 数。

    规则：中日韩字符按 1 token/字，其余按 4 字符/token。
    这是保守估算，仅用于「是否需要摘要」「是否超长」这类判断，
    不用于计费。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return cjk + (other + 3) // 4


def truncate(text: str, limit: int, *, suffix: str = "……") -> str:
    """按字符数裁剪文本，保留尾部省略号。"""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(suffix))
    return text[:keep] + suffix


def normalize_space(text: str) -> str:
    """压缩空白字符，便于做去重与指纹。"""
    return _WS_RE.sub(" ", (text or "").replace("\r\n", "\n")).strip()


def fingerprint(text: str, *, length: int = 16) -> str:
    """对文本生成稳定短指纹（用于记忆去重）。"""
    normalized = normalize_space(text).lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:length]


def now_ts() -> int:
    return int(time.time())


def format_ts(ts: int | float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    try:
        return time.strftime(fmt, time.localtime(int(ts)))
    except (ValueError, OSError, OverflowError):
        return "-"


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.4,
    max_delay: float = 4.0,
    on_error: Callable[[Exception, int], None] | None = None,
) -> T:
    """指数退避重试。

    最后一次失败会原样抛出异常，由上层决定如何处理。
    """
    attempts = max(1, int(attempts))
    last_exc: Exception | None = None
    for index in range(attempts):
        try:
            return await func()
        except Exception as exc:  # noqa: BLE001 - 需要捕获任意异常以重试
            last_exc = exc
            if on_error is not None:
                on_error(exc, index + 1)
            if index == attempts - 1:
                break
            delay = min(max_delay, base_delay * (2**index))
            await asyncio.sleep(delay + random.uniform(0, 0.2))
    assert last_exc is not None
    raise last_exc


def extract_json(text: str) -> Any:
    """从可能带 markdown 代码块的模型输出中提取 JSON。"""
    import json

    if not text:
        raise ValueError("空文本无法解析 JSON")
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # 退一步：抓取第一个大括号 / 中括号片段
    for start, end in (("{", "}"), ("[", "]")):
        left = candidate.find(start)
        right = candidate.rfind(end)
        if left != -1 and right > left:
            try:
                return json.loads(candidate[left : right + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("模型输出中未找到合法 JSON")
