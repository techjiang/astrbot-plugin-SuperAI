"""用于单元测试的最小 AstrBot 替身（stub）。

真实运行环境由 AstrBot 提供 ``astrbot`` 包；本地跑测试时用它来隔离
对框架的依赖，只保留插件纯逻辑所需的接口。
"""

import logging

logger = logging.getLogger("astrbot")
