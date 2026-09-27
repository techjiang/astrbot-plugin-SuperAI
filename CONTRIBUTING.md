# 贡献指南

感谢愿意一起把 SuperAI 做好。本文说明**怎么提问题、怎么改代码、怎么发出去**。

## 反馈问题

提 Issue 前请先看 [常见问题与排错](./docs/faq.md) —— 大部分「插件没反应」
都能在那里定位。

提 Issue 时请带上：

1. **版本**：AstrBot 版本 + SuperAI 版本（`/superai status` 第一行）；
2. **状态输出**：`/superai status` 与 `/superai route` 的完整返回；
3. **日志**：开启 `调试日志` 后的相关片段；
   - ⚠️ **请脱敏**：provider key、token、真实群号/用户 ID；
4. **复现步骤**与**预期行为**；
5. 如果涉及面板 / 指令输出，附截图更快。

## 提交代码

### 1. 分支

从 `main` 切分支，命名遵循：

| 前缀 | 用途 |
| --- | --- |
| `feat/` | 新功能 |
| `fix/` | 缺陷修复 |
| `docs/` | 文档 |
| `chore/` | 构建 / CI / 依赖 |

### 2. 本地自检（提交前必跑）

```bash
ruff check .
ruff format --check .     # 需要格式化时用 ruff format .
python -m pytest tests
```

有条件的话再跑一遍真实框架联调：

```bash
git clone --depth 1 https://github.com/AstrBotDevs/AstrBot /tmp/astrbot-ref
ASTRBOT_REF=/tmp/astrbot-ref python scripts/e2e_smoke.py
```

### 3. 改代码前的必读

👉 [AstrBot 集成契约](./docs/dev/astrbot-contracts.md)

里面每一条都是踩过的坑。尤其注意：

- 插件类与 `@filter` 钩子**必须**留在根 `main.py`；
- LLM 钩子**不能**写成 async generator（不能有 `yield`）；
- 子指令组要用 `@xxx_group.group(...)`，不要用 `filter.command_group`；
- 工具归属改写要在 `add_llm_tools()` **之后**做；
- 所有提前 `return` 的路径都不能泄漏 `_request_started`。

### 4. 提交信息

采用 Conventional Commits 风格（中文描述）：

```
fix: 修复 /superai memory 子指令不可达
feat: 新增 SearXNG 搜索后端
docs: 补充路由档位与降级链说明
chore(ci): 安装运行依赖修复收集失败
```

修复类提交请在正文写清**「为什么原来没有报错」**，
这类「静默失效」问题光看 diff 是看不出来的。

### 5. 测试要求

- 修 Bug → 加一条能复现该 Bug 的回归测试；
- 改契约（钩子形态 / 注册方式 / 生命周期）→ 在 `tests/` 里对应的
  **契约测试**中补断言；
- 新增三方 import → 写进 `requirements.txt`
  （`tests/test_ci_requirements.py` 会校验）；
- 新增配置项 → `_conf_schema.json` + [配置手册](./docs/configuration.md)；
- 新增指令 → [指令手册](./docs/commands.md)。

### 6. PR

- 目标分支 `main`；
- 描述里写清：**改了什么 / 为什么 / 怎么验证的**；
- 有行为变化或破坏性变更时单独标出「⚠️ 升级注意」；
- CI 必须全绿（lint + 单测 + 真实 AstrBot 端到端联调）。

## 文档贡献

文档也是代码的一部分，同样走 PR：

- 使用者文档放 `docs/*.md`；
- 开发者文档放 `docs/dev/*.md`；
- 发布说明归档放 `docs/releases/`；
- 新增文档请在同级 `README` / `docs/README.md` 里加索引链接。

写文档的原则：

1. **先讲「为什么」**，再讲「怎么做」—— 尤其是反直觉的设计；
2. 命令、配置项、路径要能**直接复制粘贴**；
3. 提到代码位置时给出**相对链接**，别只写文件名。

## 发布

维护者见 [发布流程](./docs/dev/release.md)。

## 行为准则

- 讨论对事不对人；
- 不要提交含真实密钥、真实用户数据的内容；
- 不欢迎「一键 AI 生成、未经验证」的大段 PR ——
  本项目大量问题属于框架语义级，必须实际验证过再提。
