"""Shared office-series layout kit (historical).

Constants and ground-plane helpers used by the retired office series
(``office-slab`` / ``office-tower`` / ``setback-tower`` compositions were removed;
source restorable from git history). Kept as a generic layout reference.

Module grid (z = depth, 17 deep):  z 0..6 north office row, z 7..9 corridor,
z 10..16 south office row.  x 0..11 west core, rooms every 9 from x=12,
east core at x=48.  Floor stack is 5 = 1 slab + 3 clear + 1 ceiling.
"""
from __future__ import annotations

from mckit.grammar import Kit, planter, plaza_pave

FH = 5
ROOM_W = 9
CORE_W = 12
N_ROOMS = 4
X_ROOMS = [12 + i * ROOM_W for i in range(N_ROOMS)]
X_CORE_B = 48
Z_N, Z_C, Z_S = 0, 7, 10
W, D = 60, 17

TREES = [(-6, -6), (-6, 18), (62, -6), (62, 18),
         (18, -6), (38, -6), (14, 20), (44, 20)]
LAMPS = [(-4, 6), (-4, 14), (64, 6), (64, 14),
         (16, -4), (34, -4), (20, 22), (24, 22), (28, 22), (36, 22)]
PLANTERS = [(4, 6, -4, -2), (52, 54, -4, -2),
            (4, 6, 18, 20), (52, 54, 18, 20)]


def window_ranges() -> dict[str, list[tuple[int, int]]]:
    rooms_ns = [(x + 2, x + 3) for x in X_ROOMS] + \
               [(x + 5, x + 6) for x in X_ROOMS]
    return {
        "north": rooms_ns + [(4, 6), (53, 55)],
        "south": rooms_ns + [(4, 6), (53, 55)],
        "west": [(7, 9), (12, 14)],
        "east": [(2, 4), (7, 9)],
    }


def entrance(k: Kit) -> None:
    """Ground-floor entrance bay on the south facade (room index 1)."""
    x0 = X_ROOMS[1]
    ex = [x0 + 3, x0 + 5]                       # 3-wide opening
    k.box(ex[0], ex[1], 1, 3, D - 1, D - 1, "air", overwrite=True)
    k.box(ex[0], ex[1], 1, 3, D - 2, D - 2, "air", overwrite=True)
    for x in range(ex[0], ex[1] + 1):
        k.set(x, 1, D, "air", overwrite=True)   # clear the sill under the door
    k.box(x0 + 2, x0 + 2, 1, 3, D - 1, D - 1, "polished_blackstone",
          overwrite=True)                       # dark door jambs
    k.box(x0 + 6, x0 + 6, 1, 3, D - 1, D - 1, "polished_blackstone",
          overwrite=True)
    # portal frame (dark) + canopy (white) + two outer columns
    for x in (x0 + 2, x0 + 6):
        k.box(x, x, 1, 3, D + 1, D + 1, "polished_blackstone")
    k.box(x0 + 2, x0 + 6, 4, 4, D + 1, D + 4, "white_concrete",
          overwrite=True)
    k.box(x0 + 2, x0 + 6, 4, 4, D + 1, D + 1, "polished_blackstone",
          overwrite=True)                       # dark portal beam
    for x in (x0 + 2, x0 + 6):
        k.box(x, x, 1, 3, D + 4, D + 4, "white_concrete")
        k.set(x, 0, D + 4, "polished_blackstone", overwrite=True)
    k.box(x0 + 3, x0 + 5, 0, 0, D + 1, D + 3, "polished_andesite",
          overwrite=True)                       # entrance apron
    for bx in (x0 + 3, x0 + 5):                     # bollards + lit sign
        k.set(bx, 1, D + 3, "polished_blackstone")
    k.set(x0 + 3, 4, D + 1, "sea_lantern", overwrite=True)
    k.set(x0 + 5, 4, D + 1, "sea_lantern", overwrite=True)
    planter(k, x0 + 1, x0 + 2, D + 2, D + 3, 1)
    planter(k, x0 + 6, x0 + 7, D + 2, D + 3, 1)


def plaza(k: Kit) -> None:
    plaza_pave(k, -8, 67, -8, 24, 0, "smooth_stone", "polished_andesite",
               period=8, skip=(0, W - 1, 0, D - 1))
    for x0, x1, z0, z1 in PLANTERS:
        planter(k, x0, x1, z0, z1, 1)
    for x, z in LAMPS:                               # glowing street lamps
        k.set(x, 4, z, "sea_lantern", overwrite=True)
        k.set(x, 5, z, "end_rod", overwrite=True)
    for x in range(-8, 68):                          # site edge + hedges
        k.set(x, 0, -8, "polished_blackstone", overwrite=True)
        k.set(x, 0, 24, "polished_blackstone", overwrite=True)
        k.set(x, 1, -7, "oak_leaves")
        if not (16 <= x <= 34):
            k.set(x, 1, 23, "oak_leaves")
    for z in range(-8, 25):
        k.set(-8, 0, z, "polished_blackstone", overwrite=True)
        k.set(67, 0, z, "polished_blackstone", overwrite=True)
