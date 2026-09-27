"""路由决策测试。"""

from superai.core.config import build_config
from superai.router.router import (
    TIER_CHEAP,
    TIER_DEFAULT,
    TIER_LONG_CONTEXT,
    TIER_REASONING,
    TIER_STRONG,
    TIER_VISION,
    SuperRouter,
)


def make_router(**router_overrides):
    config = build_config(
        {
            "router": {
                "enabled": True,
                "strategy": "rule",
                "cheap_provider_id": "cheap-model",
                "strong_provider_id": "strong-model",
                "reasoning_provider_id": "reason-model",
                "vision_provider_id": "vision-model",
                "long_context_provider_id": "long-model",
                **router_overrides,
            }
        }
    )
    return SuperRouter(config)


def test_image_request_routes_to_vision():
    router = make_router()
    decision = router.decide(prompt="看看这张图", has_image=True)
    assert decision.tier == TIER_VISION
    assert decision.primary == "vision-model"


def test_long_context_detected_by_tokens():
    router = make_router()
    decision = router.decide(prompt="继续", context_tokens=100000)
    assert decision.tier == TIER_LONG_CONTEXT


def test_reasoning_keyword():
    router = make_router()
    decision = router.decide(prompt="帮我推导一下这个公式，为什么成立")
    assert decision.tier == TIER_REASONING


def test_strong_keyword():
    router = make_router()
    decision = router.decide(prompt="帮我写一篇产品介绍")
    assert decision.tier == TIER_STRONG


def test_cheap_greeting():
    router = make_router()
    decision = router.decide(prompt="在吗")
    assert decision.tier == TIER_CHEAP


def test_custom_keyword_map_wins():
    router = make_router(keyword_map={"季度报表": "reasoning"})
    decision = router.decide(prompt="生成季度报表")
    assert decision.tier == TIER_REASONING
    assert "自定义关键词" in decision.reason


def test_strategy_cheap_first():
    router = make_router(strategy="cheap_first")
    assert router.decide(prompt="写一篇论文").tier == TIER_CHEAP


def test_strategy_quality_first():
    router = make_router(strategy="quality_first")
    assert router.decide(prompt="你好").tier == TIER_STRONG


def test_session_tier_overrides_everything():
    router = make_router()
    decision = router.decide(prompt="你好", session_tier="reasoning")
    assert decision.tier == TIER_REASONING


def test_fallback_chain_deduplicates_and_orders():
    router = make_router()
    decision = router.decide(prompt="你好")
    chain = router.fallback_chain(decision, primary=decision.primary, max_retries=5)
    assert chain[0] == "cheap-model"
    assert len(chain) == len(set(chain))


def test_fallback_chain_respects_max_retries():
    router = make_router(max_retries=0)
    decision = router.decide(prompt="你好")
    chain = router.fallback_chain(decision, primary=decision.primary)
    assert len(chain) == 1


def test_resolve_provider_prefers_healthy():
    router = make_router()
    router.mark_failure("cheap-model")
    decision = router.decide(prompt="你好")
    resolved = router.resolve_provider_id(decision)
    assert resolved != "cheap-model"


def test_resolve_provider_without_any_provider_raises():
    import pytest

    from superai.core.errors import RouteNotFoundError

    config = build_config({"router": {"enabled": True}})
    router = SuperRouter(config)
    decision = router.decide(prompt="你好")
    with pytest.raises(RouteNotFoundError):
        router.resolve_provider_id(decision, available_ids={"something-else"})


def test_router_disabled_returns_default():
    router = make_router(enabled=False)
    decision = router.decide(prompt="帮我写代码")
    assert decision.tier == TIER_DEFAULT


def test_needs_web_heuristic():
    router = make_router()
    assert router.needs_web("今天有什么新闻")
    assert not router.needs_web("你好呀")


# ---------------------------------------------------------------------------
# 降级链排序：单模型场景不得把唯一可用模型判为「不健康」
# ---------------------------------------------------------------------------
def test_single_provider_is_never_treated_as_unhealthy():
    """只有一个候选时，不该被判定为「不健康」。

    ``is_unhealthy`` 的语义是「连续失败达到阈值，**而且还有别的可用 provider**」
    （否则所有模型都在报错时会把全部候选都判为不健康，反而失去意义）。
    ``ordered_candidates`` 早前调用 ``is_unhealthy(pid)`` 时没有传
    ``available_ids``，等于绕开了这道保护。
    """
    config = build_config({"router": {"enabled": True, "strong_provider_id": "only-one"}})
    router = SuperRouter(config)
    router.mark_failure("only-one")
    router.mark_failure("only-one")

    assert router.is_unhealthy("only-one"), "不传 available_ids 时按旧语义仍判不健康"
    assert not router.is_unhealthy("only-one", available_ids={"only-one"})

    decision = router.decide(prompt="你好")
    assert router.ordered_candidates(decision, primary="only-one", available_ids={"only-one"}) == [
        "only-one"
    ]


def test_unhealthy_provider_is_demoted_in_order():
    """多候选中，连续失败的 provider 应排到后面。"""
    router = make_router()
    decision = router.decide(prompt="帮我写代码")
    assert decision.chain()[0] == "strong-model"

    router.mark_failure("strong-model")
    router.mark_failure("strong-model")
    ordered = router.ordered_candidates(
        decision, primary="strong-model", available_ids={"strong-model", "cheap-model"}
    )
    assert ordered[0] == "cheap-model", f"失败模型未被降级：{ordered}"
    assert set(ordered) == {"strong-model", "cheap-model"}


def test_failure_count_expires():
    """故障记录超过 TTL 后应自动遗忘，不长期拖累排序。"""
    import time

    router = make_router()
    router.mark_failure("strong-model")
    router.mark_failure("strong-model")
    assert router.failure_count("strong-model") == 2

    # 手动把失败时间推早到 TTL 之前
    router._last_failure["strong-model"] = time.time() - router.FAILURE_TTL - 1
    assert router.failure_count("strong-model") == 0
