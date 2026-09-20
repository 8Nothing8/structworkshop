"""结构质检：palette 合法性 / 悬浮块 / 门配对（有 ERROR 退码 1）。

Checks
------
1. palette lint      -- every palette entry must be a known block of the
                        current version, and every property/value must be
                        legal (illegal blockstates silently fall back to the
                        default state -> wrong renders).
2. floating components -- non-air components (6-connected) whose bottom is
                        above y=0 are ERRORs, unless every block in the
                        component is self-supporting (chains, lanterns,
                        torches, vines, leaves...) -> WARN.
3. door pairing      -- *_door halves must stack, with consistent
                        facing/hinge/open.
4. stats             -- size, block counts, palette, void ratio.

Usage:
  python -m mcqa.qa_check build.schem [--allow-float] [--json]
      [--max-lines 40]
Exit code: 1 if any ERROR, else 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from mccore.paths import repo_root  # noqa: E402

ROOT = repo_root()
from mccore import structure_io as S  # noqa: E402

BLOCK_DATA = (
    ROOT / "skills" / "minecraft-block-models" / "data" / "all_blocks.json"
)

# blocks that are allowed to hang / attach without support below
SELF_SUPPORTING = {
    "torch", "soul_torch", "redstone_torch", "redstone_wall_torch", "wall_torch",
    "iron_chain", "copper_chain", "exposed_copper_chain", "weathered_copper_chain",
    "oxidized_copper_chain", "waxed_copper_chain", "waxed_exposed_copper_chain",
    "waxed_weathered_copper_chain", "waxed_oxidized_copper_chain",
    "lantern", "soul_lantern", "vine", "ladder", "glow_lichen", "sculk_vein",
    "hanging_roots", "spore_blossom", "big_dripleaf", "small_dripleaf",
    "mangrove_roots", "cave_vines", "cave_vines_plant", "weeping_vines",
    "weeping_vines_plant", "twisting_vines", "twisting_vines_plant",
    "moss_carpet", "pink_petals", "cobweb", "sea_pickle", "turtle_egg",
    "scaffolding",
}
DOOR_KEYS = ("facing", "hinge", "open")
STRUCT6 = np.zeros((3, 3, 3), dtype=np.uint8)
for axis in range(3):
    idx = [1, 1, 1]
    for off in (-1, 1):
        idx[axis] = 1 + off
        STRUCT6[tuple(idx)] = 1


def load_blocks() -> dict:
    return json.loads(BLOCK_DATA.read_text(encoding="utf-8"))


def cap(lst: list, n: int) -> list:
    if len(lst) <= n:
        return lst
    return lst[:n] + [f"... 还有 {len(lst) - n} 条已省略"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help=".schem/.schem 文件")
    ap.add_argument("--allow-float", action="store_true",
                    help="悬浮组件降级为警告(浮空城等刻意悬浮的设计)")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--max-lines", type=int, default=40)
    a = ap.parse_args(argv)

    d = S.read_structure(a.input)
    voxels: np.ndarray = d["voxels"]  # (sy, sz, sx)
    palette: list[dict] = d["palette"]
    sy, sz, sx = voxels.shape
    blocks = load_blocks()

    errors: list[str] = []
    warns: list[str] = []

    # ---- 1. palette lint ------------------------------------------------
    names = [e["Name"].removeprefix("minecraft:") for e in palette]
    for i, entry in enumerate(palette):
        name = names[i]
        if name not in blocks:
            errors.append(f"palette[{i}] {entry['Name']}: 未知方块(不在当前版本映射表)")
            continue
        legal = blocks[name]["properties"]
        for k, v in (entry.get("Properties") or {}).items():
            if k not in legal:
                errors.append(
                    f"palette[{i}] {name}: 非法属性 {k}={v}"
                    f"(合法属性: {sorted(legal)})")
            elif v not in legal[k]:
                errors.append(
                    f"palette[{i}] {name}: 非法属性值 {k}={v}"
                    f"(合法值: {legal[k]})")

    # ---- 2. floating components -----------------------------------------
    non_air = voxels != 0
    if non_air.any():
        from scipy import ndimage  # lazy: only needed when there are blocks

        # compact name grid: palette index -> unique-name id (uint8)
        uniq, name_ids = np.unique(names, return_inverse=True)
        ngrid = name_ids.astype(np.uint8)[voxels]
        soft = np.array([
            n in SELF_SUPPORTING or n.endswith("_leaves") for n in uniq
        ], dtype=bool)

        lab, ncomp = ndimage.label(non_air, structure=STRUCT6)
        for c in range(1, ncomp + 1):
            mask = lab == c
            ymin = int(np.nonzero(mask.any(axis=(1, 2)))[0].min())
            if ymin == 0:
                continue  # touches the bottom of the region: grounded
            count = int(mask.sum())
            ids_here = ngrid[mask]
            dom = uniq[int(np.bincount(ids_here).argmax())]
            y0, z0, x0 = map(int, np.argwhere(mask)[0])
            msg = (f"悬浮组件: {count} 格, 底部 y={ymin}, 主方块 {dom}, "
                   f"起始 (x,y,z)=({x0},{y0},{z0})")
            if soft[ids_here].all():
                msg += " [全部为自支撑方块(锁链/灯笼/藤蔓/树叶等)]"
                warns.append(msg)
            else:
                msg += " [含非自支撑方块]"
                (warns if a.allow_float else errors).append(msg)

    # ---- 3. door pairing --------------------------------------------------
    for idx, name in enumerate(names):
        if not name.endswith("_door"):
            continue
        props = palette[idx].get("Properties") or {}
        half = props.get("half")
        if half not in ("lower", "upper"):
            errors.append(f"palette[{idx}] {name}: 缺 half 属性")
            continue
        cells = np.argwhere(voxels == idx)
        for y, z, x in cells:
            ny = y + 1 if half == "lower" else y - 1
            if ny < 0 or ny >= sy:
                errors.append(
                    f"{name} {half} @ (x,y,z)=({x},{y},{z}) 越界,缺另一半")
                continue
            oid = voxels[ny, z, x]
            if oid == 0 or names[oid] != name:
                errors.append(
                    f"{name} {half} @ (x,y,z)=({x},{y},{z}) "
                    f"{'上方' if half == 'lower' else '下方'}不是同种门")
                continue
            oprops = palette[oid].get("Properties") or {}
            for k in DOOR_KEYS:
                if props.get(k) != oprops.get(k):
                    errors.append(
                        f"{name} @ (x,y,z)=({x},{y},{z}) 与配对门 {k} 不一致"
                        f"({props.get(k)} vs {oprops.get(k)})")

    # ---- 4. stats ---------------------------------------------------------
    total = int(voxels.size)
    filled = int(non_air.sum())
    stats = {
        "file": str(Path(a.input).resolve()),
        "size": {"x": sx, "y": sy, "z": sz},
        "position": dict(zip(("x", "y", "z"), d["position"])),
        "total_volume": total,
        "filled_blocks": filled,
        "void_ratio": round((total - filled) / total, 4) if total else 0.0,
        "palette_entries": len(palette),
        "unique_names": len(set(names)),
        "errors": len(errors),
        "warnings": len(warns),
    }

    if a.json:
        print(json.dumps({
            "stats": stats,
            "errors": errors,
            "warnings": warns,
        }, ensure_ascii=False, indent=1))
        return 1 if errors else 0

    # ---- report -----------------------------------------------------------
    print(f"QA: {stats['file']}")
    print(f"    尺寸 {sx}×{sy}×{sz}  方块 {filled}/{total}"
          f"(空腔 {stats['void_ratio']:.0%})  palette {len(palette)} 项 / "
          f"{len(set(names))} 种")
    if errors:
        print(f"\n[ERROR] {len(errors)} 条")
        for m in cap(errors, a.max_lines):
            print("  ✗ " + m)
    else:
        print("\n[ERROR] 0")
    if warns:
        print(f"\n[WARN] {len(warns)} 条")
        for m in cap(warns, a.max_lines):
            print("  ⚠ " + m)
    print("\n结论: " + ("不通过(必须修复 ERROR)" if errors else "通过 ✓"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
