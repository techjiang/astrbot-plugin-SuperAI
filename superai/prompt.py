"""提示词构建：把摘要、记忆、知识库等增强内容注入到 LLM 请求中。

遵循 AstrBot 官方建议：

- **稳定内容**（角色设定、长期规则）追加到 ``system_prompt``，不随轮次变化，
  以保护模型侧的提示词缓存。
- **动态内容**（本轮检索到的记忆、摘要、知识库片段）通过
  ``extra_user_content_parts`` 追加，并标记为「仅本轮有效」，避免破坏缓存
  与污染会话历史。

注入策略遵循「**没内容就不注入**」：``extra_user_content_parts`` 里只要出现
一个 part，provider 端就会被从「纯字符串 user 消息」升级为「多模态 content
数组」，自动前缀缓存随之失效。因此没有摘要 / 记忆可注入时，SuperAI 完全不
碰这一字段。
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

DYNAMIC_TEMPLATE = """<superai_context>
{blocks}
</superai_context>"""

SUMMARY_BLOCK = "<history_summary>\n{summary}\n</history_summary>"

MEMORY_BLOCK = "<long_term_memory>\n{memories}\n</long_term_memory>"

KB_BLOCK = "<knowledge_base>\n{kb_context}\n</knowledge_base>"

ROUTE_BLOCK = '<request_route tier="{tier}">\n{reason}\n</request_route>'

WEB_HINT_BLOCK = "<web_search_hint>\n{hint}\n</web_search_hint>"

#: 注入到 system_prompt 的稳定指令（不随轮次变化）
STABLE_INSTRUCTION = (
    "你由 SuperAI 增强运行。用户消息末尾可能附带 <superai_context> 区块，"
    "其中 <history_summary>、<long_term_memory>、<knowledge_base> 是本轮系统"
    "为你准备的辅助上下文（历史摘要 / 相关长期记忆 / 知识库片段），请自然地运用，"
    "不要向用户复述标签或区块本身。"
    "记忆与知识库可能已过时：与用户当前说法冲突时以用户当前说法为准，"
    "并在必要时说明该记忆可能已过期。"
    "<request_route> 仅用于内部说明本轮使用的模型档位，不要在回复里提及。"
)


def build_dynamic_block(
    *,
    summary: str = "",
    memories: list[str] | None = None,
    kb_context: str = "",
    route_tier: str = "",
    route_reason: str = "",
    web_hint: str = "",
    include_time: bool = False,
    timestamp: str = "",
) -> str:
    """拼装本轮的动态上下文块；没有任何内容时返回空字符串。

    参数:
        include_time: 是否注入当前时间。注入会让该 user 段落每轮都不同，
            所以只有真正需要时（例如用户问「现在几点」）才开启。
        timestamp: 预先格式化好的时间字符串；留空则使用本机时间。
    """
    blocks: list[str] = []
    if include_time:
        blocks.append(
            f"<current_time>{timestamp or time.strftime('%Y-%m-%d %H:%M:%S')}</current_time>"
        )
    if summary:
        blocks.append(SUMMARY_BLOCK.format(summary=summary.strip()))
    if memories:
        joined = "\n".join(f"- {item}" for item in memories if item)
        if joined:
            blocks.append(MEMORY_BLOCK.format(memories=joined))
    if kb_context:
        blocks.append(KB_BLOCK.format(kb_context=kb_context.strip()[:3000]))
    if route_tier:
        blocks.append(ROUTE_BLOCK.format(tier=route_tier, reason=route_reason or "-"))
    if web_hint:
        blocks.append(WEB_HINT_BLOCK.format(hint=web_hint.strip()))
    if not blocks:
        return ""
    return DYNAMIC_TEMPLATE.format(blocks="\n\n".join(blocks))


def apply_stable_prompt(req: Any, *, extra: str = "") -> None:
    """把稳定指令追加到 ``system_prompt``（幂等）。"""
    if req is None:
        return
    current = getattr(req, "system_prompt", "") or ""
    addition = "\n\n".join(part for part in (STABLE_INSTRUCTION, extra) if part)
    if not addition or addition in current:
        return
    req.system_prompt = f"{current}\n\n{addition}" if current else addition


def has_dynamic_content(
    *,
    summary: str = "",
    memories: list[str] | None = None,
    kb_context: str = "",
    route_tier: str = "",
    web_hint: str = "",
    include_time: bool = False,
) -> bool:
    """判断是否真的需要注入动态块。

    注入会打断「user 段落文本一致」这一前提，进而让 provider 端的自动
    前缀缓存（OpenAI / DeepSeek / Anthropic prompt caching）失效，
    所以要尽量少注入、只在有实质内容时注入。
    """
    return bool(
        include_time
        or (summary or "").strip()
        or [item for item in (memories or []) if str(item).strip()]
        or (kb_context or "").strip()
        or (route_tier or "").strip()
        or (web_hint or "").strip()
    )


def ensure_temp_part(part: Any) -> Any:
    """尽量把 content part 标记为「仅本轮有效」，失败时保持原样。"""
    marker = getattr(part, "mark_as_temp", None)
    if callable(marker):
        with contextlib.suppress(Exception):
            marked = marker()
            if marked is not None:
                return marked
    return part


def append_dynamic(req: Any, text: str, *, temporary: bool = True) -> bool:
    """把动态块追加到 ``extra_user_content_parts``。

    参数:
        req: ``ProviderRequest`` 对象。
        text: 动态上下文文本。
        temporary: 是否标记为「仅本轮有效」（AstrBot >= 4.24.0 支持）。
    """
    if req is None or not text:
        return False
    try:
        from astrbot.core.agent.message import TextPart
    except ImportError:  # pragma: no cover - 老版本兼容
        return False

    part = TextPart(text=text)
    if temporary:
        part = ensure_temp_part(part)
    parts = getattr(req, "extra_user_content_parts", None)
    if parts is None:
        # 老版本没有该字段时不静默失败：调用方需要知道注入没生效
        return False
    parts.append(part)
    return True
