"""文档一致性测试。

文档里最容易腐烂的是**具体事实**：指令名写错、配置项改名后没同步、
文件删了链接还在、README 里的版本号不对。

这些错误不会让代码跑不起来，但会让用户照着文档操作却失败。
所以这里把「文档中出现的可验证事实」与代码对齐。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted((ROOT / "docs").rglob("*.md"))
TOP_DOCS = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md"]

#: 文档里应当被引用的顶层文档（缺一个都说明索引被破坏）
REQUIRED_DOCS = [
    "docs/README.md",
    "docs/install.md",
    "docs/quickstart.md",
    "docs/configuration.md",
    "docs/commands.md",
    "docs/routing.md",
    "docs/memory.md",
    "docs/tools-and-workflows.md",
    "docs/usage-and-budget.md",
    "docs/studio.md",
    "docs/faq.md",
    "docs/dev/architecture.md",
    "docs/dev/development.md",
    "docs/dev/astrbot-contracts.md",
    "docs/dev/release.md",
]


def _all_markdown() -> list[Path]:
    return [*TOP_DOCS, *DOCS]


# ---------------------------------------------------------------------------
# 链接
# ---------------------------------------------------------------------------
def test_all_docs_exist():
    for relative in REQUIRED_DOCS:
        assert (ROOT / relative).is_file(), f"文档缺失：{relative}"


def test_markdown_links_are_resolvable():
    """相对链接必须指向真实存在的文件（锚点只在同文件内校验前缀）。"""
    pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    broken: list[str] = []
    for path in _all_markdown():
        for target in pattern.findall(path.read_text(encoding="utf-8")):
            target = target.strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            file_part = target.split("#", 1)[0]
            if not file_part:
                continue
            if not (path.parent / file_part).resolve().exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, "存在失效的相对链接：\n" + "\n".join(broken)


def test_readme_links_to_docs_index():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/README.md" in readme, "README 必须链接到文档索引"
    for link in ("docs/quickstart.md", "docs/configuration.md", "docs/faq.md"):
        assert link in readme, f"README 缺少文档入口：{link}"


def test_docs_index_links_every_document():
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    for relative in REQUIRED_DOCS:
        name = Path(relative).name
        assert name in index, f"docs/README.md 未索引 {relative}"


# ---------------------------------------------------------------------------
# 指令
# ---------------------------------------------------------------------------
def test_documented_commands_exist_in_entry():
    """指令手册里写出的每个子指令都必须真的注册在入口模块里。"""
    entry = (ROOT / "main.py").read_text(encoding="utf-8")
    registered = set(
        re.findall(r'@(?:superai_group|superai_memory_group)\.command\("([^"]+)"\)', entry)
    )
    registered |= set(
        re.findall(r'@(?:superai_group|superai_memory_group)\.group\("([^"]+)"\)', entry)
    )
    registered |= set(re.findall(r'@filter\.command\("([^"]+)"\)', entry))

    documented = set()
    for path in (ROOT / "docs" / "commands.md", ROOT / "README.md"):
        for match in re.findall(r"/superai ([a-z]+)", path.read_text(encoding="utf-8")):
            documented.add(match)

    unknown = {cmd for cmd in documented if cmd not in registered and cmd != "route"}
    assert not unknown, f"文档里出现了未注册的子指令：{sorted(unknown)}"


def test_command_doc_covers_all_registered_commands():
    """反向校验：注册了但文档没写的指令。"""
    entry = (ROOT / "main.py").read_text(encoding="utf-8")
    registered = set(
        re.findall(r'@(?:superai_group|superai_memory_group)\.command\("([^"]+)"\)', entry)
    )
    commands_doc = (ROOT / "docs" / "commands.md").read_text(encoding="utf-8")
    missing = {cmd for cmd in registered if cmd not in commands_doc}
    assert not missing, f"以下指令已注册但未写入指令手册：{sorted(missing)}"


def test_help_text_matches_commands_doc():
    """插件内置帮助文本里提到的子指令也要在手册里。"""
    entry = (ROOT / "main.py").read_text(encoding="utf-8")
    help_block = entry.split("def _help_text", 1)[1].split('"""', 1)[0]
    commands_doc = (ROOT / "docs" / "commands.md").read_text(encoding="utf-8")
    for cmd in re.findall(r"/superai ([a-z]+)", help_block):
        assert cmd in commands_doc, f"帮助文本提到 {cmd}，但指令手册里没有"


