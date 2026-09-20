"""包分层与文档冒烟：`packages/ARCHITECTURE.md` 写的契约要真的成立。

  1. 每个包都有 README.md，且带「分层位置」（上游 / 不做）与模块表
  2. README 列全了该包的每个 .py（新增文件不许悄悄漏掉）
  3. 有 __main__.py / cli.py 的包，README 里给了 `python -m <包> …` 的用法
  4. packages/ARCHITECTURE.md 存在，且提到了每个包
  5. **依赖方向**：只能上层 import 下层；反向边 / 同层横边必须逐条登记在 ARCHITECTURE.md 的例外表里
     （登记的例外若已不存在，也算失败 —— 表不许腐烂）
  6. 组合的 `structure.json` 里 `entry_points` 指向的文件真的存在（域专用件搬家后不许留悬空指针）

Usage:  python tests/packages_doc_smoke.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKGS_DIR = ROOT / "packages"

sys.path.insert(0, str(PKGS_DIR))
from mccore.paths import configure_stdio  # noqa: E402

configure_stdio()   # PASS 行是中文：Windows 管道默认 cp1252，不打这针会 UnicodeEncodeError

#: 层号：越小越靠内核（见 packages/ARCHITECTURE.md）
LAYER = {"mccore": 0, "mckit": 1, "mcrender": 1,
         "mctools": 2, "mcqa": 2, "mcslice": 2,
         "mcstudio": 3, "mcmaterials": 3, "mckb": 3}

#: 允许的「真反向边」（下层 ← 上层 import）：必须与 ARCHITECTURE.md §2 一致
ALLOWED_REVERSE = {
    ("packages/mccore/stage_build.py", "mcqa"),      # 分段评审步骤（CLI 分支，懒导入）
}

#: 允许的「同层横边」：同上
ALLOWED_SAME_LAYER = {
    ("packages/mcrender/cli.py", "mckit"),           # --update-states 渲染前补连接
    ("packages/mctools/__main__.py", "mcqa"),        # --qa 自检
}


def mods(pkg: Path) -> list[str]:
    """包袱名文件（`__init__.py`）另有检查，不要求写进模块表。"""
    return sorted(p.name for p in pkg.glob("*.py") if p.name != "__init__.py")


def cross_imports(path: Path, self_pkg: str) -> set[str]:
    """本文件里出现的其他 structworkshop 包的顶层包名（含函数内懒导入）。"""
    out: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    for n in ast.walk(tree):
        names: list[str] = []
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and not n.level:
            names = [n.module or ""]
        for full in names:
            top = full.split(".")[0]
            if top in LAYER and top != self_pkg:
                out.add(top)
    return out


def is_cli_entry(path: Path) -> bool:
    """CLI 入口：定义 main()，或文件名就是 __main__.py。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    if path.name == "__main__.py" or 'if __name__ == "__main__"' in src:
        return True
    return any(isinstance(n, ast.FunctionDef) and n.name == "main"
               for n in ast.parse(src).body)


def main() -> int:
    pkgs = sorted(p.name for p in PKGS_DIR.iterdir()
                  if p.is_dir() and (p / "__init__.py").is_file())
    assert pkgs == sorted(LAYER), f"包集合与 ARCHITECTURE.md 不一致：{pkgs}"

    arch = (PKGS_DIR / "ARCHITECTURE.md").read_text(encoding="utf-8")
    for pkg in pkgs:
        assert re.search(rf"`{pkg}`", arch), f"ARCHITECTURE.md 没提到包 {pkg}"
    print(f"PASS 1/6  {len(pkgs)} 个包都在 ARCHITECTURE.md 里")

    for pkg in pkgs:
        rd = PKGS_DIR / pkg / "README.md"
        assert rd.is_file(), f"{pkg} 缺 README.md"
        text = rd.read_text(encoding="utf-8")
        assert "## 分层位置" in text, f"{pkg}/README.md 缺「分层位置」小节"
        assert "上游" in text and "不做" in text, f"{pkg}/README.md 的分层说明不完整"
        assert len(text) > 800, f"{pkg}/README.md 太短（{len(text)} 字）"
    print("PASS 2/6  每个包的 README 都有分层位置（上游/不做）")

    for pkg in pkgs:
        d = PKGS_DIR / pkg
        text = (d / "README.md").read_text(encoding="utf-8")
        missing = [m for m in mods(d) if m not in text]
        assert not missing, f"{pkg}/README.md 没列出这些模块：{missing}"
        init = ast.get_docstring(ast.parse((d / "__init__.py").read_text(encoding="utf-8")))
        assert init and init.strip(), f"{pkg}/__init__.py 缺一行包简介 docstring"
    print("PASS 3/6  README 列全了各包的 .py（且每个包有 __init__ 简介）")

    for pkg in pkgs:
        d = PKGS_DIR / pkg
        text = (d / "README.md").read_text(encoding="utf-8")
        if (d / "__main__.py").is_file() or (d / "cli.py").is_file():
            assert f"python -m {pkg}" in text, f"{pkg}/README.md 没写 `python -m {pkg}` 用法"
    print("PASS 4/6  有 CLI 的包都在 README 里写了入口")

    seen_rev: set[tuple[str, str]] = set()
    seen_same: set[tuple[str, str]] = set()
    bad: list[str] = []
    for pkg in pkgs:
        for f in sorted((PKGS_DIR / pkg).glob("*.py")):
            rel = f.relative_to(ROOT).as_posix()
            for top in cross_imports(f, pkg):
                kind = ("反向" if LAYER[top] > LAYER[pkg] else
                        "同层横边" if LAYER[top] == LAYER[pkg] else None)
                if kind is None:                     # 向下依赖，正常
                    continue
                allowed = (ALLOWED_REVERSE if kind == "反向" else ALLOWED_SAME_LAYER)
                (seen_rev if kind == "反向" else seen_same).add((rel, top))
                if (rel, top) not in allowed:
                    bad.append(f"{rel} {kind}依赖 {top}（层 {LAYER[pkg]} -> {LAYER[top]}）")
                elif not is_cli_entry(f):
                    bad.append(f"{rel} 是{kind}例外但不是 CLI 入口（库代码只能向下依赖）")
    assert not bad, "未登记/违规的跨层依赖：\n  " + "\n  ".join(bad)
    stale = (ALLOWED_REVERSE - seen_rev, ALLOWED_SAME_LAYER - seen_same)
    assert not (stale[0] or stale[1]), \
        f"ARCHITECTURE.md 里登记的例外已不存在（表腐烂了）：{sorted(stale[0]) + sorted(stale[1])}"
    print(f"PASS 5/6  依赖方向成立（{len(seen_rev)} 条反向边 + {len(seen_same)} 条同层横边，全部是已登记的 CLI 例外）")

    # 6) 组合的入口点不许悬空（§4 的「域专用件归组合」就是靠这张表指路）
    import json  # noqa: PLC0415
    checked = 0
    dangling: list[str] = []
    for sj in sorted((ROOT / "compositions").glob("*/structure.json")):
        data = json.loads(sj.read_text(encoding="utf-8"))
        for key, val in (data.get("entry_points") or {}).items():
            if not val.endswith(".py"):
                continue                     # `python -m …` 形式的命令不做路径校验
            checked += 1
            if not (ROOT / val).is_file():
                dangling.append(f"{sj.parent.name}.{key} -> {val}")
    assert not dangling, "structure.json 的 entry_points 指向不存在的文件：\n  " + "\n  ".join(dangling)
    print(f"PASS 6/6  组合 entry_points 无悬空（校验 {checked} 个 .py 路径）")

    print("\nALL PASS: 包分层与文档契约")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
