"""仓库一致性测试：把「容易再次踩坑」的约定固化下来。"""

from __future__ import annotations

import json
import os
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

    回归（Issue #1 v0.2.6）：``author`` 一度是平台账号 ``cosc``，
    商店卡片上因此显示「作者：cosc」而不是插件作者。
    现在统一为作者「科技酱」的包身份 ``TechSauce``；
    字形（大小写、空格）也由断言锁住，避免出现 ``tech sauce`` 之类的变体。
    """
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    assert metadata["author"] == "TechSauce"
    assert metadata["name"] == "astrbot_plugin_superai"
    assert f"{metadata['author']}/{metadata['name']}" == "TechSauce/astrbot_plugin_superai"


# ---------------------------------------------------------------------------
# 发布通道契约（v0.2.5 起）
# ---------------------------------------------------------------------------
def test_metadata_repo_points_to_github():
    """``repo`` 必须是 GitHub 仓库地址。

    AstrBot 官方插件市场只接受 GitHub 仓库或 ZIP 包，市场里的插件
    ``repo`` 全部是 ``github.com/<owner>/<repo>``。指向别处会导致
    提交被拒，或上架后无法做更新检测。
    """
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    repo = str(metadata["repo"])
    assert repo.startswith("https://github.com/"), f"repo 必须指向 GitHub：{repo}"
    assert repo.count("/") >= 4, f"repo 应是 https://github.com/<owner>/<repo>：{repo}"


def test_metadata_plugin_id_stable():
    """``author`` 必须是稳定包身份（不是展示名）。

    ``plugin_id = author + "/" + name`` 是市场里的全局唯一标识，
    也是已安装插件匹配更新的依据 —— 改成展示名会让老用户收不到更新。
    """
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    assert metadata["author"] == "TechSauce", "author 必须是稳定包身份 TechSauce"
    assert metadata["name"] == "astrbot_plugin_superai"


def test_release_scripts_present_and_executable():
    """发布通道依赖的两个脚本必须存在且可执行。

    它们分别负责「构建可上传官方市场的 ZIP」与「同步 GitHub 发布镜像」，
    缺失会让发布流程在打 tag 时断掉。
    """
    for name in ("sync_github.sh", "build_plugin_zip.sh"):
        path = ROOT / "scripts" / name
        assert path.exists(), f"缺少发布脚本 {name}"
        assert os.access(path, os.X_OK), f"{name} 不可执行（git 会丢失可执行位）"


def test_cnb_pipeline_has_tag_release_stage():
    """``.cnb.yml`` 必须有 tag 触发的发布阶段。"""
    pipeline = (ROOT / ".cnb.yml").read_text(encoding="utf-8")
    assert "tag_push" in pipeline, "缺少 tag 触发配置"
    assert "build_plugin_zip.sh" in pipeline, "tag 流水线未构建插件 ZIP"
    assert "sync_github.sh" in pipeline, "tag 流水线未同步 GitHub 镜像"


# ---------------------------------------------------------------------------
# 发布包内容契约
# ---------------------------------------------------------------------------
#: 发布包中绝对不允许出现的东西。
#: ``data/`` / ``shots/`` / ``mockllm/`` 都被 .gitignore 忽略，是本地运行插件
#: 或端到端联调后留下的产物；其中 ``data/cmd_config.json`` 含 dashboard 密码哈希，
#: ``data/data_v4.db`` 是完整运行库。它们曾经被 ``build_plugin_zip.sh``
#: 直接 ``tar`` 工作区时误打进市场的 ZIP。
FORBIDDEN_IN_PACKAGE = [
    "data/",
    "shots/",
    "mockllm/",
    ".git/",
    "cmd_config.json",
    ".db",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "/tests/",
    "/docs/",
    ".cnb.yml",
]


def _package_entries() -> list[str]:
    import subprocess
    import zipfile

    root = ROOT
    subprocess.run(
        ["bash", "scripts/build_plugin_zip.sh"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    from superai.version import __version__

    zip_path = root / "dist" / f"astrbot_plugin_superai-v{__version__}.zip"
    assert zip_path.is_file(), f"打包脚本没有产出 {zip_path.name}"
    with zipfile.ZipFile(zip_path) as archive:
        return archive.namelist()


@pytest.fixture(scope="module")
def package_entries() -> list[str]:
    return _package_entries()


def test_package_has_no_runtime_artifacts(package_entries):
    """发布包不得包含本地运行时产物（数据库 / 配置 / 缓存）。

    回归：``build_plugin_zip.sh`` 早期直接打包**工作区**，只要维护者在打包前
    本地跑过一次插件，``data/cmd_config.json``（含 dashboard 密码哈希）与
    ``data/data_v4.db`` 就会被发到市场。现在改为按 ``git ls-files`` 打包，
    并在脚本内做显性自检。
    """
    leaked = [
        entry for entry in package_entries if any(bad in entry for bad in FORBIDDEN_IN_PACKAGE)
    ]
    assert not leaked, f"发布包混进了不该有的内容：{leaked}"


def test_package_contains_runtime_essentials(package_entries):
    """发布包必须齐全：入口、元数据、图标、配置 schema、全部子包。"""
    required = [
        "astrbot_plugin_superai/main.py",
        "astrbot_plugin_superai/metadata.yaml",
        "astrbot_plugin_superai/logo.png",
        "astrbot_plugin_superai/_conf_schema.json",
        "astrbot_plugin_superai/superai/version.py",
        "astrbot_plugin_superai/superai/tools/registry.py",
        "astrbot_plugin_superai/pages/studio/index.html",
        "astrbot_plugin_superai/.astrbot-plugin/i18n/zh-CN.json",
    ]
    missing = [path for path in required if path not in package_entries]
    assert not missing, f"发布包缺少必需文件：{missing}"


def test_package_version_matches_repo(package_entries):
    """发布包里的版本必须与仓库一致（防止打出旧包）。"""
    import re
    import zipfile

    from superai.version import __version__

    zip_path = ROOT / "dist" / f"astrbot_plugin_superai-v{__version__}.zip"
    with zipfile.ZipFile(zip_path) as archive:
        metadata = archive.read("astrbot_plugin_superai/metadata.yaml").decode("utf-8")
        code = archive.read("astrbot_plugin_superai/superai/version.py").decode("utf-8")
    assert re.search(rf"^version: v{re.escape(__version__)}$", metadata, re.M), (
        "发布包 metadata.yaml 的版本与仓库不一致"
    )
    assert f'__version__ = "{__version__}"' in code, "发布包代码里的版本与仓库不一致"


def test_build_script_is_git_driven():
    """打包脚本必须基于 git 清单，而不是直接 tar 工作区。

    这是上一处泄漏的**根因防线**：只要来源是 git 跟踪的文件，
    .gitignore 里的任何本地产物都不可能被误打包。
    """
    script = (ROOT / "scripts" / "build_plugin_zip.sh").read_text(encoding="utf-8")
    assert "git ls-files" in script, "打包脚本必须按 git 跟踪文件收集"
    assert "禁止内容" in script or "forbidden" in script, "打包脚本必须自带泄漏自检"
