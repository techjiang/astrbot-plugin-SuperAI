"""仓库一致性测试：把「容易再次踩坑」的约定固化下来。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_metadata_has_logo():
    """AstrBot 会读取插件根目录的 logo.png 作为插件图标。"""
    logo = ROOT / "logo.png"
    assert logo.is_file(), "插件 Logo 必须放在仓库根目录的 logo.png"
    assert logo.stat().st_size > 1024, "Logo 文件过小，可能不是有效图片"
    header = logo.read_bytes()[:8]
    assert header == b"\x89PNG\r\n\x1a\n", "logo.png 必须是 PNG 格式"


def test_metadata_yaml_registers_studio_page():
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    pages = metadata.get("pages") or []
    names = {page["name"] for page in pages if isinstance(page, dict) and "name" in page}
    assert "studio" in names, "Studio 面板需要在 metadata.yaml 的 pages 中声明"
    assert (ROOT / "pages" / "studio" / "index.html").is_file()


def test_config_schema_types_are_supported():
    """schema 里出现的 type 必须是 AstrBot 认识的类型。"""
    supported = {
        "int",
        "float",
        "bool",
        "string",
        "text",
        "list",
        "file",
        "object",
        "template_list",
        "dict",
    }
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    def walk(node: dict, path: str = "") -> None:
        for key, meta in node.items():
            if not isinstance(meta, dict):
                continue
            if "type" not in meta:
                continue
            assert meta["type"] in supported, f"{path}{key} 使用了不支持的类型 {meta['type']}"
            if meta["type"] == "object":
                assert isinstance(meta.get("items"), dict), f"{path}{key} 缺少 items"
                walk(meta["items"], f"{path}{key}.")

    walk(schema)


def test_config_defaults_match_code_defaults():
    """schema 的默认值必须能被 build_config 接受，且不互相矛盾。"""
    from superai.core.config import build_config

    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    def defaults(node: dict) -> dict:
        out: dict = {}
        for key, meta in node.items():
            if not isinstance(meta, dict):
                continue
            if meta.get("type") == "object":
                out[key] = defaults(meta.get("items") or {})
            elif "default" in meta:
                out[key] = meta["default"]
        return out

    config = build_config(defaults(schema))
    assert config.provider_map()  # 五个档位都能读出来
    assert isinstance(config.enabled, bool)


def test_localized_files_share_the_same_keys():
    zh = json.loads((ROOT / ".astrbot-plugin/i18n/zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((ROOT / ".astrbot-plugin/i18n/en-US.json").read_text(encoding="utf-8"))

    def flatten(node: dict, prefix: str = "") -> set[str]:
        keys: set[str] = set()
        for key, value in node.items():
            path = f"{prefix}{key}"
            if isinstance(value, dict):
                keys |= flatten(value, f"{path}.")
            else:
                keys.add(path)
        return keys

    assert flatten(zh) == flatten(en), "中英文 i18n 的键必须完全一致"


def test_studio_page_escapes_dynamic_values():
    """Studio 面板把后端数据拼进 innerHTML，必须经过转义函数。"""
    app = (ROOT / "pages" / "studio" / "app.js").read_text(encoding="utf-8")
    assert "function esc(" in app, "缺少 HTML 转义函数"

    # 只看真正的 innerHTML 赋值语句，避免误伤 textContent / 模板字符串
    assignments = list(re.finditer(r"innerHTML\s*=\s*(?P<expr>.*?);", app, re.DOTALL))
    assert assignments, "应至少有一处 innerHTML 赋值"
    for match in assignments:
        expr = match.group("expr")
        for interpolation in re.finditer(r"\$\{([^}]*)\}", expr):
            inner = interpolation.group(1)
            if "esc(" in inner:
                continue
            # 唯一允许不转义的场景：宽度百分比（纯数字表达式）
            assert inner.strip().startswith("Math.round("), (
                f"innerHTML 插值未转义：{inner.strip()[:80]}"
            )


def test_pages_declared_have_matching_dirs():
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    for page in metadata.get("pages") or []:
        directory = ROOT / "pages" / page["name"]
        assert (directory / "index.html").is_file(), f"缺少 pages/{page['name']}/index.html"


@pytest.mark.parametrize("name", ["logo.svg"])
def test_studio_assets_are_valid_svg(name):
    import xml.dom.minidom

    xml.dom.minidom.parseString((ROOT / "pages" / "studio" / name).read_text(encoding="utf-8"))


def test_no_runtime_imports_of_test_stubs():
    """生产代码不得依赖 tests/stubs。"""
    for path in (ROOT / "superai").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "tests.stubs" not in text
        assert "tests/stubs" not in text


def test_docs_mention_author_links():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for link in (
        "https://docs.asoe.cn",
        "https://github.com/techjiang/",
        "https://space.bilibili.com/1768832152",
        "https://forums.asoe.cn/",
    ):
        assert link in readme, f"README 缺少作者链接：{link}"
