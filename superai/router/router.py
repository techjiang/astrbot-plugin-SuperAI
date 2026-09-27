"""SuperRouter —— 多模型智能路由。

解决的问题：

- 一个 Bot 往往配置了多个模型（便宜的、强的、支持长上下文的、支持视觉的），
  但 AstrBot 默认只会用「当前使用的」那一个。
- SuperAI 根据「任务类型 + 消息内容 + 上下文长度 + 会话偏好」自动挑一个最合适的，
  并在失败时按预设顺序降级，保证回复不中断。

路由档位（tier）:

===========  ==================================================
tier         适用场景
===========  ==================================================
cheap        闲聊、翻译、改写等对推理要求低的请求
strong       复杂问答、写作、代码等需要高质量输出的请求
reasoning    数学、逻辑、推理、需要深度思考的请求
vision       带图片的请求
long_context 超长上下文的请求
default      未命中任何规则时使用会话当前模型
===========  ==================================================
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from ..core.config import SuperAIConfig
from ..core.errors import ProviderUnavailableError, RouteNotFoundError
from ..core.utils import estimate_tokens, format_ts

#: 路由档位常量
TIER_CHEAP = "cheap"
TIER_STRONG = "strong"
TIER_REASONING = "reasoning"
TIER_VISION = "vision"
TIER_LONG_CONTEXT = "long_context"
TIER_DEFAULT = "default"

ALL_TIERS = (
    TIER_CHEAP,
    TIER_STRONG,
    TIER_REASONING,
    TIER_VISION,
    TIER_LONG_CONTEXT,
    TIER_DEFAULT,
)

#: 命中这些关键词时优先走高推理档位
REASONING_KEYWORDS = (
    "证明",
    "推导",
    "为什么",
    "计算",
    "解方程",
    "算法",
    "复杂度",
    "debug",
    "报错",
    "堆栈",
    "traceback",
    "logic",
    "reason",
    "prove",
    "derive",
    "step by step",
    "一步步",
)

#: 命中这些关键词时优先走高质量档位
STRONG_KEYWORDS = (
    "写一篇",
    "写个",
    "帮我写",
    "总结",
    "翻译",
    "润色",
    "代码",
    "重构",
    "review",
    "方案",
    "报告",
    "文案",
    "论文",
    "translate",
    "summarize",
    "refactor",
)

#: 命中这些关键词时优先走便宜档位
CHEAP_KEYWORDS = (
    "你好",
    "在吗",
    "早上好",
    "晚安",
    "哈哈",
    "好的",
    "谢谢",
    "hi",
    "hello",
    "thanks",
    "ok",
)

#: 疑似需要联网的问题（配合 Web 搜索工具）
WEB_KEYWORDS = (
    "最新",
    "今天",
    "现在",
    "实时",
    "新闻",
    "股价",
    "天气",
    "赛程",
    "latest",
    "today",
    "news",
    "price",
)


@dataclass(slots=True)
class RouteDecision:
    """一次路由决策的结果。"""

    tier: str = TIER_DEFAULT
    reason: str = ""
    """决策原因，便于调试与展示"""

    provider_ids: list[str] = field(default_factory=list)
    """候选提供商 ID（按优先级排序）"""

    @property
    def primary(self) -> str:
        return self.provider_ids[0] if self.provider_ids else ""

    def chain(self) -> list[str]:
        """返回去重后的降级链。"""
        seen: set[str] = set()
        result: list[str] = []
        for pid in self.provider_ids:
            if pid and pid not in seen:
                seen.add(pid)
                result.append(pid)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "reason": self.reason,
            "provider_ids": self.provider_ids,
        }


class SuperRouter:
    """模型路由器。"""

    #: 连续失败达到该次数后，该 provider 被视作「不健康」
    UNHEALTHY_THRESHOLD = 2
    #: 失败记录的保留时长（秒）；超过后自动遗忘，避免旧故障长期拖累排序
    FAILURE_TTL = 1800.0

    def __init__(self, config: SuperAIConfig, *, store: Any | None = None) -> None:
        self.config = config
        self._failures: dict[str, int] = {}
        """provider_id -> 连续失败次数，用于健康度排序"""
        self._last_failure: dict[str, float] = {}
        """provider_id -> 最近一次失败时间戳"""
        self._store = store
        """可选的 JsonStore，用于把健康度持久化到磁盘"""
        self._load_health()

    # -- 健康度 -----------------------------------------------------------
    def _load_health(self) -> None:
        if self._store is None:
            return
        data = self._store.get("router_health")
        if not isinstance(data, dict):
            return
        now = time.time()
        for pid, raw in data.items():
            if not isinstance(raw, dict):
                continue
            try:
                ts = float(raw.get("ts") or 0)
                count = int(raw.get("count") or 0)
            except (TypeError, ValueError):
                continue
            if now - ts > self.FAILURE_TTL or count <= 0:
                continue
            self._failures[str(pid)] = count
            self._last_failure[str(pid)] = ts

    def _save_health(self) -> None:
        if self._store is None:
            return
        self._store.set(
            {
                pid: {"count": count, "ts": self._last_failure.get(pid, time.time())}
                for pid, count in self._failures.items()
                if count > 0
            },
            "router_health",
        )

    def _expire_failures(self) -> None:
        now = time.time()
        expired = [pid for pid, ts in self._last_failure.items() if now - ts > self.FAILURE_TTL]
        for pid in expired:
            self._failures.pop(pid, None)
            self._last_failure.pop(pid, None)

    def mark_failure(self, provider_id: str) -> None:
        if not provider_id:
            return
        self._expire_failures()
        self._failures[provider_id] = self._failures.get(provider_id, 0) + 1
        self._last_failure[provider_id] = time.time()
        self._save_health()
        logger.debug(
            f"[SuperAI] 记录模型失败：{provider_id}（连续 {self._failures[provider_id]} 次）"
        )

    def mark_success(self, provider_id: str) -> None:
        if not provider_id:
            return
        if self._failures.pop(provider_id, None) is not None:
            self._last_failure.pop(provider_id, None)
            self._save_health()

    def failure_count(self, provider_id: str) -> int:
        self._expire_failures()
        return self._failures.get(provider_id, 0)

    def is_unhealthy(self, provider_id: str, *, available_ids: set[str] | None = None) -> bool:
        """判断某 provider 是否「不健康」。

        不健康 = 连续失败次数达到阈值，**而且**还存在别的可用 provider。
        这样即使所有模型都在报错，也不会因为「全部不健康」而彻底不可用。
        """
        if self.failure_count(provider_id) < self.UNHEALTHY_THRESHOLD:
            return False
        if available_ids is None:
            return True
        others = [pid for pid in available_ids if pid != provider_id]
        return bool(others)

    def health(self) -> dict[str, int]:
        self._expire_failures()
        return dict(self._failures)

    def health_detail(self, *, available_ids: set[str] | None = None) -> list[dict[str, Any]]:
        """返回面向展示的健康度列表（Studio 面板使用）。"""
        self._expire_failures()
        ids = set(available_ids or self._failures)
        ids |= set(self._failures)
        rows: list[dict[str, Any]] = []
        for pid in sorted(ids):
            count = self._failures.get(pid, 0)
            rows.append(
                {
                    "provider_id": pid,
                    "failures": count,
                    "healthy": count < self.UNHEALTHY_THRESHOLD,
                    "last_failure": format_ts(self._last_failure.get(pid, 0))
                    if pid in self._last_failure
                    else "",
                }
            )
        return rows

    # -- 决策 -------------------------------------------------------------
    def decide(
        self,
        *,
        prompt: str,
        has_image: bool = False,
        has_audio: bool = False,
        context_tokens: int = 0,
        session_tier: str = "",
        hint_texts: list[str] | None = None,
    ) -> RouteDecision:
        """根据请求特征决定路由档位与候选提供商。

        参数:
            prompt: 本轮用户输入。
            has_image: 是否包含图片。
            has_audio: 是否包含语音。
            context_tokens: 预估的上下文 token 数。
            session_tier: 会话级强制档位（例如用户用 ``/superai route reason`` 指定）。
            hint_texts: 额外的判断文本（例如历史消息、事件附加信息）。
        """
        if not self.config.router_enabled:
            return RouteDecision(tier=TIER_DEFAULT, reason="路由未启用")

        if session_tier and session_tier in ALL_TIERS:
            return self._build(session_tier, f"会话指定档位 {session_tier}")

        strategy = str(self.config.router.get("strategy") or "rule")

        if has_image:
            return self._build(TIER_VISION, "包含图片，选择视觉模型")

        long_ctx_tokens = int(self.config.router.get("long_context_tokens") or 64000)
        if context_tokens and context_tokens >= long_ctx_tokens:
            return self._build(
                TIER_LONG_CONTEXT,
                f"上下文约 {context_tokens} tokens，超过长上下文阈值 {long_ctx_tokens}",
            )

        if strategy == "cheap_first":
            return self._build(TIER_CHEAP, "策略：优先省钱")
        if strategy == "quality_first":
            return self._build(TIER_STRONG, "策略：优先质量")

        text = " ".join([prompt or "", *(hint_texts or [])]).lower()
        if strategy == "rule":
            decision = self._decide_by_rule(text)
            if decision is not None:
                return decision

        # auto / 未命中规则：按内容长度与特征启发式判断
        tokens = estimate_tokens(text)
        if any(keyword in text for keyword in REASONING_KEYWORDS):
            return self._build(TIER_REASONING, "命中推理关键词")
        if tokens >= 800:
            return self._build(TIER_STRONG, f"输入较长（约 {tokens} tokens），选择高质量模型")
        if tokens <= 24 and any(keyword in text for keyword in CHEAP_KEYWORDS):
            return self._build(TIER_CHEAP, "短问候语，选择低成本模型")
        return self._build(TIER_DEFAULT, "未命中规则，使用会话默认模型")

    def _decide_by_rule(self, text: str) -> RouteDecision | None:
        """规则策略：关键词映射 > 内置关键词表。"""
        # 1) 用户在配置里自定义的关键词映射
        for keyword, tier in self.config.keyword_routes().items():
            if keyword and keyword in text:
                target = tier if tier in ALL_TIERS else TIER_STRONG
                return self._build(target, f"自定义关键词「{keyword}」命中")

        # 2) 内置关键词表
        if has_image_like(text):
            return self._build(TIER_VISION, "命中图片相关描述")
        if any(keyword in text for keyword in REASONING_KEYWORDS):
            return self._build(TIER_REASONING, "命中推理关键词")
        if any(keyword in text for keyword in STRONG_KEYWORDS):
            return self._build(TIER_STRONG, "命中高质量任务关键词")
        if any(keyword in text for keyword in CHEAP_KEYWORDS) and len(text) <= 30:
            return self._build(TIER_CHEAP, "命中闲聊关键词")
        return None

    def needs_web(self, text: str) -> bool:
        """启发式判断是否需要联网。"""
        lowered = (text or "").lower()
        return any(keyword in lowered for keyword in WEB_KEYWORDS)

    def _build(self, tier: str, reason: str) -> RouteDecision:
        """构造决策，附带降级链。

        降级链只包含「真正配了模型的档位」，并保证主模型在最前面。
        """
        provider_map = self.config.provider_map()
        chain: list[str] = []
        primary = provider_map.get(tier, "")
        if primary:
            chain.append(primary)
        for fallback_tier in (TIER_STRONG, TIER_CHEAP, TIER_REASONING, TIER_LONG_CONTEXT):
            if fallback_tier == tier:
                continue
            candidate = provider_map.get(fallback_tier, "")
            if candidate and candidate not in chain:
                chain.append(candidate)
        return RouteDecision(tier=tier, reason=reason, provider_ids=chain)

    def resolve_provider_id(
        self,
        decision: RouteDecision,
        *,
        session_provider_id: str = "",
        available_ids: set[str] | None = None,
    ) -> str:
        """从决策结果中挑出真实可用的 provider_id。

        参数:
            decision: 路由决策。
            session_provider_id: 会话当前模型 ID，作为最终兜底。
            available_ids: 当前已加载的 provider id 集合；为 ``None`` 时不做校验。

        异常:
            RouteNotFoundError: 没有任何可用候选，且会话默认模型也缺失。
        """

        def _usable(pid: str) -> bool:
            return bool(pid) and (available_ids is None or pid in available_ids)

        candidates: list[str] = [pid for pid in decision.chain() if _usable(pid)]
        if not candidates and _usable(session_provider_id):
            # 只在「一个档位模型都没配」时才回落到会话默认模型，
            # 否则用户配置的路由档位会被悄悄忽略。
            candidates.append(session_provider_id)

        if not candidates:
            if available_ids:
                raise RouteNotFoundError(
                    "没有可用的模型提供商",
                    hint="请在 WebUI 的「服务提供商」中至少启用一个对话模型，并在插件配置里指定各档位模型。",
                )
            raise ProviderUnavailableError("没有可用的模型提供商")

        # 健康度优先：连续失败的 provider 排到后面（稳定排序，不改变同分顺序）
        order = {pid: index for index, pid in enumerate(candidates)}
        candidates.sort(key=lambda pid: (self.failure_count(pid), order[pid]))
        return candidates[0]

    def ordered_candidates(
        self,
        decision: RouteDecision,
        *,
        primary: str = "",
        session_provider_id: str = "",
        available_ids: set[str] | None = None,
        max_retries: int | None = None,
    ) -> list[str]:
        """返回「健康 provider 优先」的候选顺序。

        这是真正被 main.py 使用的入口：先取完整降级链，再把连续失败的
        provider 排到后面，这样即使链里第一个模型一直挂，也不会每轮都先
        撞一次墙。
        """
        chain = self.fallback_chain(
            decision,
            primary=primary,
            session_provider_id=session_provider_id,
            available_ids=available_ids,
            max_retries=max_retries,
        )
        if len(chain) <= 1:
            return chain
        healthy = [pid for pid in chain if not self.is_unhealthy(pid)]
        unhealthy = [pid for pid in chain if self.is_unhealthy(pid)]
        return healthy + unhealthy

    def fallback_chain(
        self,
        decision: RouteDecision,
        *,
        primary: str,
        session_provider_id: str = "",
        available_ids: set[str] | None = None,
        max_retries: int | None = None,
    ) -> list[str]:
        """返回完整降级链（primary 在最前）。"""
        chain: list[str] = []
        if primary:
            chain.append(primary)
        for pid in decision.chain():
            if pid != primary:
                chain.append(pid)
        if session_provider_id and not chain:
            # 档位模型都没配时才回落到会话默认模型，避免抢走用户配置的档位
            chain.append(session_provider_id)

        if not self.config.router.get("fallback_enabled", True):
            max_candidates = 1
        else:
            if max_retries is None:
                raw = self.config.router.get("max_retries")
                max_retries = 2 if raw is None else int(raw)
            # 候选数 = 1 个主模型 + max_retries 次重试
            max_candidates = max(1, int(max_retries) + 1)

        deduped: list[str] = []
        for pid in chain:
            if len(deduped) >= max_candidates:
                break
            if not pid or pid in deduped:
                continue
            if available_ids is not None and pid not in available_ids:
                continue
            deduped.append(pid)
        return deduped


def has_image_like(text: str) -> bool:
    """粗略判断文本里是否在描述图片需求。"""
    return bool(re.search(r"(看|识别|这张|这幅).{0,4}(图|图片)|describe.*image", text))
