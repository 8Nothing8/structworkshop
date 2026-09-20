"""把画廊每一列渲染成带标签的特写 → 拼成一张联络表（视觉回归用）。

Used as the visual regression test for the model resolver / renderer.

    python -m mcrender.sheet 方块画廊.schem 画廊sheet.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
from mccore import structure_io as S  # noqa: E402
from mcrender.assets import auto_assets  # noqa: E402
from mcrender.model import ModelResolver  # noqa: E402
from mcrender.renderer import Camera, TextureAtlas, build_scene, render_view  # noqa: E402

from mcrender.gallery import GALLERY  # noqa: E402

#: 标签字体：跨平台候选（Windows / macOS / Linux），找不到就退回 PIL 内置位图字体。
FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


def load_font(size: int):
    """第一个存在的候选字体；都没有则 PIL 内置（无参调用，可跨版本）。"""
    for p in FONT_CANDIDATES:
        if Path(p).is_file():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def main(src="方块画廊.schem", out="画廊sheet.png", scale=16):
    scale = float(scale)
    d = S.read_structure(src)
    vox = d["voxels"]
    palette = d["palette"]
    assets = auto_assets(d["data_version"])
    resolver = ModelResolver(assets)
    baked = [resolver.resolve_block(p["Name"].replace("minecraft:", ""), p.get("Properties") or {})
             for p in palette]
    refs = set()
    for bb in baked:
        refs |= bb.all_textures
    atlas = TextureAtlas(refs, assets, verbose=False)
    for bb in baked:
        core = bb.cube_textures or (bb.textures if bb.is_full_cube else set())
        if core:
            bb.occludes = bool(all(
                not atlas.info[t].has_zero and not atlas.info[t].has_partial
                for t in core if t in atlas.info))

    step = 3
    tiles = []
    for ci, (name, states) in enumerate(GALLERY):
        sub = np.zeros_like(vox)
        for ri in range(len(states)):
            x = 1 + ci * step
            z = 1 + ri * step
            sub[:, z, x] = vox[:, z, x]
        scene = build_scene(sub, baked, atlas, ao=True, verbose=False)
        cam = Camera(azimuth=40, elevation=28, scale=scale, proj="ortho")
        img = render_view(scene, cam, atlas, ssaa=2, bloom=0.4, verbose=False)
        label = "%d %s (%d)" % (ci, name, len(states))
        canvas = Image.new("RGB", (img.width, img.height + 22), (250, 250, 252))
        canvas.paste(img, (0, 0))
        dr = ImageDraw.Draw(canvas)
        dr.text((6, img.height + 3), label, fill=(30, 32, 36),
                font=load_font(15))
        tiles.append(canvas)

    cols = 4
    rows = (len(tiles) + cols - 1) // cols
    W = max(t.width for t in tiles)
    H = max(t.height for t in tiles)
    sheet = Image.new("RGB", (W * cols + 8 * (cols + 1), H * rows + 8 * (rows + 1)), (238, 240, 244))
    for k, t in enumerate(tiles):
        r, c = divmod(k, cols)
        sheet.paste(t, (8 + c * (W + 8), 8 + r * (H + 8)))
    sheet.save(out)
    print("wrote %s  %dx%d  (%d tiles)" % (out, sheet.width, sheet.height, len(tiles)))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(*a[:3]) if a else main()
