# -*- coding: utf-8 -*-
"""Full-block material catalog: every Minecraft block -> texture metrics.

Per block the catalog records
  * colour      : avg / median / HSV / quantized dominant colours (+ shares)
  * transparency: opaque / translucent / empty pixel ratios
  * texture     : luminance std, contrast, edge density, hf energy, grain
                  direction (horizontal / vertical), roughness 0..1, class
  * geometry    : is_full (full cube), quads, bbox, textures[], multi_texture
  * roles       : technical, emissive, facade_safe (full + opaque + not emissive)

The heavy work is per *texture*; results are cached in data/texture_stats.json so
re-runs only compute new textures.  Fetches go through mcrender.assets.Assets
(jsdelivr/raw.github mirrors, .cache/mcassets/<version>).
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from mcrender.assets import Assets  # type: ignore
from mcmaterials.net import (BASES, REF_TAG, cached,  # noqa: E402
                             fetch_into_cache, robust_get)

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache" / "mcassets"
DATA = ROOT / "skills" / "minecraft-material-lab" / "data"
BLOCK_DATA = ROOT / "skills" / "minecraft-block-models" / "data" / "all_blocks.json"
INCOMPLETE = ROOT / "skills" / "minecraft-block-models" / "data" / "incomplete_blocks.json"

CATALOG = DATA / "block_catalog.json"
TEXSTATS = DATA / "texture_stats.json"
FAMILY_INDEX = DATA / "family_index.json"

_assets: Assets | None = None

TECHNICAL = re.compile(
    r"^(air|cave_air|void_air|water|lava|fire|soul_fire|bubble_column|light|barrier|"
    r"structure_void|jigsaw|moving_piston|piston_head|end_portal|end_gateway|"
    r"nether_portal|.*command_block|chain_command_block|repeating_command_block|"
    r"spawner|trial_spawner|vault|.*_head$|.*_wall_head$)$")
EMISSIVE_EXACT = {
    "torch", "wall_torch", "soul_torch", "soul_wall_torch", "redstone_torch",
    "redstone_wall_torch", "lantern", "soul_lantern", "glowstone", "sea_lantern",
    "shroomlight", "magma_block", "campfire", "soul_campfire", "beacon", "conduit",
    "respawn_anchor", "crying_obsidian", "end_rod", "light", "redstone_lamp",
    "furnace", "smoker", "blast_furnace", "sculk_catalyst", "glow_berries",
    "glow_lichen", "candle", "jack_o_lantern", "enchanting_table", "brewing_stand",
    "cauldron", "water_cauldron", "end_portal_frame", "dragon_egg", "sculk_sensor",
    "calibrated_sculk_sensor", "daylight_detector", "sculk_shrieker", "trial_spawner",
    "vault", "copper_bulb", "exposed_copper_bulb", "weathered_copper_bulb",
    "oxidized_copper_bulb", "waxed_copper_bulb", "waxed_exposed_copper_bulb",
    "waxed_weathered_copper_bulb", "waxed_oxidized_copper_bulb",
}
EMISSIVE_RE = re.compile(
    r"(?:_froglight|_candle|_bulb|_lantern|_torch|_lamp|"
    r"_(?:tube|brain|bubble|fire|horn)_coral(?:_fan|_wall_fan)?$|"
    r"magma_block|campfire|glowstone|sea_lantern|shroomlight|beacon|conduit|end_rod|"
    r"respawn_anchor|crying_obsidian|amethyst_cluster|glow_berries|glow_lichen|"
    r"sculk_catalyst|sculk_sensor|sculk_shrieker|furnace|smoker|blast_furnace)")


def is_emissive(name: str) -> bool:
    return name in EMISSIVE_EXACT or bool(EMISSIVE_RE.search(name))


TECHNICAL_RE = TECHNICAL


def assets(refetch_missing: bool = False, workers: int = 24) -> Assets:
    global _assets
    if refetch_missing:
        for p in CACHE.glob("*/_missing.json"):
            backup = p.with_suffix(".json.bak.%d" % int(time.time()))
            p.rename(backup)
            print("  refetch: %s -> %s" % (p.name, backup.name))
        _assets = None
    if _assets is None:
        _assets = Assets(cache_dir=str(CACHE), workers=workers)
    return _assets


def prefetch(names: list[str], batch: int = 400) -> None:
    """Bulk-warm blockstates/models/textures with retries (robust, parallel)."""
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        rep = prefetch_robust(chunk)
        print("  prefetch %d/%d  blockstates=%d models=%d textures=%d failed=%d"
              % (min(i + batch, len(names)), len(names), rep["blockstates"],
                 rep["models"], rep["textures"], rep["failed"]), flush=True)


# ------------------------------------------------------------------ block list
@lru_cache(maxsize=1)
def incomplete_meta() -> dict:
    if INCOMPLETE.exists():
        return json.loads(INCOMPLETE.read_text(encoding="utf-8"))
    return {}


@lru_cache(maxsize=1)
def all_blocks_json() -> set:
    d = json.loads(BLOCK_DATA.read_text(encoding="utf-8"))
    return set(d.keys())


def block_list() -> list[str]:
    """All blocks of the render version (summary) union the validation table."""
    a = assets()
    summary = a.blocks_summary() or {}
    names = set(summary.keys()) | all_blocks_json()
    names = {n for n in names if n and not n.startswith("#")}
    return sorted(names)


# ------------------------------------------------------- model / texture refs
@lru_cache(maxsize=4096)
def blockstate(block: str):
    return assets().blockstate(block)


@lru_cache(maxsize=8192)
def model(ref: str):
    ref = ref.removeprefix("minecraft:")
    if ref.startswith("builtin/"):
        return None
    return assets().model(ref)


def model_textures(ref: str, depth: int = 0) -> dict[str, str]:
    """Resolve a model's textures through its parent chain (child overrides)."""
    if depth > 8:
        return {}
    d = model(ref)
    if not d:
        return {}
    out: dict[str, str] = {}
    parent = d.get("parent")
    if parent:
        out.update(model_textures(parent, depth + 1))
    for k, v in (d.get("textures") or {}).items():
        if isinstance(v, str):
            out[k] = v
        elif isinstance(v, dict) and v.get("sprite"):
            out[k] = v["sprite"]
    return out


