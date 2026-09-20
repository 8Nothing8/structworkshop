"""生成「画廊」结构：每种不完整方块各来一份，用来回归渲染器。

Each column is one block type; each row is one state.  Render it with::

    python -m mcrender.cli 方块画廊.schem --views iso,front,top --scale 4 --ssaa 3

The gallery is the visual regression test for the model resolver: slabs, stairs
(all facings/shapes), walls, fences, gates, panes, bars, doors, trapdoors,
chains, lanterns, rods, buttons, levers, grindstones, plants, glass and water.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
from mccore import structure_io as S  # noqa: E402

AIR = {"Name": "minecraft:air"}

# (column label, [(props, ...), ...])
GALLERY = [
    ("stone", [{}, ]),
    ("oak_planks", [{}, ]),
    ("glass", [{}, ]),
    ("oak_leaves", [{"distance": "7", "persistent": "true", "waterlogged": "false"}]),
    ("grass_block", [{"snowy": "false"}]),
    ("water", [{"level": "0"}]),
    ("oak_slab", [{"type": "bottom", "waterlogged": "false"},
                  {"type": "top", "waterlogged": "false"},
                  {"type": "double", "waterlogged": "false"}]),
    ("oak_stairs", [
        {"facing": "north", "half": "bottom", "shape": "straight", "waterlogged": "false"},
        {"facing": "east", "half": "bottom", "shape": "straight", "waterlogged": "false"},
        {"facing": "south", "half": "bottom", "shape": "straight", "waterlogged": "false"},
        {"facing": "west", "half": "bottom", "shape": "straight", "waterlogged": "false"},
        {"facing": "east", "half": "top", "shape": "straight", "waterlogged": "false"},
        {"facing": "east", "half": "bottom", "shape": "inner_left", "waterlogged": "false"},
        {"facing": "east", "half": "bottom", "shape": "outer_right", "waterlogged": "false"},
    ]),
    ("polished_deepslate_wall", [
        {"up": "true", "north": "none", "east": "none", "south": "none", "west": "none", "waterlogged": "false"},
        {"up": "true", "north": "tall", "east": "none", "south": "none", "west": "none", "waterlogged": "false"},
        {"up": "true", "north": "tall", "east": "low", "south": "tall", "west": "low", "waterlogged": "false"},
        {"up": "false", "north": "tall", "east": "tall", "south": "tall", "west": "tall", "waterlogged": "false"},
    ]),
    ("oak_fence", [
        {"north": "false", "east": "false", "south": "false", "west": "false", "waterlogged": "false"},
        {"north": "true", "east": "false", "south": "false", "west": "false", "waterlogged": "false"},
        {"north": "true", "east": "true", "south": "true", "west": "true", "waterlogged": "false"},
    ]),
    ("oak_fence_gate", [
        {"facing": "north", "open": "false", "in_wall": "false", "powered": "false"},
        {"facing": "north", "open": "true", "in_wall": "false", "powered": "false"},
    ]),
    ("glass_pane", [
        {"north": "false", "east": "false", "south": "false", "west": "false", "waterlogged": "false"},
        {"north": "true", "east": "false", "south": "false", "west": "false", "waterlogged": "false"},
        {"north": "true", "east": "true", "south": "true", "west": "true", "waterlogged": "false"},
    ]),
    ("iron_bars", [
        {"north": "false", "east": "false", "south": "false", "west": "false", "waterlogged": "false"},
        {"north": "true", "east": "true", "south": "false", "west": "false", "waterlogged": "false"},
    ]),
    ("oak_door", [
        {"facing": "north", "half": "lower", "hinge": "left", "open": "false", "powered": "false"},
        {"facing": "north", "half": "upper", "hinge": "left", "open": "false", "powered": "false"},
        {"facing": "east", "half": "lower", "hinge": "right", "open": "true", "powered": "false"},
    ]),
    ("dark_oak_trapdoor", [
        {"facing": "north", "half": "bottom", "open": "false", "powered": "false", "waterlogged": "false"},
        {"facing": "north", "half": "top", "open": "false", "powered": "false", "waterlogged": "false"},
        {"facing": "north", "half": "bottom", "open": "true", "powered": "false", "waterlogged": "false"},
    ]),
    ("iron_chain", [{"axis": "y", "waterlogged": "false"},
                    {"axis": "x", "waterlogged": "false"},
                    {"axis": "z", "waterlogged": "false"}]),
    ("lantern", [{"hanging": "false", "waterlogged": "false"},
                 {"hanging": "true", "waterlogged": "false"}]),
    ("end_rod", [{"facing": "up"}, {"facing": "down"}, {"facing": "north"}, {"facing": "east"}]),
    ("grindstone", [{"face": "floor", "facing": "north"},
                    {"face": "wall", "facing": "west"},
                    {"face": "ceiling", "facing": "north"}]),
    ("stone_button", [{"face": "floor", "facing": "north", "powered": "false"},
                      {"face": "wall", "facing": "north", "powered": "false"},
                      {"face": "ceiling", "facing": "north", "powered": "false"}]),
    ("lever", [{"face": "floor", "facing": "north", "powered": "false"},
               {"face": "wall", "facing": "east", "powered": "false"},
               {"face": "ceiling", "facing": "west", "powered": "false"}]),
    ("oak_log", [{"axis": "y"}, {"axis": "x"}, {"axis": "z"}]),
    ("smooth_quartz", [{}]),
    ("sea_lantern", [{}]),
    ("moss_carpet", [{}]),
    ("hanging_roots", [{"waterlogged": "false"}]),
]


def build(out: str = "方块画廊.schem") -> None:
    palette: list[dict] = [AIR]
    index: dict[tuple, int] = {}

    def pid(name: str, props: dict) -> int:
        key = (name, tuple(sorted(props.items())))
        if key in index:
            return index[key]
        entry = {"Name": "minecraft:" + name}
        if props:
            entry["Properties"] = {k: str(v) for k, v in props.items()}
        palette.append(entry)
        index[key] = len(palette) - 1
        return index[key]

    step = 3
    max_rows = max(len(states) for _, states in GALLERY)
    sx = len(GALLERY) * step + 2
    sz = max_rows * step + 2
    sy = 6
    vox = np.zeros((sy, sz, sx), dtype=np.uint16)
    for ci, (name, states) in enumerate(GALLERY):
        for ri, props in enumerate(states):
            x = 1 + ci * step
            z = 1 + ri * step
            vox[1, z, x] = pid(name, props)
    S.write_structure(out, vox, palette, (0, 0, 0), (sx, sy, sz),
                      name="Block Gallery", author="structworkshop")
    print("wrote %s  %dx%dx%d  %d palette entries" % (out, sx, sy, sz, len(palette)))


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "方块画廊.schem")
