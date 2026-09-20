# -*- coding: utf-8 -*-
"""Contact-sheet charts: block textures + labels + average colour swatch."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mcmaterials.textures import average_color, block_texture, luminance

#: 跨平台字体候选；可用 ``STRUCTWORKSHOP_FONT`` 指定绝对路径。
FONT_PATHS = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    env = os.environ.get("STRUCTWORKSHOP_FONT")
    for p in ([env] if env else []) + FONT_PATHS:
        if p and Path(p).is_file():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


MISSING_FILL = (150, 40, 40)


def compose(blocks: list[str], title: str, cols: int = 6, cell: int = 104,
            report: dict | None = None) -> Image.Image:
    rows = (len(blocks) + cols - 1) // cols
    head = 44
    W, H = cols * cell, head + rows * cell
    img = Image.new("RGB", (W, H), (24, 24, 26))
    d = ImageDraw.Draw(img)
    d.text((10, 12), title, font=_font(20), fill=(240, 240, 235))
    missing = 0
    for i, b in enumerate(blocks):
        cx, cy = (i % cols) * cell, head + (i // cols) * cell
        tex, ref = block_texture(b)
        x0, y0 = cx + 8, cy + 8
        if tex is None:
            missing += 1
            d.rectangle([x0, y0, x0 + cell - 41, y0 + cell - 41], fill=MISSING_FILL, outline=(255, 220, 120))
            d.text((x0 + 6, y0 + 18), "MISSING", font=_font(14), fill=(255, 255, 255))
            d.text((x0 + 4, y0 + 36), ref[:20] or "no texture", font=_font(10), fill=(255, 230, 200))
        else:
            t = tex.resize((cell - 40, cell - 40), Image.NEAREST)
            img.paste(t, (x0, y0), t)
            d.rectangle([x0, y0, x0 + cell - 41, y0 + cell - 41], outline=(90, 90, 90))
            avg = average_color(tex)
            if avg:
                d.rectangle([x0 + cell - 38, y0, x0 + cell - 12, y0 + cell - 41], fill=avg)
                lum = int(luminance(avg))
                d.text((x0 + cell - 36, y0 + cell - 38), str(lum), font=_font(11), fill=(20, 20, 20))
        d.text((x0, y0 + cell - 30), b[:17], font=_font(12), fill=(225, 225, 220))
    if report is not None:
        report["cells"] = report.get("cells", 0) + len(blocks)
        report["missing"] = report.get("missing", 0) + missing
    if missing:
        d.text((W - 240, 14), f"MISSING: {missing}", font=_font(16), fill=(255, 120, 120))
    return img


def save_family_charts(outdir: Path, families: dict[str, list[str]], cols: int = 6,
                       report: dict | None = None) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    made = []
    for fam, blocks in families.items():
        img = compose(blocks, f"{fam}  ({len(blocks)} blocks)", cols=cols, report=report)
        p = outdir / f"{fam}.png"
        img.save(p)
        made.append(p)
    # walls master chart: everything that can go on a facade, sorted by luminance
    walls = []
    for key in ("concrete", "stone", "glass"):
        walls += families.get(key, [])
    order = []
    for b in walls:
        tex, _ = block_texture(b)
        avg = average_color(tex) if tex is not None else None
        order.append((luminance(avg) if avg else 0.0, b))
    order.sort(key=lambda t: -t[0])
    img = compose([b for _l, b in order], "facade palette (sorted light -> dark)", cols=cols,
                  report=report)
    p = outdir / "walls_by_luminance.png"
    img.save(p)
    made.append(p)
    return made


def compose_paged(blocks: list[str], title: str, outdir: Path, prefix: str,
                  cols: int = 8, cell: int = 104, per_page: int = 64,
                  report: dict | None = None) -> list[Path]:
    """Write a family chart split into pages of `per_page` blocks."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    blocks = list(blocks)
    pages = max(1, (len(blocks) + per_page - 1) // per_page)
    made = []
    for i in range(pages):
        chunk = blocks[i * per_page:(i + 1) * per_page]
        img = compose(chunk, f"{title}  ({i + 1}/{pages}, {len(blocks)} blocks)", cols=cols,
                      cell=cell, report=report)
        p = outdir / (f"{prefix}_{i + 1}of{pages}.png" if pages > 1 else f"{prefix}.png")
        img.save(p)
        made.append(p)
    return made


def save_all_charts(outdir: Path, families: dict[str, list[str]], per_page: int = 64,
                    cols: int = 8, report: dict | None = None) -> list[Path]:
    """Curated families as single charts, big families paginated."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for fam, blocks in families.items():
        if not blocks:
            continue
        if len(blocks) <= per_page:
            img = compose(blocks, f"{fam}  ({len(blocks)} blocks)", cols=cols, report=report)
            p = outdir / f"{fam}.png"
            img.save(p)
            made.append(p)
        else:
            made += compose_paged(blocks, fam, outdir, fam, cols=cols, per_page=per_page)
    return made


def facade_luminance_chart(catalog: dict, outdir: Path, cols: int = 10, per_page: int = 90) -> list[Path]:
    """All full-cube blocks (glass included) sorted by luminance, paged."""
    rows = []
    for b, e in catalog.items():
        c = (e.get("color") or {}).get("avg")
        if not c or not e.get("facade_safe"):
            continue
        rows.append((sum(c) / 3.0, b))
    rows.sort(key=lambda t: -t[0])
    return compose_paged([b for _l, b in rows], "facade (full blocks, light -> dark)",
                         outdir, "facade_all_by_luminance", cols=cols, per_page=per_page)