def resolve_textures(block: str) -> list[str]:
    """All distinct texture refs used by a block, in model order."""
    refs: list[str] = []
    bs = blockstate(block)
    if bs:
        models: list[str] = []
        for v in (bs.get("variants") or {}).values():
            for it in (v if isinstance(v, list) else [v]):
                if isinstance(it, dict) and it.get("model"):
                    models.append(it["model"])
        for part in (bs.get("multipart") or []):
            apply = part.get("apply")
            for it in (apply if isinstance(apply, list) else [apply]):
                if isinstance(it, dict) and it.get("model"):
                    models.append(it["model"])
        for mref in models:
            for val in model_textures(mref).values():
                if isinstance(val, str) and not val.startswith("#"):
                    refs.append(val)
    if not refs:
        meta = incomplete_meta().get(block) or {}
        refs = [t for t in (meta.get("textures") or []) if isinstance(t, str)]
    if not refs:
        refs = ["block/" + block]
    seen, out = set(), []
    for r in refs:
        r = r.removeprefix("minecraft:")
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


# --------------------------------------------------------------- texture stats
def _hsv(rgb):
    r, g, b = [v / 255.0 for v in rgb]
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / d)) % 360
    elif mx == g:
        h = 60 * ((b - r) / d) + 120
    else:
        h = 60 * ((r - g) / d) + 240
    s = 0.0 if mx == 0 else d / mx
    return round(h, 1), round(s, 3), round(mx, 3)


