"""图标自检（``superai/assets.py``）的回归测试。

背景是 Issue #1 的反馈：「解析了，插件的 Logo 图标却不显示」。

图标这条链路的共同点是**没有任何反馈**：

1. 框架写死 ``StarManager.logo_fname = "logo.png"``，只对插件根目录做一次
   ``os.path.exists``，找不到就静默回落默认图标，一行日志都不打；
2. 找到了也不代表用户看得到 —— 商店详情页会把这个文件**整份下载**，
   体积过大在弱网 / 移动端上就是「图片区域一直空着」；
3. 面板图标走静态路由，是另一条链路，坏了也不报错。

所以这里对**每一种真实出现过的故障形态**都设一条断言：
自检必须认出它，并给出可执行的修复方向。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from superai.assets import (
    LOGO_FNAME,
    LOGO_MAX_BYTES,
    LOGO_MAX_SIDE,
    PANEL_LOGO_MAX_BYTES,
    effective_logo_path,
    format_logo_report,
    inspect_logo,
    png_metadata,
)

ROOT = Path(__file__).resolve().parent.parent

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


@pytest.fixture()
def plugin_dir(tmp_path: Path) -> Path:
    """一个「一切正常」的插件目录副本（图标 + 面板图标都就位）。"""
    (tmp_path / "pages" / "studio").mkdir(parents=True)
    shutil.copy(ROOT / LOGO_FNAME, tmp_path / LOGO_FNAME)
    panel = Image.open(ROOT / LOGO_FNAME).convert("RGBA").resize((128, 128))
    panel.save(tmp_path / "pages" / "studio" / LOGO_FNAME)
    return tmp_path


def _problems(report: dict) -> str:
    return " / ".join(item["symptom"] for item in report["problems"])


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
def test_healthy_repo_passes_self_check():
    """仓库当前状态必须自检通过 —— 否则用户会看到异常。"""
    report = inspect_logo(ROOT)
    assert report["ok"], f"仓库图标自检未通过：{report['problems']}"


def test_healthy_plugin_dir_passes(plugin_dir: Path):
    report = inspect_logo(plugin_dir)
    assert report["ok"], _problems(report)


def test_report_is_info_level_when_healthy():
    level, message = format_logo_report(inspect_logo(ROOT))
    assert level == "info", "正常时不应该打 warning"
    assert LOGO_FNAME in message


def test_effective_logo_path_matches_framework_lookup(plugin_dir: Path):
    """复刻框架行为：只认插件**根目录**下的 ``logo.png``。"""
    assert effective_logo_path(plugin_dir) == plugin_dir / LOGO_FNAME

    moved = plugin_dir / "assets.png"
    (plugin_dir / LOGO_FNAME).rename(moved)
    assert effective_logo_path(plugin_dir) is None, (
        "框架只做一次 os.path.exists(根目录/logo.png)，换名字必须解析不到"
    )


def test_png_metadata_reads_ihdr_without_pillow():
    """自检不能用 Pillow（运行时只有 AstrBot 内置依赖）。"""
    meta = png_metadata(ROOT / LOGO_FNAME)
    assert meta is not None
    assert meta["width"] == meta["height"] > 0
    assert meta["has_alpha"] is True, "无背景 Logo 必须被识别为带透明通道"


# ---------------------------------------------------------------------------
# 故障形态 —— 每一种都对应一类真实症状
# ---------------------------------------------------------------------------
def test_missing_icon_is_reported_as_default_icon(plugin_dir: Path):
    (plugin_dir / LOGO_FNAME).unlink()
    report = inspect_logo(plugin_dir)
    assert not report["ok"]
    assert "默认图标" in _problems(report)
    assert report["path"] is None


def test_icon_in_subdirectory_is_reported(plugin_dir: Path):
    """图标被套进子目录 —— 框架不会递归查找，表现与「没有图标」一样。"""
    (plugin_dir / LOGO_FNAME).rename(plugin_dir / "static-logo.png")
    report = inspect_logo(plugin_dir)
    assert not report["ok"]
    assert "默认图标" in _problems(report)
    assert "static-logo.png" in str(report["problems"]), "要提示目录下有哪些疑似图标文件"


def test_non_png_content_is_reported(plugin_dir: Path):
    """JPEG / SVG 改扩展名 —— 文件名对得上，浏览器解码失败则是破图。"""
    Image.open(ROOT / LOGO_FNAME).convert("RGB").save(plugin_dir / LOGO_FNAME, "JPEG")
    report = inspect_logo(plugin_dir)
    assert not report["ok"]
    assert "破图" in _problems(report)


def test_truncated_png_is_reported(plugin_dir: Path):
    raw = (plugin_dir / LOGO_FNAME).read_bytes()
    (plugin_dir / LOGO_FNAME).write_bytes(raw[: len(raw) // 2])
    report = inspect_logo(plugin_dir)
    assert not report["ok"], "被截断的 PNG 必须被判为不合法"


def test_oversized_icon_is_reported(plugin_dir: Path):
    """大位图 = 用户等图片加载。这是「图标不显示」里最容易被忽略的一种。

    回归：这里以前只断言 ``>= 256`` 且没有上限，1024×1024 的 600 KB 位图
    被判为「越大越好」，结果商店详情页每次都要整份下载。
    """
    big = Image.open(ROOT / LOGO_FNAME).convert("RGBA").resize((1024, 1024))
    big.save(plugin_dir / LOGO_FNAME)
    report = inspect_logo(plugin_dir)
    assert not report["ok"]
    assert "不显示" in _problems(report) or "加载慢" in _problems(report)
    detail = str(report["problems"])
    assert "1024×1024" in detail, "报告要说清实际尺寸，便于维护者定位"


def test_opaque_background_is_reported(plugin_dir: Path):
    """白底图在深色主题下是一块白斑 —— 必须识别为「不带透明通道」。"""
    Image.open(ROOT / LOGO_FNAME).convert("RGB").save(plugin_dir / LOGO_FNAME)
    report = inspect_logo(plugin_dir)
    assert not report["ok"]
    assert "白底" in _problems(report)


def test_non_square_icon_is_reported(plugin_dir: Path):
    Image.open(ROOT / LOGO_FNAME).convert("RGBA").resize((512, 256)).save(plugin_dir / LOGO_FNAME)
    report = inspect_logo(plugin_dir)
    assert not report["ok"]
    assert "变形" in _problems(report)


def test_missing_panel_icon_is_reported(plugin_dir: Path):
    """面板图标是独立链路（静态路由），缺了不会影响插件列表。"""
    (plugin_dir / "pages" / "studio" / LOGO_FNAME).unlink()
    report = inspect_logo(plugin_dir)
    assert not report["ok"]
    assert "Studio 面板" in _problems(report)


def test_oversized_panel_icon_is_reported(plugin_dir: Path):
    """面板图标是缩略图，超限会让面板打开变慢（它是首屏元素）。"""
    Image.open(ROOT / LOGO_FNAME).convert("RGBA").resize((256, 256)).save(
        plugin_dir / "pages" / "studio" / LOGO_FNAME, format="PNG", compress_level=0
    )
    panel = plugin_dir / "pages" / "studio" / LOGO_FNAME
    assert panel.stat().st_size > PANEL_LOGO_MAX_BYTES, (
        "测试夹具本身要能触发这条检查；若压缩后体积已降到上限内，"
        "说明上限需要重新评估（改 PANEL_LOGO_MAX_BYTES 的注释理由）"
    )
    report = inspect_logo(plugin_dir)
    assert not report["ok"]
    assert "面板" in _problems(report)


def test_report_always_carries_symptom_and_fix(plugin_dir: Path):
    """每条问题都必须带「症状 / 原因 / 修复」三件套 —— 否则等于没说。"""
    (plugin_dir / LOGO_FNAME).unlink()
    report = inspect_logo(plugin_dir)
    assert report["problems"], "缺图标必须报告问题"
    for problem in report["problems"]:
        assert problem["symptom"].strip()
        assert problem["detail"].strip()
        assert problem["fix"].strip()

    level, message = format_logo_report(report)
    assert level == "warning"
    for problem in report["problems"]:
        assert problem["symptom"] in message
        assert problem["fix"] in message


def test_self_check_never_raises(tmp_path: Path):
    """自检必须「尽力而为」—— 图标坏了不能把插件拖死。"""
    for broken in (tmp_path, tmp_path / "not-a-file"):
        if broken.name == "not-a-file":
            continue
        report = inspect_logo(broken)
        assert report["ok"] is False
        assert report["problems"]

    # 路径本身不存在时也要给出可读结论，而不是抛异常
    report = inspect_logo(tmp_path / "does-not-exist")
    assert report["ok"] is False


def test_limits_are_sane_and_documented():
    """上限值本身要合理：能容纳当前资源，又能拦住明显没优化的文件。"""
    assert 128 <= LOGO_MAX_SIDE <= 1024
    assert LOGO_MAX_BYTES <= 512 * 1024
    icon_size = (ROOT / LOGO_FNAME).stat().st_size
    assert icon_size <= LOGO_MAX_BYTES, "仓库自身的图标必须在上限内"

    panel = ROOT / "pages" / "studio" / LOGO_FNAME
    assert panel.stat().st_size <= PANEL_LOGO_MAX_BYTES


# ---------------------------------------------------------------------------
# 与真实框架的一致性
# ---------------------------------------------------------------------------
def test_framework_has_no_fallback_logo_names():
    """把「框架只认一个文件名」钉在**真实源码**上。

    回归：`docs/install.md` 曾写成「按顺序找
    ``logo.png > logo.jpg > logo.jpeg > logo.webp > logo.svg``」——
    真实框架里只有 `self.logo_fname = "logo.png"` 一行，
    没有后缀回落列表。文档写错不会让 CI 红，但会让维护者
    把图片存成 `logo.jpg` 然后奇怪为什么图标不显示。

    这条测试在能拿到真实 AstrBot 源码时才跑（其余场景静默跳过）。
    """
    import os
    import re

    ref = Path(os.environ.get("ASTRBOT_REF", "/tmp/astrbot-ref"))
    source = ref / "astrbot" / "core" / "star" / "star_manager.py"
    if not source.is_file():
        pytest.skip("未找到 AstrBot 源码，跳过框架一致性校验")

    text = source.read_text(encoding="utf-8")
    names = re.findall(r'logo_fname\s*=\s*"([^"]+)"', text)
    assert names == [LOGO_FNAME], (
        f"框架的 logo 文件名不再是唯一且为 {LOGO_FNAME}：{names}；"
        "docs/dev/astrbot-contracts.md 与 docs/install.md 需要同步更新"
    )
    for other in ("logo.jpg", "logo.jpeg", "logo.webp", "logo.svg", "logo.gif"):
        assert other not in text, (
            f"真实框架源码里出现了 {other} —— 若框架真的支持后缀回落，文档与自检逻辑都要跟着改"
        )


def test_docs_do_not_claim_a_fallback_logo_chain():
    """文档里不得再出现「按后缀顺序找图标」的说法。"""
    for relative in ("docs/install.md", "docs/dev/astrbot-contracts.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "logo.png  >  logo.jpg" not in text, f"{relative} 仍在描述不存在的后缀回落列表"

    install = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
    assert "图标自检" in install, "安装文档要告诉用户从启动日志确认图标是否正常"
    assert LOGO_FNAME in install and "根" in install
