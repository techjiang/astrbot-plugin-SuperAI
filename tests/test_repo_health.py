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


def test_studio_logo_is_a_transparent_png():
    """Studio 面板与插件图标使用同一张无背景 Logo。

    回归：面板此前用的是一张手绘 SVG，和 AstrBot 插件列表里的
    ``logo.png`` 视觉不一致；现在两处共用同一份美术资源。
    ``logo.png`` 必须带透明通道 —— 面板有深色主题，
    不透明的白底图在深色背景上会非常刺眼。
    """
    from PIL import Image

    # 插件图标：AstrBot 会读取仓库根目录的 logo.png
    icon = Image.open(ROOT / "logo.png")
    assert icon.format == "PNG"
    assert icon.size == (1024, 1024), "插件图标应为 1024×1024"
    assert icon.mode in {"RGBA", "LA", "P"}, "插件图标必须带透明通道（无背景）"
    alpha = icon.convert("RGBA").getchannel("A")
    assert alpha.getextrema()[0] == 0, "图片必须存在全透明像素，说明背景确实是透明的"

    # 面板图标
    panel_logo = ROOT / "pages" / "studio" / "logo.png"
    assert panel_logo.is_file(), "Studio 面板需要有 logo.png"
    panel = Image.open(panel_logo).convert("RGBA")
    assert panel.getchannel("A").getextrema()[0] == 0, "面板 Logo 也必须无背景"


def test_studio_index_references_existing_logo():
    html = (ROOT / "pages" / "studio" / "index.html").read_text(encoding="utf-8")
    assert 'src="./logo.png"' in html, "index.html 应引用 logo.png"
    assert (ROOT / "pages" / "studio" / "logo.png").is_file()


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


# ---------------------------------------------------------------------------
# AstrBot 官方插件市场发布要求
# ---------------------------------------------------------------------------
def test_metadata_satisfies_plugin_store_rules():
    """按 AstrBot 官方市场规范校验 metadata.yaml 的必填字段与约束。

    依据：``docs/zh/dev/plugin-market/2026-06-27.md``（Schema Version 1），
    以及 ``astrbot/core/star/updater.py`` 的
    ``PLUGIN_METADATA_REQUIRED_FIELDS = ("name", "desc", "version", "author")``。

    这里用**同一套规则**做本地校验，避免「提交后才发现 CI 拒绝」。
    """
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))

    # 必填字段：非空字符串
    for field in ("name", "desc", "version", "author"):
        assert field in metadata, f"metadata.yaml 缺少必填字段 {field}"
        assert isinstance(metadata[field], str) and metadata[field].strip(), (
            f"metadata.yaml 的 {field} 必须是非空字符串"
        )

    # plugin_id = author/name，两者都不得含 "/"
    for field in ("author", "name"):
        assert "/" not in metadata[field], f"{field} 不得包含 '/'（否则 plugin_id 非法）"

    # name 必须是合法的 Python 标识符（框架用 importlib 加载）
    assert metadata["name"].isidentifier(), "name 必须是合法 Python 标识符"

    # 可选字段的类型约束
    if "tags" in metadata:
        assert isinstance(metadata["tags"], list) and all(
            isinstance(item, str) for item in metadata["tags"]
        ), "tags 必须是字符串数组"
    if "support_platforms" in metadata:
        assert isinstance(metadata["support_platforms"], list), "support_platforms 必须是数组"
    if "social_link" in metadata:
        assert str(metadata["social_link"]).startswith("https://"), "social_link 必须是 HTTPS URL"

    # repo 必须是可解析的仓库地址（框架用 normalize_repository_url 解析）
    from urllib.parse import urlparse

    repo = str(metadata["repo"])
    parsed = urlparse(repo)
    assert parsed.scheme == "https", "repo 必须是 HTTPS 地址"
    assert parsed.hostname, "repo 缺少主机名"
    assert not parsed.query and not parsed.fragment, "repo 不得带 query / fragment"


def test_metadata_urls_are_reachable():
    """``social_link`` / ``repo`` 必须是真实可达的地址（避免发布后 404）。"""
    import urllib.error
    import urllib.request

    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    for key in ("repo", "social_link"):
        url = metadata.get(key)
        if not url:
            continue
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "superai-test"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                assert response.status < 400, f"{key} 返回 {response.status}"
        except urllib.error.HTTPError as exc:  # pragma: no cover - 网络受限时跳过
            raise AssertionError(f"{key} 不可达：{url} -> HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError):  # pragma: no cover - 无外网
            pytest.skip(f"网络不可用，跳过 {key} 可达性检查")


def test_version_consistency_across_files():
    """``metadata.yaml`` / ``superai/version.py`` / README 徽章三处版本必须一致。"""
    from superai.version import __version__

    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    assert str(metadata["version"]).lstrip("v") == __version__.lstrip("v"), (
        "metadata.yaml 与 superai/version.py 版本不一致"
    )

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"version-v{__version__}" in readme, "README 版本徽章未与 version.py 同步"


def test_plugin_market_identity_is_stable():
    """``author`` 必须是稳定的包身份，不得随展示需求随意改动。

    市场规范把 ``plugin_id`` 定义为 ``metadata.author + "/" + metadata.name``，
    它是插件在市场里的**全局唯一标识**，也是已安装插件匹配更新的依据。
    改动它会导致老用户无法收到更新。
    """
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    assert metadata["author"] == "cosc"
    assert metadata["name"] == "astrbot_plugin_superai"
    assert f"{metadata['author']}/{metadata['name']}" == "cosc/astrbot_plugin_superai"
