# -*- coding: utf-8 -*-
"""Reference photo -> dominant colours -> nearest Minecraft blocks."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from mcmaterials.chart import _font
from mcmaterials.textures import average_color, block_texture, luminance

DATA = Path(__file__).resolve().parents[2] / "skills" / "minecraft-material-lab" / "data"


def catalog() -> dict:
    p = DATA / "block_catalog.json"
    if p.exists():
        try:
            import json
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def resolve_pool(pool: str, classes: set[str] | None = None) -> dict[str, tuple[int, int, int]]:
    """Colour table for a pool: 'walls' | 'all' | 'facade' | family name."""
    cat = catalog()
    if cat:
        if pool == "walls":
            names = POOLS["walls"]
        elif pool == "all":
            names = sorted(cat)
        elif pool == "facade":
            import json
            fi = DATA / "family_index.json"
            names = json.loads(fi.read_text(encoding="utf-8")).get("facade", []) if fi.exists()                 else [b for b in cat if cat[b].get("facade_safe")]
        else:
            import json
            fi = DATA / "family_index.json"
            fams = json.loads(fi.read_text(encoding="utf-8")) if fi.exists() else {}
            names = fams.get(pool)
            if names is None:
                names = [b for b in cat if pool in b]
        out = {}
        for b in names:
            e = cat.get(b) or {}
            if classes and e.get("class") not in classes:
                continue
            c = (e.get("color") or {}).get("avg")
            if c:
                out[b] = tuple(int(round(v)) for v in c)
        if out:
            return out
    names = POOLS["all"] if pool == "all" else POOLS.get(pool) or POOLS["walls"]
    cols = {}
    for b in names:
        tex, _ = block_texture(b)
        avg = average_color(tex) if tex is not None else None
        if avg:
            cols[b] = avg
    return cols

POOLS = {
    "walls": ["white_concrete", "light_gray_concrete", "gray_concrete", "black_concrete",
              "stone", "smooth_stone", "cobblestone", "andesite", "polished_andesite",
              "granite", "diorite", "tuff", "deepslate", "calcite",
              "smooth_sandstone", "sandstone", "white_terracotta", "light_gray_terracotta",
              "bricks", "mud_bricks", "packed_mud", "quartz_block", "smooth_quartz"],
    "all": [],   # filled lazily from families
}


def dominant_colors(img: Image.Image, n: int = 6) -> list[tuple[tuple[int, int, int], float]]:
    im = img.convert("RGB")
    q = im.quantize(colors=n, method=Image.MEDIANCUT).convert("RGB")
    counts: dict[tuple[int, int, int], int] = {}
    for c in q.getdata():
        counts[c] = counts.get(c, 0) + 1
    tot = sum(counts.values())
    return sorted(((c, 100.0 * v / tot) for c, v in counts.items()), key=lambda t: -t[1])


def _dist(a, b) -> float:
    # weighted RGB distance (eyes are green-sensitive)
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return (2 * dr * dr + 4 * dg * dg + 3 * db * db) ** 0.5


def suggest(path: str | Path, n_colors: int = 6, topk: int = 4, pool: str = "walls",
            sample_box: tuple[float, float, float, float] | None = None,
            classes: set[str] | None = None):
    img = Image.open(path)
    if sample_box:
        w, h = img.size
        img = img.crop((int(w * sample_box[0]), int(h * sample_box[1]),
                        int(w * sample_box[2]), int(h * sample_box[3])))
    doms = dominant_colors(img, n_colors)
    cols = resolve_pool(pool, classes)
    out = []
    for col, pct in doms:
        near = sorted(cols.items(), key=lambda kv: _dist(col, kv[1]))[:topk]
        out.append({"color": col, "pct": pct,
                    "nearest": [{"block": b, "avg": c, "d": round(_dist(col, c), 1)}
                                for b, c in near]})
    return out


def ref_profile(path: str | Path, box=None, n_colors: int = 6) -> dict:
    """Reference image profile: dominant colours + texture metrics (edges/grain/contrast)."""
    import numpy as np
    img = Image.open(path).convert("RGB")
    if box:
        w, h = img.size
        img = img.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), int(h * box[3])))
    doms = dominant_colors(img, n_colors)
    a = np.asarray(img, dtype=np.float32)
    lum = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    ex = float(np.abs(np.diff(lum, axis=1)).mean())
    ey = float(np.abs(np.diff(lum, axis=0)).mean())
    lv = lum.ravel()
    grain = "horizontal" if (ey - ex) / (ey + ex + 1e-6) > 0.18 else (
        "vertical" if (ey - ex) / (ey + ex + 1e-6) < -0.18 else "none")
    return {
        "colors": [{"rgb": list(c), "share": round(p / 100.0, 3)} for c, p in doms],
        "stats": {
            "lum_mean": round(float(lv.mean()), 1),
            "lum_std": round(float(lv.std()), 1),
            "contrast": round(float(lv.std() / (lv.mean() + 1e-6)), 3),
            "edge_x": round(ex, 2),
            "edge_y": round(ey, 2),
            "edge": round((ex + ey) / 2.0, 2),
            "roughness": round(min(1.0, (ex + ey) / 2.0 / 12.0), 3),
            "grain": grain,
        },
    }


def render_report(path, result, out_png: Path):
    rows = len(result)
    cell = 88
    W = 240 + 4 * (cell + 132)
    H = 40 + rows * cell
    img = Image.new("RGB", (W, H), (24, 24, 26))
    d = ImageDraw.Draw(img)
    d.text((10, 10), f"reference: {Path(path).name}", font=_font(16), fill=(240, 240, 235))
    d.text((240, 10), "dominant colour -> nearest Minecraft blocks (texture / avg / distance)",
           font=_font(13), fill=(200, 200, 195))
    for r, item in enumerate(result):
        y = 40 + r * cell
        yc = y + cell // 2
        d.rectangle([10, yc - 26, 10 + 56, yc + 26], fill=tuple(item["color"]))
        d.text((74, yc - 8), f"{item['color']}  {item['pct']:.0f}%", font=_font(12), fill=(230, 230, 225))
        for k, nb in enumerate(item["nearest"]):
            x = 240 + k * (cell + 132)
            tex, ref = block_texture(nb["block"])
            if tex is not None:
                t = tex.resize((48, 48), Image.NEAREST)
                img.paste(t, (x, yc - 24), t)
                d.rectangle([x, yc - 24, x + 47, yc + 23], outline=(90, 90, 90))
            d.text((x + 54, yc - 22), nb["block"][:16], font=_font(12), fill=(225, 225, 220))
            d.rectangle([x + 54, yc - 6, x + 54 + 40, yc + 10], fill=nb["avg"])
            d.text((x + 54, yc + 12), f"d={nb['d']}", font=_font(11), fill=(190, 190, 185))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return out_png