def texture_stats(ref: str) -> dict:
    """Colour + transparency + texture metrics for one texture ref."""
    rel = "textures/%s.png" % ref
    raw = cached(rel)
    if not raw:
        return {"ref": ref, "missing": True}
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    a = np.asarray(img, dtype=np.float32)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    opaque = al >= 250
    translucent = (al > 8) & (al < 250)
    empty = al <= 8
    visible = al > 8                      # colour/texture stats use visible pixels
    out: dict = {
        "ref": ref,
        "alpha": {"opaque": round(float(opaque.mean()), 3),
                  "translucent": round(float(translucent.mean()), 3),
                  "empty": round(float(empty.mean()), 3)},
        "transparent": bool(translucent.mean() > 0.01 or empty.mean() > 0.02),
    }
    if visible.sum() == 0:
        out.update({"color": None, "texture": None})
        return out
    R, G, B = r[visible], g[visible], b[visible]
    mean = (float(R.mean()), float(G.mean()), float(B.mean()))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    L = lum
    lv = lum[visible]
    # edges only where both neighbours are visible
    mh = visible[:, 1:] & visible[:, :-1]
    mv = visible[1:, :] & visible[:-1, :]
    ex = float(np.abs(np.diff(L, axis=1))[mh].mean()) if mh.any() else 0.0
    ey = float(np.abs(np.diff(L, axis=0))[mv].mean()) if mv.any() else 0.0
    # second-order (fine detail) energy
    lx = np.abs(np.diff(L, axis=1))
    hf = float(np.abs(np.diff(lx, axis=1))[mh[:, :-1] & mh[:, 1:]].mean()) if mh.shape[1] > 1 else 0.0
    q = img.convert("RGB").quantize(colors=4, method=Image.MEDIANCUT)
    idx = np.asarray(q)
    codes, counts = np.unique(idx[visible], return_counts=True)
    order = np.argsort(-counts)[:4]
    pal = q.getpalette() or []
    dom = []
    for i in order:
        code = int(codes[i])
        rgb = pal[code * 3:code * 3 + 3]
        if len(rgb) == 3:
            dom.append({"rgb": [int(v) for v in rgb],
                        "share": round(float(counts[i]) / float(counts.sum()), 3)})
    lum_std = float(lv.std())
    dir_raw = (ey - ex) / (ey + ex + 1e-6)
    grain = "horizontal" if dir_raw > 0.18 else ("vertical" if dir_raw < -0.18 else "none")
    edge = (ex + ey) / 2.0
    out.update({
        "color": {
            "avg": [round(v, 1) for v in mean],
            "median": [float(np.median(R)), float(np.median(G)), float(np.median(B))],
            "hsv": list(_hsv(mean)),
            "dominant": dom,
        },
        "texture": {
            "lum_mean": round(float(lv.mean()), 1),
            "lum_std": round(lum_std, 1),
            "contrast": round(lum_std / (float(lv.mean()) + 1e-6), 3),
            "edge_x": round(ex, 2),
            "edge_y": round(ey, 2),
            "edge": round(edge, 2),
            "hf": round(hf, 2),
            "grain": grain,
            "grain_score": round(dir_raw, 3),
            "roughness": round(min(1.0, edge / 12.0), 3),
        },
    })
    return out


def classify(block: str, alpha: dict, tex: dict | None) -> str:
    if TECHNICAL_RE.match(block):
        return "technical"
    if is_emissive(block):
        return "emissive"
    if alpha.get("translucent", 0) >= 0.99:
        return "translucent"
    if alpha.get("translucent", 0) > 0.01 and alpha.get("empty", 0) < 0.2:
        return "translucent"
    if alpha.get("empty", 0) > 0.25 and alpha.get("opaque", 0) + alpha.get("translucent", 0) < 0.75:
        return "cutout"
    if tex is None:
        return "unknown"
    edge = tex["edge"]
    if tex["grain"] != "none" and edge >= 3.0:
        return "grained"
    if edge < 3.0:
        return "smooth"
    if edge < 8.0:
        return "matte"
    if edge < 14.0:
        return "textured"
    return "rough"


