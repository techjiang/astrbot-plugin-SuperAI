"""通用工具测试。"""

import pytest

from superai.core.utils import (
    estimate_tokens,
    extract_json,
    fingerprint,
    format_ts,
    normalize_space,
    retry_async,
    truncate,
)


def test_estimate_tokens_cjk():
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("") == 0
    # 英文按 4 字符 1 token 估算
    assert estimate_tokens("abcdefgh") == 2


def test_truncate():
    assert truncate("hello", 10) == "hello"
    assert truncate("hello world", 8).endswith("……")
    assert len(truncate("hello world", 8)) == 8
    assert truncate("hello", 0) == ""


def test_normalize_space():
    assert normalize_space("  a \t b  ") == "a b"


def test_fingerprint_stable():
    assert fingerprint("Hello World") == fingerprint("hello   world")
    assert fingerprint("a") != fingerprint("b")


def test_format_ts_invalid():
    assert format_ts(0) != ""
    assert format_ts("bad") == "-"


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_from_fence():
    text = '这是结果：\n```json\n{"a": [1, 2]}\n```\n完毕'
    assert extract_json(text) == {"a": [1, 2]}


def test_extract_json_embedded():
    assert extract_json("前缀 [1, 2, 3] 后缀") == [1, 2, 3]


def test_extract_json_invalid():
    with pytest.raises(ValueError):
        extract_json("完全没有 JSON")


@pytest.mark.asyncio
async def test_retry_async_succeeds_after_failures():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    assert await retry_async(flaky, attempts=3, base_delay=0.01) == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_retry_async_raises_after_exhaustion():
    async def always_fail():
        raise RuntimeError("always")

    with pytest.raises(RuntimeError):
        await retry_async(always_fail, attempts=2, base_delay=0.01)
