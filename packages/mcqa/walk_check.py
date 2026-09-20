"""可行走性：BFS 穿过门与楼梯，检查各房间 / 探针能不能走到。

Minecraft movement model used here (deliberately simple):
  - a cell is passable if it is air or a door
  - a cell is standable if it is passable and the cell below is solid
  - moves: 4-way horizontal on the same level, plus one block up (jump) and
    one block down (fall)
Doors count as passable; stairs / slabs count as support (solid).

Usage:
  python -m mcqa.walk_check build.schem --start 24,1,20 \
      --probe 16,1,8 --probe 16,6,8 ... [--json]

Prints the reachable floor levels and whether each probe is reachable.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

import numpy as np

from mccore import structure_io as S  # noqa: E402


FLAT_DECOR = ("_carpet", "_pressure_plate", "_button")   # 无碰撞的地面装饰
FLAT_BLOCKS = ("snow", "tripwire", "rail", "lily_pad")       # 同高度不挡路


def passable_name(name: str) -> bool:
    """Passable = air / door / flat floor decor (carpets & co. are walkable in MC)."""
    n = name.removeprefix("minecraft:")
    if n == "air":
        return True
    if n.endswith("_door"):
        return True
    if n.endswith(FLAT_DECOR) or n in FLAT_BLOCKS:
        return True
    return False


def build_masks(d: dict):
    pal = d["palette"]
    names = [e["Name"] for e in pal]
    v = d["voxels"]
    flat = np.asarray(v)
    pas = np.zeros(v.shape, dtype=bool)
    for i, name in enumerate(names):
        if passable_name(name):
            pas |= (flat == i)
    solid = ~pas
    return pas, solid, d["position"]


def standable(pas: np.ndarray, solid: np.ndarray) -> np.ndarray:
    st = pas.copy()
    st[0, :, :] = False
    st[1:, :, :] &= solid[:-1, :, :]
    return st


def bfs(pas, solid, start_idx, max_steps: int = 4_000_000):
    st = standable(pas, solid)
    sy, sz, sx = st.shape
    seen = np.zeros(st.shape, dtype=bool)
    x, y, z = start_idx
    if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz) or not st[y, z, x]:
        # snap to the nearest standable column cell at that x/z
        for dy in range(sy):
            for yy in (y + dy, y - dy):
                if 0 <= yy < sy and st[yy, z, x]:
                    y = yy
                    break
            else:
                continue
            break
        else:
            return seen, None
    q = deque([(x, y, z)])
    seen[y, z, x] = True
    steps = 0
    while q:
        cx, cy, cz = q.popleft()
        steps += 1
        if steps > max_steps:
            break
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = cx + dx, cz + dz
            if not (0 <= nx < sx and 0 <= nz < sz):
                continue
            for ny in (cy, cy + 1, cy - 1):
                if not (0 <= ny < sy):
                    continue
                if st[ny, nz, nx] and not seen[ny, nz, nx]:
                    seen[ny, nz, nx] = True
                    q.append((nx, ny, nz))
    return seen, steps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help=".schem/.schem 文件")
    ap.add_argument("--start", required=True, help="起点世界坐标 x,y,z")
    ap.add_argument("--probe", action="append", default=[],
                    help="探针世界坐标 x,y,z(可重复)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    d = S.read_structure(a.input)
    pas, solid, (px, py, pz) = build_masks(d)

    def to_idx(pt: str):
        x, y, z = (int(v) for v in pt.split(","))
        return (x - px, y - py, z - pz)

    seen, steps = bfs(pas, solid, to_idx(a.start))
    if seen is None:
        print("起点不可站立:", a.start)
        return 1

    sy, sz, sx = seen.shape
    ys, zs, xs = np.nonzero(seen)
    result = {
        "reachable_cells": int(seen.sum()),
        "bfs_steps": steps,
        "start": a.start,
        "world_bbox": {
            "x": [px + int(xs.min()), px + int(xs.max())],
            "y": [py + int(ys.min()), py + int(ys.max())],
            "z": [pz + int(zs.min()), pz + int(zs.max())],
        },
        "reachable_by_level": {},
        "probes": [],
    }
    for y in range(int(ys.min()), int(ys.max()) + 1):
        n = int(seen[y].sum())
        if n:
            result["reachable_by_level"][int(y) + py] = n
    for p in a.probe:
        ix, iy, iz = to_idx(p)
        ok = bool(0 <= ix < sx and 0 <= iy < sy and 0 <= iz < sz
                  and seen[iy, iz, ix])
        result["probes"].append({"at": p, "reachable": ok})

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(f"起点 {a.start} -> 可达 {result['reachable_cells']} 格 "
              f"(BFS {steps} 步)")
        print("可达高度层(世界 y: 格数):")
        for y, n in result["reachable_by_level"].items():
            print(f"  y={y:>3}: {n}")
        print("探针:")
        for pr in result["probes"]:
            print(f"  {'OK ' if pr['reachable'] else 'BAD'} {pr['at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
