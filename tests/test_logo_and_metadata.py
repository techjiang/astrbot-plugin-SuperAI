"""Logo 与商店元数据契约测试。

这一组测试守住两类**不会报错、但用户一眼就能看见**的问题：

1. **Logo 解析链路**。框架不会主动去找图片 ——
   ``StarManager`` 里写死了 ``self.logo_fname = "logo.png"``，
   然后对 ``os.path.join(plugin_dir_path, self.logo_fname)`` 做一次
   ``os.path.exists`` 探测；命中才写进 ``metadata.logo_path``，
   否则**静默回落到默认图标，一行日志都不打**。

   于是「图标放到子目录」「改名成 ``logo.svg``」
   「把 JPEG 存成 ``logo.png``」「PNG 被截断」这几类问题，
   本地怎么看都正常，只有用户会看到默认图标或破图。

2. **商店元数据**。市场卡片直接读 ``metadata.yaml``：卡片上的「作者」
   就是 ``author`` 字段。它一度被写成平台账号而不是插件作者，
   商店里显示的作者因此与项目实际作者不符 —— 这是 Issue #1 报告的问题。

   同时 ``plugin_id = author + "/" + name`` 是插件在市场里的**全局唯一标识**，
   也是已安装用户匹配更新的依据，所以这一项必须被显式锁住。
"""

from __future__ import annotations

import io
import re
import struct
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent

#: AstrBot 唯一认可的图标文件名（源码 ``StarManager.logo_fname``）。
#:
#: ``astrbot/core/star/star_manager.py``::
#:
#:     self.logo_fname = "logo.png"          # 第 213 行
#:     ...
#:     logo_path = os.path.join(plugin_dir_path, self.logo_fname)
#:     if os.path.exists(logo_path):
#:         metadata.logo_path = logo_path    # 第 1376-1377 行
#:
#: 注意这是**精确路径匹配**：换名字（``logo.svg``）、换大小写
#: （``LOGO.PNG``）、往下套一层目录，都不会被识别。
LOGO_FNAME = "logo.png"

#: 面板图标是同一份美术资源的缩放版，尺寸应明显小于插件图标。
PANEL_LOGO_MAX_SIDE = 256

#: 插件图标（``logo.png``）的边长上限。
#:
#: 图标在插件列表 / 商店卡片里只显示几十像素，
#: 而**商店详情页会直接把这个文件整份下载下来**。
#: 1024×1024 的 RGBA 位图未压缩数据是 4 MB，PNG 编码后仍有 600 KB 左右
#: —— 弱网或移动端上表现为「图片区域一直空着、转圈」。
LOGO_MAX_SIDE = 512

#: 插件图标文件的体积上限（KB）。512×512 量化 PNG 约 45 KB，
#: 这里留出宽裕余量，只拦截「明显没优化过」的文件。
LOGO_MAX_BYTES = 256 * 1024

#: 插件元数据（``metadata.yaml``）—— 发布信息与作者信息的**唯一来源**。
METADATA_PATH = ROOT / "metadata.yaml"

#: 商店卡片上展示的作者（展示名）。
DISPLAY_AUTHOR = "科技酱"
#: ``metadata.yaml`` 的 ``author``（包身份，参与 plugin_id）。
PACKAGE_AUTHOR = "TechSauce"


def _metadata() -> dict:
    return yaml.safe_load(METADATA_PATH.read_text(encoding="utf-8"))


def _icon_candidates() -> list[Path]:
    """插件根目录下所有「看起来像图标」的文件（用于查出误导维护者的多余文件）。"""
    names = (
        "logo.png",
        "logo.jpg",
        "logo.jpeg",
        "logo.webp",
        "logo.svg",
        "logo.gif",
        "LOGO.PNG",
        "icon.png",
    )
    return sorted((ROOT / n for n in names if (ROOT / n).is_file()), key=lambda p: p.name)


