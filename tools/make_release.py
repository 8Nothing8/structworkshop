"""打发行包：把仓库整理成「解压就能用」的文件夹（可选再压成 zip）。

为什么要有它：**仓库本体**带 `.git/` `.github/` `.cache/` 这些「开发/本机」的东西，
直接压缩发出去又大又乱；手删又容易删错（`.cache` 几百 MB、`.venv` 里全是绝对路径）。
这个脚本按一份**明确的白/黑名单**复制出一份干净副本，并在复制后扫一遍
「机器相关字符串」（绝对路径、用户名），有问题先告诉你。

用法::

    python tools/make_release.py                  # -> dist/structworkshop-portable/
    python tools/make_release.py --zip            # 再压成 dist/structworkshop-portable.zip
    python tools/make_release.py --wheels         # 顺带下 wheels/（给不能上网的收件人）
    python tools/make_release.py --with-content   # 连 packs/ builds/ compositions/ kb/ 一起带
    python tools/make_release.py --out D:/x/y     # 换输出位置

排除的东西（三类）::

    版本控制   .git  .github  .gitignore  .gitattributes  CONTRIBUTING.md
    本机状态   .cache  .venv  venv  *.egg-info  *.log  .env  auth.json  __pycache__
    构建产物   dist  build  *.zip  _smoke_*

带上的东西：`packages/ skills/ tests/ tools/ docs/ .pi/ pyproject.toml registry.json
README.md AGENT.md INSTALL.md .env.example .editorconfig 启动工作台.bat start-workbench.sh`
（`packs/ builds/ compositions/ kb/` 是可选内容，不加 `--with-content` 就不带 ——
它们体积大、且属于个人素材。）
"""
from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
import zipfile
from pathlib import Path

#: 目录级排除（任何层级命中即跳过）
EXCLUDE_DIRS = {
    ".git", ".github", ".cache", ".venv", "venv", "env", "build", "dist",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules",
    "wheels",
}

#: 文件级排除（按名字精确匹配）
EXCLUDE_FILES = {
    ".gitignore", ".gitattributes", "CONTRIBUTING.md", ".env", "auth.json",
    ".DS_Store", "Thumbs.db", "desktop.ini",
}

#: 通配排除
EXCLUDE_GLOBS = ("*.egg-info", "*.pyc", "*.pyo", "*.log", "*.zip", "_smoke_*", "*.swp")

#: 只有加了 --with-content 才带的本地内容目录
CONTENT_DIRS = ("packs", "builds", "compositions", "kb")

#: 扫「机器相关字符串」时跳过的文件（相对发行目录的 POSIX 路径）。
#: 这里每一条都是**人工确认过的假阳性**：文档/脚本里的示例路径（`D:\structworkshop`、
#: `C:\Program Files`）、JS 里的转义序列（`'\nFAILED:\n'` 里含字符串 `D:\n`）、
#: 以及本脚本自己那份模式表。
MACHINE_SCAN_SKIP = {
    ".env.example",
    "INSTALL.md",
    "启动工作台.bat",
    "start-workbench.sh",
    "tools/make_release.py",
    "tools/lf_check.py",
    "tests/editor_mesh_bench.js",   # `FAILED:\n` 被当成 `D:\`
    "tests/renderer_ab.js",
}
MACHINE_PATTERNS = ("D:\\", "C:\\", "/Users/", "/home/", "yingy")


def repo_root() -> Path:
    p = Path(__file__).resolve().parent
    for cand in (p, *p.parents):
        if (cand / "pyproject.toml").is_file() and (cand / "packages").is_dir():
            return cand
    raise SystemExit("找不到仓库根（要含 pyproject.toml + packages/）；"
                     "也可以直接把它拷到仓库根下再跑。")


def should_skip(rel: Path) -> bool:
    # 目录级排除要看**所有**层级：`dist/` 这种空目录如果只看 rel.parts[:-1]，
    # 目录本身会被 mkdir 出来，发行包里就多几个空壳目录。
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    if rel.name in EXCLUDE_FILES:
        return True
    return any(fnmatch.fnmatch(rel.name, g) for g in EXCLUDE_GLOBS)


