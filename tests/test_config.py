"""配置解析测试。"""

import pytest

from superai.core.config import build_config
from superai.core.errors import ConfigError


def test_defaults_applied_when_empty():
    config = build_config({})
    assert config.enabled is True
    assert config.router_enabled is True
    assert config.web_enabled is False
    assert config.provider_map()["cheap"] == ""


def test_none_config_is_tolerated():
    config = build_config(None)
    assert config.enabled_tasks


def test_router_section_merges_defaults():
    config = build_config({"router": {"strategy": "quality_first", "cheap_provider_id": "p1"}})
    assert config.router["strategy"] == "quality_first"
    assert config.provider_map()["cheap"] == "p1"
    # 未覆盖的默认值保留
    assert config.router["max_retries"] == 2


def test_enabled_tasks_string_is_split():
    config = build_config({"enabled_tasks": "chat, agent"})
    assert config.enabled_tasks == ["chat", "agent"]


def test_invalid_section_type_raises():
    with pytest.raises(ConfigError):
        build_config({"router": "not-a-dict"})


def test_is_task_enabled_respects_master_switch():
    config = build_config({"enabled": False, "enabled_tasks": ["chat"]})
    assert config.is_task_enabled("chat") is False


def test_group_allow_and_deny():
    config = build_config({"commands": {"allow_groups": ["100", "200"], "deny_groups": ["200"]}})
    assert config.group_allowed("100", is_private=False) is True
    assert config.group_allowed("200", is_private=False) is False
    assert config.group_allowed("300", is_private=False) is False
    # 私聊不受白名单限制
    assert config.group_allowed("999", is_private=True) is True


def test_keyword_routes_normalized():
    config = build_config({"router": {"keyword_map": {" 报表 ": "strong", "": "cheap"}}})
    assert config.keyword_routes() == {"报表": "strong"}