# ---------------------------------------------------------------------------
# Logo 解析链路
# ---------------------------------------------------------------------------
def test_plugin_icon_is_where_the_framework_looks_for_it():
    """图标必须正好是**插件目录根下**的 ``logo.png``。

    这是「Logo 到底能不能被解析」的底线：路径差一点就是静默失效。
    """
    icon = ROOT / LOGO_FNAME
    assert icon.is_file(), (
        f"插件根目录下必须有 {LOGO_FNAME} —— AstrBot 只认这一个文件名"
        "（StarManager.logo_fname）；换成 logo.jpg / logo.svg 都会静默回落到默认图标"
    )
    assert icon.parent == ROOT, "图标必须直接放在插件目录下，不能塞进子目录"
    assert LOGO_FNAME == "logo.png", "框架里的文件名是硬编码的小写 logo.png"


def test_logo_filename_is_lowercase_exact():
    """框架做的是精确路径匹配，大小写必须完全一致（``LOGO.PNG`` 不生效）。"""
    assert not (ROOT / "LOGO.PNG").exists(), "框架只认小写的 logo.png，LOGO.PNG 不会被识别"
    assert (ROOT / LOGO_FNAME).is_file()


def test_no_extra_icon_variants_in_repo_root():
    """根目录只保留 ``logo.png``，避免「改了 logo.svg，实际显示没变」这类错位。

    框架只读 ``logo.png``，多出来的 ``logo.svg`` / ``logo.jpg``
    永远不会被使用，只会让维护者改错文件。
    """
    found = _icon_candidates()
    assert found == [ROOT / LOGO_FNAME], (
        "插件根目录应只有一份图标文件 logo.png；框架只读它，"
        "多余的图标不会生效且会误导维护者：" + ", ".join(p.name for p in found)
    )


def test_logo_is_a_real_png_not_renamed_other_format():
    """``logo.png`` 的**真实内容**必须是 PNG。

    最常见的坏法：把 JPEG / SVG 直接改扩展名。文件名叫 ``logo.png``，
    框架按 PNG 交给前端，浏览器解码失败就是一个破图。
    """
    raw = (ROOT / LOGO_FNAME).read_bytes()

    # 1) 文件头必须是 PNG 签名
    assert raw.startswith(b"\x89PNG\r\n\x1a\n"), (
        "logo.png 的魔数不是 PNG —— 可能是把 JPEG / WEBP 直接改了扩展名"
    )

    # 2) 不能是 SVG 文本伪装
    head = raw[:512].lstrip().lower()
    assert not head.startswith(b"<?xml") and b"<svg" not in head, (
        "logo.png 实际是 SVG 文本，必须导出为真正的 PNG"
    )

    # 3) Pillow 视角二次确认（它会按内容识别格式）
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as probe:
        assert probe.format == "PNG", (
            f"logo.png 的真实格式是 {probe.format}，与文件名不符，会导致显示异常"
        )


def test_logo_png_structure_is_complete():
    """手工校验 PNG 结构：``IHDR`` 在最前、``IEND`` 存在且 CRC 正确。

    比 Pillow 更严格 —— ``Image.open`` 对截断文件有容错，前端解码器没有。
    文件在传输/提交过程中被截断时，这条会先报出来。
    """
    raw = (ROOT / LOGO_FNAME).read_bytes()
    assert len(raw) > 8, "logo.png 过短，不是有效文件"
    assert struct.unpack(">I", raw[8:12])[0] == 13, "IHDR 长度必须为 13"
    assert raw[12:16] == b"IHDR", "PNG 第一个块必须是 IHDR"
    assert raw[-8:-4] == b"IEND", "PNG 缺少 IEND 结束块（文件可能被截断）"
    assert raw[-4:] == b"\xaeB`\x82", "PNG 的 IEND CRC 不正确"


