"""测试引导：注入 AstrBot 替身并让 ``superai`` 可导入。

若本机存在真实 AstrBot 源码（默认 ``/tmp/astrbot-ref``），则优先使用真实框架，
并把 ``tests/stubs`` 从 ``sys.path`` 中移除，避免替身遮蔽真实实现。

切换必须发生在任何测试模块被导入之前，否则同一进程里可能出现
「真实 AstrBot 与 stub 同时被加载」的状态（例如 sqlmodel 重复注册表），
表现为与插件无关的收集期报错。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
STUBS = ROOT / "tests" / "stubs"

#: 可通过环境变量指向 AstrBot 源码目录
ASTRBOT_REF = Path(os.environ.get("ASTRBOT_REF", "/tmp/astrbot-ref"))

_REAL_AVAILABLE = ASTRBOT_REF.joinpath("astrbot", "api", "event", "__init__.py").exists()


def _drop_modules(prefixes: tuple[str, ...]) -> None:
    for name in [
        module for module in sys.modules if module == "astrbot" or module.startswith("astrbot.")
    ]:
        if name.startswith(prefixes) or name == "astrbot":
            del sys.modules[name]


def pytest_configure(config) -> None:  # noqa: ANN001 - pytest 钩子
    """在收集测试之前确定 import 路径。"""
    if _REAL_AVAILABLE:
        stubs = str(STUBS)
        while stubs in sys.path:
            sys.path.remove(stubs)
        _drop_modules(("astrbot",))
        sys.path.insert(0, str(ASTRBOT_REF))
    else:
        # 优先使用本地 stub，避免依赖真实的 AstrBot 安装
        for path in (str(STUBS), str(ROOT)):
            if path not in sys.path:
                sys.path.insert(0, path)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def load_superai_entry():
    """按 AstrBot 的方式加载插件入口模块。

    AstrBot 用 ``__import__("data.plugins.astrbot_plugin_superai.main", ...)``
    加载插件，因此插件类与 ``@filter`` 钩子都定义在仓库根目录的 ``main.py``
    （详见该文件的说明）。测试也走同一入口，避免「测试绿、线上死」。
    """
    import importlib.util

    entry = ROOT / "main.py"
    name = "superai_entry_main"
    if name in sys.modules:
        return sys.modules[name]

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    spec = importlib.util.spec_from_file_location(name, entry)
    assert spec is not None and spec.loader is not None, f"无法加载 {entry}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
