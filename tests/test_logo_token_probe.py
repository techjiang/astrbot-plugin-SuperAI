"""插件图标「刷新后变默认星形」的框架侧根因探测（``probe_logo_token_service``）。

Issue #1 的最后一轮反馈：图标**第一次能显示，刷新后就没了**。

根因不在本插件，而在 AstrBot 框架的图标分发链路：

1. ``plugin_service.get_plugin_logo_token()`` 用
   ``file_token_service.register_file(logo_path, timeout=300)`` 签发图标 URL；
2. 而 ``file_token_service.handle_file()`` 内部是
   ``file_path, _ = self.staged_files.pop(file_token)`` —— **取一次即失效**；
3. 前端 ``ExtensionCard.vue`` 只在 ``@error`` 时回落到默认图标。

于是图标只在「第一次请求」时可见，之后（刷新 / 换设备 / 令牌 5 分钟过期）
全是 404，卡片永久变成默认星形。

框架渲染的列表卡片插件改不了，但**至少要让日志一眼指出根因** ——
这条链路原本是全静默的。本文件守住这个探测：能认出问题、不误报、
且在任何上下文（同步 / 异步）下都不会抛异常影响插件启动。
"""

from __future__ import annotations

import asyncio
import sys
import types

from superai.assets import (
    FRAMEWORK_LOGO_TOKEN_HINT,
    format_logo_token_report,
    probe_logo_token_service,
)


def _install_fake_astrbot(monkeypatch, *, reusable: bool, single_use: bool = True):
    """注入一个可控的 ``astrbot.core.file_token_service`` 替身。

    ``reusable`` 表示框架是否支持可重复令牌（对应上游修复后的签名）；
    ``single_use`` 表示令牌是否仍然取一次就失效（对应修复没真正生效）。
    """
    module = types.ModuleType("astrbot.core.file_token_service")
    used: set[str] = set()

    class _Service:
        async def register_file(self, path, timeout=None, **kwargs):
            assert kwargs.get("reusable") is True or not reusable
            return f"token:{path}"

        async def handle_file(self, token):
            if token in used and (single_use or not reusable):
                raise KeyError(f"Invalid or expired file token: {token}")
            used.add(token)
            return token.split(":", 1)[1]

    svc = _Service()
    if not reusable:
        # 老签名：没有 reusable 形参
        async def _register(path, timeout=None):
            return await svc.register_file(path, timeout)

        svc.register_file = _register

    module.file_token_service = svc

    astrbot = types.ModuleType("astrbot")
    core = types.ModuleType("astrbot.core")
    core.file_token_service = svc
    astrbot.core = core
    monkeypatch.setitem(sys.modules, "astrbot", astrbot)
    monkeypatch.setitem(sys.modules, "astrbot.core", core)
    monkeypatch.setitem(sys.modules, "astrbot.core.file_token_service", module)
    return svc


def test_probe_detects_single_use_token(monkeypatch):
    """老框架（无 reusable）→ 必须报出问题。"""
    _install_fake_astrbot(monkeypatch, reusable=False)
    result = probe_logo_token_service()
    assert result["available"] is True
    assert result["single_use"] is True
    assert result["detail"] == FRAMEWORK_LOGO_TOKEN_HINT


def test_probe_detects_broken_fix(monkeypatch):
    """框架声明了 reusable，但实际上仍取一次就失效 → 也要报出来。"""
    _install_fake_astrbot(monkeypatch, reusable=True, single_use=True)
    result = probe_logo_token_service()
    assert result["single_use"] is True


def test_probe_is_silent_on_fixed_framework(monkeypatch):
    """真正修好的框架不得误报。"""
    _install_fake_astrbot(monkeypatch, reusable=True, single_use=False)
    result = probe_logo_token_service()
    assert result["available"] is True
    assert result["single_use"] is False
    assert format_logo_token_report(result) is None


def test_probe_is_silent_when_import_fails(monkeypatch):
    """``astrbot`` 导入失败（纯单元测试环境）→ 视为不可用，不报问题、不抛异常。"""
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "astrbot" or name.startswith("astrbot."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    result = probe_logo_token_service()
    assert result["available"] is False
    assert result["single_use"] is False
    assert format_logo_token_report(result) is None


def test_probe_never_raises_on_broken_service(monkeypatch):
    """框架内部炸了（比如锁被占用）→ 探测失败也要优雅降级。"""
    module = types.ModuleType("astrbot.core.file_token_service")

    class _Broken:
        async def register_file(self, *a, **k):
            raise RuntimeError("boom")

    module.file_token_service = _Broken()
    astrbot = types.ModuleType("astrbot")
    core = types.ModuleType("astrbot.core")
    core.file_token_service = module.file_token_service
    astrbot.core = core
    monkeypatch.setitem(sys.modules, "astrbot", astrbot)
    monkeypatch.setitem(sys.modules, "astrbot.core", core)
    monkeypatch.setitem(sys.modules, "astrbot.core.file_token_service", module)

    result = probe_logo_token_service()
    assert result["available"] is True
    assert result["single_use"] is False
    assert "探测失败" in str(result["detail"])


def test_probe_works_inside_running_event_loop(monkeypatch):
    """插件初始化在异步上下文里跑，探测必须在那里同样有效（不能只是不崩）。"""
    _install_fake_astrbot(monkeypatch, reusable=False)

    async def main():
        return probe_logo_token_service()

    result = asyncio.run(main())
    assert result["single_use"] is True, "异步上下文里也必须能探测出问题"


def test_probe_does_not_leak_a_running_loop(monkeypatch):
    """探测不得干扰调用方的事件循环。"""
    _install_fake_astrbot(monkeypatch, reusable=False)

    async def main():
        loop = asyncio.get_running_loop()
        probe_logo_token_service()
        assert asyncio.get_running_loop() is loop
        return True

    assert asyncio.run(main()) is True


def test_report_message_names_symptom_and_fix(monkeypatch):
    """告警文案必须同时给出「症状」与「怎么修」，否则用户拿不到行动项。"""
    _install_fake_astrbot(monkeypatch, reusable=False)
    report = format_logo_token_report(probe_logo_token_service())
    assert report is not None
    level, message = report
    assert level == "warning"
    assert "症状" in message
    assert "原因" in message
    assert "修复" in message
    assert "刷新" in message
