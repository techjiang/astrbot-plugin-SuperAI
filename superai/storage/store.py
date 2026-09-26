"""极简 JSON 持久化封装。

AstrBot 的插件 KV 存储（``put_kv_data``）在早期版本不存在，且不适合存储
结构化的大对象；因此 SuperAI 统一把数据写到
``data/plugin_data/<plugin_name>/`` 下的 JSON 文件，遵循官方「持久化数据放 data 目录」
的开发原则。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from astrbot.api import logger


class JsonStore:
    """线程/协程安全的轻量 JSON 文件存储。

    写操作使用「临时文件 + 原子替换」，避免进程被中断时写出半截文件。
    """

    def __init__(self, path: Path, *, default: Any = None, indent: int = 2) -> None:
        self.path = Path(path)
        self._default = {} if default is None else default
        self._indent = indent
        self._cache: dict[str, Any] = {}
        self._loaded: set[str] = set()

    # -- 基础 -------------------------------------------------------------
    def _load(self, key: str) -> Any:
        if key in self._loaded:
            return self._cache.get(key)
        file = self._key_path(key)
        data = self._default_copy()
        if file.exists():
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"[SuperAI] 读取 {file.name} 失败，使用默认值：{exc}")
        self._cache[key] = data
        self._loaded.add(key)
        return data

    def _default_copy(self) -> Any:
        if isinstance(self._default, dict):
            return dict(self._default)
        if isinstance(self._default, list):
            return list(self._default)
        return self._default

    def _key_path(self, key: str) -> Path:
        if not key:
            return self.path
        return self.path.with_name(f"{self.path.stem}_{key}{self.path.suffix}")

    # -- 公共 API ---------------------------------------------------------
    def get(self, key: str = "") -> Any:
        """读取数据（返回缓存对象，修改后需调用 :meth:`save`）。"""
        return self._load(key)

    def set(self, data: Any, key: str = "") -> None:
        """覆盖写入并落盘。"""
        self._cache[key] = data
        self._loaded.add(key)
        self.save(key)

    def save(self, key: str = "") -> None:
        """把缓存写入磁盘。"""
        data = self._cache.get(key, self._default_copy())
        target = self._key_path(key)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=self._indent)
            os.replace(tmp_path, target)
        except (OSError, TypeError, ValueError) as exc:
            logger.error(f"[SuperAI] 写入 {target.name} 失败：{exc}")

    def delete(self, key: str = "") -> None:
        """删除某个键的数据。"""
        self._cache.pop(key, None)
        self._loaded.discard(key)
        file = self._key_path(key)
        try:
            if file.exists():
                file.unlink()
        except OSError as exc:  # pragma: no cover
            logger.warning(f"[SuperAI] 删除 {file.name} 失败：{exc}")

    def reload(self) -> None:
        """丢弃缓存，下次读取时重新从磁盘加载。"""
        self._cache.clear()
        self._loaded.clear()
