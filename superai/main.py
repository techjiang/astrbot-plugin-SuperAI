"""向后兼容垫片：真正的插件入口在仓库根目录的 ``main.py``。

历史上插件类定义在 ``superai/main.py``，但那样会导致 AstrBot 绑定
``self`` 失败（处理器 ``handler_module_path`` 与插件入口
``metadata.module_path`` 不一致，见根目录 ``main.py`` 的说明）。

保留本模块只是为了不破坏既有导入路径（``import superai.main``、
``from superai.main import SuperAIPlugin``）。
"""

from __future__ import annotations

import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_spec = importlib.util.spec_from_file_location(
    "superai_entry_main",
    os.path.join(_ROOT, "main.py"),
)
if _spec is None or _spec.loader is None:  # pragma: no cover - 环境异常
    raise ImportError("无法加载 SuperAI 插件入口 main.py")
_module = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("superai_entry_main", _module)
_spec.loader.exec_module(_module)

PLUGIN_NAME = _module.PLUGIN_NAME
SuperAIPlugin = _module.SuperAIPlugin
__version__ = _module.__version__

__all__ = ["PLUGIN_NAME", "SuperAIPlugin", "__version__"]
