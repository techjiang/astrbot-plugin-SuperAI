"""SuperAI —— 新一代 AstrBot AI 增强插件。

本包按职责拆分：

- ``core``：配置、日志、通用工具、统计等与 AstrBot 弱耦合的基建。
- ``router``：模型路由（SuperRouter），负责选择 Provider、降级与故障转移。
- ``tools``：SuperAgent 提供给大模型调用的函数工具（function calling）。
- ``storage``：插件持久化（记忆、摘要、用量、工作流等）。
"""

from .version import __version__

__all__ = ["__version__"]