def aggregate(stats: list[dict]) -> dict:
    """Merge several texture stats into one block entry."""
    ok = [s for s in stats if s and not s.get("missing") and s.get("color")]
    if not ok:
        return {"textures": [s.get("ref") for s in stats if s],
                "missing": all(s.get("missing") for s in stats) if stats else True}
    alpha = {k: round(float(np.mean([s["alpha"][k] for s in ok])), 3)
             for k in ("opaque", "translucent", "empty")}
    wvis = lambda s: max(1e-3, s["alpha"]["opaque"] + s["alpha"]["translucent"])  # noqa: E731
    avg = []
    for ch in range(3):
        vals = np.asarray([s["color"]["avg"][ch] for s in ok], dtype=float)
        ws = np.asarray([wvis(s) for s in ok], dtype=float)
        avg.append(round(float(np.average(vals, weights=ws)), 1))
    med = []
    for ch in range(3):
        vals = np.asarray([s["color"]["median"][ch] for s in ok], dtype=float)
        ws = np.asarray([wvis(s) for s in ok], dtype=float)
        med.append(round(float(np.average(vals, weights=ws)), 1))
    tex = {}
    for k in ("lum_mean", "lum_std", "contrast", "edge_x", "edge_y", "edge", "hf", "grain_score", "roughness"):
        vals = np.asarray([s["texture"][k] for s in ok], dtype=float)
        ws = np.asarray([wvis(s) for s in ok], dtype=float)
        tex[k] = round(float(np.average(vals, weights=ws)), 3)
    tex["grain"] = "horizontal" if tex["grain_score"] > 0.18 else ("vertical" if tex["grain_score"] < -0.18 else "none")
    dom = [d for s in ok for d in s["color"]["dominant"]]
    dom.sort(key=lambda d: -d["share"])
    color = {"avg": avg, "median": med, "hsv": list(_hsv(avg)), "dominant": dom[:4]}
    return {"color": color, "alpha": alpha, "texture": tex,
            "textures": [s["ref"] for s in ok]}


# ------------------------------------------------------------------ block entry
def block_entry(block: str, tex_cache: dict, stats_cache: dict) -> dict:
    meta = incomplete_meta().get(block) or {}
    is_full = meta.get("is_full_cube")
    if is_full is None:
        # not listed in the incomplete table -> treat blocks whose model is a
        # single full cube as full; be conservative for known non-cube names
        is_full = not re.search(r"(_slab|_stairs|_fence|_wall|_pane|_door|_trapdoor|_button|_sign|_torch|_candle|_chain|_bed|_banner|_carpet|_sapling|_rail|.*_head|.*_pot|.*_seeds|.*_stem|.*_leaves)$", block)
    refs = resolve_textures(block)
    stats = []
    for ref in refs:
        if ref in tex_cache:
            stats.append(tex_cache[ref])
            continue
        st = texture_stats(ref)
        tex_cache[ref] = st
        stats.append(st)
    agg = aggregate(stats)
    alpha = agg.get("alpha") or {"opaque": 0.0, "translucent": 0.0, "empty": 1.0}
    tex = agg.get("texture")
    cls = classify(block, alpha, tex)
    technical = bool(TECHNICAL_RE.match(block))
    emissive = is_emissive(block)
    entry = {
        "block": block,
        "class": cls,
        "is_full": bool(is_full) if is_full is not None else None,
        "quads": meta.get("quads"),
        "render": meta.get("render"),
        "technical": technical,
        "emissive": emissive,
        "alpha": alpha,
        "transparent": bool(agg.get("transparent")) or alpha["empty"] > 0.02 or alpha["translucent"] > 0.01,
        "color": agg.get("color"),
        "texture": tex,
        "textures": agg.get("textures", refs),
        "multi_texture": len(agg.get("textures", refs)) > 1,
        "missing": bool(agg.get("missing")) and not agg.get("color"),
        # full-cube blocks usable on a facade (glass included; only technical excluded)
        "facade_safe": bool(is_full) and not technical,
    }
    return entry