def test_logo_is_decodable_square_and_high_resolution():
    """图标要能被真正解码；正方形；分辨率够清晰，但也不能大到拖慢加载。

    回归（Issue #1「Logo 图标不显示」）：这里原本断言 ``>= 256`` 且**没有上限**，
    于是 1024×1024 的位图被判为「越大越好」。实际影响是商店详情页每次都要
    下载整份 600 KB 文件 —— 图标只显示几十像素，真实症状是「图标一直不显示」。
    """
    from PIL import Image

    with Image.open(ROOT / LOGO_FNAME) as icon:
        width, height = icon.size
        assert icon.format == "PNG"
        assert width == height, f"图标应为正方形，实际 {width}×{height}"
        assert width >= 128, f"图标分辨率过低（{width}×{height}），列表里会糊"
        assert width <= LOGO_MAX_SIDE, (
            f"图标 {width}×{height} 过大 —— 列表里只显示几十像素，"
            f"过大的位图只会拖慢商店详情页与 WebUI 的加载（建议 ≤ {LOGO_MAX_SIDE}）"
        )


def test_logo_background_is_actually_transparent():
    """无背景是作者的明确要求：必须是**真透明**，而不是白底图。

    判断方式：统计真正全透明的像素占比。
    白底图的全透明像素为 0；只把边缘抠一圈抗锯齿像素也不足以通过。
    """
    from PIL import Image

    with Image.open(ROOT / LOGO_FNAME) as icon:
        assert icon.mode in {"RGBA", "LA", "P"}, "图标必须带透明通道（无背景）"
        rgba = icon.convert("RGBA")
        histogram = rgba.getchannel("A").histogram()
        total = sum(histogram)
        transparent_ratio = histogram[0] / total
        assert transparent_ratio >= 0.2, (
            f"全透明像素仅占 {transparent_ratio:.1%}，"
            "看起来仍是白底图而不是透明背景 —— 深色主题下会是一块白斑"
        )


def test_logo_file_size_is_reasonable():
    """不能小到像占位图，也不能大到拖累仓库与商店上传。

    上限从「4 MB」（等于不设防）收紧到 256 KB：图标是会被**整份下载**的资源，
    体积直接决定弱网下「图标能不能及时显示」。
    """
    size = (ROOT / LOGO_FNAME).stat().st_size
    assert size > 4096, "图标过小，可能是占位图或损坏文件"
    assert size <= LOGO_MAX_BYTES, (
        f"图标 {size / 1024:.0f} KB 过大（上限 {LOGO_MAX_BYTES // 1024} KB）—— "
        "图标只需在列表里清晰，过大只会让用户等图片加载"
    )


def test_studio_panel_icon_is_a_real_png_thumb():
    """面板图标必须也是真 PNG + 透明背景，且是插件图标的缩小版。

    ``pages/studio/logo.png`` 走的是 WebUI 静态路由，与插件列表图标
    是两条独立链路 —— 任何一条坏掉用户都会看到破图。
    """
    from PIL import Image

    panel = ROOT / "pages" / "studio" / "logo.png"
    assert panel.is_file(), "缺少 pages/studio/logo.png"
    raw = panel.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n"), "面板图标必须是真正的 PNG（不是改名的 SVG）"

    with Image.open(panel) as image:
        assert image.format == "PNG"
        width, height = image.size
        assert width == height, f"面板图标应为正方形，实际 {width}×{height}"
        assert max(width, height) <= PANEL_LOGO_MAX_SIDE, (
            f"面板图标 {width}×{height} 过大，它只需作为缩略图显示"
        )
        assert image.convert("RGBA").getchannel("A").getextrema()[0] == 0, (
            "面板图标必须无背景（面板有深色主题）"
        )

    plugin_side = Image.open(ROOT / LOGO_FNAME).size[0]
    assert plugin_side > max(width, height), (
        "面板图标应是插件图标的缩小版（两处共用同一份美术资源）"
    )


def test_studio_index_references_an_existing_logo():
    html = (ROOT / "pages" / "studio" / "index.html").read_text(encoding="utf-8")
    assert 'src="./logo.png"' in html, "Studio 页面应引用 ./logo.png"
    assert (ROOT / "pages" / "studio" / "logo.png").is_file(), "页面引用的文件必须存在"


