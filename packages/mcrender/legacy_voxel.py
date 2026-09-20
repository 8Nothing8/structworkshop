"""旧版正交体素渲染器：结构 → PNG（已被 mcrender.renderer 取代，保留做 A/B）。

Usage:
  python -m mcrender.legacy_voxel <file.schem> [out_prefix]
"""
from __future__ import annotations

import math
import sys

import numpy as np
from PIL import Image

from mccore import structure_io as S  # noqa: E402

C = {
    "light_blue_stained_glass": (150, 195, 228),
    "white_stained_glass": (235, 240, 245),
    "gray_stained_glass": (150, 155, 160),
    "tinted_glass": (52, 54, 60),
    "black_stained_glass": (16, 16, 20),
    "glass": (200, 220, 235),
    "white_concrete": (207, 213, 214),
    "smooth_quartz": (236, 230, 223),
    "quartz_block": (236, 230, 223),
    "light_gray_concrete": (125, 125, 115),
    "calcite": (223, 224, 220),
    "white_terracotta": (209, 178, 161),
    "polished_blackstone": (48, 44, 52),
    "black_concrete": (10, 11, 16),
    "polished_deepslate": (72, 72, 76),
    "deepslate_tiles": (55, 55, 58),
    "smooth_stone": (158, 158, 158),
    "stone_bricks": (122, 122, 122),
    "iron_block": (220, 220, 220),
    "iron_bars": (200, 200, 200),
    "chain": (110, 110, 115),
    "iron_chain": (110, 110, 115),
    "waxed_lightning_rod": (205, 140, 90),
    "waxed_oxidized_copper": (82, 158, 132),
    "waxed_oxidized_cut_copper": (75, 150, 125),
    "oxidized_copper": (82, 158, 132),
    "waxed_copper_grate": (70, 140, 118),
    "waxed_copper_bulb": (255, 195, 115),
    "lightning_rod": (205, 140, 90),
    "lantern": (240, 200, 120),
    "end_rod": (240, 235, 225),
    "sea_lantern": (215, 232, 226),
    "glowstone": (250, 225, 140),
    "ochre_froglight": (250, 230, 180),
    "pearlescent_froglight": (250, 220, 245),
    "shroomlight": (250, 180, 110),
    "moss_block": (85, 120, 55),
    "moss_carpet": (85, 120, 55),
    "azalea_leaves": (70, 110, 45),
    "flowering_azalea_leaves": (110, 135, 65),
    "spruce_leaves": (55, 85, 50),
    "hanging_roots": (150, 120, 90),
    "dark_oak_planks": (66, 43, 20),
    "dark_oak_log": (60, 40, 20),
    "water": (60, 110, 190),
    "polished_diorite": (200, 200, 200),
    "polished_andesite": (130, 130, 130),
    "polished_granite": (155, 110, 90),
    "gray_concrete": (55, 58, 60),
    "blue_ice": (110, 170, 240),
}
FALLBACK = (180, 180, 180)

FACES = [
    ((0, 0, 1), (0, 0, 1)),
    ((0, 0, -1), (0, 0, -1)),
    ((0, 1, 0), (0, 1, 0)),
    ((0, -1, 0), (0, -1, 0)),
    ((1, 0, 0), (1, 0, 0)),
    ((-1, 0, 0), (-1, 0, 0)),
]
LIGHT = np.array([0.45, 0.80, 0.40])
LIGHT = LIGHT / np.linalg.norm(LIGHT)


def shifted(a, dy, dz, dx):
    out = np.zeros_like(a)
    y0, y1 = max(0, dy), a.shape[0] + min(0, dy)
    z0, z1 = max(0, dz), a.shape[1] + min(0, dz)
    x0, x1 = max(0, dx), a.shape[2] + min(0, dx)
    out[y0:y1, z0:z1, x0:x1] = a[y0 - dy:y1 - dy, z0 - dz:z1 - dz, x0 - dx:x1 - dx]
    return out


