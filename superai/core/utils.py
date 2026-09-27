"""通用小工具：文本裁剪、token 估算、异步重试等。"""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

T = TypeVar("T")

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_WS_RE = re.compile(r"[ \t\u3000]+")
_TAG_RE = re.compile(r"<[^>]{0,200}>")
_CRLF_RE = re.compile(r"\r\n?")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def estimate_tokens(text: Any) -> int:
    """粗略估算文本 token 数。

    规则：中日韩字符按 1 token/字，其余按 4 字符/token。
    这是保守估算，仅用于「是否需要摘要」「是否超长」这类判断，
    不用于计费。
    """
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return cjk + (other + 3) // 4


def truncate(text: Any, limit: int, *, suffix: str = "……") -> str:
    """按字符数裁剪文本，保留尾部省略号。"""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if limit <= 0:
        return ""
    if suffix and len(suffix) >= limit:
        # 后缀本身就超限时退化为硬截断，避免返回超长文本
        return text[:limit]
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(suffix))
    return text[:keep] + suffix


def normalize_space(text: Any) -> str:
    """压缩空白字符，便于做去重与指纹。"""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = _CRLF_RE.sub("\n", text)
    text = _WS_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def strip_markup(text: Any) -> str:
    """去掉类 XML 标签（``<think>``、``<superai_context>`` 等）。

    仅用于把模型输出写回聊天记录等展示场景，避免内部标记泄漏给用户。
    """
    if not isinstance(text, str):
        return "" if text is None else str(text)
    return _TAG_RE.sub("", text).strip()


def fingerprint(text: Any, *, length: int = 16) -> str:
    """对文本生成稳定短指纹（用于记忆去重）。"""
    normalized = normalize_space(text).lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[: max(4, int(length))]


def now_ts() -> int:
    return int(time.time())


def format_ts(ts: int | float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    try:
        return time.strftime(fmt, time.localtime(int(ts)))
    except (ValueError, OSError, OverflowError):
        return "-"


def as_int(
    value: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None
) -> int:
    """把任意配置值安全地转成 int，并可选地夹紧到给定区间。

    配置来自 WebUI，用户可能填了空串、``"abc"`` 或数字字符串；
    直接 ``int()`` 会让整条消息链路抛异常，所以统一走这里。
    """
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def as_bool(value: Any, default: bool = False) -> bool:
    """把任意配置值安全地读成 bool（兼容 ``"true"`` / ``"1"`` / ``"是"``）。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y", "是", "开"}
    return bool(value)


def as_list(value: Any) -> list[str]:
    """把配置项安全地读成字符串列表（兼容 ``"a,b"`` 形式的字符串）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


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


@asynccontextmanager
async def task_group():
    """创建后台任务并在退出时统一回收，避免「task was destroyed」告警。"""
    tasks: set[asyncio.Task] = set()

    def spawn(coro: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    try:
        yield spawn
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
