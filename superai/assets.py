"""插件自检：图标等「静态美术资源」是否真的能被用户看到。

为什么需要运行时自检 —— 图标是一条**没有任何反馈**的链路：

1. 框架写死 ``StarManager.logo_fname = "logo.png"``，只对插件根目录做一次
   ``os.path.exists``，找不到就静默回落到默认图标，**一行日志都不打**；
2. 找到了也不代表能显示 —— 商店详情页会把这个文件**整份下载**下来，
   体积过大在弱网 / 移动端上就是「图片区域一直空着」；
3. 面板图标（``pages/studio/logo.png``）走的是另一条静态路由，
   坏了同样不会报错。

因此这里做一次**只读、尽力而为**的检查，并把结论写进启动日志：
正常时是几行 info，异常时是明确的 warning，带上具体症状与修复方向。
检查本身绝不抛异常 —— 图标坏了不该阻止插件工作。
"""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path

#: 插件根目录（``superai/`` 的上一级）。
PLUGIN_ROOT = Path(__file__).resolve().parent.parent

#: AstrBot 唯一认可的图标文件名（``StarManager.logo_fname``，写死）。
LOGO_FNAME = "logo.png"

#: 图标边长上限。列表里只显示几十像素，而商店详情页会整份下载这个文件。
LOGO_MAX_SIDE = 512

#: 图标体积上限（字节）。512×512 量化 PNG 约 45 KB。
LOGO_MAX_BYTES = 256 * 1024