def test_logo_carries_full_opacity_edges_so_it_renders_on_any_theme():
    """图标必须**自带完整的不透明边缘**，换任何背景都不依赖底图的颜色。

    透明 PNG 在解码后是「预乘 alpha」的，边缘半透明像素会与背景混合。
    只要美术资源本身留有亮色 / 白色边缘，深色背景上就会出现白毛刺；
    反之全透明像素里若残留杂色，浅色背景上也会渗色。
    这里用「贴到白底与深色底再比较」的方式，把这类问题变成可测的断言。
    """
    from PIL import Image

    with Image.open(ROOT / LOGO_FNAME) as icon:
        rgba = icon.convert("RGBA")

    assert rgba.getchannel("A").getextrema()[1] > 16, "图标内容为空"

    # 全透明像素的 RGB 必须是 0（否则缩放/解码时可能渗出杂色）
    assert rgba.convert("RGB").getbbox() == rgba.getbbox(), (
        "全透明像素残留了 RGB 值（在只按颜色统计的边界上多出一圈），"
        "缩放或解码时会在浅色背景上渗出杂色"
    )

    def _flatten(background: tuple[int, int, int]) -> Image.Image:
        canvas = Image.new("RGBA", rgba.size, (*background, 255))
        canvas.alpha_composite(rgba)
        return canvas.convert("RGB")

    for background in ((255, 255, 255), (24, 24, 27)):
        flattened = _flatten(background)
        colors = len(flattened.getcolors(maxcolors=1 << 20) or [])
        assert colors > 64, f"图标在背景 {background} 上几乎不可见（颜色数 {colors}）"


def test_install_docs_explain_logo_placement():
    """安装文档要写清图标放哪、叫什么，避免维护者凭猜测放错位置。"""
    install = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
    assert LOGO_FNAME in install, "安装文档要说明图标文件名"
    assert "插件目录根下" in install, "安装文档要强调图标必须放在插件目录根下"


# ---------------------------------------------------------------------------
# 商店元数据（作者信息 / 文案）
# ---------------------------------------------------------------------------
def test_metadata_author_is_the_plugin_author_not_the_platform_account():
    """``author`` 必须是**插件作者**（商店卡片上展示的人），不是平台账号。

    回归（Issue #1）：这里一度是 CNB 账号 ``cosc``，于是商店卡片显示
    「作者：cosc」，而真正的插件作者「科技酱」只出现在 README 里，两处说法打架。

    约束：``plugin_id = author + "/" + name``，改这一项等于换一个插件身份，
    已安装用户会收不到更新 —— 因此只在**尚无存量用户**阶段修正，之后锁死。
    """
    author = str(_metadata()["author"]).strip()
    assert author == PACKAGE_AUTHOR, (
        f"metadata.yaml 的 author 应为插件作者 {PACKAGE_AUTHOR}，实际是 {author!r}"
    )
    assert author.lower() != "cosc", "author 不得是平台账号 cosc"
    # 包身份不能带空格 / 中文，否则各平台解析 plugin_id 的行为不一致
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", author), (
        f"author 作为包身份只能含字母数字与 _.-，实际是 {author!r}"
    )


def test_metadata_author_matches_readme_about_section():
    """metadata 的作者必须与 README「关于作者」一致，不允许两处打架。"""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## 关于作者" in readme, "README 必须有「关于作者」章节"
    section = readme.split("## 关于作者", 1)[1].split("\n## ", 1)[0]

    assert DISPLAY_AUTHOR in section, f"README 关于作者章节必须写明作者「{DISPLAY_AUTHOR}」"
    assert PACKAGE_AUTHOR in section, (
        f"README 关于作者章节必须出现 metadata 的 author（{PACKAGE_AUTHOR}），"
        "让维护者知道商店卡片上显示的是哪个身份"
    )
    for link in (
        "https://docs.asoe.cn",
        "https://github.com/techjiang/",
        "https://space.bilibili.com/1768832152",
        "https://forums.asoe.cn/",
        "291974598",
        "474819022",
    ):
        assert link in section, f"README 关于作者章节缺少作者信息：{link}"


