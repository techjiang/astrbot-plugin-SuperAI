# CI 构建镜像：Python 3.12 + 依赖预装
#
# 为什么不用 python:3.12-slim + Stage 内 apt-get install git：
# 1) python:3.12-slim 不含 git，端到端联调 Stage 需要 `git clone` AstrBot 源码；
# 2) 每个 Stage 都装一遍系统包既慢又受网络波动影响，放到镜像里只装一次。
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 预装运行依赖与开发依赖，Stage 内即可直接跑 ruff / pytest / 联调脚本
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
