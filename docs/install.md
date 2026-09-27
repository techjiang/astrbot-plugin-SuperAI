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

⚠️ 不建议把 `data/plugins/astrbot_plugin_superai` 做成软链接：
加载器只按**名字**去找 `main.py` / `<dirname>.py`，
路径解析的任何偏差都会让它判定「没有入口」并静默跳过，
表现为「插件列表里有、但什么效果都没有」。

联调开发推荐两种做法（见 [开发与测试](./dev/development.md)）：

1. 把仓库目录当成插件目录 —— 直接在仓库根跑 `python scripts/e2e_smoke.py`，
   它复刻了框架的发现与绑定逻辑；
2. 需要跟真实 AstrBot 一起跑时，用 `cp -r` 复制一份进 `data/plugins/`
   （或让 AstrBot 的插件目录直接指向你的开发副本）。

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

## 发布到 AstrBot 官方插件市场

官方发布入口：<https://cloud.astrbot.app/publish>（需注册 AstrBot Cloud 账号）。

该入口只支持两种来源：

1. **GitHub 仓库** —— 通过 GitHub App 授权，平台直接读取仓库里的
   `metadata.yaml`；
2. **ZIP 压缩包上传**。

### 双仓库结构

本项目的**开发主仓库在 CNB**，**发布镜像在 GitHub**：

| 角色 | 地址 | 用途 |
| --- | --- | --- |
| 主仓库 | `https://cnb.cool/asoe/TechSauce/astrbot-plugin-SuperAI` | 开发、CI、Release、Issue |
| 发布镜像 | `https://github.com/techjiang/astrbot-plugin-SuperAI` | 官方商店提交、GitHub 用户安装 |

`metadata.yaml` 的 `repo` 指向 **GitHub 镜像** —— 这是被官方商店接受的形式。
两个仓库内容完全一致（镜像由 `scripts/sync_github.sh` 或 `.cnb.yml` 的
`mirror-github` 阶段同步，见 [发布流程](./dev/release.md)）。

### 上架步骤

1. 确认 GitHub 镜像已同步到最新发布提交；
2. 在 <https://cloud.astrbot.app/publish> 选择「GitHub」来源并授权，
   平台会自动解析 `metadata.yaml`；
3. 若选择「ZIP 上传」，直接用 CNB Release 里附带的
   `astrbot_plugin_superai-v<版本>.zip`（每次打 tag 自动构建）。

> GitHub 镜像不可达时，也可以临时用 ZIP 上传完成上架 —— 两条通道等价。

### 发布字段一览

`metadata.yaml` 的发布字段均已就绪：

| 字段 | 值 | 说明 |
| --- | --- | --- |
| `name` | `astrbot_plugin_superai` | 插件包名，`plugin_id` 的组成部分 |
| `display_name` | `SuperAI` | 市场展示名 |
| `short_desc` | 一句话简介 | 市场卡片文案 |
| `version` | 与 `superai/version.py` 一致 | 更新检测依据 |
| `author` | `cosc` | **稳定的包身份**，不可改为展示名 |
| `repo` | GitHub 镜像地址 | 官方商店读取与更新检测入口 |
| `social_link` | `https://docs.asoe.cn` | 作者主页，进入市场索引 |
| `tags` | 8 个标签 | 市场搜索与分类依据 |
| `astrbot_version` | `>=4.5.7` | PEP 440 范围 |
| `support_platforms` | 9 个平台 | 必须是官方 `ADAPTER_NAME_2_TYPE` 的 key |

> `author` 与 `name` 共同构成 `plugin_id = author/name`，是插件在市场中的
> **全局唯一标识**，也是已安装插件匹配更新的依据。改动它会导致老用户
> 无法收到更新 —— `tests/test_repo_health.py` 有断言守住这一点。

---

**最后核对**：`v0.2.5`（逐条对照 `installation` / 发布要求与测试断言，无凭空描述）