def test_metadata_display_fields_are_present_and_well_formed():
    """市场卡片依赖 display_name / short_desc / desc，字段与长度都必须合规。"""
    metadata = _metadata()
    for field in ("name", "display_name", "short_desc", "desc", "author", "repo", "version"):
        assert field in metadata, f"metadata.yaml 缺少 {field}"
        assert isinstance(metadata[field], str) and metadata[field].strip(), (
            f"{field} 必须是非空字符串"
        )

    display_name = metadata["display_name"]
    assert 1 <= len(display_name) <= 32, "display_name 过长会被市场截断"
    assert "SuperAI" in display_name, "display_name 必须体现插件名"

    short_desc = metadata["short_desc"]
    assert 8 <= len(short_desc) <= 60, f"short_desc 长度不合适（{len(short_desc)} 字，卡片会截断）"

    desc = metadata["desc"]
    assert len(desc) >= 60, "desc 太短，商店详情页会显得空"
    assert "SuperAI" in desc, "desc 应说明这是什么插件"


def test_metadata_tags_are_market_friendly():
    tags = _metadata().get("tags")
    assert isinstance(tags, list) and tags, "tags 必须是非空数组"
    assert all(isinstance(t, str) and t.strip() for t in tags), "tags 不得有空项"
    assert len(tags) == len(set(tags)), "tags 不得重复"
    assert 3 <= len(tags) <= 12, f"标签数量不合适（{len(tags)} 个）"


def test_metadata_links_are_clean_https_urls():
    """商店与框架都会解析这两个 URL，格式必须严格（无 query / fragment / 空格）。"""
    metadata = _metadata()
    for key in ("repo", "social_link"):
        url = str(metadata[key])
        parsed = urlparse(url)
        assert parsed.scheme == "https", f"{key} 必须是 https：{url}"
        assert parsed.hostname, f"{key} 缺少主机名：{url}"
        assert not parsed.query and not parsed.fragment, f"{key} 不得带 query / fragment"
        assert " " not in url, f"{key} 不得包含空格"


def test_metadata_repo_points_to_the_publish_repo():
    """``repo`` 指向 GitHub 发布仓库（官方市场只接受 GitHub 仓库或 ZIP）。"""
    repo = str(_metadata()["repo"]).rstrip("/")
    assert repo.startswith("https://github.com/"), f"repo 必须指向 GitHub：{repo}"
    assert repo.endswith("astrbot-plugin-SuperAI"), f"repo 应指向插件发布仓库：{repo}"
    assert urlparse(repo).path.count("/") == 2, (
        f"repo 应为 https://github.com/<owner>/<repo>：{repo}"
    )


def test_metadata_social_link_is_the_author_site():
    """作者主页要与 README 里的作者官网一致，避免商店索引指向过期地址。"""
    social = str(_metadata()["social_link"])
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert social in readme, f"social_link（{social}）未出现在 README 的作者信息里"


def test_metadata_version_is_plain_semver():
    """商店按语义化版本比较更新；预发布后缀会让更新检测行为不可预期。"""
    version = str(_metadata()["version"])
    assert re.fullmatch(r"v\d+\.\d+\.\d+", version), (
        f"商店发布版本必须是三段式语义化版本且带 v 前缀：{version}"
    )


def test_metadata_yaml_is_plain_and_parseable():
    """metadata.yaml 必须是 UTF-8 + 合法 YAML，且不能用 Tab 缩进、不用 CRLF。"""
    raw = METADATA_PATH.read_bytes()
    assert raw.decode("utf-8")
    assert b"\r\n" not in raw, "metadata.yaml 应使用 LF 换行"
    for lineno, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        assert not line.startswith("\t"), (
            f"metadata.yaml 第 {lineno} 行使用了 Tab 缩进（YAML 非法）"
        )
    assert _metadata(), "metadata.yaml 解析结果为空"