def copy_tree(src: Path, dst: Path, with_content: bool) -> tuple[int, int]:
    """按名单复制；返回 (文件数, 字节数)。软链/特殊文件按文件复制。"""
    files = 0
    total = 0
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if should_skip(rel):
            continue
        if not with_content and rel.parts[0] in CONTENT_DIRS:
            continue
        if p.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
            continue
        if not p.is_file():
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)          # 按字节复制：.bat 的 CRLF 必须原样保留
        files += 1
        total += p.stat().st_size
    return files, total


def scan_machine_paths(dst: Path) -> list[str]:
    """扫复制出来的文本文件里有没有本机绝对路径 / 用户名。"""
    hits: list[str] = []
    for p in sorted(dst.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(dst).as_posix()
        if rel in MACHINE_SCAN_SKIP:
            continue
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".zip", ".schem",
                                ".litematic", ".nbt", ".pdf", ".ttf", ".whl"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in MACHINE_PATTERNS:
            if pat in text:
                hits.append(f"{rel}  <- 含 {pat!r}")
                break
    return hits


def download_wheels(src: Path, py: str | None = None) -> int:
    """把运行时依赖下到 <repo>/wheels（收件人不能上网时用）。

    注意：wheel 与**平台 + Python 版本**绑定，给 Windows/cp312 的人就得在这台
    Windows/cp312 上下载；跨平台要用 ``pip download --platform …`` 手工拉。
    """
    import subprocess  # noqa: PLC0415

    out = src / "wheels"
    out.mkdir(parents=True, exist_ok=True)
    argv = [py or sys.executable, "-m", "pip", "download", "-d", str(out),
            "numpy", "numba", "pillow", "nbt"]
    print("下载 wheels:", " ".join(argv))
    return subprocess.call(argv)


def make_zip(folder: Path) -> Path:
    z = folder.with_suffix(".zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=f"{folder.name}/{p.relative_to(folder).as_posix()}")
    return z


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="打 structworkshop 发行包（干净副本 + 可选 zip）")
    ap.add_argument("--out", default=None, help="输出目录（默认 <repo>/dist/structworkshop-portable）")
    ap.add_argument("--zip", action="store_true", help="同时压成 zip")
    ap.add_argument("--wheels", action="store_true", help="先把 numpy/numba/pillow/nbt 下到 wheels/")
    ap.add_argument("--with-content", action="store_true",
                    help="连 packs/ builds/ compositions/ kb/ 一起带（体积大）")
    ap.add_argument("--force", action="store_true", help="输出目录已存在时先删掉重建")
    a = ap.parse_args(argv)

    src = repo_root()
    dst = Path(a.out) if a.out else src / "dist" / "structworkshop-portable"
    dst = dst.resolve()

    if a.wheels:
        download_wheels(src)

    for need in ("启动工作台.bat", "start-workbench.sh", "INSTALL.md", "pyproject.toml"):
        if not (src / need).exists():
            print(f"[!] 仓库里缺 {need} —— 发行包里会少东西（先在仓库根补上）", file=sys.stderr)

    if dst.exists():
        if not a.force:
            print(f"[x] 输出目录已存在：{dst}\n    加 --force 覆盖，或换 --out。", file=sys.stderr)
            return 2
        shutil.rmtree(dst)
    dst.mkdir(parents=True)

    files, total = copy_tree(src, dst, a.with_content)
    print(f"发行目录: {dst}")
    print(f"  文件 {files} 个 / {total / 1048576:.1f} MB"
          f"（{'含' if a.with_content else '不含'} packs·builds·compositions·kb）")

    hits = scan_machine_paths(dst)
    if hits:
        print(f"\n[!] 发现 {len(hits)} 处「机器相关字符串」，发出去之前确认一下"
              "（文档里的示例路径不算问题）:")
        for h in hits[:20]:
            print("   ", h)
    else:
        print("  机器相关字符串：无（没有绝对路径 / 用户名残留）")

    if a.zip:
        z = make_zip(dst)
        print(f"zip: {z}  ({z.stat().st_size / 1048576:.1f} MB)")

    print("\n下一步：双击 `启动工作台.bat`（Windows）或 `./start-workbench.sh`（macOS/Linux）"
          "\n        收件人第一次跑会自动建 .venv 并装依赖；删掉文件夹 = 卸载（不动系统 Python）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
