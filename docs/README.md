# SuperAI 文档导航

按「我是谁、我想干什么」挑一篇读即可。

## 我只是想用起来

| 文档 | 内容 |
| --- | --- |
| [安装与升级](./install.md) | 插件市场 / 手动安装、依赖与版本要求、升级与卸载、目录结构（含链接 `main.py` 的坑） |
| [快速上手](./quickstart.md) | 10 分钟跑通：装好、配一个模型、发第一句话 | 
| [配置手册](./configuration.md) | `_conf_schema.json` 每一个配置项的作用、默认值、推荐值 |
| [指令手册](./commands.md) | `/ai`、`/superai ...` 全部指令与输出示例 |
| [Studio 面板](./studio.md) | WebUI 面板的每个区块怎么看 |
| [常见问题与排错](./faq.md) | 「插件装了但没反应」「一直走同一个模型」「摘要不生成」等 |

## 我想把能力用透

| 文档 | 内容 |
| --- | --- |
| [模型路由 SuperRouter](./routing.md) | 五档位、四策略、降级链、健康度、关键词自定义 |
| [记忆与摘要 SuperMemory](./memory.md) | 滚动摘要、长期记忆抽取、注入、衰减、数据文件 |
| [工具与工作流 SuperAgent](./tools-and-workflows.md) | 七个内置工具的参数、工作流语法与示例 |
| [用量统计与成本控制](./usage-and-budget.md) | 统计口径、配额拦截、数据落盘、如何省钱 |

## 我要改代码

| 文档 | 内容 |
| --- | --- |
| [架构总览](./dev/architecture.md) | 模块职责、一次请求的完整生命周期、数据流 |
| [开发与测试](./dev/development.md) | 本地环境、测试分层、真实 AstrBot 联调、CI 流水线 |
| [AstrBot 集成契约](./dev/astrbot-contracts.md) | 与框架交互的所有硬约束与踩坑记录（改代码前必读） |
| [发布流程](./dev/release.md) | 版本号、CHANGELOG、打 Release、发布检查清单 |

## 其他

- [贡献指南](../CONTRIBUTING.md)
- [安全策略](../SECURITY.md)
- [更新日志](../CHANGELOG.md)
- [发布说明索引](./releases/README.md)

> 文档与代码不同步时，**以代码为准**，并欢迎提 Issue 指出。
> `tests/test_docs_consistency.py` 会校验文档里的指令名与配置项是否真实存在。
