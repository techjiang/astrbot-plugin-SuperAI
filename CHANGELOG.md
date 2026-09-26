# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## v0.1.0 (首个版本)

### 新增

- **SuperRouter 多模型路由**：支持 `cheap` / `strong` / `reasoning` / `vision` /
  `long_context` 五个档位，提供 `rule` / `auto` / `cheap_first` / `quality_first`
  四种策略，并支持自定义关键词映射。
- **失败自动降级**：按降级链重试，结合 provider 健康度排序，主模型失败时自动切换。
- **SuperMemory 记忆系统**：滚动摘要（降低 token 消耗）+ 长期记忆自动抽取与相关性注入 +
  记忆权重衰减清理。
- **SuperAgent 工具集**：联网搜索、网页抓取、知识库检索、记忆读写、工作流触发。
- **工作流编排**：配置化多步 AI 任务，支持 `{{prev}}` / `{{input}}` / `{{step}}` 占位符。
- **用量统计与预算**：按天记录 token / 耗时 / 路由分布，支持每日 token 与请求数上限。
- **AI 指令**：`/ai` 快捷问答与 `/superai` 管理指令组（status / stats / memory / route /
  tools / workflow / help）。
- **SuperAI Studio**：WebUI 插件页面，查看状态、统计、工具与会话记忆。
- **持久化**：记忆、摘要、工作流运行记录、统计数据均落盘到
  `data/plugin_data/astrbot_plugin_superai/`。
- **国际化**：`zh-CN` / `en-US` 插件与页面文案。
- **测试与流水线**：73 个单元/冒烟测试（含 CI 依赖完整性校验），ruff 风格检查，
  CNB 流水线显式安装 `requirements.txt` 与 `requirements-dev.txt`。
