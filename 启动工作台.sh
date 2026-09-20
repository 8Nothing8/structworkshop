#!/usr/bin/env bash
# structworkshop 结构工坊 · 启动本地工作台（macOS / Linux）
#
# 首次运行：建 .venv → 装依赖 → 把本项目注册进这个 .venv → 起服务
# 不碰系统 Python：所有东西都在本文件夹的 .venv 里，删掉文件夹 = 彻底卸载。
#
# 用法：
#   chmod +x 启动工作台.sh && ./启动工作台.sh
#   ./启动工作台.sh --port 8700        # 后面跟的参数原样传给 mcstudio serve
#   STRUCTWORKSHOP_NO_OPEN=1 ./启动工作台.sh   # 不自动开浏览器（服务器/测试用）
set -u
cd "$(dirname "$0")" || exit 1

say() { printf '%s\n' "$*"; }
die() { say "$*"; say ""; read -r -p "按回车键退出…" _ || true; exit 1; }

say "=========================================="
say "  structworkshop 结构工坊 - 本地工作台"
say "=========================================="
say ""

# ---- 1) 找一个 Python 3.10+ ----
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && \
     "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  die "[x] 没找到 Python 3.10 以上版本。
    macOS：https://www.python.org/downloads/ 或 brew install python@3.12
    Linux：apt install python3 python3-venv（或 dnf install python3）"
fi

# ---- 2) 本文件夹内的虚拟环境（不污染系统 Python）----
VENV="$PWD/.venv"
VPY="$VENV/bin/python"
if [ -x "$VPY" ]; then
  say "[1/3] 虚拟环境已就绪。"
else
  say "[1/3] 建虚拟环境 .venv（第一次要几十秒）..."
  "$PY" -m venv "$VENV" || die "[x] 建虚拟环境失败。
    Debian/Ubuntu 可能要先装：apt install python3-venv"
  [ -x "$VPY" ] || die "[x] 建虚拟环境失败：$VPY 不存在。"
fi

# ---- 3) 依赖 + 把本项目注册进 .venv（只写 .venv 内部）----
if "$VPY" -c 'import numpy, numba, PIL, nbt' >/dev/null 2>&1; then
  say "[2/3] 依赖已就绪。"
elif [ -d "$PWD/wheels" ]; then
  say "[2/3] 从本地 wheels/ 离线安装依赖..."
  "$VPY" -m pip install --no-index --find-links "$PWD/wheels" -e ".[runtime]" \
    || die "[x] 离线安装失败：wheels/ 里的包与当前平台或 Python 版本不匹配。"
else
  say "[2/3] 安装依赖 numpy / numba / pillow / nbt（第一次要下载几十 MB）..."
  "$VPY" -m pip install --upgrade pip >/dev/null 2>&1 || true
  "$VPY" -m pip install -e ".[runtime]" \
    || die "[x] 依赖安装失败：检查网络；不能上网的机器请用 tools/make_release.py --wheels 打离线包。"
fi

# 文件夹被挪动过（.venv 里的注册路径失效）时就地补一次注册，只补注册不下载
if ! "$VPY" -c 'import mccore' >/dev/null 2>&1; then
  say "[3/3] 修复本项目在 .venv 里的注册（不下载依赖）..."
  "$VPY" -m pip install -e . --no-deps >/dev/null 2>&1 || true
fi

# PYTHONPATH 兜底：包被挪动/注册失效也照样能跑，并保证引擎来自**本文件夹**
export PYTHONPATH="$PWD/packages"

# ---- 自检：确认引擎是本文件夹这一份（不是别处 pip install -e . 绑定的那一份）----
say "[自检] 本次使用的引擎："
"$VPY" tools/which_copy.py --expect "$PWD" \n  || die "[x] 自检没过：上面「引擎」那行指向的不是本文件夹。删掉本文件夹里的 .venv 再跑一次；
    说明见 INSTALL.md 的「装了好几份，怎么启动确定的那一份」。"

# ---- 4) 起服务 ----
OPEN="--open"
[ "${STRUCTWORKSHOP_NO_OPEN:-0}" = "1" ] && OPEN=""
say "[3/3] 启动工作台：浏览器会自动打开（端口被占会自动换，看下面打印的地址）"
say "      Ctrl+C 退出工作台"
say ""
exec "$VPY" -m mcstudio serve $OPEN "$@"
