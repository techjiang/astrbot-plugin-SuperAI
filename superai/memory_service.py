"""记忆服务：自动提取事实 + 滚动摘要。

职责边界：

- ``MemoryService.observe``：在 ``on_llm_request`` 钩子里观察本轮消息，
  决定是否需要更新摘要、是否需要注入长期记忆。
- ``MemoryService.summarize``：调用「便宜档」模型生成摘要。
- ``MemoryService.extract_facts``：调用模型抽取值得长期记住的事实。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from astrbot.api import logger

from .core.utils import extract_json, normalize_space, truncate

if TYPE_CHECKING:  # pragma: no cover
    from .main import SuperAIPlugin

SUMMARY_PROMPT = """你是对话摘要器。请把下面的对话压缩成一段不超过 {limit} 字的中文摘要。

要求：
1. 保留关键事实、结论、用户偏好、待办和未解决的问题；
2. 丢弃寒暄、客套与重复内容；
3. 只输出摘要正文，不要任何前后缀、标题或解释。

对话内容：
{dialogue}
"""

EXTRACT_PROMPT = """你是长期记忆抽取器。请从下面的对话中抽取「值得长期记住」的信息。

只抽取满足以下任一条件的内容：
- 用户明确表达的稳定偏好（如「我喜欢简洁的回答」）；
- 关于用户自身、团队、项目的客观事实（如「我在做 AstrBot 插件」）；
- 已经确认的约定或结论。

不要抽取：寒暄、临时性问题、可从常识推断的内容、以及纯助手输出。

输出严格的 JSON 数组，每个元素形如：
{{"content": "一句话陈述", "kind": "fact|preference|event"}}

没有值得记住的内容时输出 []。不要输出任何额外文字。

对话内容：
{dialogue}
"""


class MemoryService:
    """记忆与摘要服务。"""

    def __init__(self, plugin: SuperAIPlugin) -> None:
        self.plugin = plugin

    # -- 摘要 -------------------------------------------------------------
    async def summarize(self, session: str, dialogue: str) -> str:
        """把对话压缩成摘要；失败时返回空字符串。"""
        if not dialogue.strip():
            return ""
        limit = int(self.plugin.config.memory.get("summary_max_chars") or 600)
        prompt = SUMMARY_PROMPT.format(limit=limit, dialogue=dialogue[:12000])
        provider_id = str(
            self.plugin.config.memory.get("summary_provider_id")
            or self.plugin.config.provider_map().get("cheap")
            or ""
        )
        try:
            text = await self.plugin.llm_simple(prompt, provider_id=provider_id, session=session)
        except Exception as exc:  # noqa: BLE001 - 摘要失败不应影响对话
            logger.warning(f"[SuperAI] 生成摘要失败：{exc}")
            return ""
        return truncate(normalize_space(text), limit, suffix="")

    async def maybe_summarize(self, session: str, history: list[str]) -> str:
        """按阈值决定是否生成摘要。

        参数:
            session: 会话标识。
            history: 人类可读的历史消息片段（旧 -> 新）。

        返回:
            最新摘要文本（未触发或不成功时返回已有摘要）。
        """
        cfg = self.plugin.config.memory
        if not cfg.get("auto_summary", True):
            return self.plugin.summaries.get(session).summary

        trigger = max(4, int(cfg.get("summary_trigger_rounds") or 12))
        current = self.plugin.summaries.get(session)
        pending = len(history) - current.covered_rounds
        if pending < trigger:
            return current.summary

        dialogue = "\n".join(history[-max(trigger * 2, 10) :])
        summary = await self.summarize(session, dialogue)
        if not summary:
            return current.summary

        self.plugin.summaries.update(
            session,
            summary,
            covered_rounds=len(history),
            covered_until=self.plugin.last_active_ts(),
            total_rounds=max(current.total_rounds, len(history)),
        )
        logger.info(f"[SuperAI] 会话 {session} 摘要已更新（覆盖 {len(history)} 条历史）")
        return summary

    # -- 事实抽取 ---------------------------------------------------------
    async def extract_facts(self, session: str, dialogue: str) -> list[str]:
        """从对话中抽取值得长期记住的内容并写入记忆库。"""
        cfg = self.plugin.config.memory
        if not (cfg.get("long_term_enabled", True) and cfg.get("extract_facts", True)):
            return []
        if len(normalize_space(dialogue)) < 20:
            return []

        provider_id = str(
            cfg.get("summary_provider_id") or self.plugin.config.provider_map().get("cheap") or ""
        )
        try:
            raw = await self.plugin.llm_simple(
                EXTRACT_PROMPT.format(dialogue=dialogue[:8000]),
                provider_id=provider_id,
                session=session,
            )
            data = extract_json(raw)
        except Exception as exc:  # noqa: BLE001 - 抽取失败属正常情况
            logger.debug(f"[SuperAI] 记忆抽取失败：{exc}")
            return []

        if not isinstance(data, list):
            return []
        written: list[str] = []
        for item in data[:5]:
            if isinstance(item, str):
                content, kind = item, "fact"
            elif isinstance(item, dict):
                content = str(item.get("content") or "").strip()
                kind = str(item.get("kind") or "fact")
            else:
                continue
            if not content:
                continue
            if kind not in {"fact", "preference", "event"}:
                kind = "fact"
            entry = self.plugin.memory.add(session, content, kind=kind, source="auto")
            if entry:
                written.append(entry.content)
        return written

    @staticmethod
    def dialogue_fingerprint(dialogue: str) -> str:
        """生成对话指纹，用于避免重复抽取。"""
        return json.dumps(normalize_space(dialogue)[-500:], ensure_ascii=False)
