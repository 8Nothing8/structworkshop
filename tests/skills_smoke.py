"""Skill 契约冒烟：**每个命令都要有 skill 讲，每个 skill 都得是合法的**。

为什么需要它：`packages/` 里加一条命令很容易，但忘了写进 skill 就没人知道它存在 ——
AI 拿到仓库只会按 skill 找命令，没写 = 不存在。这个测试把「命令面 ⊆ skill 覆盖面」
变成可执行的约束，CI 里跑。

检查：
  1. 每个 `SKILL.md` 的 frontmatter 合法（`name` 合规 + `description` 非空）
  2. 每个可执行入口（带 `__main__` 的模块 / tools/）至少被一个 skill 提到
  3. 每个 argparse 子命令至少被一个 skill 提到
  4. skill 里引用的仓库路径真实存在（可选目录另算）
  5. 总览 skill 的命令地图与代码同步（交给 `mccore.bootstrap` 算）

Usage:  python tests/skills_smoke.py
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from mccore.paths import configure_stdio  # noqa: E402

configure_stdio()   # PASS 行是中文：Windows 管道默认 cp1252，不打这针会 UnicodeEncodeError

FAILS: list[str] = []
SKIPS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name}  {detail}")


def skill_paths() -> list[Path]:
    out: list[Path] = []
    for base in ("skills", "compositions"):
        out += sorted((ROOT / base).glob("*/SKILL.md"))
    return out


#: 这些目录是**可选内容**（本地数据），skill 里引用它们不算坏链
OPTIONAL = ("packs/", "builds/", "kb/", "dist/", ".cache/", "out/",
            "compositions/", "plans/", "renders/", "projects/")


def parse_frontmatter(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    fm = text[4:end]
    out: dict[str, str] = {}
    key = None
    for line in fm.splitlines():
        if re.match(r"^[a-zA-Z_]+:", line):
            key, _, val = line.partition(":")
            out[key.strip()] = val.strip()
        elif key and line.strip():
            out[key] += " " + line.strip()
    return out


def main() -> int:
    skills = skill_paths()
    if not skills:
        print("没有找到任何 SKILL.md —— 跳过")
        return 2
    texts = {p: p.read_text(encoding="utf-8") for p in skills}
    blob = "\n".join(texts.values())
    print(f"找到 {len(skills)} 个 skill\n")

    # ---- 1) frontmatter 合法 ----
    print("== 1. frontmatter ==")
    name_re = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
    for p, t in texts.items():
        fm = parse_frontmatter(t)
        rel = p.relative_to(ROOT).as_posix()
        if fm is None:
            check(f"{rel}: 有 frontmatter", False, "缺 `---` 头")
            continue
        nm, desc = fm.get("name", ""), fm.get("description", "")
        check(f"{rel}: name/description 合法",
              bool(name_re.match(nm)) and len(desc) >= 20,
              f"name={nm!r} desc={len(desc)} 字")

    # ---- 2) 命令面 ⊆ skill 覆盖面 ----
    print("\n== 2. 每个可执行入口都被 skill 提到 ==")
    entries: set[str] = set()
    for f in sorted(ROOT.glob("packages/**/*.py")):
        src = f.read_text(encoding="utf-8", errors="replace")
        if "__main__" not in src:
            continue
        rel = f.relative_to(ROOT).as_posix()
        mod = rel[len("packages/"):].removesuffix(".py").replace("/", ".")
        entries.add(mod)
        entries.add(mod.split(".")[0])            # mckb.cli / mckb.__main__ -> mckb
    for f in sorted(ROOT.glob("tools/*")):
        if f.suffix not in (".py", ".js"):
            continue
        if f.suffix == ".py" and "__main__" not in f.read_text(encoding="utf-8",
                                                              errors="replace"):
            continue
        entries.add(f"tools/{f.name}")
    # 归一：去掉 `.cli` / `.__main__` 尾巴
    norm = {e.rsplit(".", 1)[0] if e.endswith((".cli", ".__main__")) else e
            for e in entries}
    missing = sorted(e for e in norm if f"-m {e}" not in blob and e not in blob)
    check(f"{len(norm) - len(missing)}/{len(norm)} 个入口有 skill 讲",
          not missing, "没人讲：" + ", ".join(missing))

    # ---- 3) 子命令也都要有 ----
    print("\n== 3. 每个子命令都被 skill 提到 ==")
    subs: set[str] = set()
    for f in ROOT.glob("packages/**/*.py"):
        for m in re.finditer(r'add_parser\("([a-z0-9-]+)"',
                             f.read_text(encoding="utf-8", errors="replace")):
            subs.add(m.group(1))
    miss_sub = sorted(s for s in subs
                      if not re.search(r"\b" + re.escape(s) + r"\b", blob))
    check(f"{len(subs) - len(miss_sub)}/{len(subs)} 个子命令有 skill 讲",
          not miss_sub, "没人讲：" + ", ".join(miss_sub))

    # ---- 4) skill 里引用的仓库路径真实存在 ----
    print("\n== 4. skill 引用的路径存在 ==")
    pat = re.compile(r"`((?:packages|tests|tools|skills|compositions|\.pi)/"
                     r"[A-Za-z0-9_./\u4e00-\u9fff-]+\.(?:json|toml|txt|py|md|js))`")
    broken: list[str] = []
    for p, t in texts.items():
        for ref in set(pat.findall(t)):
            if ref.startswith(OPTIONAL):
                continue
            if not (ROOT / ref).exists():
                broken.append(f"{p.relative_to(ROOT).as_posix()} -> {ref}")
    check(f"{len(texts)} 个 skill 里的路径引用无坏链", not broken,
          "\n        ".join(broken[:6]))

    # ---- 5) 命令地图与代码同步 ----
    print("\n== 5. 总览 skill 的命令地图与代码同步 ==")
    try:
        from mccore import bootstrap as BS  # noqa: PLC0415
        want = BS._skill_with_map()
        if want is None:
            SKIPS.append("总览 skill 里没有 cli-map 标记")
            print("  skip 总览 skill 里没有 cli-map 标记")
        else:
            have = (ROOT / BS.SKILL_PATH).read_text(encoding="utf-8")
            check("命令地图是最新的（跑 `python -m mccore.bootstrap` 可修）",
                  have == want, "过期：命令地图与代码不一致")
    except Exception as e:  # noqa: BLE001
        check("能加载 mccore.bootstrap", False, f"{type(e).__name__}: {e}")

    # ---- 6) .pi/settings.json 的 skills 路径 ----
    print("\n== 6. .pi/settings.json ==")
    sp = ROOT / ".pi" / "settings.json"
    if not sp.is_file():
        SKIPS.append("没有 .pi/settings.json")
        print("  skip 没有 .pi/settings.json")
    else:
        try:
            cfg = json.loads(sp.read_text(encoding="utf-8"))
            paths = cfg.get("skills") or []
            # 相对路径是相对 **.pi/** 解析的（`../skills` = <repo>/skills）
            def exists(rel: str) -> bool:
                p = Path(rel).expanduser()
                if p.is_absolute():
                    return p.exists()
                return (sp.parent / p).exists() or (ROOT / p).exists()

            missing_dir = [x for x in paths if not exists(x)]
            # 缺 `compositions` 是正常的（可选内容），缺 `skills` 才是错
            hard = [x for x in missing_dir if "compositions" not in x]
            check(f"skills 路径 {paths} 里除 compositions 外都存在",
                  not hard, f"缺：{hard}")
        except json.JSONDecodeError as e:
            check(".pi/settings.json 是合法 JSON", False, str(e))

    print()
    if SKIPS:
        print("跳过：" + "; ".join(SKIPS))
    if FAILS:
        print(f"FAILED ({len(FAILS)} 项)：")
        for x in FAILS:
            print("  -", x)
        return 1
    print("ALL PASS: skill 契约（frontmatter / 命令覆盖 / 路径 / 地图同步）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
