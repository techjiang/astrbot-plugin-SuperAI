"""SuperAI 配置读取与规范化。

AstrBot 会把 ``_conf_schema.json`` 解析结果以 ``AstrBotConfig``（dict 子类）
传入插件构造函数。本模块把这些原始配置包装成强类型视图，并提供安全的
默认值，避免插件代码里到处写 ``config.get("x", {})``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import ConfigError

# ---------------------------------------------------------------------------
# 默认值：与 _conf_schema.json 保持一致，便于单测与缺省兜底
# ---------------------------------------------------------------------------

DEFAULT_ENABLED_TASKS = ["chat", "agent", "long_context"]

ROUTER_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "strategy": "rule",
    "fallback_enabled": True,
    "max_retries": 2,
    "timeout": 120,
    "cheap_provider_id": "",
    "strong_provider_id": "",
    "reasoning_provider_id": "",
    "vision_provider_id": "",
    "long_context_provider_id": "",
    "long_context_tokens": 64000,
    "keyword_map": {},
}

MEMORY_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "auto_summary": True,
    "summary_trigger_rounds": 12,
    "summary_provider_id": "",
    "summary_max_chars": 600,
    "long_term_enabled": True,
    "long_term_top_k": 5,
    "extract_facts": True,
    "inject_into_prompt": True,
}

WEB_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "engine": "duckduckgo",
    "searxng_base": "",
    "api_key": "",
    "max_results": 5,
    "timeout": 15,
    "fallback_name": "search_web",
}

KB_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "kb_names": [],
    "top_k": 5,
    "auto_inject": False,
    "score_threshold": 0.0,
}

AGENT_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "max_steps": 12,
    "tool_call_timeout": 60,
    "stream": True,
    "system_prompt": "",
}

COMMAND_DEFAULTS: dict[str, Any] = {
    "prefix": "",
    "allow_groups": [],
    "deny_groups": [],
    "cooldown_seconds": 3,
    "max_input_chars": 4000,
}

METRICS_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "daily_token_budget": 0,
    "daily_request_budget": 0,
    "retention_days": 30,
}

WORKFLOW_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "max_steps": 8,
    "workflows": {},
}


def _as_dict(value: Any, name: str) -> dict[str, Any]:
    """确保配置项是 dict，否则回落到空 dict。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"配置项 `{name}` 应为 object 类型，实际为 {type(value).__name__}")
    return dict(value)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """浅合并：override 中的 None / 空字符串不覆盖默认值。"""
    merged = dict(base)
    for key, value in override.items():
        if value is None:
            continue
        merged[key] = value
    return merged


