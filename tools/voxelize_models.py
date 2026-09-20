"""把下载的 3D 模型批量倒模成 structworkshop 模块（`.schem` + `.module.json`）。

    python tools/voxelize_models.py models/glb --out packs/gun-models/modules \
        --pack packs/gun-models --long 64 --map auto
    python tools/voxelize_models.py gun.glb --out /tmp/x --long 128 --map metal

* `--map`：`auto`（按明度自动）· `metal`（冷灰钢）· `bone`（白骨）· `brass`（黄铜）
* 生成的 `.module.json` 带 `grid.size`（模块尺寸）与 `anchor` 接口，能被
  `mccore.module_lib` / mcstudio 直接识别；
* `--pack` 指定后会把模型登记进资产包并跑一次 `mccore.pack scan`。

支持的输入：`.glb` / `.gltf` / `.obj`（见 `mckit.meshvox`）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages"))

from mccore import structure_io as S  # noqa: E402
from mckit import meshvox as MV  # noqa: E402

SUFFIXES = (".glb", ".gltf", ".obj")


def convert(src: Path, out: Path, long_side: int, mat_map: str,
            category: str, tags: list[str], dry: bool = False) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in ([src] if src.is_file()
                               else src.rglob("*")) if p.suffix.lower() in SUFFIXES)
    done = []
    for f in files:
        tag = f.stem
        try:
            grid = MV.to_grid(f, long_side=long_side, mat_map=MV.MAPS[mat_map])
        except Exception as e:                       # noqa: BLE001
            print(f"  ! {tag}: {type(e).__name__} {e}")
            continue
        sx, sy, sz = grid.sx, grid.sy, grid.sz
        if dry:
            print(f"  ~ {tag}: {sx}x{sy}x{sz}")
            continue
        S.write_structure(str(out / f"{tag}.schem"), grid.v, grid.pal, (0, 0, 0),
                          (sx, sy, sz), name=tag, author="structworkshop/meshvox",
                          description=f"{sx}x{sy}x{sz} 模型倒模（{f.name}）")
        spec = {
            "id": tag, "category": category, "version": 1,
            "description": f"{sx}x{sy}x{sz} 由 {f.name} 倒模的体素模块",
            "tags": list(tags) + ["voxelized", f"src:{f.suffix.lstrip('.')}"],
            "axis": "x", "flip": True,
            "ports": [{"id": "anchor", "type": "anchor", "face": "down",
                       "origin": [0, 0], "size": [sx, sz], "tags": []}],
            "grid": {"size": [sx, sy, sz], "position": [0, 0, 0]},
            "taxonomy": {"source": "poly-pizza", "kind": category},
        }
        (out / f"{tag}.module.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        done.append({"id": tag, "size": [sx, sy, sz], "cells": grid.count()})
        print(f"  + {tag:34s} {sx:3d}x{sy:3d}x{sz:3d} {grid.count():7d} 格")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="模型文件或目录（.glb/.gltf/.obj）")
    ap.add_argument("--out", required=True, help="输出目录（放 .schem + .module.json）")
    ap.add_argument("--long", type=int, default=64, help="体素化后的长边格数")
    ap.add_argument("--map", default="auto", choices=tuple(MV.MAPS))
    ap.add_argument("--category", default="model", help="模块分类")
    ap.add_argument("--tag", action="append", default=[], help="附加标签（可重复）")
    ap.add_argument("--pack", default=None, help="资产包目录：写 pack.json 并重建索引")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    tags = ["model", "imported"] + a.tag
    done = convert(Path(a.src), Path(a.out), a.long, a.map, a.category, tags,
                   dry=a.dry_run)
    if a.pack and not a.dry_run:
        pack = Path(a.pack)
        pj = pack / "pack.json"
        if not pj.is_file():
            pj.write_text(json.dumps({
                "id": pack.name, "name": f"{pack.name}（模型倒模模块）",
                "version": "0.1.0",
                "description": "由外部 3D 模型（.glb/.obj）倒模得到的体素模块库："
                               "枪械 / 骷髅 / 弹药。用 tools/voxelize_models.py 生成。",
                "author": "structworkshop", "license": "CC0/CC-BY（见各模型来源）",
                "mc_versions": ["1.21.4"], "engine_requires": ">=0.2.0",
                "dependencies": {}, "tags": ["model", "voxelized"],
            }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        import subprocess  # noqa: PLC0415
        sys.path.insert(0, str(REPO / "packages"))
        from mccore.paths import child_env  # noqa: PLC0415
        subprocess.run([sys.executable, "-m", "mccore.pack", "scan"], cwd=REPO,
                       check=False, env=child_env())
    print(f"\n共 {len(done)} 个模块 -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
