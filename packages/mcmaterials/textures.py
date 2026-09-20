# -*- coding: utf-8 -*-
"""Load block textures from the mcrender asset cache (vc = vanilla-like pack)."""
from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path

from PIL import Image

from mcrender.assets import Assets  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache" / "mcassets"

_ASSETS: Assets | None = None


def assets() -> Assets:
    global _ASSETS
    if _ASSETS is None:
        _ASSETS = Assets(cache_dir=str(CACHE))
    return _ASSETS


def _decode(ref: str, fetch: bool = True) -> Image.Image | None:
    from mcmaterials.net import cached
    name = ref if ref.startswith("block/") else "block/" + ref
    raw = cached("textures/%s.png" % name, fetch=fetch)
    if not raw:
        return None
    try:
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def _model_refs_from_state(st: dict) -> list[str]:
    out: list[str] = []
    for v in (st.get("variants") or {}).values():
        for it in (v if isinstance(v, list) else [v]):
            if isinstance(it, dict) and it.get("model"):
                out.append(it["model"])
    for part in (st.get("multipart") or []):
        apply = part.get("apply")
        for it in (apply if isinstance(apply, list) else [apply]):
            if isinstance(it, dict) and it.get("model"):
                out.append(it["model"])
    return out


def _model_textures(ref: str, depth: int = 0) -> list[str]:
    """Textures of a model resolved through its parent chain."""
    from mcmaterials.net import cached_json
    ref = ref.removeprefix("minecraft:")
    if ref.startswith("builtin/") or depth > 8:
        return []
    d = cached_json("models/%s.json" % ref)
    if not d:
        return []
    out: list[str] = []
    parent = d.get("parent")
    if parent:
        out += _model_textures(parent, depth + 1)
    for val in (d.get("textures") or {}).values():
        if isinstance(val, str) and not val.startswith("#"):
            out.append(val.removeprefix("minecraft:"))
        elif isinstance(val, dict) and val.get("sprite"):
            out.append(str(val["sprite"]).removeprefix("minecraft:"))
    return out


@lru_cache(maxsize=4096)
def block_texture(block: str, fetch: bool = True) -> tuple[Image.Image | None, str]:
    """Most representative texture of a block (cache -> robust download)."""
    from mcmaterials.net import cached_json
    refs: list[str] = []
    st = cached_json("blockstates/%s.json" % block)
    if st:
        for mref in _model_refs_from_state(st):
            refs += _model_textures(mref)
    if not refs:
        meta = {}
        try:
            meta = (json.loads((ROOT / "skills" / "minecraft-block-models" /
                                "data" / "incomplete_blocks.json").read_text(encoding="utf-8"))
                    .get(block) or {})
        except Exception:  # noqa: BLE001
            meta = {}
        refs = [t for t in (meta.get("textures") or []) if isinstance(t, str)]
    if not refs:
        refs = ["block/" + block]
    seen = []
    for r in refs:
        r = r.removeprefix("minecraft:")
        if r not in seen:
            seen.append(r)
    for r in seen:
        img = _decode(r, fetch=fetch)
        if img is not None:
            return img, r
    return None, (seen[0] if seen else "")


def average_color(img: Image.Image) -> tuple[int, int, int] | None:
    """Average opaque color (ignores transparent pixels)."""
    img = img.convert("RGBA")
    px = list(img.getdata())
    tot = [0, 0, 0]
    n = 0
    for r, g, b, a in px:
        if a < 24:
            continue
        tot[0] += r; tot[1] += g; tot[2] += b; n += 1
    if not n:
        return None
    return (tot[0] // n, tot[1] // n, tot[2] // n)


def luminance(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
