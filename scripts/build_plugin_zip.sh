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

# 只收插件运行必需的文件：排除测试、CI、文档源、版本控制与缓存。
tar --create \
    --exclude='./.git' \
    --exclude='./.cnb.yml' \
    --exclude='./.ci' \
    --exclude='./dist' \
    --exclude='./tests' \
    --exclude='./conftest.py' \
    --exclude='./pytest.ini' \
    --exclude='./ruff.toml' \
    --exclude='./requirements-dev.txt' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    . | ( mkdir -p "$OUT_DIR/${NAME}" && tar --extract --directory="$OUT_DIR/${NAME}" )

# metadata.yaml 的 name 即插件目录名，AstrBot 依赖它匹配目录。
STAGE="$OUT_DIR/.stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
mv "$OUT_DIR/${NAME}" "$STAGE/astrbot_plugin_superai"
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