def build(blocks: list[str] | None = None, limit: int | None = None,
          refetch: bool = False, recompute: bool = False, progress_every: int = 100,
          save_every: int = 200) -> dict:
    """Build (or refresh) the full block catalog.  Returns the catalog dict."""
    if refetch:
        assets(refetch_missing=True)
    names = blocks or block_list()
    if limit:
        names = names[:limit]
    DATA.mkdir(parents=True, exist_ok=True)
    tex_cache: dict = {}
    if TEXSTATS.exists():
        try:
            tex_cache = json.loads(TEXSTATS.read_text(encoding="utf-8"))
        except Exception:
            tex_cache = {}
    catalog: dict = {}
    if CATALOG.exists():
        try:
            catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        except Exception:
            catalog = {}
    if recompute:
        catalog = {}          # keep texture_stats, recompute entries from cache
    t0 = time.time()
    if not recompute:
        prefetch([b for b in names if b not in catalog] or names)
    processed = 0
    for i, b in enumerate(names):
        if b in catalog and not refetch:
            continue
        try:
            catalog[b] = block_entry(b, tex_cache, catalog)
        except Exception as e:  # noqa: BLE001
            catalog[b] = {"block": b, "error": "%s: %s" % (type(e).__name__, e)}
        processed += 1
        if progress_every and processed % progress_every == 0:
            dt = time.time() - t0
            print("  %5d/%d  (+%d, %.1fs, textures cached %d)"
                  % (i + 1, len(names), processed, dt, len(tex_cache)), flush=True)
        if save_every and processed % save_every == 0:
            TEXSTATS.write_text(json.dumps(tex_cache, ensure_ascii=False), encoding="utf-8", newline="\n")
            CATALOG.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8", newline="\n")
    TEXSTATS.write_text(json.dumps(tex_cache, ensure_ascii=False), encoding="utf-8", newline="\n")
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8", newline="\n")
    write_markdown(catalog)
    write_family_index(catalog)
    print("catalog blocks=%d  textures=%d  %.1fs -> %s"
          % (len(catalog), len(tex_cache), time.time() - t0, CATALOG))
    return catalog


def write_markdown(catalog: dict) -> None:
    lines = ["# 全量方块材质目录（颜色 / 透明度 / 质感）", "",
             "字段：class · is_full · alpha(op/tr/em) · 平均色 · 明度/对比 · 边缘/粗糙度 · 纹路方向 · 主色",
             ""]
    for b in sorted(catalog):
        e = catalog[b]
        if e.get("error") or not e.get("color"):
            continue
        c = e["color"]; t = e.get("texture") or {}; a = e.get("alpha") or {}
        lines.append(
            "| `%s` | %s | %s | %.2f/%.2f/%.2f | #%02x%02x%02x | %s / %.1f | %.2f / %.2f | %s | %s |"
            % (b, e["class"], e["is_full"], a.get("opaque", 0), a.get("translucent", 0), a.get("empty", 0),
               int(c["avg"][0]), int(c["avg"][1]), int(c["avg"][2]),
               int(t.get("lum_mean", 0)), t.get("contrast", 0),
               t.get("edge", 0), t.get("roughness", 0), t.get("grain", "?"),
               " ".join("#%02x%02x%02x" % tuple(d["rgb"]) for d in c["dominant"][:3])))
    (DATA / "block_catalog.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("markdown ->", DATA / "block_catalog.md", "(%d rows)" % (len(lines) - 4))


def write_family_index(catalog: dict) -> None:
    from mcmaterials.families import all_families
    fams = all_families(list(catalog))
    index = {}
    for fam, blocks in fams.items():
        index[fam] = [b for b in blocks if b in catalog]
    FAMILY_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print("families ->", FAMILY_INDEX, "(%d families)" % len(index))


# --------------------------------------------------------- robust bulk fetching
REF_TAG = "26.2-assets-tiny"
BASES = (
    "https://raw.githubusercontent.com/misode/mcmeta/%s/assets/minecraft/%s",
    "https://cdn.jsdelivr.net/gh/misode/mcmeta@%s/assets/minecraft/%s",
)
_HTTP_HEADERS = {"User-Agent": "mcmaterials/1.0 (+mcmeta)"}


def robust_get(rel: str, tries: int = 3, timeout: int = 30) -> bytes | None:
    """Download one resource with retries across mirrors (no session pessimism)."""
    import urllib.error
    import urllib.request
    rel = rel.replace("\\", "/").lstrip("/")
    for attempt in range(tries):
        for tmpl in BASES:
            url = tmpl % (REF_TAG, rel)
            try:
                req = urllib.request.Request(url, headers=_HTTP_HEADERS)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    if r.status == 200:
                        return r.read()
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.4 * (attempt + 1))
    return None