# ---------------------------------------------------------------------------
# 配置项
# ---------------------------------------------------------------------------
def _schema_keys() -> dict[str, list[str]]:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    result: dict[str, list[str]] = {}
    for group, meta in schema.items():
        if isinstance(meta, dict) and isinstance(meta.get("items"), dict):
            result[group] = sorted(meta["items"].keys())
        else:
            result[group] = []
    return result


def test_configuration_doc_mentions_every_option():
    """配置手册必须覆盖 schema 里的全部配置项（按 schema 的分组小节逐组校验）。"""
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    doc = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    labels: list[str] = []
    for meta in schema.values():
        if not isinstance(meta, dict):
            continue
        items = meta.get("items")
        if isinstance(items, dict):
            for key, option in items.items():
                if isinstance(option, dict) and option.get("description"):
                    labels.append(option["description"])
                else:
                    labels.append(key)
        elif meta.get("description"):
            labels.append(meta["description"])
    missing = [label for label in labels if label not in doc]
    assert not missing, f"配置手册缺少配置项：{missing}"


# ---------------------------------------------------------------------------
# 其他事实性内容
# ---------------------------------------------------------------------------
def test_readme_version_matches_metadata():
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    version = str(metadata["version"]).lstrip("v")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"version-v{version}" in readme, "README 的版本徽章与 metadata.yaml 不一致"
    from superai.version import __version__

    assert __version__.lstrip("v") == version, "metadata.yaml 与 superai/version.py 版本不一致"


def test_plugin_name_documented_consistently():
    """文档里出现的插件目录名必须与入口模块里的 PLUGIN_NAME 一致。"""
    entry = (ROOT / "main.py").read_text(encoding="utf-8")
    name = re.search(r'PLUGIN_NAME = "([^"]+)"', entry)
    assert name, "入口未定义 PLUGIN_NAME"
    plugin_name = name.group(1)
    for path in _all_markdown():
        text = path.read_text(encoding="utf-8")
        for stray in re.findall(r"data/plugins/([\w-]+)", text):
            assert stray == plugin_name, f"{path.name} 中的插件目录名 {stray} 与 PLUGIN_NAME 不符"


def test_documented_data_dir_matches_code():
    entry = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'data_dir = Path(get_astrbot_data_path()) / "plugin_data" / DATA_DIR_NAME' in entry
    for path in _all_markdown():
        text = path.read_text(encoding="utf-8")
        for stray in re.findall(r"data/plugin_data/([\w-]+)", text):
            assert stray == "astrbot_plugin_superai", f"{path.name} 中的数据目录名不符"


def test_api_paths_documented_match_code():
    """Studio 面板文档里的接口路径必须真实注册。"""
    entry = (ROOT / "main.py").read_text(encoding="utf-8")
    registered = set(re.findall(r"\(f\"/\{PLUGIN_NAME\}([\w/]*)\"", entry))
    doc = (ROOT / "docs" / "studio.md").read_text(encoding="utf-8")
    documented = set(
        re.findall(r"`(/(?:status|stats|memory|memory/clear|tools|workflows|sessions))`", doc)
    )
    assert documented, "Studio 文档里没有解析到任何接口路径"
    assert documented <= registered, f"文档里的接口未注册：{sorted(documented - registered)}"


def test_faq_covers_documented_pitfalls():
    """FAQ 必须覆盖那些「静默失效」的坑（这是排查成本最高的一类）。"""
    faq = (ROOT / "docs" / "faq.md").read_text(encoding="utf-8")
    for keyword in (
        "skipping",
        "missing 1 required positional argument",
        "钩子",
        "平均耗时",
    ):
        assert keyword in faq, f"FAQ 缺少对「{keyword}」的说明"


def test_changelog_mentions_current_version():
    from superai.version import __version__

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {__version__}" in changelog or f"## v{__version__.lstrip('v')}" in changelog


