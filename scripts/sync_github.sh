#!/usr/bin/env bash
# 把当前仓库（CNB 主仓库）镜像同步到 GitHub 发布仓库。
#
# 为什么需要它：AstrBot 官方插件市场只接受 GitHub 仓库或 ZIP 包，
# metadata.yaml 的 repo 必须指向 GitHub 地址才能被商店读取。
# 因此 CNB 仍是开发主仓库，GitHub 作为发布镜像，每次发布同步一次。
#
# 用法：
#   GITHUB_TOKEN=<PAT> ./scripts/sync_github.sh
#
# 需要在 GitHub 侧设置的环境变量：
#   GITHUB_TOKEN  具备 repo（或 contents:write）权限的令牌，勿提交到仓库；
#   GITHUB_REPO   默认 astrbot-plugin-SuperAI；
#   GITHUB_OWNER  默认 techjiang。
set -euo pipefail

GITHUB_OWNER="${GITHUB_OWNER:-techjiang}"
GITHUB_REPO="${GITHUB_REPO:-astrbot-plugin-SuperAI}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

if [ -z "$GITHUB_TOKEN" ]; then
    echo "缺少 GITHUB_TOKEN —— 请在仓库环境变量中配置具备 contents:write 权限的令牌。" >&2
    echo "本次跳过镜像同步（CNB 主仓库与 Release 不受影响）。" >&2
    exit 0
fi

REMOTE="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_OWNER}/${GITHUB_REPO}.git"

# 只镜像发布基线分支与全部标签；GitHub 仓库是只读镜像，
# 不接受直接写入，避免两边历史分叉。
git push --force "$REMOTE" "refs/heads/main:refs/heads/main"
git push --force --tags "$REMOTE"

echo "已同步 main 与标签到 https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}"
