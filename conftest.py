"""测试引导：注入 AstrBot 替身并让 ``superai`` 可导入。"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent

# 优先使用本地 stub，避免依赖真实的 AstrBot 安装
STUBS = ROOT / "tests" / "stubs"
for path in (str(STUBS), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
