"""项目层：一个组合下的「一栋具体建筑」= compositions/<id>/projects/<项目>/（档案 + 产物）。

一个 composition 是**一类建筑**（提示词 + 生成器 + 参数）；
一个 project 是**一栋具体建筑**（一次生成留下的档案）::

    compositions/<id>/
    ├── SKILL.md                类提示词 / 方法索引
    ├── build.py                参数化生成器
    ├── prompts/<prompt>.md     可选：多份提示词，决定"生成什么"
    ├── params/<preset>.json    参数预设
    ├── plans/                  类共享的输入计划（可选）
    └── projects/<name>/        ← 本模块管理
        ├── project.json        档案：提示词 + 参数 + 产物 + 运行记录
        ├── out/                .schem + layout.json
        ├── plans/              该项目写出的装配计划
        ├── renders/            渲染图 / 预览图
        └── review.md           视觉评审报告（可选）

生成器约定（build.py）::

    from mccore.projects import out_dir, plans_dir
    OUT_DIR = out_dir(HERE)       # 项目模式 -> <project>/out；否则 HERE/out
    PLANS   = plans_dir(HERE)

项目模式由环境变量 ``STRUCTWORKSHOP_PROJECT_DIR`` 传入
（改名前的 ``MCFORGE_PROJECT_DIR`` 仍然认；``python -m mccore.compose <id> --project <name>`` 自动设置），
所以同一个生成器既能平铺输出（旧行为），也能一键落进项目档案。

Commands:
  list <composition>                列出该组合下的项目
  show <composition> <name>         打印项目档案
  adopt <composition> [name...]     把 compositions/<id>/out/ 里的产物迁进 projects/
      [--prompt P] [--preset S] [--param k=v] [--note "..."]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from mccore import structure_io as S
from mccore.paths import compositions_dir, env_first, repo_root, write_text_lf

SCHEMA = "mccore/project@1"
PROJECT_ENV = "STRUCTWORKSHOP_PROJECT_DIR"
#: 改名前的旧名：只作兜底。
PROJECT_ENV_LEGACY = "MCFORGE_PROJECT_DIR"

# 产物按扩展名归位：渲染图 -> renders/，其余 -> out/
RENDER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


# ---------------------------------------------------------------- runtime env
def current() -> Path | None:
    """当前项目目录（由 compose --project 通过环境变量传入），没有则 None。"""
    env = env_first(PROJECT_ENV, PROJECT_ENV_LEGACY)
    return Path(env).resolve() if env else None


def _sub(kind: str, here: Path) -> Path:
    proj = current()
    d = (proj / kind) if proj else (here / kind)
    d.mkdir(parents=True, exist_ok=True)
    return d


def out_dir(here: Path) -> Path:
    """生成器输出目录：项目模式 -> <project>/out，否则 <composition>/out。"""
    return _sub("out", here)


def plans_dir(here: Path) -> Path:
    """计划输出目录：项目模式 -> <project>/plans，否则 <composition>/plans。"""
    return _sub("plans", here)


def renders_dir(here: Path) -> Path:
    """渲染图目录：项目模式 -> <project>/renders，否则 <composition>/renders。"""
    return _sub("renders", here)


# ---------------------------------------------------------------- project files
def projects_root(cid: str) -> Path:
    return compositions_dir() / cid / "projects"


def dir_for(cid: str, name: str) -> Path:
    return projects_root(cid) / name


def manifest_path(project: Path) -> Path:
    return project / "project.json"


def load(project: Path) -> dict | None:
    p = manifest_path(project)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save(project: Path, data: dict) -> None:
    project.mkdir(parents=True, exist_ok=True)
    write_text_lf(manifest_path(project),
        json.dumps(data, ensure_ascii=False, indent=1))


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def create(cid: str, name: str, *, prompt: str | None = None,
           params: dict | None = None, preset: str | None = None,
           note: str = "") -> dict:
    """新建（或取回）项目档案。"""
    p = dir_for(cid, name)
    data = load(p) or {
        "schema": SCHEMA,
        "composition": cid,
        "name": name,
        "prompt": None,
        "preset": None,
        "params": {},
        "note": "",
        "created": _now(),
        "updated": _now(),
        "runs": [],
        "artifacts": [],
    }
    if prompt is not None:
        data["prompt"] = prompt
    if preset is not None:
        data["preset"] = preset
    if params:
        data["params"] = dict(params)
    if note:
        data["note"] = note
    data["updated"] = _now()
    save(p, data)
    return data


def list_artifacts(project: Path) -> list[str]:
    """项目内的全部产物（相对路径，posix 风格，project.json 除外）。"""
    if not project.is_dir():
        return []
    out = []
    for f in sorted(project.rglob("*")):
        if f.is_file() and f.name != "project.json":
            out.append(f.relative_to(project).as_posix())
    return out


def portable_argv(argv: list[str]) -> list[str]:
    """把要记进 ``project.json`` 的命令行变**可移植**的。

    为什么要过一道：记录的目的是「以后能复现」，而 `to_argv` 用的是
    ``sys.executable`` + ``<仓库>/compositions/.../build.py`` —— 直接写进去就是
    本机解释器的绝对路径（某个 anaconda 安装）+ 旧仓库名，既换台机器就废，
    也把本机目录结构泄进仓库。这里只做两件事：

    * 仓库内的绝对路径 → 仓库相对 posix（`compositions/x/build.py`）；
    * 解释器绝对路径（名字以 ``python`` 开头）→ ``python``。

    其余情况原样保留（例如 ``--param`` 里的值可能真的是绝对路径，不能乱动）。
    回归：`tests/projects_smoke.py`。
    """
    root = repo_root()
    out: list[str] = []
    for a in argv or []:
        s = str(a)
        try:
            p = Path(s)
        except (OSError, ValueError):        # pragma: no cover —— 非法路径原样返回
            out.append(s)
            continue
        if p.is_absolute():
            if p.is_relative_to(root):
                s = p.relative_to(root).as_posix()
            elif p.name.lower().startswith("python"):
                s = "python"
        out.append(s)
    return out


def record_run(cid: str, name: str, *, prompt: str | None = None,
               params: dict | None = None, argv: list[str] | None = None,
               ok: bool = True) -> dict:
    """一次生成结束后更新档案：参数、产物清单、运行记录。"""
    p = dir_for(cid, name)
    data = create(cid, name, prompt=prompt, params=params)
    data["artifacts"] = list_artifacts(p)
    data["updated"] = _now()
    runs = data.setdefault("runs", [])
    runs.append({"at": _now(), "ok": ok, "argv": portable_argv(list(argv or []))})
    del runs[:-5]                                     # 只留最近 5 次
    save(p, data)
    return data


def scan(cid: str) -> list[dict]:
    """该组合下的项目摘要（给 registry / --list-projects 用）。"""
    root = projects_root(cid)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        man = load(d) or {}
        out.append({
            "name": d.name,
            "prompt": man.get("prompt"),
            "preset": man.get("preset"),
            "params": man.get("params") or {},
            "artifacts": len(man.get("artifacts") or list_artifacts(d)),
            "updated": man.get("updated"),
            "note": man.get("note", ""),
            "path": str(d.relative_to(repo_root())).replace("\\", "/"),
        })
    return out


# ---------------------------------------------------------------- adopt (迁移)
def _prefix_match(fname: str, name: str) -> bool:
    """文件名属于该项目（支持 ``<name>.layout.json`` / ``<name>_preview.png``）。"""
    return fname == name or fname.startswith((name + ".", name + "_"))


def _supports_name(entry: Path) -> bool:
    """生成器 CLI 是否接受 ``--name``（轻量源码探测）。"""
    try:
        return '"--name"' in entry.read_text(encoding="utf-8")
    except OSError:
        return False


def adopt(cid: str, name: str, *, prompt: str | None = None,
          preset: str | None = None, params: dict | None = None,
          note: str = "", apply: bool = True) -> dict:
    """把 ``compositions/<id>/out|plans/`` 里属于 <name> 的旧产物迁进项目目录。

    ``apply=False`` 只返回将要移动的清单（dry-run）。
    """
    comp = compositions_dir() / cid
    dest = dir_for(cid, name)
    plan = {"out": [], "plans": [], "renders": []}
    for src, bin_ in ((comp / "out", "out"), (comp / "plans", "plans")):
        if not src.is_dir():
            continue
        for f in sorted(src.iterdir()):
            if not f.is_file() or f.name == "project.json":
                continue
            if not _prefix_match(f.name, name):
                continue
            if f.suffix.lower() in RENDER_SUFFIXES:
                plan["renders"].append((f, "renders"))
            else:
                plan[bin_].append((f, bin_))
    if not any(plan.values()):
        raise SystemExit(f"{cid}: 没找到与 {name} 匹配的产物"
                         f"（看 compositions/{cid}/out/）")
    if not apply:
        return {k: [str(f) for f, _ in v] for k, v in plan.items()}
    moved = {}
    for bin_, items in plan.items():
        for f, _ in items:
            d = dest / bin_
            d.mkdir(parents=True, exist_ok=True)
            target = d / f.name
            if target.exists():
                target.unlink()
            shutil.move(str(f), str(target))
            moved.setdefault(bin_, []).append(f.name)
    preset_params = {}
    if prompt:
        pf = comp / "prompts" / f"{prompt}.md"
        if pf.is_file():
            from mccore.compose import parse_prompt   # 局部导入，避免循环依赖
            pmeta, _ = parse_prompt(pf)
            preset_params.update(pmeta.get("params") or {})
    if preset:
        pf = comp / "params" / f"{preset}.json"
        if pf.is_file():
            preset_params.update(json.loads(pf.read_text(encoding="utf-8")))
    preset_params.update(params or {})
    # 生成器支持 --name 时，用项目名固定产物文件名（方便复现）
    if "name" not in preset_params:
        e = json.loads((comp / "structure.json").read_text(encoding="utf-8"))
        if _supports_name(comp / e.get("entry", "build.py")):
            preset_params["name"] = name
    create(cid, name, prompt=prompt, preset=preset, params=preset_params, note=note)
    data = record_run(cid, name, prompt=prompt, params=preset_params)
    data["migrated"] = _now()
    save(dest, data)
    return moved


# ---------------------------------------------------------------- CLI
def cmd_list(a) -> None:
    rows = scan(a.composition)
    if not rows:
        print(f"{a.composition}: 还没有项目（projects/ 为空）")
        return
    print(f"{'项目':28} {'提示词':14} {'参数':34} 产物 更新时间")
    for r in rows:
        params = ",".join(f"{k}={v}" for k, v in r["params"].items())
        if len(params) > 32:
            params = params[:32] + "…"
        print(f"{r['name']:28} {str(r['prompt'] or '-'):14} {params:34} "
              f"{r['artifacts']:>3}  {r['updated'] or '-'}")


def cmd_show(a) -> None:
    p = dir_for(a.composition, a.name)
    man = load(p)
    if man is None:
        raise SystemExit(f"没有项目档案 {p}")
    print(json.dumps(man, ensure_ascii=False, indent=1))


def cmd_adopt(a) -> None:
    comp = compositions_dir() / a.composition
    names = list(a.names)
    if not names:
        names = sorted((f.stem for f in (comp / "out").glob("*" + S.PRIMARY_SUFFIX)),
                       key=len, reverse=True)
        if not names:
            names = sorted((f.stem for f in (comp / "out").glob("*.schem")),
                           key=len, reverse=True)
        if not names:
            raise SystemExit(f"{comp}/out/ 里没有 .schem/.schem")
    params = {}
    for it in a.param:
        k, _, v = it.partition("=")
        params[k.strip()] = _val(v.strip())
    for name in names:
        if not a.apply:
            todo = adopt(a.composition, name, apply=False)
            print(f"{name}: 将移动 " +
                  ", ".join(f"{k}:{len(v)}" for k, v in todo.items() if v))
            continue
        moved = adopt(a.composition, name, prompt=a.prompt, preset=a.preset,
                      params=params, note=a.note or "adopt: 从 out/ 迁入")
        print(f"{name}: 迁入 " +
              ", ".join(f"{k}×{len(v)}" for k, v in moved.items()))
    if a.apply:
        print(f"-> compositions/{a.composition}/projects/")


def _val(v: str):
    """CLI 值 -> Python 值（与 compose._yaml_val 同一套规则）。"""
    low = v.lower()
    if low in ("true", "false", "yes", "no"):
        return low in ("true", "yes")
    try:
        return int(v)
    except ValueError:
        return v


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ls = sub.add_parser("list", help="列出组合下的项目")
    p_ls.add_argument("composition")
    p_ls.set_defaults(fn=cmd_list)

    p_sh = sub.add_parser("show", help="打印项目档案")
    p_sh.add_argument("composition")
    p_sh.add_argument("name")
    p_sh.set_defaults(fn=cmd_show)

    p_ad = sub.add_parser("adopt", help="把 out/ 的旧产物迁进 projects/<name>/")
    p_ad.add_argument("composition")
    p_ad.add_argument("names", nargs="*", help="项目名（省略=按 .schem/.schem 自动识别）")
    p_ad.add_argument("--prompt", default=None)
    p_ad.add_argument("--preset", default=None)
    p_ad.add_argument("--param", action="append", default=[],
                      metavar="K=V")
    p_ad.add_argument("--note", default="")
    p_ad.add_argument("--dry-run", dest="apply", action="store_false",
                      help="只列将移动的文件")
    p_ad.set_defaults(fn=cmd_adopt, apply=True)

    a = ap.parse_args()
    a.fn(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
