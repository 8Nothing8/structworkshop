"""组合运行器：一类建筑 = 一个文件夹（structure.json + build.py + params/ + prompts/ + SKILL.md）。

Every structure project in ``compositions/<id>/`` exposes its generator as a
CLI.  This runner applies a params preset (or ``--set`` overrides) so the AI
gets one command shape for all structures::

    python -m mccore.compose --list
    python -m mccore.compose modern-skyscraper
    python -m mccore.compose modern-skyscraper --prompt brutalist
    python -m mccore.compose modern-skyscraper --set floors=26 --set facade=glass --set palette=high-tech

Params map straight onto CLI flags: ``{"floors": 26, "atrium": true}`` becomes
``--floors 26 --atrium``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from mccore import paths, projects
from mccore.paths import (composition_dirs, compositions_dir, has_compositions,
                          repo_root)

BOOLS = {"true": True, "false": False, "yes": True, "no": False}


def find(cid: str) -> Path:
    """按 id 找组合目录；找不到时区分「一个组合都没有」和「没这个 id」。"""
    d = compositions_dir() / cid
    if (d / "structure.json").is_file():
        return d
    if not has_compositions():
        raise SystemExit(
            f"没有组合 {cid} —— `compositions/` 里一个组合都没有。\n"
            "  这是**可选内容**：把一套 compositions 拷进仓库根目录即可（拷进来就能用）。\n"
            "  只想验证引擎装好了，可以先用零依赖的： python -m mccore.compose math-cube")
    ids = ", ".join(p.name for p in composition_dirs())
    raise SystemExit(f"没有组合 {cid}（现有：{ids}）")


def load_params(cid: str, preset: str) -> dict:
    p = find(cid) / "params" / f"{preset}.json"
    if not p.is_file():
        raise SystemExit(f"没有参数预设 {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def parse_set(items: list[str]) -> dict:
    out = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"--set 需要 key=value: {it}")
        k, v = it.split("=", 1)
        out[k.strip()] = _yaml_val(v.strip())
    return out


def _yaml_val(v: str):
    low = v.lower()
    if low in BOOLS:
        return BOOLS[low]
    try:
        return int(v)
    except ValueError:
        return v.strip("'\"")


def parse_frontmatter(fm: str) -> dict:
    """Minimal YAML-ish frontmatter: top-level keys + one nested map."""
    out: dict = {}
    cur: dict | None = None
    for line in fm.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith(("  ", "\t")) and cur is not None:
            k, _, v = line.strip().partition(":")
            cur[k.strip()] = _yaml_val(v.strip())
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if v == "":
            cur = {}
            out[k] = cur
        else:
            out[k] = _yaml_val(v)
            cur = None
    return out


def parse_prompt(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta = parse_frontmatter(text[3:end].strip("\n"))
    return meta, text[end + 4:].lstrip("\n")


def prompt_files(cid: str) -> list[Path]:
    d = find(cid) / "prompts"
    return sorted(d.glob("*.md")) if d.is_dir() else []


def cmd_list_prompts(cid: str) -> None:
    files = prompt_files(cid)
    if not files:
        print(f"{cid}: 没有 prompts/*.md")
        return
    print(f"{'prompt':18} {'标题':28} 参数")
    for f in files:
        meta, _body = parse_prompt(f)
        params = meta.get("params") or {}
        print(f"{f.stem:18} {str(meta.get('title', '')):28} "
              + ", ".join(f"{k}={v}" for k, v in params.items()))


def to_argv(entry: Path, params: dict) -> list[str]:
    argv = [sys.executable, str(entry)]
    for k, v in params.items():
        arg = k.replace("_", "-")
        if v is True:
            argv.append(f"--{arg}")
        elif v is None or v is False:
            continue
        elif isinstance(v, (list, tuple)):
            argv += [f"--{arg}", ",".join(str(x) for x in v)]
        else:
            argv += [f"--{arg}", str(v)]
    return argv


def cmd_list() -> None:
    dirs = composition_dirs()
    if not dirs:
        print("（`compositions/` 里一个组合都没有 —— 这是可选内容，"
              "把一套 compositions 拷进仓库根目录即可）")
        return
    print(f"{'id':18} {'名称':34} 预设")
    for d in dirs:
        f = d / "structure.json"
        if not f.is_file():
            continue
        e = json.loads(f.read_text(encoding="utf-8"))
        presets = ",".join(p.stem for p in sorted((d / "params").glob("*.json")))
        print(f"{e['id']:18} {e['name']:34} {presets}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("composition", nargs="?", help="组合 id")
    ap.add_argument("--list", action="store_true", help="列出全部组合")
    ap.add_argument("--preset", default="default", help="params/<name>.json")
    ap.add_argument("--style", default=None, metavar="PACK/NAME",
                    help="用风格包(packs/<pack>/styles/<name>.json)建议参数")
    ap.add_argument("--set", action="append", default=[], metavar="K=V",
                    help="覆盖参数(可重复)")
    ap.add_argument("--dry-run", action="store_true", help="只打印命令")
    ap.add_argument("--prompt", default=None, metavar="NAME",
                    help="用 prompts/<NAME>.md 提示词（frontmatter 里的 params 会参与生成）")
    ap.add_argument("--list-prompts", action="store_true",
                    help="列出该组合可用的提示词")
    ap.add_argument("--project", default=None, metavar="NAME",
                    help="生成到 compositions/<id>/projects/<NAME>/（建档案，可复现）")
    ap.add_argument("--list-projects", action="store_true",
                    help="列出该组合下已有的项目")
    a = ap.parse_args()
    if a.list or not a.composition:
        cmd_list()
        return 0

    params = load_params(a.composition, a.preset)
    meta = json.loads((find(a.composition) / "structure.json")
                      .read_text(encoding="utf-8"))
    if a.list_prompts:
        cmd_list_prompts(a.composition)
        return 0
    if a.list_projects:
        projects.cmd_list(argparse.Namespace(composition=a.composition))
        return 0
    project = None
    replay_prompt = None
    if a.project:
        project = projects.dir_for(a.composition, a.project)
        man = projects.load(project)
        if man:
            # 复现已有项目：档案参数优先（它们是上次的最终值），--set 可继续覆盖
            params.update(man.get("params") or {})
            if not a.prompt and man.get("prompt"):
                replay_prompt = man["prompt"]
                print(f"project {a.project}: 档案提示词 {replay_prompt}"
                      f"（参数已恢复；--prompt 覆盖提示词，--set 覆盖单值）")
        else:
            print(f"project {a.project}: 新建 -> "
                  + str(project.relative_to(repo_root())).replace(chr(92), "/"))
    if a.prompt:
        pf = find(a.composition) / "prompts" / f"{a.prompt}.md"
        if not pf.is_file():
            raise SystemExit(f"没有提示词 {pf}（用 --list-prompts 看可用列表）")
        pmeta, pbody = parse_prompt(pf)
        for k, v in (pmeta.get("params") or {}).items():
            params[k] = v
        print(f"prompt {a.prompt}: {pmeta.get('title', '')} — "
              f"{pmeta.get('description', '')}")
        if pbody.strip():
            print("（提示词正文见 " + str(pf.relative_to(repo_root())).replace(chr(92), "/") + "）")
    if a.style:
        from mccore.paths import packs_dir
        sp = packs_dir() / a.style.replace("/", "/styles/", 1)
        sp = sp if sp.suffix == ".json" else sp.with_suffix(".json")
        style = json.loads(sp.read_text(encoding="utf-8"))
        for key, ref in (meta.get("style_params") or {}).items():
            if style.get(ref) is not None:
                params[key] = style[ref]
        print(f"style {sp.name}: "
              + ", ".join(f"{k}={style.get(v)}"
                          for k, v in (meta.get("style_params") or {}).items()))
    params.update(parse_set(a.set))
    entry = find(a.composition) / meta["entry"]
    argv = to_argv(entry, params)
    env = os.environ.copy()
    if project is not None:
        env[projects.PROJECT_ENV] = str(project)
    print("$", " ".join(argv),
          ("  -> " + str(project.relative_to(repo_root())).replace(chr(92), "/"))
          if project is not None else "")
    if a.dry_run:
        return 0
    rc = subprocess.run(argv, cwd=str(repo_root()),
                        env=paths.child_env(env)).returncode
    if project is not None:
        projects.record_run(a.composition, a.project,
                            prompt=a.prompt or replay_prompt,
                            params=params, argv=argv, ok=(rc == 0))
        print(f"project -> {project.relative_to(repo_root())}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
