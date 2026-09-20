"""能力清单 registry.json：扫 compositions/*/structure.json + 资产包清单，供 AI 发现「这仓库能造什么」。
into ``registry.json`` so an agent can discover what the repo can build.

A composition (a "structure project") owns its generation code, params,
plans and SKILL.md::

    compositions/<id>/
    ├── structure.json      # identity card (this module validates it)
    ├── SKILL.md            # how the AI composes this structure
    ├── build.py            # parametric generator
    ├── params/*.json       # parameter presets
    ├── plans/*.json        # assembly / staged plans
    ├── source/             # optional input build (remix jobs)
    └── out/               # generated .schem + reports (gitignored)

Commands:
  scan        rebuild registry.json and print the table
  list        table of registered compositions
  validate    schema + file-existence checks
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mccore import projects
from mccore.module_lib import INDEX
from mccore.paths import (composition_dirs, compositions_dir, has_compositions,
                          pack_dirs, packs_dir, repo_root, write_text_lf)

REGISTRY = repo_root() / "registry.json"

REQUIRED = ("id", "name", "description", "entry", "skill", "packs", "tags")
VALID_ID = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def load_composition(d: Path) -> dict:
    return json.loads((d / "structure.json").read_text(encoding="utf-8"))


TOOLS = [
    {
        "id": "mcstudio",
        "name": "mcstudio 可视化工作台",
        "description": "本地浏览器工作台：模块库（标签管理/批量打标/导入）、3D 预览（deepslate，"
                       "真实方块模型与贴图）、.schem/.litematic 结构编辑（放置/擦除/替换/撤销/另存为模块）。",
        "command": "python -m mcstudio serve --open",
        "url": "http://127.0.0.1:8617/",
        "selftest": "http://127.0.0.1:8617/static/selftest.html",
        "skill": "minecraft-studio",
        "features": ["modules-tags", "3d-preview", "structure-editor"],
    },
]


def collect() -> dict:
    packs = {}
    if INDEX.exists():
        packs = json.loads(INDEX.read_text(encoding="utf-8")).get("packs", {})
    corpus = {}
    kb_reg = repo_root() / "kb" / "registry.json"
    if kb_reg.is_file():
        try:
            kb = json.loads(kb_reg.read_text(encoding="utf-8"))
            corpus = {
                "releases": [
                    {k: r.get(k) for k in ("release", "code", "title", "pages",
                                           "chunks", "extract", "complete", "path")}
                    for r in kb.get("releases", [])
                ],
                "retrieval": "python -m mckb readlist --profile <office|residential|general>",
            }
        except (json.JSONDecodeError, OSError):
            corpus = {}
    comps = []
    for cd in composition_dirs():
        f = cd / "structure.json"
        if f.is_file():
            e = load_composition(cd)
            e["dir"] = cd.name
            e["path"] = str(cd.relative_to(repo_root())).replace("\\", "/")
            e["modules"] = {p: len(packs.get(p, {}).get("modules", []))
                            for p in e.get("packs", [])}
            e["projects"] = projects.scan(cd.name)
            comps.append(e)
    return {"compositions": comps, "packs": packs, "corpus": corpus,
            "tools": TOOLS}


def cmd_scan(_a) -> None:
    data = collect()
    write_text_lf(REGISTRY, json.dumps(data, ensure_ascii=False, indent=1))
    cmd_list(_a)


def cmd_list(_a) -> None:
    data = collect()
    if not data["compositions"]:
        print("（`compositions/` 里一个组合都没有 —— 这是可选内容，"
              "把一套 compositions 拷进仓库根目录即可；"
              "零依赖的自检： python -m mccore.compose math-cube）")
        return
    print(f"{'id':18} {'名称':30} {'资源包':24} 项目 参数")
    for e in data["compositions"]:
        packs = ",".join(f"{p}({e['modules'].get(p, 0)})"
                         for p in e.get("packs", []))
        print(f"{e['id']:18} {e['name']:30} {packs:24} "
              f"{len(e.get('projects', [])):>3}  {e.get('params', '-')}")
    nproj = sum(len(e.get("projects", [])) for e in data["compositions"])
    print(f"\n{len(data['compositions'])} 个组合 + "
          f"{nproj} 个项目 + "
          f"{len(data['packs'])} 个资产包 + "
          f"{len(data.get('corpus', {}).get('releases', []))} 份规范 -> {REGISTRY}")


def cmd_validate(_a) -> None:
    errs = []
    warns = []
    pack_ids = {p.name for p in pack_dirs()}
    # 没有任何资产包 = 「本地数据不在」而不是「组合写错了」：
    # `packs/` 是可选内容（仓库只带引擎 + 组合定义），降级成 WARN。
    # 但只要 `packs/` 里**有**包，缺哪个就是真错 —— 那时说明你确实带了这套资产。
    packs_absent = not pack_ids
    if packs_absent:
        warns.append("`packs/` 里没有资产包（本地/可选内容），资源包引用未校验；"
                     "`python -m mccore.pack create <名字>` 可新建")
    if not has_compositions():
        warns.append("`compositions/` 里没有任何组合（可选内容）—— 跳过组合校验；"
                     "把一套 compositions 拷进仓库即可")
    for cd in composition_dirs():
        f = cd / "structure.json"
        if not f.is_file():
            errs.append(f"{cd.name}: 缺 structure.json")
            continue
        e = json.loads(f.read_text(encoding="utf-8"))
        for k in REQUIRED:
            if k not in e:
                errs.append(f"{cd.name}: 缺字段 {k}")
        if e.get("id") != cd.name:
            errs.append(f"{cd.name}: id={e.get('id')} != 目录名")
        if e.get("id") and set(e["id"]) - VALID_ID:
            errs.append(f"{cd.name}: id 只能用小写/数字/连字符")
        entry = cd / e.get("entry", "")
        if not entry.is_file():
            errs.append(f"{cd.name}: entry 不存在 {entry.name}")
        skill = cd / "SKILL.md"
        if not skill.is_file():
            errs.append(f"{cd.name}: 缺 SKILL.md")
        for p in e.get("packs", []):
            if p not in pack_ids:
                msg = f"{cd.name}: 资源包不存在 {p}"
                (warns if packs_absent else errs).append(msg)
        root = cd / "projects"
        if root.is_dir():
            for pd in sorted(root.iterdir()):
                if not pd.is_dir():
                    continue
                man = projects.load(pd)
                if man is None:
                    errs.append(f"{cd.name}/projects/{pd.name}: 缺 project.json")
                    continue
                if man.get("composition") != cd.name:
                    errs.append(f"{cd.name}/projects/{pd.name}: "
                                f"composition={man.get('composition')} != {cd.name}")
                if man.get("prompt") and not (cd / "prompts" /
                                              f"{man['prompt']}.md").is_file():
                    errs.append(f"{cd.name}/projects/{pd.name}: "
                                f"提示词不存在 prompts/{man['prompt']}.md")
    if warns:
        print("WARN:")
        for x in warns:
            print("  ~", x)
    if errs:
        print("ERRORS:")
        for x in errs:
            print("  !", x)
        raise SystemExit(1)
    print("compositions OK")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help="重建 registry.json").set_defaults(fn=cmd_scan)
    sub.add_parser("list", help="列出组合").set_defaults(fn=cmd_list)
    sub.add_parser("validate", help="校验 structure.json").set_defaults(fn=cmd_validate)
    a = ap.parse_args()
    a.fn(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
