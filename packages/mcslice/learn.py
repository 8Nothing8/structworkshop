"""从已有建筑学出「风格包」（比例 / 材质 / 节奏）。

Extracts the numbers an author (or an AI) needs to imitate a building's
language: palette + material roles, floor pitch, bay pitch, window rhythm,
silhouette ratios.  The result is a *style pack* JSON that any composition can
reference (``packs/<pack>/styles/<name>.json``).

    python -m mcslice.learn 高层写字楼.schem --out /tmp/office_tower.style.json
    python -m mcslice.learn build.schem --out style.json --preview palette.png
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from mccore.paths import write_text_lf
from mcslice.slice import detect_cells, detect_ports, load, passable_mask

ROLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("glass", ("glass", "pane")),
    ("light", ("lantern", "glowstone", "end_rod", "shroomlight", "torch",
               "froglight", "sea_lantern", "candle")),
    ("vegetation", ("leaves", "grass", "vine", "moss", "flower", "dirt",
                    "azalea", "sapling", "bamboo", "cactus")),
    ("wood", ("planks", "log", "wood", "stripped_")),
    ("metal", ("copper", "iron_block", "gold", "steel", "anvil")),
    ("water", ("water", "ice", "snow")),
    ("structure", ("concrete", "stone", "deepslate", "brick", "terracotta",
                   "andesite", "blackstone", "tuff", "quartz", "sandstone",
                   "prismarine", "basalt", "calcite", "diorite", "granite")),
    ("detail", ("stairs", "slab", "wall", "fence", "bars", "door",
                "trapdoor", "chain", "carpet", "button", "lever")),
)


def role_of(name: str) -> str:
    for role, keys in ROLE_RULES:
        if any(k in name for k in keys):
            return role
    return "other"


def floor_pitch(occ: np.ndarray) -> int | None:
    peaks = [y for y in range(1, len(occ) - 1)
             if occ[y] >= occ[y - 1] and occ[y] >= occ[y + 1] and occ[y] > 0.15]
    diffs = [b - a for a, b in zip(peaks, peaks[1:]) if 3 <= b - a <= 8]
    if not diffs:
        return None
    return int(statistics.median(diffs))


def window_stats(st: dict, floor: int, pitch: int) -> dict:
    """Median light-opening width/height + count, from the sliced ports."""
    cells = detect_cells(st, floor, pitch, 0.5, 3)
    widths, heights = [], []
    for c in cells:
        x0, y0, z0, x1, y1, z1 = c["bbox"]
        if 1.0 - st["passable"][y1, z0:z1 + 1, x0:x1 + 1].mean() < 0.25:
            continue
        for p in detect_ports(st, c["bbox"]):
            if p["type"] == "light" and p["face"] in ("north", "south",
                                                      "west", "east"):
                widths.append(p["size"][1])
                heights.append(p["size"][0])
    if not widths:
        return {"count": 0}
    return {"count": len(widths),
            "median_width": int(statistics.median(widths)),
            "median_height": int(statistics.median(heights))}


def swatch_png(rows: list[tuple[str, int]], path: Path, size: int = 44) -> None:
    from PIL import Image, ImageDraw
    try:
        from mcrender.assets import auto_assets
        assets = auto_assets(4903)
    except Exception:  # noqa: BLE001 - preview is best effort
        assets = None
    img = Image.new("RGB", (size * 6, size * ((len(rows) + 5) // 6)),
                    (245, 246, 248))
    dr = ImageDraw.Draw(img)
    for i, (name, count) in enumerate(rows):
        x, y = (i % 6) * size, (i // 6) * size
        color = (150, 150, 150)
        if assets is not None:
            for cand in (f"block/{name}", f"block/{name}_top",
                         f"block/stripped_{name}"):
                raw = assets.texture(cand)
                if raw:
                    try:
                        import io
                        im = Image.open(io.BytesIO(raw)).convert("RGBA")
                        arr = np.asarray(im)
                        rgb = arr[..., :3][arr[..., 3] > 0]
                        if len(rgb):
                            color = tuple(int(v) for v in rgb.mean(axis=0))
                        break
                    except Exception:  # noqa: BLE001
                        pass
        dr.rectangle([x + 1, y + 1, x + size - 2, y + size - 2], fill=color)
        dr.text((x + 4, y + size - 14), name[:9], fill=(20, 20, 20))
    img.save(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--out", required=True, help="style pack JSON")
    ap.add_argument("--id", default=None)
    ap.add_argument("--floor", type=int, default=None,
                    help="用于开间/窗户分析的楼层(默认: 实心率最高的一层)")
    ap.add_argument("--preview", default=None, help="调色板预览 PNG")
    ap.add_argument("--top", type=int, default=18, help="预览/统计的方块数")
    a = ap.parse_args()

    st = load(a.input)
    st["passable"] = passable_mask(st["names"], st["vox"])
    v = st["vox"]
    sx, sy, sz = st["size"]
    counts: dict[str, int] = {}
    roles: dict[str, int] = {}
    for i, n in enumerate(st["names"]):
        if n == "air":
            continue
        c = int((v == i).sum())
        if not c:
            continue
        counts[n] = c
        roles[role_of(n)] = roles.get(role_of(n), 0) + c
    filled = sum(counts.values())

    occ = (v != 0).mean(axis=(1, 2))
    pitch = floor_pitch(occ) or 5
    if a.floor is None:
        # floor band with the most solid material = a typical interior floor
        best, floor = -1.0, 1
        n_floors = max(1, sy // pitch)
        for k in range(n_floors):
            y0, y1 = k * pitch, min(k * pitch + pitch - 1, sy - 1)
            f = float((v[y0:y1 + 1] != 0).mean())
            if f > best:
                best, floor = f, k
    else:
        floor = a.floor

    cells = detect_cells(st, floor, pitch, 0.5, 3)
    widths = [c["dx"] for c in cells if min(c["dx"], c["dz"]) > 3]
    bay = int(statistics.median(widths)) if widths else None
    win = window_stats(st, floor, pitch)

    tags = [r for r, n in sorted(roles.items(), key=lambda kv: -kv[1])
            if r not in ("other", "detail")][:3]
    style = {
        "schema": 1,
        "id": a.id or Path(a.input).stem,
        "source": str(a.input),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "size": [sx, sy, sz],
        "blocks": filled,
        "floor_pitch": pitch,
        "bay_pitch": bay,
        "floors": sy // pitch,
        "ratios": {"h_w": round(sy / max(1, sx), 2),
                   "d_w": round(sz / max(1, sx), 2)},
        "roles": {k: round(n / max(1, filled), 3)
                  for k, n in sorted(roles.items(), key=lambda kv: -kv[1])},
        "windows": win,
        "palette": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "tags": tags,
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps(style, ensure_ascii=False, indent=1))
    print(f"style -> {out}")
    print(f"  {sx}x{sy}x{sz}  blocks={filled}  floor_pitch={pitch}  "
          f"bay={bay}  window={win}")
    print("  roles: " + ", ".join(f"{k}={v_n}" for k, v_n in style["roles"].items()))

    if a.preview:
        rows = sorted(counts.items(), key=lambda kv: -kv[1])[:a.top]
        swatch_png(rows, Path(a.preview))
        print(f"  preview -> {a.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
