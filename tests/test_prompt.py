"""提示词构建测试。"""

from superai.prompt import (
    STABLE_INSTRUCTION,
    append_dynamic,
    apply_stable_prompt,
    build_dynamic_block,
)


class FakeRequest:
    def __init__(self):
        self.system_prompt = ""
        self.extra_user_content_parts = []


def test_build_dynamic_block_empty():
    assert build_dynamic_block(include_time=False) == ""


def test_build_dynamic_block_contains_sections():
    block = build_dynamic_block(
        summary="用户在做插件",
        memories=["用户喜欢 Python"],
        route_tier="cheap",
        route_reason="短问候",
        include_time=False,
    )
    assert "<superai_context>" in block
    assert "用户在做插件" in block
    assert "用户喜欢 Python" in block
    assert "cheap" in block


def test_apply_stable_prompt_is_idempotent():
    req = FakeRequest()
    apply_stable_prompt(req)
    apply_stable_prompt(req)
    assert req.system_prompt.count(STABLE_INSTRUCTION) == 1


def test_apply_stable_prompt_keeps_existing():
    req = FakeRequest()
    req.system_prompt = "你是助手。"
    apply_stable_prompt(req, extra="风格：简洁")
    assert req.system_prompt.startswith("你是助手。")
    assert "风格：简洁" in req.system_prompt


def test_append_dynamic_without_textpart_support():
    req = FakeRequest()
    # 没有真实 TextPart 时返回 False，不应抛异常
    assert append_dynamic(req, "") is False
