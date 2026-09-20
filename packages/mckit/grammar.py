"""建筑语法套件：立面 / 楼层线 / 屋顶 / 地面细节。

Sits on top of the modular assembler (python -m mccore.assemble). Modules provide the
rooms / corridors / cores; this kit dresses the envelope with human-scale
detail: inset windows + protruding sills, floor bands, vertical piers, parapet
+ coping, entrance canopy, roof equipment and the ground plaza.

Everything writes through `Kit`, which shares the Assembler palette, so the
whole pipeline (module assembly + grammar) comes out as one .schem.

Usage (library):
    from arch_grammar import Kit, facade_window, floor_band_ring
    k = Kit(asm)
    facade_window(k, "north", 0, y0, [(14, 15), (17, 18)])
    floor_band_ring(k, 0, 59, 0, 16, y0 + 4, "light_gray_concrete")

Self test:
    python -m mckit.grammar --self-test
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from mccore.assemble import Assembler  # noqa: E402

# ---------------------------------------------------------------- palette
# Modern office kit. Three-colour law: structural white / light grey >= 60%,
# secondary grey concrete ~ 30%, wood / accent <= 10%.
BLOCKS: dict[str, dict] = {
    "air": {"Name": "minecraft:air"},
    "glass": {"Name": "minecraft:glass"},
    "tinted_glass": {"Name": "minecraft:tinted_glass"},
    "gray_stained_glass": {"Name": "minecraft:gray_stained_glass"},
    "light_blue_stained_glass": {"Name": "minecraft:light_blue_stained_glass"},
    "white_concrete": {"Name": "minecraft:white_concrete"},
    "light_gray_concrete": {"Name": "minecraft:light_gray_concrete"},
    "gray_concrete": {"Name": "minecraft:gray_concrete"},
    "smooth_stone": {"Name": "minecraft:smooth_stone"},
    "polished_andesite": {"Name": "minecraft:polished_andesite"},
    "polished_blackstone": {"Name": "minecraft:polished_blackstone"},
    "sea_lantern": {"Name": "minecraft:sea_lantern"},
    "iron_bars": {"Name": "minecraft:iron_bars"},
    "iron_chain": {"Name": "minecraft:iron_chain",
                   "Properties": {"axis": "y", "waterlogged": "false"}},
    "lantern": {"Name": "minecraft:lantern",
                "Properties": {"hanging": "true", "waterlogged": "false"}},
    "copper_pipe": {"Name": "minecraft:waxed_oxidized_copper"},
    "oak_planks": {"Name": "minecraft:oak_planks"},
    "dirt": {"Name": "minecraft:dirt"},
    "stripped_oak_log": {"Name": "minecraft:stripped_oak_log"},
    "azalea_leaves": {"Name": "minecraft:azalea_leaves",
                      "Properties": {"distance": "7", "persistent": "true",
                                     "waterlogged": "false"}},
    "oak_leaves": {"Name": "minecraft:oak_leaves",
                   "Properties": {"distance": "7", "persistent": "true",
                                  "waterlogged": "false"}},
    # detail blocks -------------------------------------------------------
    "slab": {"Name": "minecraft:smooth_stone_slab"},
    "slab_gray": {"Name": "minecraft:polished_andesite_slab"},
    "slab_top": {"Name": "minecraft:smooth_stone_slab",
                 "Properties": {"type": "top"}},
    "stairs_n": {"Name": "minecraft:polished_andesite_stairs",
                 "Properties": {"facing": "north", "half": "bottom",
                                "shape": "straight", "waterlogged": "false"}},
    "stairs_s": {"Name": "minecraft:polished_andesite_stairs",
                 "Properties": {"facing": "south", "half": "bottom",
                                "shape": "straight", "waterlogged": "false"}},
    "end_rod": {"Name": "minecraft:end_rod",
                "Properties": {"facing": "up"}},
}


class Kit:
    """Thin block writer over an Assembler world grid."""

    def __init__(self, asm: Assembler):
        self.asm = asm
        self._idx: dict[str, int] = {}

    def idx(self, key: str) -> int:
        if key not in self._idx:
            entry = BLOCKS[key]
            self._idx[key] = (0 if entry["Name"] == "minecraft:air"
                              else self.asm._palette_idx(entry))
        return self._idx[key]

    def set(self, x: int, y: int, z: int, key: str,
            overwrite: bool = False) -> None:
        asm = self.asm
        asm._grow([x, y, z], [x + 1, y + 1, z + 1])
        ox, oy, oz = asm.origin
        cell = asm.world[y - oy, z - oz, x - ox]
        if overwrite or cell == 0:
            asm.world[y - oy, z - oz, x - ox] = self.idx(key)

    def box(self, x0: int, x1: int, y0: int, y1: int, z0: int, z1: int,
            key: str, overwrite: bool = False) -> None:
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    self.set(x, y, z, key, overwrite)

    def has(self, x: int, y: int, z: int) -> bool:
        asm = self.asm
        ox, oy, oz = asm.origin
        return int(asm.world[y - oy, z - oz, x - ox]) != 0


# ---------------------------------------------------------------- grammar
def facade_window(k: Kit, face: str, wall: int, y0: int,
                  ranges: list[tuple[int, int]], h: int = 2,
                  glass: str = "glass", sill_key: str = "slab_gray",
                  sill_drop: int = 1, sill: bool = True,
                  y_from: int = 2) -> None:
    """Dress a carved window opening on an exterior wall.

    face  : 'north' wall at z=wall / 'south' / 'west' x=wall / 'east'
    ranges: inclusive (u0, u1) spans along the wall
    glass is set 1 block inside (reveal); a slab sill protrudes 1 block
    outside at y0+sill_drop (window bottom defaults to y0+2).
    """
    inside = {"north": (0, 1), "south": (0, -1),
              "west": (1, 0), "east": (-1, 0)}[face]
    outside = {"north": (0, -1), "south": (0, 1),
               "west": (-1, 0), "east": (1, 0)}[face]
    for u0, u1 in ranges:
        for u in range(u0, u1 + 1):
            for dy in range(h):
                y = y0 + y_from + dy
                if face in ("north", "south"):
                    k.set(u, y, wall + inside[1], glass, overwrite=True)
                else:
                    k.set(wall + inside[0], y, u, glass, overwrite=True)
        if sill:
            ys = y0 + sill_drop
            for u in range(u0, u1 + 1):
                if face in ("north", "south"):
                    k.set(u, ys, wall + outside[1], sill_key, overwrite=True)
                else:
                    k.set(wall + outside[0], ys, u, sill_key, overwrite=True)


def floor_band_ring(k: Kit, x0: int, x1: int, z0: int, z1: int, y: int,
                    key: str, out: int = 1) -> None:
    """Continuous horizontal floor band protruding `out` around the envelope."""
    for x in range(x0 - out, x1 + 1 + out):
        for d in range(1, out + 1):
            k.set(x, y, z0 - d, key, overwrite=True)
            k.set(x, y, z1 + d, key, overwrite=True)
    for z in range(z0 - out, z1 + 1 + out):
        for d in range(1, out + 1):
            k.set(x0 - d, y, z, key, overwrite=True)
            k.set(x1 + d, y, z, key, overwrite=True)


def pier(k: Kit, face: str, wall: int, y0: int, h: int,
         us: list[int], key: str = "white_concrete") -> None:
    """Vertical facade ribs at `us`, protruding 1 block outside the wall."""
    outside = {"north": (0, -1), "south": (0, 1),
               "west": (-1, 0), "east": (1, 0)}[face]
    for u in us:
        for dy in range(h):
            y = y0 + dy
            if face in ("north", "south"):
                k.set(u, y, wall + outside[1], key, overwrite=True)
            else:
                k.set(wall + outside[0], y, u, key, overwrite=True)


def parapet(k: Kit, x0: int, x1: int, z0: int, z1: int, y0: int, h: int = 2,
            key: str = "white_concrete", cap: str = "slab") -> None:
    """Roof parapet ring + coping slab."""
    for dy in range(h):
        y = y0 + dy
        for x in range(x0, x1 + 1):
            k.set(x, y, z0, key, overwrite=True)
            k.set(x, y, z1, key, overwrite=True)
        for z in range(z0, z1 + 1):
            k.set(x0, y, z, key, overwrite=True)
            k.set(x1, y, z, key, overwrite=True)
    yc = y0 + h
    for x in range(x0, x1 + 1):
        k.set(x, yc, z0, cap, overwrite=True)
        k.set(x, yc, z1, cap, overwrite=True)
    for z in range(z0, z1 + 1):
        k.set(x0, yc, z, cap, overwrite=True)
        k.set(x1, yc, z, cap, overwrite=True)


def stair_flight(k: Kit, x0: int, x1: int, z0: int, base: int,
                 key: str = "stairs_n", steps: int = 5,
                 dz: int = 1) -> None:
    """Straight run, 1 rise per block, from floor `base`.

    Step i sits at y=base+1+i, z=z0+i*dz; the last step is at the next floor
    slab level, inside the module floor hole above. dz=-1 mirrors it.
    """
    for i in range(steps):
        y = base + 1 + i
        z = z0 + i * dz
        for x in range(x0, x1 + 1):
            k.set(x, y, z, key, overwrite=True)


def roof_box(k: Kit, x0: int, x1: int, y0: int, y1: int, z0: int, z1: int,
             key: str = "gray_concrete", grille: str | None = "iron_bars",
             grille_face: str = "south") -> None:
    """Simple roof equipment block with an optional louvre face."""
    k.box(x0, x1, y0, y1, z0, z1, key)
    if grille:
        if grille_face in ("north", "south"):
            zz = z1 if grille_face == "south" else z0
            for x in range(x0 + 1, x1):
                k.set(x, y1, zz, grille, overwrite=True)
        else:
            xx = x1 if grille_face == "east" else x0
            for z in range(z0 + 1, z1):
                k.set(xx, y1, z, grille, overwrite=True)


def tank(k: Kit, cx: int, cz: int, y0: int, h: int = 3,
         key: str = "smooth_stone", cap: str = "slab") -> None:
    """3x3 rooftop water tank (corners trimmed)."""
    for y in range(y0, y0 + h):
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if abs(dx) == 1 and abs(dz) == 1:
                    continue
                k.set(cx + dx, y, cz + dz, key, overwrite=True)
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            k.set(cx + dx, y0 + h, cz + dz, cap, overwrite=True)


def planter(k: Kit, x0: int, x1: int, z0: int, z1: int, y: int,
            pot: str = "smooth_stone", plant: str = "azalea_leaves",
            soil: str = "dirt") -> None:
    """Low planter: rim at y, soil + plant inside, connected (no floaters)."""
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            edge = x in (x0, x1) or z in (z0, z1)
            if edge:
                k.set(x, y, z, pot, overwrite=True)
            else:
                k.set(x, y, z, soil, overwrite=True)
                k.set(x, y + 1, z, plant, overwrite=True)
    if z0 == z1:                                  # 1-deep planter
        for x in range(x0 + 1, x1):
            k.set(x, y, z0, soil, overwrite=True)
            k.set(x, y + 1, z0, plant, overwrite=True)
    if x0 == x1:                                  # 1-wide planter
        for z in range(z0 + 1, z1):
            k.set(x0, y, z, soil, overwrite=True)
            k.set(x0, y + 1, z, plant, overwrite=True)


def plaza_pave(k: Kit, x0: int, x1: int, z0: int, z1: int, y: int,
               base: str = "smooth_stone", joint: str = "polished_andesite",
               period: int = 8, skip: tuple[int, int, int, int] | None = None
               ) -> None:
    """Paving with geometric joints (pattern from geometry, not noise)."""
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            if skip and skip[0] <= x <= skip[1] and skip[2] <= z <= skip[3]:
                continue
            key = joint if (x % period == 0 or z % period == 0) else base
            k.set(x, y, z, key, overwrite=False)


# ---------------------------------------------------------------- self test
def _self_test() -> int:
    asm = Assembler((40, 24, 40))
    k = Kit(asm)
    # wall with a carved window opening
    k.box(0, 0, 1, 3, 0, 8, "light_gray_concrete")
    k.box(0, 0, 2, 3, 3, 5, "air", overwrite=True)
    facade_window(k, "west", 0, 0, [(3, 5)])
    floor_band_ring(k, 0, 0, 0, 8, 4, "white_concrete")
    pier(k, "west", 0, 0, 4, [2, 6])
    parapet(k, 0, 8, 0, 8, 10, 2, "white_concrete", "slab")
    stair_flight(k, 10, 11, 1, 0)
    tank(k, 20, 20, 1)
    planter(k, 24, 26, 24, 26, 1)
    plaza_pave(k, 28, 36, 28, 36, 0)
    w = asm.world
    assert (w != 0).sum() > 100, (w != 0).sum()
    # glass must be inside the wall plane and sills outside
    ox, oy, oz = asm.origin
    names = [e["Name"].removeprefix("minecraft:") for e in asm.palette]
    def at(x, y, z):
        return names[int(w[y - oy, z - oz, x - ox])]
    assert at(1, 2, 4) == "glass", at(1, 2, 4)
    assert at(-1, 1, 4) == "polished_andesite_slab", at(-1, 1, 4)
    assert at(-1, 0, 2) == "white_concrete", at(-1, 0, 2)
    print("arch_grammar self-test OK: %d blocks, palette %d"
          % (int((w != 0).sum()), len(asm.palette)))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    print(__doc__)
