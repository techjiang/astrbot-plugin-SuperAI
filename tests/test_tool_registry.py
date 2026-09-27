"""工具装配测试：验证按配置开关工具。"""

from superai.core.config import build_config
from superai.tools.registry import build_toolset


def names(config):
    return {tool.name for tool in build_toolset(config).tools}


def test_memory_tools_enabled_by_default():
    config = build_config({})
    assert {"superai_remember", "superai_recall"} <= names(config)


def test_memory_tools_disabled():
    config = build_config({"memory": {"enabled": False}})
    assert names(config) == set()


def test_web_tools_follow_flag():
    config = build_config({"web": {"enabled": True}})
    assert "superai_web_search" in names(config)
    assert "superai_fetch_url" in names(config)


def test_kb_tool_requires_names():
    without = build_config({"knowledge_base": {"enabled": True, "kb_names": []}})
    assert "superai_knowledge_search" not in names(without)
    with_names = build_config({"knowledge_base": {"enabled": True, "kb_names": ["docs"]}})
    assert "superai_knowledge_search" in names(with_names)


def test_workflow_tool_requires_definitions():
    empty = build_config({"workflow": {"workflows": {}}})
    assert "superai_run_workflow" not in names(empty)
    filled = build_config({"workflow": {"workflows": {"早报": {"steps": [{"prompt": "x"}]}}}})
    assert "superai_run_workflow" in names(filled)


def test_workflow_tool_can_be_excluded():
    config = build_config(
        {
            "memory": {"enabled": False},
            "workflow": {"workflows": {"早报": {"steps": [{"prompt": "x"}]}}},
        }
    )
    assert "superai_run_workflow" in names(config)
    excluded = {tool.name for tool in build_toolset(config, include_workflow=False).tools}
    assert "superai_run_workflow" not in excluded
