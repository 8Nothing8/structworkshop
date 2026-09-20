"""分段建造驱动：按阶段叠加 + 逐检查点渲染 + 可选视觉评审 + .stages.json。

Stages are applied one after another into a single world grid; after every
stage the cumulative build AND the stage diff are exported as .schem.
Checkpoint stages additionally render views and (optionally) send them to
the vision model -- the AI reviews each section as it is stacked.

Plan format:
  {
    "name": "...",
    "stages": [
      {"id": "floor0", "note": "...",
       "modules": [{"ref": "A", "module": "room_basic"}],
       "place": [{"ref": "A", "pos": [0,0,0], "rot": 0}],
       "connections": [["A:w", "C:e"]]},
      ...
    ],
    "checkpoints": ["floor0"],          // stages after which to render+review
    "render": {"views": "iso,front,top", "scale": 3},
    "review": {"prompt": "..."}          // optional override
  }

Usage:
  python -m mccore.stage_build plan.json --out x
      [--no-ai] [--checkpoint-all] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from mccore.assemble import Assembler, run_plan, write_assembly  # noqa: E402
from mccore.paths import child_env  # noqa: E402

DEFAULT_PROMPT = (
    "你是 Minecraft 分段建造的现场监理。这些是当前施工阶段的渲染图"
    "(累计成品 + 本阶段新增部分)。请:\n"
    "1. 逐张描述你实际看到的内容\n"
    "2. 指出本阶段新增部分与已有部分的衔接问题"
    "(错位、缝隙、悬浮、门没对上、风格断裂)\n"
    "3. 给出修复建议(用方块名/坐标方向)\n"
    "4. 本阶段评分 /10。只基于图片事实。")


def render_views(lt: str, out: str, views: str, scale: float) -> list[str]:
    base = Path(out)
    subprocess.run(
        [sys.executable, "-m", "mcrender.cli", lt, "--views", views,
         "--scale", str(scale), "--out", out],
        check=True, env=child_env())
    return sorted(str(p) for p in base.parent.glob(base.name + "_*.png"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plan", help="分段建造计划 JSON")
    ap.add_argument("--out", default="staged")
    ap.add_argument("--no-ai", action="store_true", help="只渲染不调视觉模型")
    ap.add_argument("--checkpoint-all", action="store_true",
                    help="每个阶段都渲染评审")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    stages = plan["stages"]
    checkpoints = set(plan.get("checkpoints", []))
    rend = plan.get("render", {})
    views = rend.get("views", "iso,front,top")
    scale = float(rend.get("scale", 3))
    prompt = (plan.get("review", {}) or {}).get("prompt", DEFAULT_PROMPT)

    asm = Assembler(tuple(plan.get("region") or (128, 128, 128)),
                    overlap=bool(plan.get("overlap")))
    cum_modules: list[dict] = []
    cum_place: list[dict] = []
    cum_conns: list = []
    seen_refs: set[str] = set()
    log: list[dict] = []

    for k, stage in enumerate(stages):
        sid = stage["id"]
        if not a.quiet:
            print(f"\n== 阶段 {k + 1}/{len(stages)}: {sid}"
                  + (f" -- {stage['note']}" if stage.get("note") else ""))
        # cumulative mini-plan: all refs/placements/connections so far
        for m in stage.get("modules", []):
            if m["ref"] not in seen_refs:
                cum_modules.append(m)
                seen_refs.add(m["ref"])
        cum_place += stage.get("place", [])
        cum_conns += stage.get("connections", [])

        before = asm.world.copy()
        before_origin = list(asm.origin)
        sub = {"name": plan.get("name", "staged"),
               "modules": cum_modules, "place": cum_place,
               "connections": cum_conns, "auto": True}
        report = run_plan(sub, asm, auto=True)

        # export cumulative + diff
        name = a.out
        cum_lt = f"{name}_s{k + 1:02d}_{sid}.schem"
        diff_lt = f"{name}_s{k + 1:02d}_{sid}_diff.schem"
        write_assembly(asm, cum_lt, f"{plan.get('name', 'staged')} stage {sid}",
                       {"stage": sid, "index": k + 1})
        # rebase the before-snapshot onto the (possibly grown) grid
        reb = np.zeros_like(asm.world)
        bx = before_origin[0] - asm.origin[0]
        by = before_origin[1] - asm.origin[1]
        bz = before_origin[2] - asm.origin[2]
        bh, bz2, bw = before.shape
        reb[by:by + bh, bz:bz + bz2, bx:bx + bw] = before
        diff = (asm.world != 0) & (reb == 0)
        x0, y0, z0 = asm.origin
        # reuse same bbox as cumulative (diff region = bbox of all instances)
        xs = [i["bbox"][0][0] for i in asm.instances]
        ys = [i["bbox"][0][1] for i in asm.instances]
        zs = [i["bbox"][0][2] for i in asm.instances]
        xe = [i["bbox"][1][0] for i in asm.instances]
        ye = [i["bbox"][1][1] for i in asm.instances]
        ze = [i["bbox"][1][2] for i in asm.instances]
        bx0, by0, bz0 = min(xs), min(ys), min(zs)
        bx1, by1, bz1 = max(xe), max(ye), max(ze)
        dv = diff[by0 - y0:by1 - y0, bz0 - z0:bz1 - z0, bx0 - x0:bx1 - x0]
        if int(dv.sum()) > 0:
            from mccore import structure_io as S
            S.write_structure(
                diff_lt, dv, asm.palette, (bx0, by0, bz0),
                (bx1 - bx0, by1 - by0, bz1 - bz0), name=f"{sid} diff")
            print(f"  累计 {cum_lt}  /  新增 {diff_lt} "
                  f"({int(dv.sum())} 块)")

        entry = {"stage": sid, "index": k + 1,
                 "new_blocks": int(dv.sum()),
                 "connections_ok": sum(1 for c in report["connections"]
                                       if c.get("ok")),
                 "connections": len(report["connections"]),
                 "conflicts": report["conflicts"],
                 "instances": len(report["instances"])}
        log.append(entry)

        do_check = a.checkpoint_all or sid in checkpoints or k == len(stages) - 1
        if not do_check:
            continue
        pngs = render_views(cum_lt, f"{name}_s{k + 1:02d}_{sid}",
                            views, scale)
        entry["images"] = pngs
        if a.no_ai:
            print(f"  检查点 {sid}: 已渲染 {len(pngs)} 张(--no-ai,跳过评审)")
            continue
        from mcqa.vision_review import VisionConfigError, review  # noqa: E402
        print(f"  检查点 {sid}: 视觉评审中({len(pngs)} 张)...")
        try:
            txt, usage = review(pngs, prompt, max_tokens=1200)
        except VisionConfigError as e:
            print(f"  检查点 {sid}: 没配视觉模型凭据，跳过评审（--no-ai 可显式跳过）\n{e}")
            continue
        rep = Path(f"{name}_s{k + 1:02d}_{sid}_review.md")
        rep.write_text(
            f"# 分段评审 {sid}\n\n- 时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"- usage: {usage}\n\n## 渲染图\n\n"
            + "\n".join(f"- {p}" for p in pngs)
            + f"\n\n## 评审意见\n\n{txt}\n", encoding="utf-8", newline="\n")
        print(f"  评审 -> {rep}")

    Path(f"{a.out}.stages.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    errs = [e for e in log if e["conflicts"]]
    print(f"\n阶段: {len(stages)}  检查点: {sum(1 for e in log if 'images' in e)}"
          f"  冲突: {sum(len(e['conflicts']) for e in log)}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