#: 面板图标（静态路由，另一条链路）的体积上限。
PANEL_LOGO_MAX_BYTES = 64 * 1024

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_metadata(path: Path) -> dict[str, object] | None:
    """读取 PNG 的宽高与色彩类型；不是合法 PNG 时返回 ``None``。

    不依赖 Pillow —— 插件运行时只有 AstrBot 内置的 aiohttp 可用，
    而这里只需要解析 IHDR 这 13 个字节。
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw.startswith(_PNG_SIGNATURE) or len(raw) < 33:
        return None
    if not _has_iend_chunk(raw):
        return None
    length = struct.unpack(">I", raw[8:12])[0]
    if length != 13 or raw[12:16] != b"IHDR":
        return None
    width, height, depth, color_type = struct.unpack(">IIBB", raw[16:26])
    # color type：6 = RGBA，4 = 灰度 + alpha；3 = 调色板（带 tRNS 时同样支持透明）
    has_alpha = color_type in (4, 6)
    if color_type == 3:
        has_alpha = _has_trns_chunk(raw)
    return {
        "bytes": len(raw),
        "width": width,
        "height": height,
        "bit_depth": depth,
        "has_alpha": has_alpha,
    }


def _has_iend_chunk(raw: bytes) -> bool:
    """确认 PNG 完整（存在 ``IEND`` 结束块）。

    只校验文件头是不够的：传输 / 提交过程被截断的 PNG 依然有合法的
    ``IHDR``，但「文件名叫 logo.png、看起来也有尺寸」并不等于能显示 ——
    框架照文件名交出去，浏览器解码失败就是一个破图。
    所以这里连结束块一起验，成本只有一次字符串查找。
    """
    return raw.rstrip().endswith(b"\xaeB`\x82") and b"IEND" in raw[-16:]


def _has_trns_chunk(raw: bytes) -> bool:
    """扫块，判断调色板 PNG 是否声明了透明色（``tRNS``）。

    ``tRNS`` 必须出现在 ``IDAT`` 之前，因此扫到 ``IDAT`` 就可以停。
    """
    offset = 8
    total = len(raw)
    while offset + 8 <= total:
        length = struct.unpack(">I", raw[offset : offset + 4])[0]
        chunk_type = raw[offset + 4 : offset + 8]
        if chunk_type == b"tRNS":
            return True
        if chunk_type in (b"IDAT", b"IEND"):
            return False
        offset += 12 + length
    return False


def effective_logo_path(plugin_dir: Path | None = None) -> Path | None:
    """复刻框架的查找逻辑：只看插件根目录下的 ``logo.png``。"""
    root = Path(plugin_dir) if plugin_dir else PLUGIN_ROOT
    candidate = root / LOGO_FNAME
    return candidate if candidate.is_file() else None


def inspect_logo(plugin_dir: Path | None = None) -> dict[str, object]:
    """检查插件图标，返回一份「症状 → 说明」的自检报告。

    返回字段：

    - ``path``：框架能解析到的图标路径（``None`` 表示会回落到默认图标）
    - ``ok``：是否通过全部检查
    - ``problems``：问题列表，每项 ``{"symptom", "detail", "fix"}``
    """
    root = Path(plugin_dir) if plugin_dir else PLUGIN_ROOT
    problems: list[dict[str, str]] = []
    report: dict[str, object] = {"path": None, "ok": True, "problems": problems}

    path = effective_logo_path(root)
    if path is None:
        stray = sorted(p.name for p in root.glob("*logo*") if p.is_file())
        problems.append(
            {
                "symptom": "插件列表与商店显示默认图标",
                "detail": f"插件根目录下没有 {LOGO_FNAME}",
                "fix": (
                    f"把图标命名为 {LOGO_FNAME} 直接放在插件根目录"
                    "（框架只认这一个文件名，换名 / 套子目录都不生效）"
                    + (f"；当前目录下发现了这些疑似图标文件：{stray}" if stray else "")
                ),
            }
        )
        report["ok"] = False
        return report

    report["path"] = str(path)
    meta = png_metadata(path)
    if meta is None:
        problems.append(
            {
                "symptom": "图标显示为破图 / 空白",
                "detail": f"{path.name} 不是合法的 PNG（可能是 SVG / JPEG 改了扩展名，或文件被截断）",
                "fix": "用图片工具重新导出为真正的 PNG（保持文件名 logo.png）",
            }
        )
        report["ok"] = False
        return report

    report.update(meta)

    width, height = int(meta["width"]), int(meta["height"])
    if width != height:
        problems.append(
            {
                "symptom": "图标被拉伸变形",
                "detail": f"图标不是正方形（{width}×{height}）",
                "fix": "导出为正方形（推荐 512×512）",
            }
        )
    if max(width, height) > LOGO_MAX_SIDE:
        problems.append(
            {
                "symptom": "弱网 / 移动端上图标长时间不显示（一直转圈）",
                "detail": (
                    f"图标 {width}×{height}、{int(meta['bytes']) / 1024:.0f} KB 过大；"
                    "商店详情页会把这个文件整份下载下来，而它只显示几十像素"
                ),
                "fix": f"缩到 {LOGO_MAX_SIDE}×{LOGO_MAX_SIDE} 并压缩（推荐 256 色 PNG）",
            }
        )
    if int(meta["bytes"]) > LOGO_MAX_BYTES:
        problems.append(
            {
                "symptom": "商店详情页图标加载慢",
                "detail": f"图标体积 {int(meta['bytes']) / 1024:.0f} KB 超过上限 "
                f"{LOGO_MAX_BYTES // 1024} KB",
                "fix": "压缩后重新导出（去背景 + 量化到 256 色）",
            }
        )
    if not meta["has_alpha"]:
        problems.append(
            {
                "symptom": "深色主题下图标是一块白底",
                "detail": "图标不带透明通道（RGB 而非 RGBA）",
                "fix": "导出为带透明背景的 PNG（去掉白底）",
            }
        )

    panel = root / "pages" / "studio" / LOGO_FNAME
    if not panel.is_file():
        problems.append(
            {
                "symptom": "Studio 面板图标不显示",
                "detail": f"缺少 {panel.relative_to(root).as_posix()}（面板走静态路由，与插件列表是两条链路）",
                "fix": "把同一份美术资源缩小后放到 pages/studio/logo.png",
            }
        )
    elif panel.stat().st_size > PANEL_LOGO_MAX_BYTES:
        problems.append(
            {
                "symptom": "Studio 面板打开变慢",
                "detail": f"面板图标 {panel.stat().st_size / 1024:.0f} KB 过大（它是缩略图）",
                "fix": "缩到 128×128 以内",
            }
        )

    report["ok"] = not problems
    return report


#: AstrBot 框架里「插件图标只能用一次」的问题说明。
#: 框架用 ``file_token_service.register_file(logo, timeout=300)`` 签发图标 URL，
#: 而 ``handle_file()`` 内部是 ``staged_files.pop(token)`` —— 取一次即失效。
#: 前端会缓存 ImageList 里的 URL，刷新 / 换设备 / 缓存过期后重新请求就拿到 404，
#: ``ExtensionCard.vue`` 的 ``@error`` 会把图标永久换成默认星形。
FRAMEWORK_LOGO_TOKEN_HINT = (
    "这是 AstrBot 框架侧的问题，不是插件配置问题："
    "插件图标 URL 走的是「一次性令牌」（file_token_service.handle_file 用 pop 消费），"
    "浏览器第一次请求能拿到图，刷新 / 换设备 / 令牌 5 分钟过期后就是 404，"
    "前端于是回落到默认星形图标。"
)


def _run_probe_in_thread(probe) -> bool:
    """在独立线程里跑探测协程（当前线程已有事件循环时使用）。"""
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return bool(pool.submit(lambda: asyncio.run(probe())).result(timeout=10))


def _has_running_loop() -> bool:
    """当前是否已经在一个运行中的事件循环里（决定能不能用 ``asyncio.run``）。"""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def probe_logo_token_service() -> dict[str, object]:
    """探测框架的图标令牌是否「取一次即失效」（只读，不启动服务器）。

    做法：直接拿框架自己的 ``file_token_service`` 注册一份临时文件，
    连着取两次。第二次拿不到 → 命中该问题。

    返回 ``{"available": bool, "single_use": bool, "detail": str}``；
    框架不在位（例如纯单元测试环境）时 ``available=False``，不算问题。
    """
    import tempfile

    try:
        from astrbot.core import file_token_service
    except Exception:
        return {"available": False, "single_use": False, "detail": "框架未安装"}

    async def _probe() -> bool:
        register = file_token_service.register_file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as handle:
            handle.write(_PNG_SIGNATURE + b"probe")
            handle.flush()
            # 复刻 plugin_service.get_plugin_logo_token 的调用方式。
            # 不靠 inspect 判断签名 —— 支持 **kwargs 的框架/替身会看不出来，
            # 直接按「能不能真的重复读」来判断行为。
            try:
                token = await register(handle.name, timeout=30, reusable=True)
            except TypeError:
                # 老框架压根不接受 reusable → 图标必然是一次性令牌
                return True
            for _ in range(2):
                try:
                    await file_token_service.handle_file(token)
                except KeyError:
                    return True  # 接受了 reusable 却仍取不到第二次 → 修复没生效
        return False

    if not _has_running_loop():
        try:
            single_use = asyncio.run(_probe())
        except Exception as exc:  # pragma: no cover - 探测失败不应影响启动
            return {"available": True, "single_use": False, "detail": f"探测失败：{exc}"}
    else:
        # 已经身处事件循环（插件初始化在异步上下文里跑），不能 asyncio.run
        # （会抛 RuntimeError 并留下未 await 的协程）。改在**独立线程**里
        # 起一个临时 loop，探测逻辑本身不碰插件状态，跨线程是安全的。
        try:
            single_use = _run_probe_in_thread(_probe)
        except Exception as exc:  # pragma: no cover - 探测失败不应影响启动
            return {"available": True, "single_use": False, "detail": f"探测失败：{exc}"}

    return {
        "available": True,
        "single_use": single_use,
        "detail": FRAMEWORK_LOGO_TOKEN_HINT if single_use else "图标令牌可重复读取",
    }


def format_logo_token_report(probe: dict[str, object]) -> tuple[str, str] | None:
    """把令牌探测结果转成日志；没命中问题（或框架不在位）时返回 ``None``。"""
    if not probe.get("available") or not probe.get("single_use"):
        return None
    return (
        "warning",
        "[SuperAI] 检测到框架图标令牌「一次性」问题（插件功能不受影响）：\n"
        "  症状：刚装完第一次打开图标正常，刷新页面 / 重新进入 / 换设备后变成默认星形图标\n"
        f"  原因：{FRAMEWORK_LOGO_TOKEN_HINT}\n"
        "  修复：升级 AstrBot 到已修复该问题的版本（file_token_service 支持 reusable 令牌）；\n"
        "        或临时用 Studio 面板查看状态 —— 面板图标走静态路由，不受影响。",
    )


def format_logo_report(report: dict[str, object]) -> tuple[str, str]:
    """把自检报告转成 ``(级别, 日志文本)``。"""
    problems = report.get("problems") or []
    if not problems:
        return (
            "info",
            f"[SuperAI] 图标自检通过 | {report.get('path')} "
            f"{report.get('width')}×{report.get('height')} "
            f"{int(report.get('bytes', 0)) / 1024:.0f} KB",
        )

    lines = ["[SuperAI] 图标自检发现问题（不影响插件功能，但用户会看到异常）："]
    for index, problem in enumerate(problems, 1):
        lines.append(f"  {index}. 症状：{problem['symptom']}")
        lines.append(f"     原因：{problem['detail']}")
        lines.append(f"     修复：{problem['fix']}")
    lines.append("  详见 docs/install.md「图标不被识别 / 不显示」一节。")
    return "warning", "\n".join(lines)
