# -*- coding: utf-8 -*-
"""Side-by-side colour audit: rendered image vs reference image + metrics."""
from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

from mcmaterials.chart import _font


def metrics(im: Image.Image, box=None) -> dict:
    if box:
        w, h = im.size
        im = im.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), int(h * box[3])))
    rgb = im.convert("RGB")
    px = list(rgb.getdata())
    n = len(px)
    mean = tuple(int(sum(p[i] for p in px) / n) for i in range(3))
    lum = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in px]
    q = rgb.quantize(colors=8, method=Image.MEDIANCUT).convert("RGB")
    qc = Counter(q.getdata())
    tot = sum(qc.values())
    warm = sum(1 for r, g, b in px if r - b >= 8)
    cool = sum(1 for r, g, b in px if b - r >= 8)
    return {
        "size": rgb.size,
        "mean": mean,
        "hex": "#%02x%02x%02x" % mean,
        "lum_mean": round(statistics.mean(lum), 1),
        "lum_std": round(statistics.pstdev(lum), 1),
        "warm_cool": round(warm / max(1, cool), 2),
        "top": [(c, round(100 * v / tot)) for c, v in qc.most_common(5)],
    }


def audit(a_path: str, b_path: str, out_png: str | Path,
          box_a=None, box_b=None, label_a: str = "MINE", label_b: str = "REFERENCE") -> dict:
    a_img, b_img = Image.open(a_path), Image.open(b_path)
    ma, mb = metrics(a_img, box_a), metrics(b_img, box_b)

    def crop(im, box):
        if not box:
            return im.convert("RGB")
        w, h = im.size
        return im.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), int(h * box[3]))).convert("RGB")

    ca, cb = crop(a_img, box_a), crop(b_img, box_b)
    H = 300
    fit = lambda x: x.resize((max(1, int(x.width * H / x.height)), H), Image.LANCZOS)
    fa, fb = fit(ca), fit(cb)
    W = fa.width + fb.width + 30
    sheet = Image.new("RGB", (W, H + 118), (24, 24, 26))
    sheet.paste(fa, (10, 62))
    sheet.paste(fb, (fa.width + 20, 62))
    d = ImageDraw.Draw(sheet)
    d.text((10, 8), f"{label_a}: {Path(a_path).name}", font=_font(15), fill=(255, 225, 130))
    d.text((fa.width + 20, 8), f"{label_b}: {Path(b_path).name}", font=_font(15), fill=(255, 225, 130))
    for i, (m, x) in enumerate(((ma, 10), (mb, fa.width + 20))):
        d.text((x, 28), f"mean {m['hex']}  L={m['lum_mean']}  std={m['lum_std']}  warm/cool={m['warm_cool']}",
               font=_font(13), fill=(225, 225, 220))
        d.text((x, 45), "  ".join(f"{c}x{p}%" for c, p in m["top"][:4]), font=_font(12), fill=(190, 190, 185))
    # delta line
    dl = round(ma["lum_mean"] - mb["lum_mean"], 1)
    dw = round(ma["warm_cool"] - mb["warm_cool"], 2)
    d.text((10, H + 68), f"delta L (mine - ref) = {dl:+}    delta warm/cool = {dw:+}",
           font=_font(14), fill=(240, 240, 235))
    for i, (m, x) in enumerate(((ma, 10), (mb, fa.width + 20))):
        for k, (col, pct) in enumerate(m["top"][:5]):
            d.rectangle([x + k * 34, H + 90, x + k * 34 + 30, H + 112], fill=col)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return {"a": ma, "b": mb, "delta_lum": dl, "delta_warm_cool": dw, "out": str(out)}