# ---------------------------------------------------------------------------
# 文档准确性（防止「说法与实现不符」）
# ---------------------------------------------------------------------------
def test_docs_have_review_stamp():
    """每篇文档都要有「最后核对」标注。

    文档的腐烂不只来自「改名了没同步」，也来自「写得比实现更漂亮」——
    例如把转义后的 innerHTML 说成 textContent、把「异常转工具结果」
    说成「异常被吞掉」。留下核对锚点，是为了让下次维护知道该对着什么看。
    """
    missing = []
    for path in DOCS:
        text = path.read_text(encoding="utf-8")
        if "**最后核对**：" not in text:
            missing.append(str(path.relative_to(ROOT)))
    assert not missing, "以下文档缺少「最后核对」标注：" + ", ".join(missing)


def test_review_stamps_reference_current_version():
    """「最后核对」标注里的版本号必须与当前版本一致。

    否则会出现「文档声称核对到 v0.2.3，实际代码已是 v0.2.4」——
    这类过期锚点会让人误判文档可信度。
    """
    import re

    from superai.version import __version__

    stale: list[str] = []
    # docs/releases/ 是历史发布说明归档，标注本就停留在当时版本，不参与校验
    candidates = [path for path in [*DOCS, *TOP_DOCS] if "releases" not in path.parts]
    for path in candidates:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\*\*最后核对\*\*：`(v?[0-9]+\.[0-9]+\.[0-9]+)`", text):
            if match.group(1).lstrip("v") != __version__.lstrip("v"):
                stale.append(f"{path.relative_to(ROOT)} -> {match.group(1)}")
    assert not stale, f"以下文档的「最后核对」版本停留在旧版本（当前 {__version__}）：" + ", ".join(
        stale
    )


def test_security_doc_matches_studio_escaping_implementation():
    """SECURITY.md 对面板渲染方式的描述必须与 app.js 实现一致。

    真实实现是「所有 innerHTML 插值经过 esc() 转义」，不是「不用 innerHTML」。
    写反了会让人以为面板天然安全，从而在新代码里放心裸插。
    """
    app = (ROOT / "pages" / "studio" / "app.js").read_text(encoding="utf-8")
    assert "function esc(" in app, "app.js 应存在 HTML 转义函数"
    assert "innerHTML" in app, "app.js 使用 innerHTML 渲染（因此必须有转义防线）"

    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "esc()" in security, "SECURITY.md 必须说明 innerHTML 有 esc() 转义防线"
    assert "不用 `innerHTML`" not in security, (
        "SECURITY.md 不应声称面板「不用 innerHTML」——实际实现用了，靠转义兜住"
    )


def test_docs_do_not_reference_unknown_env_vars():
    """文档里出现的 ASTRBOT_* 环境变量必须真的被项目使用。"""
    sources = [
        (ROOT / "conftest.py").read_text(encoding="utf-8"),
        (ROOT / "scripts" / "e2e_smoke.py").read_text(encoding="utf-8"),
        (ROOT / ".cnb.yml").read_text(encoding="utf-8"),
    ]
    blob = "\n".join(sources)
    used = set(re.findall(r"ASTRBOT[A-Z_]*", blob))
    for text_path in _all_markdown():
        for name in re.findall(r"ASTRBOT[A-Z_]*", text_path.read_text(encoding="utf-8")):
            assert name in used, f"{text_path.name} 引用了不存在的环境变量 {name}"


def test_docs_tool_failure_behaviour_matches_base_class():
    """文档对「工具异常」的描述必须与 SuperAITool.call 的实现一致。"""
    base = (ROOT / "superai" / "tools" / "base.py").read_text(encoding="utf-8")
    assert "except Exception as exc" in base
    assert "工具 {self.name} 执行失败" in base, "工具异常应被转成工具结果文本"

    doc = (ROOT / "docs" / "tools-and-workflows.md").read_text(encoding="utf-8")
    assert "SuperAITool.call()" in doc, "文档应点名兜住异常的实现位置"
    assert "作为**工具结果**返回" in doc or "作为工具结果返回" in doc
