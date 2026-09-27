# 上游问题材料

本目录存放**提交给 AstrBot 上游**的问题材料（我们这边修不了、但会影响用户的部分）。

## `astrbot-logo-token-fix.patch` — 插件图标「只能用一次」

**症状**：插件图标第一次显示正常，刷新页面 / 重新进入 / 换设备后变成默认星形图标。

**根因**：图标 URL 走的是 `file_token_service` 的**一次性令牌**。

| 位置 | 代码 | 问题 |
| --- | --- | --- |
| `dashboard/services/plugin_service.py` | `_logo_cache` 复用同一 token | 认为 token 长期有效 |
| `core/file_token_service.py` | `staged_files.pop(file_token)` | 取一次即删 |
| `core/file_token_service.py` | `check_token_expired()` | 只看过期，看不出「已被取走」 |

第一次请求 200，之后 404；叠加 `timeout=300`，静置 5 分钟也会失效。
前端 `ExtensionCard.vue` 的 `@error` 会把图标**永久**换成默认星形。

**修复思路**（patch 已含）：

1. `FileTokenService` 新增「可重复令牌」：`register_file(..., reusable=False)`，
   `reusable=True` 时 `handle_file()` 不 `pop`，过期回收时同步清掉标记。
   **默认值仍是单次令牌** —— 聊天里的文件链接「取一次即失效」有安全意义，
   不能顺手改默认行为。
2. `plugin_service.get_plugin_logo_token()` 改用 `reusable=True`，
   TTL 从 300s 提到 24h（图标是静态资源）。
3. 补 3 项回归测试：可重复读取 / 默认仍是单次 / 过期回收不泄漏。

**验证**（在真实 AstrBot 源码上）：

```
修复前：图标令牌连续请求 5 次 -> [200, 404, 404, 404, 404]
修复后：图标令牌连续请求 5 次 -> [200, 200, 200, 200, 200]
修复后：默认令牌语义保持「取一次即失效」✅
tests/test_media_utils.py -k file_token -> 4 passed
tests（全量）-> 3587 passed（2 项与本改动无关的既有环境失败）
```

**应用方式**：在 AstrBot 仓库根目录 `git apply astrbot-logo-token-fix.patch`。

## 我们这边的兜底

框架渲染的列表卡片插件改不了，所以本插件的做法是**把根因写进日志**：
`superai/assets.py::probe_logo_token_service()` 在启动时探测令牌语义，
命中就输出带「症状 / 原因 / 修复」的 warning。
验收见 `tests/test_logo_token_probe.py` 与 `scripts/e2e_smoke.py`。

**最后核对**：`v0.2.10`（逐条对照真实 AstrBot 源码与测试断言，无凭空描述）