def fetch_into_cache(rels: list[str], workers: int = 24, label: str = "") -> dict:
    """Fill .cache/mcassets/<version>/<rel> for a list of resource paths."""
    from concurrent.futures import ThreadPoolExecutor
    root = CACHE / "26.2"
    todo = []
    have = 0
    for rel in rels:
        p = root / rel
        if p.exists() and p.stat().st_size > 0:
            have += 1
        else:
            todo.append(rel)
    ok = 0
    if todo:
        def job(rel):
            data = robust_get(rel)
            if data:
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(p.suffix + ".part")
                tmp.write_bytes(data)
                tmp.replace(p)
                return 1
            return 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, n in enumerate(ex.map(job, todo)):
                ok += n
                if label and (i + 1) % 200 == 0:
                    print("  %s %d/%d  (+%d)" % (label, i + 1, len(todo), ok), flush=True)
    return {"cached": have, "downloaded": ok, "failed": len(todo) - ok}


def prefetch_robust(names: list[str], workers: int = 24) -> dict:
    """Download blockstates -> models (BFS parents) -> textures for a block list."""
    from mcrender.assets import collect_model_refs, norm_ref  # type: ignore
    root = CACHE / "26.2"
    report = {"blockstates": 0, "models": 0, "textures": 0, "failed": 0}

    bs_rels = ["blockstates/%s.json" % b for b in names]
    fetch_into_cache(bs_rels, workers, "blockstates")

    model_refs: set[str] = set()
    for rel in bs_rels:
        p = root / rel
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for mref in collect_model_refs(d):
            model_refs.add(norm_ref(mref))
    seen: set[str] = set()
    frontier = set(model_refs)
    tex_refs: set[str] = set()
    while frontier:
        todo = sorted(r for r in frontier if r not in seen)
        seen.update(todo)
        frontier = set()
        if not todo:
            break
        fetch_into_cache(["models/%s.json" % r for r in todo], workers, "models")
        for r in todo:
            p = root / "models" / ("%s.json" % r)
            if not p.exists():
                report["failed"] += 1
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            parent = d.get("parent")
            if parent:
                pn = norm_ref(parent)
                if not pn.startswith("builtin/"):
                    frontier.add(pn)
            for val in (d.get("textures") or {}).values():
                if isinstance(val, str) and not val.startswith("#"):
                    tex_refs.add(norm_ref(val))
                elif isinstance(val, dict) and val.get("sprite"):
                    tex_refs.add(norm_ref(val["sprite"]))
    if tex_refs:
        fetch_into_cache(["textures/%s.png" % t for t in sorted(tex_refs)], workers, "textures")
    report["blockstates"] = sum(1 for r in bs_rels if (root / r).exists())
    report["models"] = len(seen)
    report["textures"] = sum(1 for t in tex_refs if (root / ("textures/%s.png" % t)).exists())
    return report
