"""astrbot.core.utils.astrbot_path 替身。"""

from __future__ import annotations

import tempfile


def get_astrbot_data_path() -> str:
    """返回一个临时数据目录（测试里会被 monkeypatch 覆盖）。"""
    return tempfile.gettempdir()
