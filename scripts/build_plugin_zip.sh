#!/usr/bin/env bash
# 打包插件 ZIP —— 用于 AstrBot 官方市场的「ZIP 上传」通道，
# 以及手动安装（解压到 data/plugins/ 即可）。
#
# 产物：dist/astrbot_plugin_superai-<版本>.zip
# 包内结构：astrbot_plugin_superai/{main.py, metadata.yaml, superai/, ...}
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c 'import re,pathlib;print(re.search(r"__version__ = \"([^\"]+)\"", pathlib.Path("superai/version.py").read_text(encoding="utf-8")).group(1))')"
OUT_DIR="dist"
NAME="astrbot_plugin_superai-v${VERSION}"
ZIP="${OUT_DIR}/${NAME}.zip"

rm -rf "$OUT_DIR/${NAME}" "$ZIP"
mkdir -p "$OUT_DIR"

# 只收插件运行必需的文件。
#
# ⚠️ 打包来源必须是 **git 跟踪的文件**，而不是工作区快照。
#
# 为什么：``data/``、``shots/``、``mockllm/`` 都被 .gitignore 忽略，不属于
# 发布内容 —— 但只要维护者在打包前本地跑过一次插件或端到端联调，工作区里就会
# 留下 ``data/cmd_config.json``（内含 dashboard 密码哈希）与 ``data/data_v4.db``。
# 早期版本直接 ``tar`` 整个工作区，于是这些东西被打进了发到市场的 ZIP：
# 既泄露本机配置，又给用户塞了几百 KB 无用文件。
# 依赖"记得加 --exclude"是不可靠的（下次新增运行时目录就会重演），
# 因此改为先问 git 要清单，只把**已提交**的文件复制进临时目录。
INCLUDE_EXTRA_RE='^(main\.py|metadata\.yaml|logo\.png|_conf_schema\.json|requirements\.txt|LICENSE|README\.md)$'
INCLUDE_PREFIX_RE='^(\.astrbot-plugin/|pages/|superai/)'

STAGE="$OUT_DIR/.stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/astrbot_plugin_superai"

copied=0
while IFS= read -r file; do
    case "$file" in
        tests/*|docs/*|scripts/*|conftest.py|pytest.ini|ruff.toml|requirements-dev.txt|.cnb.yml|.ci/*|CHANGELOG.md|CONTRIBUTING.md|SECURITY.md|.gitignore)
            continue
            ;;
    esac
    if [[ "$file" =~ $INCLUDE_EXTRA_RE ]] || [[ "$file" =~ $INCLUDE_PREFIX_RE ]]; then
        mkdir -p "$STAGE/astrbot_plugin_superai/$(dirname "$file")"
        cp -p "$file" "$STAGE/astrbot_plugin_superai/$file"
        copied=$((copied + 1))
    fi
done < <(git ls-files)

# 兜底校验：一个都没有文件被复制，说明 git 清单或过滤规则坏了，必须显性失败
if [ "$copied" -lt 10 ]; then
    echo "打包失败：只收集到 $copied 个文件，git 清单或过滤规则可能有问题。" >&2
    exit 1
fi

# 关键自检：发布包绝不能包含运行时目录 / 数据库 / 配置
for forbidden in 'data/' 'shots/' 'mockllm/' '.git/' 'cmd_config.json' '.db'; do
    if find "$STAGE/astrbot_plugin_superai" -name "*${forbidden}*" -print -quit | grep -q .; then
        echo "打包失败：产物中检出禁止内容「${forbidden}」—— 疑似运行时文件泄漏。" >&2
        exit 1
    fi
done

# zip 顶层目录固定为 astrbot_plugin_superai（metadata.yaml 的 name），
# AstrBot 依赖它与目录同名才能正确安装。
mv "$STAGE" "$OUT_DIR/${NAME}"
# 用 Python 标准库打包，避免依赖 zip 命令（CI 镜像里没有）
( cd "$OUT_DIR/${NAME}" && python3 -c "
import sys, zipfile, pathlib
out, src = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path('astrbot_plugin_superai')
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for f in sorted(src.rglob('*')):
        if f.is_file() and '__pycache__' not in f.parts and f.suffix != '.pyc':
            z.write(f, f.as_posix())
print('zip entries:', len(zipfile.ZipFile(out).namelist()))
" "../${NAME}.zip" )
rm -rf "$OUT_DIR/${NAME}"

echo "已生成 $ZIP"
