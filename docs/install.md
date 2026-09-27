# 安装与升级

## 环境要求

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| AstrBot | >= 4.5.7 | 插件使用 `llm_generate`、`tool_loop_agent`、`register_web_api` 等 API |
| Python | 跟随 AstrBot（>= 3.12） | 插件本身不做额外约束 |
| 运行依赖 | `aiohttp` | AstrBot 已内置；单独跑测试时才需手动安装 |
| 平台 | aiocqhttp / qq_official / telegram / wecom / lark / dingtalk / discord / slack / kook | 见 `metadata.yaml` |

## 安装方式一：插件市场（推荐）

1. 打开 AstrBot WebUI → 插件市场；
2. 搜索 `SuperAI`；
3. 点击安装，然后**重启 AstrBot** 或点「重载插件」。

## 安装方式二：手动克隆

```bash
cd AstrBot/data/plugins
git clone https://cnb.cool/asoe/TechSauce/astrbot-plugin-SuperAI.git astrbot_plugin_superai
```

重启 AstrBot，或在插件页点「重载插件」。

> **不要把仓库内容再套一层目录**。正确结构是
> `data/plugins/astrbot_plugin_superai/main.py`，
> 而不是 `data/plugins/astrbot_plugin_superai/astrbot-plugin-SuperAI/main.py`。

### 如果你习惯用符号链接

⚠️ `main.py` 如果是指向仓库外部的软链接，AstrBot 的加载器有概率跳过，
表现为「插件列表里有、但什么效果都没有」。要联调开发请用
[开发与测试](./dev/development.md) 里的 `ASTRBOT_PLUGINS_PATH` 方案，
或直接 `cp -r` 一份进 `data/plugins/`。

## 安装后必做检查

1. **插件列表**：出现 `SuperAI`，图标为仓库根目录的 `logo.png`；
2. **日志**：出现 `[SuperAI] vX.Y.Z 已加载 | 路由=开 记忆=开 ...`，
   且**没有**下面这类行：
   - `Plugin astrbot_plugin_superai has neither main.py nor astrbot_plugin_superai.py; skipping it.`
   - `TypeError: SuperAIPlugin.on_llm_request() missing 1 required positional argument`
3. **指令**：群里发 `/superai status`，应返回一行状态与今日用量。

任何一条不满足，先看 [常见问题与排错](./faq.md)。

## 数据目录

插件把运行数据写到 AstrBot 的 data 目录（遵循官方「持久化数据放 data 目录」原则）：

```
data/plugin_data/astrbot_plugin_superai/
├── memory.json      # 长期记忆（按会话分组）
├── summary.json     # 会话滚动摘要
├── workflows.json   # 工作流定义与最近运行记录
├── metrics.json     # 用量统计（按天聚合）
└── router.json      # 模型健康度（失败计数）
```

删除整个目录 = 清空所有记忆与统计。建议升级前备份。

## 升级

```bash
cd AstrBot/data/plugins/astrbot_plugin_superai
git pull
```

然后重启 AstrBot 或重载插件。升级不会动 `data/plugin_data/` 下的数据。

升级后请对照 [CHANGELOG.md](../CHANGELOG.md) 检查是否有**配置项改名**或
**行为变化**（例如 v0.2.3 修复了 `/superai memory` 子指令不可达的问题，
此前写的兼容命令已被移除）。

## 卸载

1. WebUI 插件页停用并卸载 SuperAI；
2. 如需彻底清理，删除 `data/plugin_data/astrbot_plugin_superai/`。

停用插件时，SuperAI 注册的 LLM 工具会**一起停用**（工具归属被改写成插件入口
模块路径，详见 [AstrBot 集成契约](./dev/astrbot-contracts.md)）。

## 下一步

- [快速上手](./quickstart.md)
- [配置手册](./configuration.md)