@dataclass(slots=True)
class SuperAIConfig:
    """SuperAI 的强类型配置视图。"""

    enabled: bool = True
    """插件总开关。关闭后所有指令都会直接放行给 AstrBot 默认流程。"""

    enabled_tasks: list[str] = field(default_factory=lambda: list(DEFAULT_ENABLED_TASKS))
    """启用 SuperAI 的模型任务类型，例如 chat / agent / long_context。"""

    debug: bool = False
    """输出详细调试日志。"""

    router: dict[str, Any] = field(default_factory=lambda: dict(ROUTER_DEFAULTS))
    memory: dict[str, Any] = field(default_factory=lambda: dict(MEMORY_DEFAULTS))
    web: dict[str, Any] = field(default_factory=lambda: dict(WEB_DEFAULTS))
    knowledge_base: dict[str, Any] = field(default_factory=lambda: dict(KB_DEFAULTS))
    agent: dict[str, Any] = field(default_factory=lambda: dict(AGENT_DEFAULTS))
    commands: dict[str, Any] = field(default_factory=lambda: dict(COMMAND_DEFAULTS))
    metrics: dict[str, Any] = field(default_factory=lambda: dict(METRICS_DEFAULTS))
    workflow: dict[str, Any] = field(default_factory=lambda: dict(WORKFLOW_DEFAULTS))
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # -- 便捷访问 ---------------------------------------------------------
    def is_task_enabled(self, task: str) -> bool:
        """判断某个任务类型是否启用了 SuperAI 增强。"""
        if not self.enabled:
            return False
        if not self.enabled_tasks:
            return True
        return task in self.enabled_tasks

    @property
    def router_enabled(self) -> bool:
        return self.enabled and bool(self.router.get("enabled", True))

    @property
    def memory_enabled(self) -> bool:
        return self.enabled and bool(self.memory.get("enabled", True))

    @property
    def web_enabled(self) -> bool:
        return self.enabled and bool(self.web.get("enabled", False))

    @property
    def kb_enabled(self) -> bool:
        return self.enabled and bool(self.knowledge_base.get("enabled", False))

    @property
    def agent_enabled(self) -> bool:
        return self.enabled and bool(self.agent.get("enabled", True))

    @property
    def metrics_enabled(self) -> bool:
        return self.enabled and bool(self.metrics.get("enabled", True))

    @property
    def workflow_enabled(self) -> bool:
        return self.enabled and bool(self.workflow.get("enabled", True))

    # -- 派生配置 ---------------------------------------------------------
    def provider_map(self) -> dict[str, str]:
        """返回「任务 -> provider_id」映射，用于 SuperRouter 选路。"""
        router = self.router
        return {
            "cheap": str(router.get("cheap_provider_id") or ""),
            "strong": str(router.get("strong_provider_id") or ""),
            "reasoning": str(router.get("reasoning_provider_id") or ""),
            "vision": str(router.get("vision_provider_id") or ""),
            "long_context": str(router.get("long_context_provider_id") or ""),
        }

    def keyword_routes(self) -> dict[str, str]:
        """关键词 -> 路由档位。用于 rule 策略。"""
        raw = _as_dict(self.router.get("keyword_map"), "router.keyword_map")
        result: dict[str, str] = {}
        for keyword, target in raw.items():
            if not keyword or not target:
                continue
            result[str(keyword).strip().lower()] = str(target).strip()
        return result

    def group_allowed(self, group_id: str, *, is_private: bool) -> bool:
        """判断某个群是否允许使用 SuperAI 增强。"""
        if is_private:
            # 私聊没有「群」的概念，allow_groups / deny_groups 均只针对群聊
            return True
        gid = str(group_id)
        allow = {str(g) for g in (self.commands.get("allow_groups") or [])}
        deny = {str(g) for g in (self.commands.get("deny_groups") or [])}
        if gid in deny:
            return False
        # 白名单为空 = 不限制；非空 = 必须命中
        return not (allow and gid not in allow)


def build_config(raw: Any) -> SuperAIConfig:
    """把 AstrBotConfig / dict 转换成 :class:`SuperAIConfig`。

    参数:
        raw: AstrBot 传入的插件配置对象，可能是 ``AstrBotConfig``、``dict`` 或 ``None``。
    """
    if raw is None:
        source: dict[str, Any] = {}
    elif isinstance(raw, dict):
        source = dict(raw)
    elif hasattr(raw, "__getitem__") and hasattr(raw, "get"):
        # AstrBotConfig 继承自 dict，理论上已被上面的分支覆盖；
        # 这里兜底处理自定义映射类型。
        source = {key: raw.get(key) for key in raw}  # type: ignore[union-attr]
    else:
        raise ConfigError(f"无法识别的配置对象类型：{type(raw).__name__}")

    enabled_tasks = source.get("enabled_tasks") or list(DEFAULT_ENABLED_TASKS)
    if isinstance(enabled_tasks, str):
        enabled_tasks = [item.strip() for item in enabled_tasks.split(",") if item.strip()]
    if not isinstance(enabled_tasks, list):
        enabled_tasks = list(DEFAULT_ENABLED_TASKS)

    return SuperAIConfig(
        enabled=bool(source.get("enabled", True)),
        enabled_tasks=[str(item) for item in enabled_tasks],
        debug=bool(source.get("debug", False)),
        router=_merge(ROUTER_DEFAULTS, _as_dict(source.get("router"), "router")),
        memory=_merge(MEMORY_DEFAULTS, _as_dict(source.get("memory"), "memory")),
        web=_merge(WEB_DEFAULTS, _as_dict(source.get("web"), "web")),
        knowledge_base=_merge(
            KB_DEFAULTS, _as_dict(source.get("knowledge_base"), "knowledge_base")
        ),
        agent=_merge(AGENT_DEFAULTS, _as_dict(source.get("agent"), "agent")),
        commands=_merge(COMMAND_DEFAULTS, _as_dict(source.get("commands"), "commands")),
        metrics=_merge(METRICS_DEFAULTS, _as_dict(source.get("metrics"), "metrics")),
        workflow=_merge(WORKFLOW_DEFAULTS, _as_dict(source.get("workflow"), "workflow")),
        raw=source,
    )
