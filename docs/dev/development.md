# 开发与测试

## 环境准备

```bash
git clone https://cnb.cool/asoe/TechSauce/astrbot-plugin-SuperAI.git
cd astrbot-plugin-SuperAI

# 运行依赖（AstrBot 已内置 aiohttp；单独跑测试时需要）
pip install -r requirements.txt
# 开发 / CI 依赖（ruff + pytest + pyyaml + Pillow）
pip install -r requirements-dev.txt
```

需要 Python >= 3.12（与 AstrBot 本体一致）。

## 常用命令

```bash
ruff check .              # 静态检查
ruff format --check .     # 格式检查（提交前建议跑 ruff format .）
python -m pytest tests    # 单元测试
ASTRBOT_REF=/tmp/astrbot-ref python scripts/e2e_smoke.py   # 真实框架联调
```

## 测试分层

SuperAI 的测试分成四层，各自守不同的东西：

### 1. 纯逻辑单元测试（无框架）

`tests/test_config.py`、`test_router.py`、`test_storage.py`、`test_metrics.py`、
`test_utils.py`、`test_prompt.py`、`test_web_tools.py`、`test_tool_registry.py`
—— 直接测 `superai/` 下的模块，用 `tests/stubs/astrbot/` 作为最小替身。

### 2. 契约测试（防「测试绿、线上死」）

| 文件 | 固定住的约束 |
| --- | --- |
| `test_plugin_loader_contract.py` | 入口必须在根目录、插件类必须定义在入口模块、钩子签名必须是 `(self, event, payload)`、入口不能有相对导入 |
| `test_llm_hook_contract.py` | 用 AST 扫描钩子里**不能有 `yield`**（否则变成 async generator，被框架 `await` 时抛错且被吞掉） |
| `test_command_and_tool_contract.py` | `/superai memory <子指令>` 命中的是真子指令、`superai` 组下无同名 `memory` 命令、工具归属模块被正确改写 |
| `test_request_lifecycle.py` | 三条提前返回路径都不得泄漏「请求起始时间」 |

这类 bug 的特点是**单测全绿、日志无异常、功能静默失效**，所以必须单独设防。

### 3. 仓库健康检查

`test_repo_health.py`：`metadata.yaml` 与 `_conf_schema.json` 一致性、
i18n 文件键一致、Studio 页面不裸插动态值（XSS）、logo 透明通道、
声明的 `pages` 目录真实存在、生产代码不依赖 `tests/stubs`。

`test_ci_requirements.py`：`superai/` 里 import 的三方包必须声明在 `requirements.txt`，
且 `.cnb.yml` 必须安装两个 requirements 文件。

### 4. 真实 AstrBot 联调

`tests/test_plugin_smoke.py` 与 `scripts/e2e_smoke.py` 在**真实 AstrBot** 上跑：

```bash
git clone --depth 1 https://github.com/AstrBotDevs/AstrBot /tmp/astrbot-ref
python -m pytest tests                                    # 自动切换真实框架
ASTRBOT_REF=/tmp/astrbot-ref python scripts/e2e_smoke.py  # 单独跑联调
```

联调脚本做的事（与框架完全一致）：

1. 复刻 `PluginManager` 的插件发现逻辑，断言入口能被找到；
2. 用真 `StarManager` 加载插件、注册处理器、绑定 `self`；
3. 用真 `call_event_hook` 调 `on_llm_request` / `on_llm_response`；
4. 断言路由生效、用量落盘、Studio API 可用。

## stub 替身的使用边界

- `tests/stubs/astrbot/` 是**最小替身**，只实现测试需要的接口；
- 替身必须与真实实现**签名一致**（例如真实 `LLMResponse` 是手写 `__init__`
  而非 dataclass，替身也必须是手写）；
- 生产代码**不允许** import 任何 `tests.stubs`（有测试守着）。

`conftest.py` 的 `pytest_configure` 决定用替身还是真实框架：
如果 `ASTRBOT_REF`（默认 `/tmp/astrbot-ref`）下存在 AstrBot 源码，就用真实框架，
并把 stub 从 `sys.path` 移除。

> 这个切换**必须发生在任何测试模块被导入之前**，否则同一进程里会同时存在
> 真实 AstrBot 与 stub，可能出现 sqlmodel 重复注册表之类的收集期报错。

## CI 流水线

`.cnb.yml`（push 与 pull_request 均触发）共四个 Stage：

1. **安装依赖**：`pip install -r requirements.txt -r requirements-dev.txt`
2. **代码风格检查**：`ruff check .` + `ruff format --check .`
3. **单元测试**：`python -m pytest tests`
4. **真实 AstrBot 端到端联调**：
   `git clone AstrBot` → 装 AstrBot 依赖 → `ASTRBOT_REF=... python scripts/e2e_smoke.py`

镜像由 `.ci/Dockerfile` 构建（`python:3.12-slim` + `git` + `ca-certificates`，
预装两份 requirements），避免每个 Stage 重复安装。

`docker.build` 的子字段只有 `dockerfile / target / by / versionBy / buildArgs`，
**不支持 `context`**（构建上下文固定为仓库根目录）。写成
`docker.image.dockerfile` 会让 Prepare 阶段报
`docker pull failed: image: [object Object] is not allow.`

## 代码风格

- `ruff.toml`：line-length 100，target py310；
- 中文注释与 docstring 是**刻意保留**的：本项目的坑大多来自框架语义，
  不写清楚原因后人会重新踩一遍；
- 类型注解尽量补全，`from __future__ import annotations` 开头。

## 改代码前的必读

[**AstrBot 集成契约**](./astrbot-contracts.md) —— 里面每一条都是踩过的坑，
违反任意一条都会导致「测试全绿但线上静默失效」。

## 相关文档

- [架构总览](./architecture.md)
- [发布流程](./release.md)

---

**最后核对**：`v0.2.4`（逐条对照 `.cnb.yml` 与测试断言，无凭空描述）
