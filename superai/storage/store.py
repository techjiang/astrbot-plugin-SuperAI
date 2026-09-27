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
        self._file_loaded = False
        self._file_data: Any = None

    # -- 基础 -------------------------------------------------------------
    def _read_file(self) -> Any:
        """读取整个文件（带缓存），失败时回落到默认值。"""
        if self._file_loaded:
            return self._file_data
        self._file_loaded = True
        data = self._default_copy()
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"[SuperAI] 读取 {self.path.name} 失败，使用默认值：{exc}")
        self._file_data = data
        return data

    def _load(self, key: str) -> Any:
        if key in self._loaded:
            return self._cache.get(key)
        data = self._read_file()
        if key:
            # 文件内分区：非空 key 取顶层字段，避免多个 key 互相覆盖
            value = data.get(key) if isinstance(data, dict) else None
            if not isinstance(value, type(self._default)) and not (
                isinstance(value, (dict, list)) and isinstance(self._default, (dict, list))
            ):
                value = self._default_copy()
            data = value
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
        """一个 ``JsonStore`` 实例对应一个文件；``key`` 只是文件内的分区。

        早期版本会把 key 拼进文件名（``router.json`` + ``router_health``
        变成 ``router_router_health.json``），既难读又容易在下游被当成新文件。
        现在统一读写 :attr:`path` 本身，key 仅作为顶层字段。
        """
        return self.path

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
        """把缓存写入磁盘。

        非空 ``key`` 写入文件内的同名分区，其余分区原样保留 ——
        早期版本每个 key 写一个独立文件，导致 ``router.json`` 旁边
        出现 ``router_router_health.json`` 这类文件名。
        """
        if key:
            root = self._read_file()
            if not isinstance(root, dict):
                root = {}
                self._file_data = root
            root[key] = self._cache.get(key)
            payload: Any = root
        else:
            payload = self._cache.get("", self._default_copy())
            # 若文件里已有命名分区，写根键时不能把它们抹掉
            root = self._file_data
            if isinstance(root, dict) and isinstance(payload, dict):
                payload = {**root, **payload}
            self._file_data = payload
        self._write(payload)

    def _write(self, payload: Any) -> None:
        """原子地把 ``payload`` 写到 :attr:`path`。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=self._indent)
            os.replace(tmp_path, self.path)
        except (OSError, TypeError, ValueError) as exc:
            logger.error(f"[SuperAI] 写入 {self.path.name} 失败：{exc}")

    def delete(self, key: str = "") -> None:
        """删除某个分区的数据（并落盘）。"""
        self._cache.pop(key, None)
        self._loaded.discard(key)
        if key:
            root = self._read_file()
            if isinstance(root, dict) and key in root:
                root.pop(key, None)
                self._file_data = root
                self._write(root)
            return
        self._file_data = self._default_copy()
        self._write(self._file_data)

    def reload(self) -> None:
        """丢弃缓存，下次读取时重新从磁盘加载。"""
        self._cache.clear()
        self._loaded.clear()
        self._file_loaded = False
        self._file_data = None
