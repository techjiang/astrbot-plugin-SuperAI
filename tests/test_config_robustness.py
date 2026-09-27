"""配置健壮性回归测试 —— 拦截「用户填错一个数字就整条链路静默失效」。

为什么必须单独设防：AstrBot 的 ``_conf_schema.json`` **只用来生成默认值**，
它不对用户在 WebUI 里保存的值做任何类型校验或强制转换
（见 ``AstrBotConfig.check_config_integrity``，只补缺失项、对齐顺序）。
因此 ``"long_context_tokens": "64k"`` 这种值会**原样**进入插件配置。

而插件里有一批 ``int(cfg.get(...))`` / ``float(cfg.get(...))`` 直接转换。
一旦命中非数字：

- 在 ``__init__`` 路径上 → 插件加载直接失败（``PluginManager`` 记为 failed plugin）；
- 在 ``on_llm_request`` 路径上 → 每一条消息都在钩子里抛 ``ValueError``，
  被框架的 ``call_event_hook`` 捕获并只打一段 traceback，
  表现为**路由 / 记忆 / 预算全部静默失效**，而插件看起来「加载成功」。

这两类问题此前一条测试都没有。本文件把「配置读取一律容错」固化为契约。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: 会被用户直接在 WebUI 文本框里编辑、且插件会转成数字的字段
#: （值 -> 是否浮点）
NUMERIC_CONFIG_FIELDS = {
    "workflow.max_steps",
    "metrics.retention_days",
    "metrics.daily_token_budget",
    "metrics.daily_request_budget",
    "memory.decay_half_life_days",
    "memory.summary_trigger_rounds",
    "memory.summary_max_chars",
    "memory.long_term_top_k",
    "router.long_context_tokens",
    "router.timeout",
    "router.max_retries",
    "agent.max_steps",
    "agent.tool_call_timeout",
    "web.timeout",
    "web.max_results",
    "knowledge_base.top_k",
    "knowledge_base.score_threshold",
    "commands.cooldown_seconds",
    "commands.max_input_chars",
}


def _schema_flat() -> dict[str, dict]:
    import json

    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    flat: dict[str, dict] = {}

    def walk(items: dict, prefix: str = "") -> None:
        for key, value in items.items():
            path = f"{prefix}{key}"
            flat[path] = value
            if value.get("type") == "object":
                walk(value.get("items", {}), f"{path}.")

    walk(schema)
    return flat


def test_numeric_fields_are_declared_numeric():
    """上面那份清单里的字段，必须真的在 schema 中声明为数字类型。

    防止清单本身腐烂（字段被删掉/改成 string 后，这份测试还在自说自话）。
    """
    schema = _schema_flat()
    missing = [field for field in NUMERIC_CONFIG_FIELDS if field not in schema]
    assert not missing, f"清单中的字段不在 schema 里：{missing}"

    wrong = [
        field
        for field in NUMERIC_CONFIG_FIELDS
        if schema[field].get("type") not in {"int", "float"}
    ]
    assert not wrong, f"这些字段的 schema 类型不是数字：{wrong}"


def test_no_unguarded_numeric_conversion_of_config():
    """插件源码里不得对配置值直接 ``int()`` / ``float()``。

    ``int(self.config.X.get(...))`` 这种写法在用户填了非数字时会抛异常；
    必须改用 ``as_int`` / ``as_float``（``superai/core/utils.py``）。

    允许的例外：纯内部量（时间戳、计数）不走配置字典。
    """
    pattern = re.compile(
        r"\b(?:int|float)\s*\(\s*(?:self\.(?:plugin\.)?config|self\.config|cfg|agent_cfg)"
        r"[.\w]*\.get\(",
    )
    offenders: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in {".git", "__pycache__", "tests", "dist"} for part in path.parts):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "以下位置对配置值做了不受保护的 int()/float()，"
        "用户填非数字时会抛异常：\n" + "\n".join(offenders)
    )


def test_as_int_as_float_tolerate_garbage():
    """容错解析函数本身要能吃下各种垃圾输入。"""
    from superai.core.utils import as_float, as_int

    for bad in ("64k", "很多", "", "  ", None, "abc", [1], {"a": 1}, object()):
        assert as_int(bad, 7) == 7, f"as_int({bad!r}) 应回落到默认值"
        assert as_float(bad, 1.5) == 1.5, f"as_float({bad!r}) 应回落到默认值"

    # 数字字符串/浮点字符串仍要能正确解析
    assert as_int("42", 0) == 42
    assert as_int(3.9, 0) == 3
    assert as_float("2.5", 0.0) == 2.5
    assert as_int("999", 0, maximum=10) == 10
    assert as_int("-5", 0, minimum=1) == 1


@pytest.mark.parametrize(
    ("field", "garbage"),
    [
        ("router.long_context_tokens", "64k"),
        ("memory.summary_trigger_rounds", "很多"),
        ("memory.decay_half_life_days", "三十"),
        ("workflow.max_steps", "八步"),
        ("metrics.retention_days", "三十天"),
        ("router.timeout", "两分钟"),
        ("agent.tool_call_timeout", "一分钟"),
    ],
)
def test_config_with_garbage_numbers_does_not_break_resolution(field, garbage):
    """把字段填成非数字时，配置解析必须回落到默认值而不是抛异常。

    这里不启动框架，直接走 ``build_config`` + 各服务的解析入口，
    覆盖「插件初始化」与「每条消息都会走」的路径。
    """
    from superai.core.config import build_config

    section, key = field.split(".", 1)
    raw = {section: {key: garbage}}
    config = build_config(raw)

    # 读取本身不得抛异常，且值与 schema 声明的类型一致
    value = config.__getattribute__(section).get(key)
    assert value == garbage  # 配置对象如实保存用户输入（框架不做校验）

    # 各消费点必须能安全地把它转成数字
    from superai.core.utils import as_float, as_int

    if field in {"memory.decay_half_life_days"}:
        resolved = as_float(value, 30.0)
        assert resolved == 30.0
    else:
        resolved = as_int(value, 1)
        assert resolved == 1


def test_schema_defaults_are_numeric_for_numeric_fields():
    """数字字段的默认值必须真的是数字（避免默认值本身就把代码弄崩）。"""
    schema = _schema_flat()
    for field in NUMERIC_CONFIG_FIELDS:
        default = schema[field].get("default")
        expected = float if schema[field].get("type") == "float" else int
        assert isinstance(default, expected), (
            f"{field} 的默认值 {default!r} 不是 {expected.__name__}"
        )


def test_tool_numeric_kwargs_use_tolerant_parsing():
    """工具参数来自模型，同样可能是字符串/浮点，必须容错。"""
    from superai.tools import web_tools

    source = inspect.getsource(web_tools)
    # 工具里解析 max_results / max_chars / timeout 一律走 as_int / as_float
    assert "as_int(" in source and "as_float(" in source
    assert not re.search(r"(?<![\w.])int\(kwargs\.get\(", source), "工具参数不得用裸 int()"
    assert not re.search(r"(?<![\w.])float\(kwargs\.get\(", source), "工具参数不得用裸 float()"
