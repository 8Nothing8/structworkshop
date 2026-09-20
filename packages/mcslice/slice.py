"""把已有建筑切成可复用模块，并自动识别接口（门 / 楼梯 / 窗 / 通道）。

Two modes:

* ``--by floor --pitch N``   cut horizontal floor bands (whole plate per band)
* ``--detect grid --floor K``  wall-plane decomposition of one floor band:
  project the wall (solid) mask to find vertical wall planes, split the plate
  into architectural cells (rooms / corridors / cores), then infer ports by
  looking for air-to-air openings across every face.

Every cut writes ``<out-dir>/<name>.schem`` + sidecar ModuleSpec, and can
register the result into an asset pack and emit an assembly plan that puts the
build back together.

    python -m mcslice.slice 办公板楼.schem --detect grid --floor 1
        --pitch 5 --out-dir /tmp/sliced --report
    python -m mcslice.slice build.schem --detect grid --floor 1
        --register --pack modern-arch --category sliced --plan /tmp/plan.json
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import deque
from pathlib import Path

import numpy as np

from mccore import structure_io as S
from mccore.module_lib import spec_path_for
from mccore.paths import pack_modules, write_text_lf

PASS_KEYS = ("air", "door", "trapdoor", "fence_gate", "gate")


# ---------------------------------------------------------------- basics
def load(path: str) -> dict:
    d = S.read_structure(str(path))
    names = [e["Name"].removeprefix("minecraft:") for e in d["palette"]]
    return {"data": d, "names": names,
            "vox": d["voxels"],           # (sy, sz, sx)
            "size": tuple(d["size"])}     # (sx, sy, sz)


def air_index(names: list[str]) -> int:
    for i, n in enumerate(names):
        if n == "air":
            return i
    raise SystemExit("palette 里没有 air")


def passable_mask(names: list[str], vox: np.ndarray) -> np.ndarray:
    m = np.zeros(vox.shape, dtype=bool)
    for i, n in enumerate(names):
        if any(k == n or k in n for k in PASS_KEYS):
            m |= (vox == i)
    return m


# ---------------------------------------------------------------- detection
def wall_planes(frac: np.ndarray, min_run: int = 1) -> list[tuple[int, int]]:
    """Runs where the 1-D boolean array is True -> [(start, end_inclusive)]."""
    runs, s = [], None
    for i, v in enumerate(frac):
        if v and s is None:
            s = i
        elif not v and s is not None:
            runs.append((s, i - 1))
            s = None
    if s is not None:
        runs.append((s, len(frac) - 1))
    return runs


def intervals(planes: list[tuple[int, int]], n: int, min_w: int = 3
              ) -> list[tuple[int, int]]:
    """Split [0, n-1] at wall runs; walls belong to the cell on their low side."""
    bounds = [0]
    for s, e in planes:
        if s == 0:
            continue                    # building edge, not a partition
        bounds.append(s)
    bounds.append(n)
    spans = [(bounds[i], bounds[i + 1] - 1) for i in range(len(bounds) - 1)]
    # merge spans that are too narrow into the previous one
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and (b - a + 1) < min_w:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def detect_cells(st: dict, floor: int, pitch: int, thr: float = 0.5,
                 min_w: int = 3) -> list[dict]:
    sy, sz, sx = st["vox"].shape
    y0 = floor * pitch
    y1 = min(y0 + pitch - 1, sy - 1)
    band = st["passable"]
    solid = ~band[y0:y1 + 1]
    fx = solid.mean(axis=(0, 1))         # per x
    fz = solid.mean(axis=(0, 2))         # per z
    px = wall_planes(fx >= thr)
    pz = wall_planes(fz >= thr)
    xs = intervals(px, sx, min_w)
    zs = intervals(pz, sz, min_w)
    cells = []
    for (z0, z1) in zs:
        for (x0, x1) in xs:
            cells.append({"bbox": (x0, y0, z0, x1, y1, z1),
                          "dx": x1 - x0 + 1, "dy": y1 - y0 + 1,
                          "dz": z1 - z0 + 1})
    return cells


def classify(cell: dict, st: dict) -> str:
    x0, y0, z0, x1, y1, z1 = cell["bbox"]
    names = st["names"]
    sub = st["vox"][y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
    door = stairs = 0
    for i, n in enumerate(names):
        c = int((sub == i).sum())
        if not c:
            continue
        if "door" in n:
            door += c
        elif "stairs" in n:
            stairs += c
    if door >= 4 or stairs >= 8:
        return "core"
    if min(cell["dx"], cell["dz"]) <= 3:
        return "corridor"
    return "room"


# ---------------------------------------------------------------- ports
def runs_1d(v: np.ndarray) -> list[tuple[int, int]]:
    out, s = [], None
    for i, x in enumerate(v):
        if x and s is None:
            s = i
        elif not x and s is not None:
            out.append((s, i - 1))
            s = None
    if s is not None:
        out.append((s, len(v) - 1))
    return out


def port_type(st: dict, box: tuple[int, int, int, int, int, int]
              ) -> tuple[str, list[str]]:
    """Classify an opening by the blocks in a small box around it."""
    names, vox = st["names"], st["vox"]
    sy, sz, sx = vox.shape
    x0, y0, z0, x1, y1, z1 = box
    sub = vox[max(0, y0):y1 + 1, max(0, z0):z1 + 1, max(0, x0):x1 + 1]
    kinds: set[str] = set()
    for i, n in enumerate(names):
        if not (sub == i).any():
            continue
        if "door" in n:
            kinds.add("door")
        elif "stairs" in n:
            kinds.add("stairs")
        elif "ladder" in n or "scaffolding" in n:
            kinds.add("climb")
        elif "slab" in n:
            kinds.add("slab")
        elif ("glass" in n or "pane" in n or "bars" in n):
            kinds.add("glass")
    if "door" in kinds:
        return "door", ["door"]
    if "stairs" in kinds:
        return "passage", ["stairs"]
    if "glass" in kinds or "pane" in kinds or "bars" in kinds:
        return "light", ["window"]
    return "passage", sorted(kinds)


def detect_ports(st: dict, bbox: tuple[int, int, int, int, int, int]
                 ) -> list[dict]:
    """Air-to-air openings between this module and the given neighbour boxes.

    ``neighbours`` are bboxes of adjacent cells (same band) or the full grid
    outside the cell.  Uses the source grid: an opening exists where a
    boundary cell of this module is passable AND the cell just outside (inside
    the neighbour) is passable too.
    """
    x0, y0, z0, x1, y1, z1 = bbox
    sy, sz, sx = st["vox"].shape
    P = st["passable"]
    ports: list[dict] = []
    faces = (("west", x0, x0 - 1, "z"),
             ("east", x1, x1 + 1, "z"),
             ("north", z0, z0 - 1, "x"),
             ("south", z1, z1 + 1, "x"))
    for face, inp, out, axis in faces:
        if out < 0:
            continue
        if axis == "z":
            if out >= sx:
                continue
            a = P[y0:y1 + 1, :, inp]
            b = P[y0:y1 + 1, :, out]
            lo, hi = z0, z1 + 1
        else:
            if out >= sz:
                continue
            a = P[y0:y1 + 1, inp, :]
            b = P[y0:y1 + 1, out, :]
            lo, hi = x0, x1 + 1
        m = (a[:, lo:hi] & b[:, lo:hi])          # (h, u)
        if not m.any():
            continue
        for (uu0, uu1) in runs_1d(m.any(axis=0)):
            col = m[:, uu0:uu1 + 1]
            for (ry0, ry1) in runs_1d(col.any(axis=1)):
                if axis == "z":
                    box = (inp - 1, y0 + ry0, z0 + uu0 - 1,
                           inp + 1, y0 + ry1, z0 + uu1 + 1)
                else:
                    box = (x0 + uu0 - 1, y0 + ry0, inp - 1,
                           x0 + uu1 + 1, y0 + ry1, inp + 1)
                t, tags = port_type(st, box)
                ports.append({
                    "id": f"{face[0]}{uu0}y{ry0}",
                    "type": t, "face": face,
                    "origin": [ry0, uu0],
                    "size": [ry1 - ry0 + 1, uu1 - uu0 + 1],
                    "tags": tags,
                })
    # vertical faces: probe (z, x) inside the module footprint only
    for face, out in (("up", y1 + 1), ("down", y0 - 1)):
        if out < 0 or out >= sy:
            continue
        layer = y1 if face == "up" else y0
        m = (P[layer, z0:z1 + 1, x0:x1 + 1] &
             P[out, z0:z1 + 1, x0:x1 + 1])       # (dz, dx)
        if not m.any():
            continue
        for (rz0, rz1) in runs_1d(m.any(axis=1)):
            row = m[rz0:rz1 + 1].any(axis=0)
            for (rx0, rx1) in runs_1d(row):
                box = (x0 + rx0 - 1, min(y0, out), z0 + rz0 - 1,
                       x0 + rx1 + 1, max(y1, out), z0 + rz1 + 1)
                t, tags = port_type(st, box)
                if "stairs" in tags:
                    t = "stair_up" if face == "up" else "stair_down"
                else:
                    t = "shaft"
                ports.append({
                    "id": f"{'u' if face == 'up' else 'd'}{rx0}x{rz0}",
                    "type": t, "face": face,
                    "origin": [rx0, rz0],
                    "size": [rx1 - rx0 + 1, rz1 - rz0 + 1],
                    "tags": tags or ["vertical"],
                })
    return ports


# ---------------------------------------------------------------- writing
def prune_palette(vox: np.ndarray, palette: list[dict]
                  ) -> tuple[np.ndarray, list[dict]]:
    """Drop unused palette entries, keep full properties, force air first."""
    used = sorted({int(i) for i in np.unique(vox)})
    air = None
    for i, e in enumerate(palette):
        if e["Name"].removeprefix("minecraft:") == "air":
            air = i
            break
    order = ([air] + [i for i in used if i != air]
             if air is not None and air in used else used)
    remap = {old: new for new, old in enumerate(order)}
    lut = np.zeros(int(vox.max()) + 1, dtype=np.int64)
    for old, new in remap.items():
        lut[old] = new
    return lut[vox].astype(np.uint16), [palette[i] for i in order]


def write_module(out_dir: Path, name: str, vox: np.ndarray,
                 palette_full: list[dict], spec: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    pvox, palette = prune_palette(vox, palette_full)
    sy, sz, sx = pvox.shape
    lt = out_dir / f"{name}.schem"
    S.write_structure(str(lt), pvox, palette, (0, 0, 0), (sx, sy, sz),
                      name=name, data_version=4903)
    write_text_lf(spec_path_for(lt),
        json.dumps(spec, ensure_ascii=False, indent=1))
    return lt


def slice_floor(st: dict, floor: int, pitch: int, out_dir: Path,
                prefix: str, thr: float, min_w: int,
                ceiling_min: float = 0.25) -> list[dict]:
    cells = detect_cells(st, floor, pitch, thr, min_w)
    names = st["names"]
    vox = st["vox"]
    P = st["passable"]
    made = []
    for idx, cell in enumerate(cells):
        x0, y0, z0, x1, y1, z1 = cell["bbox"]
        ceiling = 1.0 - P[y1, z0:z1 + 1, x0:x1 + 1].mean()
        if ceiling < ceiling_min:
            continue                      # outdoor / plaza chunk
        sub = vox[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
        kind = classify(cell, st)
        name = f"{prefix}f{floor}_{kind}{idx:02d}"
        ports = detect_ports(st, cell["bbox"])
        spec = {
            "id": name, "category": kind, "version": 1,
            "description": (f"{kind} sliced from floor {floor} "
                            f"({cell['dx']}x{cell['dy']}x{cell['dz']})"),
            "tags": ["sliced", f"floor{floor}", kind],
            "grid": {"size": [cell["dx"], cell["dy"], cell["dz"]],
                     "position": [0, 0, 0]},
            "axis": "x", "flip": True,
            "ports": ports,
            "notes": f"sliced at ({x0},{y0},{z0}) from source build",
        }
        lt = write_module(out_dir, name, sub, st["data"]["palette"], spec)
        made.append({"name": name, "kind": kind, "bbox": list(cell["bbox"]),
                     "dx": cell["dx"], "dy": cell["dy"], "dz": cell["dz"],
                     "ports": ports, "path": str(lt)})
    return made


def floor_band(st: dict, floor: int, pitch: int) -> dict:
    sy, sz, sx = st["vox"].shape
    y0 = floor * pitch
    y1 = min(y0 + pitch - 1, sy - 1)
    return {"bbox": (0, y0, 0, sx - 1, y1, sz - 1), "dx": sx,
            "dy": y1 - y0 + 1, "dz": sz}


# ---------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help=".schem/.schem 源建筑")
    ap.add_argument("--by", choices=("floor", "grid"), default="grid")
    ap.add_argument("--floor", type=int, default=0, help="楼层号(从 0)")
    ap.add_argument("--pitch", type=int, default=5, help="层高(默认 5)")
    ap.add_argument("--thr", type=float, default=0.5, help="墙投影阈值")
    ap.add_argument("--min-w", type=int, default=3, help="最小开间")
    ap.add_argument("--ceiling-min", type=float, default=0.25,
                    help="天花板实心率下限(低于此判为室外,不切)")
    ap.add_argument("--out-dir", default="sliced")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--register", action="store_true", help="写入资产包")
    ap.add_argument("--pack", default="modern-arch")
    ap.add_argument("--category", default=None, help="资产包分类目录")
    ap.add_argument("--plan", default=None, help="导出重组装配计划 JSON")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    st = load(a.input)
    st["passable"] = passable_mask(st["names"], st["vox"])
    stem = Path(a.input).stem
    prefix = a.prefix or f"{stem}_"
    out_dir = Path(a.out_dir)

    if a.by == "floor":
        band = floor_band(st, a.floor, a.pitch)
        x0, y0, z0, x1, y1, z1 = band["bbox"]
        cells = [band]
        # single whole-floor module
        names = st["names"]
        sub = st["vox"][y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
        name = f"{prefix}f{a.floor}_plate"
        spec = {"id": name, "category": "plate", "version": 1,
                "description": f"floor plate {a.floor}",
                "tags": ["sliced", "plate", f"floor{a.floor}"],
                "grid": {"size": [band["dx"], band["dy"], band["dz"]],
                         "position": [0, 0, 0]},
                "axis": "x", "flip": True, "ports": [], "notes": ""}
        lt = write_module(out_dir, name, sub, st["data"]["palette"], spec)
        made = [{"name": name, "kind": "plate", "bbox": list(band["bbox"]),
                 "dx": band["dx"], "dy": band["dy"], "dz": band["dz"],
                 "ports": [], "path": str(lt)}]
    else:
        made = slice_floor(st, a.floor, a.pitch, out_dir, prefix,
                           a.thr, a.min_w, a.ceiling_min)

    if a.register:
        cat = a.category or stem
        pack_dir = pack_modules(a.pack).parent
        pack_dir.mkdir(parents=True, exist_ok=True)
        pack_json = pack_dir / "pack.json"
        if not pack_json.exists():
            write_text_lf(pack_json, json.dumps({
                "id": a.pack,
                "name": f"{a.pack} (sliced)",
                "version": 1,
                "description": f"从 {stem} 切块生成的模块包",
                "provides": {"modules": [], "styles": [],
                             "generator": None},
                "tags": ["sliced"],
            }, ensure_ascii=False, indent=2) + "\n")
        dst = pack_modules(a.pack) / cat
        dst.mkdir(parents=True, exist_ok=True)
        for m in made:
            for ext in (".schem", ".module.json"):
                src = Path(m["path"]).with_suffix(ext)
                if src.exists():
                    shutil.copy2(src, dst / src.name)
        print(f"registered {len(made)} modules -> {dst}")

    if a.plan:
        plan = {
            "name": f"{stem}_sliced",
            "auto": False,
            "region": list(st["size"]),
            "modules": [{"ref": f"M{i}", "module": m["name"]}
                        for i, m in enumerate(made)],
            "place": [{"ref": f"M{i}", "pos": m["bbox"][:3], "rot": 0}
                      for i, m in enumerate(made)],
            "connections": [],
        }
        write_text_lf(Path(a.plan),
                      json.dumps(plan, ensure_ascii=False, indent=1))
        print(f"plan -> {a.plan}")

    if a.report:
        print(f"\n{'name':28} {'kind':9} {'尺寸':10} {'接口':30} bbox")
        for m in made:
            ports = ",".join(f"{p['face']}:{p['type']}x{p['size'][0]}"
                             for p in m["ports"])
            print(f"{m['name']:28} {m['kind']:9} "
                  f"{m['dx']}x{m['dy']}x{m['dz']:6} {ports:30} {m['bbox']}")
        print(f"\n{len(made)} 个模块 -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
