"""astrbot.api 替身。"""

from __future__ import annotations

from astrbot import logger  # noqa: F401


class AstrBotConfig(dict):
    """AstrBot 配置对象替身。"""

    def save_config(self) -> None:  # pragma: no cover - 测试中不需要
        pass
