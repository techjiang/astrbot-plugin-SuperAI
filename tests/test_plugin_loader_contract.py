"""插件「加载器契约」测试 —— 守住一类会让插件彻底失效的陷阱。

这一组测试源于在真实 AstrBot 4.28.1 上联调时暴露的三个致命问题：

1. **入口缺失**：AstrBot 的 ``PluginManager._get_modules`` 只认插件目录下的
   ``main.py`` 或「与目录同名的 ``<dirname>.py``」，否则日志里只有一行
   ``Plugin <dir> has neither main.py nor <dir>.py; skipping it.``，
   插件被**静默跳过** —— 表面「装上了」，实际一个钩子都没注册。

2. **模块路径不匹配导致 ``self`` 未绑定**：框架用
   ``metadata.module_path``（入口模块，如
   ``data.plugins.astrbot_plugin_superai.main``）去
   ``get_handlers_by_module_name()`` 查处理器，命中后才执行
   ``handler.handler = functools.partial(raw_handler, metadata.star_cls)``。
   而处理器的 ``handler_module_path`` 记录的是**装饰器所在模块**。
   若插件类定义在子模块（``superai/main.py``）而入口只是转口，两者永不相等，
   绑定不会发生，调用时直接抛
   ``TypeError: SuperAIPlugin.on_llm_request() missing 1 required positional
   argument: 'req'``，且异常被 ``call_event_hook()`` 吞掉只记一行 error，
   表现为**路由 / 记忆注入 / 图片保护 / 预算拦截全部静默失效**。

3. **钩子签名**：``@filter.on_llm_request()`` 要求 ``(self, event, req)``，
   少一个参数同样会退化成上面那个被吞掉的 TypeError。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: AstrBot 认可的入口文件名（见 star_manager.PluginManager._get_modules）
ENTRY_CANDIDATES = ("main.py",)


def _entry_module():
    from conftest import load_superai_entry

    return load_superai_entry()


# ---------------------------------------------------------------------------
# 1. 入口文件必须存在，且能暴露插件类
# ---------------------------------------------------------------------------
def test_entry_file_exists():
    """仓库根目录必须有 ``main.py``，否则插件会被框架静默跳过。"""
    found = [name for name in ENTRY_CANDIDATES if (ROOT / name).is_file()]
    assert found, (
        "仓库根目录缺少插件入口 main.py —— AstrBot 会在插件列表里直接跳过本插件"
        "（日志：has neither main.py nor astrbot_plugin_superai.py; skipping it）"
    )


def test_entry_exposes_plugin_class():
    module = _entry_module()
    assert hasattr(module, "SuperAIPlugin"), "入口模块必须暴露 SuperAIPlugin"
    assert hasattr(module, "PLUGIN_NAME")


def test_entry_plugin_class_is_star_subclass():
    from astrbot.api.star import Star

    module = _entry_module()
    assert issubclass(module.SuperAIPlugin, Star)


# ---------------------------------------------------------------------------
# 2. 插件类必须定义在入口模块里（否则框架不会绑定 self）
# ---------------------------------------------------------------------------
def test_plugin_class_defined_in_entry_module():
    """``SuperAIPlugin.__module__`` 必须指向入口模块。

    这是本文件要守的核心不变量：框架按**入口模块路径**查找并绑定处理器，
    插件类定义在别处会导致 ``self`` 缺失（见模块文档第 2 条）。
    """
    module = _entry_module()
    assert module.SuperAIPlugin.__module__ == module.__name__, (
        f"SuperAIPlugin 定义在 {module.SuperAIPlugin.__module__}，"
        f"而入口模块是 {module.__name__}；"
        "AstrBot 只会按入口模块路径绑定 self，定义在子模块会导致钩子全部失效"
    )


@pytest.mark.parametrize("hook_name", ["on_llm_request", "on_llm_response"])
def test_hooks_defined_in_entry_module(hook_name):
    """两个 LLM 钩子都必须定义在入口模块里。"""
    module = _entry_module()
    hook = getattr(module.SuperAIPlugin, hook_name)
    assert hook.__module__ == module.__name__, (
        f"{hook_name} 定义在 {hook.__module__}，不是入口模块 {module.__name__}；"
        "框架找不到处理器就不会绑定 self，钩子会被静默吞掉"
    )


# ---------------------------------------------------------------------------
# 3. 钩子签名必须匹配 AstrBot 的调用约定
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("hook_name", "second_param"),
    [("on_llm_request", "req"), ("on_llm_response", "resp")],
)
def test_hook_signature_takes_self_event_and_payload(hook_name, second_param):
    """``(self, event, <payload>)`` —— 缺任何一个都会抛被吞掉的 TypeError。"""
    module = _entry_module()
    hook = getattr(module.SuperAIPlugin, hook_name)
    params = list(inspect.signature(hook).parameters)
    assert len(params) == 3, (
        f"{hook_name} 参数应为 (self, event, {second_param})，实际为 {params}；"
        "AstrBot 调用时只传 event 与 payload，参数数量不符会抛 TypeError"
    )
    assert params[0] == "self"
    assert params[1] == "event"


# ---------------------------------------------------------------------------
# 4. 入口模块不能有相对导入（框架以顶层模块导入入口）
# ---------------------------------------------------------------------------
def test_entry_has_no_relative_imports():
    """入口被 ``__import__("...main")`` 导入时不是包，相对导入会直接失败。"""
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    bad = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0
    ]
    assert not bad, (
        "入口 main.py 里出现相对导入；AstrBot 以顶层模块导入入口，"
        "相对导入会抛 ImportError 使插件加载失败"
    )


# ---------------------------------------------------------------------------
# 5. 反向验证：模拟框架的绑定逻辑，确认能绑定成功
# ---------------------------------------------------------------------------
def test_framework_binding_contract_holds():
    """按框架逻辑走一遍：入口模块路径应能命中处理器并完成 partial 绑定。

    这里复刻 ``star_manager`` 的关键判断：
    ``handler.handler_module_path == metadata.module_path``。
    """
    module = _entry_module()
    hook = module.SuperAIPlugin.on_llm_request
    handler_module_path = hook.__module__
    entry_module_path = module.__name__
    assert handler_module_path == entry_module_path, (
        "按 AstrBot 的绑定逻辑，handler_module_path 必须等于入口模块路径，"
        f"否则 functools.partial(handler, star_cls) 不会执行（{handler_module_path} != {entry_module_path}）"
    )


def test_packaged_config_and_metadata_still_present():
    """确保那轮重构没有顺手丢掉插件清单文件。"""
    assert (ROOT / "metadata.yaml").is_file()
    assert (ROOT / "_conf_schema.json").is_file()
    pages = ROOT / "pages" / "studio"
    assert (pages / "index.html").is_file()
    assert (pages / "app.js").is_file()