def render(vox, colors, azim, elev, out, scale=2, pad=12):
    occ = vox != 0
    best = np.full(occ.shape, -2.0, dtype=np.float32)
    bestn = np.zeros(occ.shape, dtype=np.int8)
    az, el = math.radians(azim), math.radians(elev)
    d = np.array([math.sin(az) * math.cos(el), -math.sin(el),
                  math.cos(az) * math.cos(el)])
    for i, ((dy, dz, dx), n) in enumerate(FACES):
        exp = occ & ~shifted(occ, dy, dz, dx)
        dot = float(-(n[0] * d[0] + n[1] * d[1] + n[2] * d[2]))
        upd = exp & (dot > best)
        best[upd] = dot
        bestn[upd] = i

    vis = best > -2.0
    ys, zs, xs = np.nonzero(vis)
    if len(xs) == 0:
        return
    n = bestn[ys, zs, xs].astype(np.int32)
    nx = np.choose(n, [0, 0, 0, 0, 1, -1]).astype(np.float32)
    ny = np.choose(n, [0, 0, 1, -1, 0, 0]).astype(np.float32)
    nz = np.choose(n, [1, -1, 0, 0, 0, 0]).astype(np.float32)

    # light
    diff = np.clip(nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2], 0.0, 1.0)
    shade = 0.42 + 0.58 * diff
    # gentle height tint so the massing reads
    shade *= 0.86 + 0.14 * (ys / float(vox.shape[0]))

    base = colors[vox[ys, zs, xs].astype(np.int32)]
    rgb = np.clip(base.astype(np.float32) * shade[:, None], 0, 255).astype(np.uint8)

    # camera basis
    world_up = np.array([0.0, 1.0, 0.0])
    right = np.cross(d, world_up)
    right /= np.linalg.norm(right) + 1e-9
    up = np.cross(right, d)
    up /= np.linalg.norm(up) + 1e-9

    P = np.stack([xs.astype(np.float32), ys.astype(np.float32),
                  zs.astype(np.float32)], axis=1)
    sx = P @ right
    sy = P @ up
    depth = P @ d

    sx -= sx.min()
    sy -= sy.min()
    W = int(sx.max() * scale) + scale + 2 * pad
    H = int(sy.max() * scale) + scale + 2 * pad
    px = (sx * scale).astype(np.int64) + pad
    py = (H - 1 - pad - (sy * scale).astype(np.int64))

    order = np.argsort(-depth)  # far -> near
    px, py, rgb = px[order], py[order], rgb[order]

    img = np.zeros((H, W, 3), dtype=np.uint8)
    # sky gradient
    grad = np.linspace(228, 176, H).astype(np.uint8)
    img[:, :, 0] = grad[:, None]
    img[:, :, 1] = (grad[:, None] * 1.0).astype(np.uint8)
    img[:, :, 2] = np.clip(grad[:, None] * 1.03, 0, 255).astype(np.uint8)

    for oy in range(scale):
        for ox in range(scale):
            yy = np.clip(py + oy, 0, H - 1)
            xx = np.clip(px + ox, 0, W - 1)
            img[yy, xx] = rgb
    Image.fromarray(img).save(out)
    print("wrote %s  (%dx%d, %d visible blocks)" % (out, W, H, len(xs)))


def main():
    if len(sys.argv) < 2:
        # 故意**不给**默认文件名：以前写死过一个本机作品名，别人 clone 下来跑必然
        # FileNotFoundError（而且把本机作品名泄出去了）。现在缺参数就打印用法退出。
        print(__doc__.strip())
        raise SystemExit(2)
    src = sys.argv[1]
    prefix = sys.argv[2] if len(sys.argv) > 2 else "render"
    d = S.read_structure(src)
    vox = d["voxels"]
    names = [p["Name"].replace("minecraft:", "") for p in d["palette"]]
    colors = np.array([C.get(nm, FALLBACK) for nm in names], dtype=np.uint8)
    views = [
        ("front", 0, 12),
        ("q45", 45, 14),
        ("q135", 135, 14),
        ("back", 180, 12),
        ("high", 35, 35),
    ]
    for tag, az, el in views:
        render(vox, colors, az, el, "%s_%s.png" % (prefix, tag))


if __name__ == "__main__":
    main()
