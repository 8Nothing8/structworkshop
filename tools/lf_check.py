"""行尾守卫：把仓库里的文本文件钉死在 **LF**（默认只检查，`--fix` 才动手）。

为什么需要它：`.gitattributes` 是 `* text=auto eol=lf`，仓库按字节算 sha256 的地方
（`packs/*/pack.json` 的模块校验和、`kb/**/manifest.json`）要求「工作区字节 == git blob 字节」。
Python 文本模式在 Windows 上把 `\\n` 写成 CRLF，编辑器/生成脚本也会带 CRLF —— 一旦工作区
是 CRLF，新 clone 出来的 LF 就对不上，`pack validate` 会全红（历史坑：commit 1b09018）。
引擎侧的写入点已统一走 `mccore.paths.write_text_lf`；这个脚本守的是**存量文件与外部编辑器**。

用法:
  python tools/lf_check.py                 # 列清单，有 CRLF 就退出码 1（可进 CI / 冒烟）
  python tools/lf_check.py --fix           # 就地转成 LF（只改行尾，不动内容）
  python tools/lf_check.py --json          # 机器可读
  python tools/lf_check.py --root packs    # 只看某个子树

范围 = `git ls-files --cached --others --exclude-standard`（跟踪的 + 未跟踪但未被忽略的），
二进制按扩展名与 NUL 字节双重识别，永不改写。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

#: 即使是文本，也不该做行尾归一（按字节存的文件）
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf",
    ".nbt", ".litematic", ".schem", ".schematic", ".mcstructure", ".nusn",
    ".zip", ".gz", ".jar", ".whl", ".pt", ".onnx", ".ttf", ".otf", ".woff", ".woff2",
}

#: 文本，但**必须 CRLF**：cmd.exe 读 LF-only 的 .bat/.cmd 会吞掉行首字符
#: （实测：`chcp 65001` 被解析成 `01`，脚本直接跑不动）。它们不在 packs/ 里、
#: 不参与按字节的 sha256，所以这里放行。`.gitattributes` 里有对应的 `*.bat eol=crlf`。
CRLF_EXT = {".bat", ".cmd"}

SNIFF = 8192

#: 没有 git 时的目录剪枝（GitHub 下载 ZIP / 源码包解压出来的目录就是这种情形）。
#: 这份清单只要与 .gitignore 的「构建产物 + 缓存 + 临时产物」部分对齐就够了 ——
#: 那些本来就不进仓库，也不需要守行尾。
SKIP_DIRS = {".git", ".cache", ".venv", "venv", "build", "dist",
             "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             "node_modules", "out"}

#: 同上：按文件名前缀/后缀剪掉的临时产物（对应 .gitignore 里的 `_smoke_*` / `*.log`）。
SKIP_GLOBS = ("_smoke_*", "*.log", "*.pyc", "*.pyo")


def git_files(root: Path) -> list[str]:
    """跟踪的 + 未跟踪但未被忽略的文件（相对仓库根，正斜杠）。

    没有 git（下载的 ZIP、导出的源码包）或 git 不可用时，**退回目录遍历** ——
    否则在这个工具里会报一个看不懂的 ``CalledProcessError``。
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, text=True, check=True).stdout
        return [f for f in out.split("\0") if f]
    except (OSError, subprocess.SubprocessError):
        return walk_files(root)


def walk_files(root: Path) -> list[str]:
    """目录遍历（跳过 SKIP_DIRS / SKIP_GLOBS），返回相对正斜杠路径。"""
    import fnmatch  # noqa: PLC0415

    out: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if any(fnmatch.fnmatch(rel.name, g) for g in SKIP_GLOBS):
            continue
        out.append(rel.as_posix())
    return sorted(out)


def is_text(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXT:
        return False
    try:
        head = path.open("rb").read(SNIFF)
    except OSError:
        return False
    return b"\0" not in head


def scan(root: Path, sub: str | None = None) -> list[tuple[str, int]]:
    """返回 [(相对路径, CRLF 个数)]，按 CRLF 数降序。"""
    hits: list[tuple[str, int]] = []
    for rel in git_files(root):
        if sub and not (rel == sub or rel.startswith(sub.rstrip("/") + "/")):
            continue
        p = root / rel
        if not p.is_file() or not is_text(p):
            continue
        if p.suffix.lower() in CRLF_EXT:   # .bat/.cmd 必须 CRLF，见上面
            continue
        n = p.read_bytes().count(b"\r\n")
        if n:
            hits.append((rel, n))
    return sorted(hits, key=lambda x: (-x[1], x[0]))


def to_lf(root: Path, hits: list[tuple[str, int]]) -> int:
    """只把 CRLF 对换成 LF（落单的 \\r 原样保留），返回改动的文件数。"""
    changed = 0
    for rel, _ in hits:
        p = root / rel
        b = p.read_bytes()
        fixed = b.replace(b"\r\n", b"\n")
        if fixed != b:
            p.write_bytes(fixed)
            changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="文本文件行尾守卫（LF）")
    ap.add_argument("--fix", action="store_true", help="就地转成 LF")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--root", default=None, help="只看这个子树（如 packs / packages/mccore）")
    a = ap.parse_args(argv)

    from mccore.paths import repo_root  # noqa: PLC0415  (工具自身也要能在仓库外跑)
    root = repo_root()

    hits = scan(root, a.root)
    if a.fix:
        changed = to_lf(root, hits)
        if a.json:
            print(json.dumps({"fixed": changed, "files": [h[0] for h in hits]},
                             ensure_ascii=False))
        else:
            print(f"行尾归一：{changed} 个文件 CRLF -> LF")
            for rel, n in hits:
                print(f"  {n:5d}  {rel}")
        return 0

    if a.json:
        print(json.dumps({"crlf": [{"file": f, "lines": n} for f, n in hits]},
                         ensure_ascii=False))
    elif hits:
        print(f"发现 CRLF 文本文件 {len(hits)} 个（`.gitattributes` 要求 LF）:")
        for rel, n in hits:
            print(f"  {n:5d}  {rel}")
        print("\n修复：python tools/lf_check.py --fix")
    else:
        print("行尾 OK：仓库内文本文件全部 LF")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
