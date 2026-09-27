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
