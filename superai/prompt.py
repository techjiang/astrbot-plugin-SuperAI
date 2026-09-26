"""提示词构建：把摘要、记忆、知识库等增强内容注入到 LLM 请求中。

遵循 AstrBot 官方建议：

- **稳定内容**（角色设定、长期规则）追加到 ``system_prompt``，不随轮次变化，
  以保护模型侧的提示词缓存。
- **动态内容**（当前时间、本轮检索到的记忆、摘要）通过
  ``extra_user_content_parts`` 追加，避免破坏缓存、显著增加成本。
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

DYNAMIC_TEMPLATE = """<superai_context>
{blocks}
</superai_context>"""

SUMMARY_BLOCK = "【历史对话摘要】\n{summary}"

MEMORY_BLOCK = "【相关长期记忆】\n{memories}"

KB_BLOCK = "【知识库参考】\n{kb_context}"

ROUTE_BLOCK = "【当前请求特征】\n路由档位：{tier}（{reason}）"

#: 注入到 system_prompt 的稳定指令（不随轮次变化）
STABLE_INSTRUCTION = (
    "你由 SuperAI 增强运行。当对话中出现 <superai_context> 区块时，"
    "它是本轮系统为你准备的辅助上下文（历史摘要 / 相关记忆 / 知识库片段），"
    "请自然地运用其中的信息，不要向用户复述标签本身。"
    "当记忆或知识库内容与用户当前说法冲突时，以用户当前说法为准，并提示可能已过时。"
)


def build_dynamic_block(
    *,
    summary: str = "",
    memories: list[str] | None = None,
    kb_context: str = "",
    route_tier: str = "",
    route_reason: str = "",
    include_time: bool = True,
) -> str:
    """拼装本轮的动态上下文块；没有任何内容时返回空字符串。"""
    blocks: list[str] = []
    if include_time:
        blocks.append(f"【当前时间】{time.strftime('%Y-%m-%d %H:%M:%S')}")
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
    if temporary and hasattr(part, "mark_as_temp"):
        # 标记失败（老版本不支持）不影响主流程
        with contextlib.suppress(Exception):
            part = part.mark_as_temp()
    parts = getattr(req, "extra_user_content_parts", None)
    if parts is None:
        return False
    parts.append(part)
    return True
