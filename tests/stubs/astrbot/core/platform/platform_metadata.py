"""``PlatformMetadata`` 替身。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlatformMetadata:
    name: str = "stub"
    description: str = "stub platform"
    id: str = "stub"
    default_config_tmpl: dict | None = None
    adapter_display_name: str | None = None
    logo_path: str | None = None
    support_streaming_message: bool = True
    support_proactive_message: bool = True


__all__ = ["PlatformMetadata"]
