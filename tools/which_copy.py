"""这台机器上「哪一份 structworkshop 会被 python 用到」—— 一眼看清，并找出悬空的旧登记。

装过好几份（或者以前手动 `pip install -e .` 过）时最容易出两类怪事：

1. **跑得好好的，产物却写进了另一个目录** —— python 解析到的引擎不是你想的那一份；
2. **删了旧目录之后**，`site-packages` 里那条 `.pth` 还指着不存在的路径
   （轻则 `import mccore` 悄悄落到别处，重则报错）。

这个脚本把这两件事都摊开：当前 python 解析到哪份引擎、`site-packages` 里有哪些
本项目的登记、每条登记指向的目录**是否还存在**。

用法::

    python tools/which_copy.py                      # 看「当前 python」会用哪一份
    python tools/which_copy.py --expect <目录>      # 自检：必须是这一份，否则退码 1
    <副本>/.venv/Scripts/python.exe tools/which_copy.py     # 看某份副本的 venv 认哪一份
    python tools/which_copy.py --json               # 机器可读

启动脚本（`启动工作台.bat` / `启动工作台.sh`）内部就用 `--expect` 自检，
保证「双击哪一份 = 用哪一份」。
"""
from __future__ import annotations

import argparse
import json
import site
import sys
import sysconfig
from pathlib import Path


def _site_dirs() -> list[Path]:
    dirs: list[Path] = []
    for key in ("purelib", "platlib"):
        p = sysconfig.get_paths().get(key)
        if p:
            dirs.append(Path(p))
    for p in getattr(site, "getsitepackages", lambda: [])():
        dirs.append(Path(p))
    try:
        u = site.getusersitepackages()
    except Exception:  # noqa: BLE001
        u = None
    if u:
        dirs.append(Path(u))
    out: list[Path] = []
    for d in dirs:
        if d not in out and d.is_dir():
            out.append(d)
    return out


def _engine() -> dict:
    """当前 python 解析到的引擎（没装/找不到就返回 available=False + 原因）。"""
    try:
        import mccore  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
    f = Path(mccore.__file__).resolve()
    root = f.parents[2] if len(f.parents) > 2 else f.parent
    try:
        from mccore.paths import repo_root  # noqa: PLC0415
        root = repo_root()
    except Exception:  # noqa: BLE001
        pass
    return {"available": True, "module": str(f), "repo": str(root),
            "packs": (root / "packs").is_dir()}


def _registrations() -> list[dict]:
    """site-packages 里跟本项目有关的登记（.pth / dist-info），标注是否悬空。"""
    out: list[dict] = []
    for d in _site_dirs():
        for p in sorted(d.glob("*structworkshop*")) + sorted(d.glob("__editable__*")):
            if "structworkshop" not in p.name:
                continue
            item: dict = {"path": str(p), "kind": "dist-info" if p.is_dir() else "file"}
            if p.is_file() and p.suffix == ".pth":
                try:
                    targets = [ln.strip() for ln in p.read_text(
                        encoding="utf-8", errors="replace").splitlines() if ln.strip()]
                except OSError:
                    targets = []
                item["targets"] = targets
                item["dangling"] = [t for t in targets
                                    if not Path(t).expanduser().is_dir()]
            out.append(item)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="看这台机器上哪一份 structworkshop 会被用到")
    ap.add_argument("--expect", default=None,
                    help="自检模式：引擎必须是这个目录（否则退码 1，启动脚本用它）")
    ap.add_argument("--json", action="store_true", help="机器可读")
    a = ap.parse_args(argv)

    eng = _engine()
    regs = _registrations()
    dangle = [t for r in regs for t in r.get("dangling", [])]

    if a.json:
        print(json.dumps({"python": sys.executable, "engine": eng,
                          "registrations": regs, "dangling": dangle},
                         ensure_ascii=False, indent=1))
        return 0

    print(f"解释器      : {sys.executable}")
    print(f"版本        : {sys.version.split()[0]}"
          f"{'（虚拟环境）' if sys.prefix != sys.base_prefix else '（系统环境）'}")
    if eng["available"]:
        print(f"引擎        : {eng['module']}")
        print(f"仓库根      : {eng['repo']}"
              f"{'' if eng['packs'] else '  （没有 packs/ —— 正常，资产包是本地内容）'}")
    else:
        print(f"引擎        : 找不到 mccore（{eng['error']}）")

    print(f"site 登记   : {len(regs)} 条")
    for r in regs:
        line = f"  - {r['path']}"
        print(line)
        for t in r.get("targets", []):
            mark = "⚠ 目标不存在" if not Path(t).expanduser().is_dir() else "ok"
            print(f"      -> {t}   [{mark}]")

    if eng["available"] and dangle:
        print("\n[!] 有悬空登记：删了旧目录但它还记着。清理：pip uninstall -y structworkshop")
    if eng["available"]:
        print("\n小抄：")
        print(f"  · 想用**这一份**的引擎：用它的启动脚本 / 或 "
              f"`PYTHONPATH={Path(eng['repo']) / 'packages'} python -m mcstudio serve`")
        print("  · 想换掉「默认绑定」：在那份目录里跑 pip install -e .（会写全局登记，慎用）")
        print("  · 想彻底不打架：pip uninstall -y structworkshop，之后各份只认自己的启动脚本")

    if a.expect:
        # 容错：Windows 批处理里 `--expect "%~dp0"` 的末尾反斜杠会被 C 运行时当成转义引号，
        # 于是参数里会多出一个 `"`。这里把引号和末尾分隔符吃掉再比。
        want = Path(a.expect.strip().strip('"')).expanduser()
        try:
            want = want.resolve()
        except OSError:
            pass
        ok = bool(eng["available"]) and Path(eng["repo"]).resolve() == want
        print(f"\n自检 --expect {want}: {'通过 ✓' if ok else '不通过 ✗'}"
              + ("" if ok else f"（实际用的是 {eng.get('repo') or '未找到'}）"))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
