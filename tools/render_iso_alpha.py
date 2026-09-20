"""Isometric multi-view renders with a *true* transparent background.

``mcrender.cli --background transparent`` only fills the canvas black (RGB).
This wrapper renders each view twice (black + white backdrop), recovers per-pixel
alpha from the difference and writes RGBA PNGs.  One scene build is shared by
all views, so a multi-angle orbit costs one geometry pass plus one raster pass
per view.

Examples
--------
    python tools/render_iso_alpha.py build.litematic --views orbit --scale 3
    python tools/render_iso_alpha.py build.litematic --views iso,high,low \
        --out-dir renders --bg 243,244,246 --sheet
"""
from __future__ import annotations

import argparse
import gc
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from mccore import structure_io as S
from mcrender.assets import auto_assets
from mcrender.model import ModelResolver
from mcrender.renderer import (Camera, TextureAtlas, build_scene, concat, _emit_fast,
                               _emit_fallback_cube, _emit_general, _fallback_color,
                               _is_emissive, _is_fullbright, _raster, _triangles)

VIEWS = {
    "iso": (45.0, 30.0),
    "iso2": (135.0, 30.0),
    "iso3": (225.0, 30.0),
    "iso4": (315.0, 30.0),
    "high": (45.0, 55.0),
    "low": (45.0, 15.0),
    "top": (0.0, 80.0),
}
VIEW_SETS = {
    "orbit": "iso,iso2,iso3,iso4",
    "all": "iso,iso2,iso3,iso4,high,low",
}


def parse_views(spec: str):
    expanded = []
    for raw in spec.split(","):
        raw = raw.strip()
        if raw in VIEW_SETS:
            expanded.extend(VIEW_SETS[raw].split(","))
        elif raw:
            expanded.append(raw)
    spec = ",".join(expanded)
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if part in VIEWS:
            az, el = VIEWS[part]
            out.append((part, az, el))
        elif ":" in part:
            az, el = part.split(":", 1)
            out.append(("az%s_el%s" % (az, el), float(az), float(el)))
        else:
            raise SystemExit("unknown view %r" % part)
    return out


def build_scene_fast(vox, baked, atlas, verbose=True):
    """Drop-in copy of mcrender.renderer.build_scene without the per-palette
    full-array scans: one stable argsort groups voxel positions by palette id,
    so each id only touches its own positions (same output order/geometry)."""
    ny, nz, nx = vox.shape
    order = np.argsort(vox.ravel(), kind="stable")
    sids = vox.ravel()[order]
    n = len(baked)
    starts = np.searchsorted(sids, np.arange(n), "left")
    ends = np.searchsorted(sids, np.arange(n), "right")

    occ = np.zeros(vox.shape, bool)
    occ_flat = occ.ravel()
    for i, bb in enumerate(baked):
        if bb.is_full_cube and bb.occludes and ends[i] > starts[i]:
            occ_flat[order[starts[i]:ends[i]]] = True
    occp = np.zeros((ny + 2, nz + 2, nx + 2), bool)
    occp[1:-1, 1:-1, 1:-1] = occ

    parts, n_fast, n_gen = [], 0, 0
    for i in np.flatnonzero(ends > starts):
        bb = baked[i]
        if bb.render == "invisible":
            continue
        ys, zs, xs = np.unravel_index(order[starts[i]:ends[i]], vox.shape)
        fb = _is_fullbright(bb.name, bb.props)
        em = _is_emissive(bb.name)
        if not bb.quads:
            mask = np.zeros(vox.shape, bool)
            mask[ys, zs, xs] = True
            tile = atlas.add_solid(_fallback_color(bb.name))
            s = _emit_fallback_cube(mask, occ, tile)
            if len(s):
                parts.append(s)
                n_gen += len(s)
            continue
        if bb.is_full_cube and not fb:
            qbf = {q.face: q for q in bb.quads}
            got = _emit_fast(bb, qbf, ys, zs, xs, occ, occp, vox, i, atlas, em, fb)
            if got:
                parts.extend(got)
                n_fast += sum(len(s) for s in got)
            continue
        bpos = np.stack([ys, zs, xs], axis=1)
        s = _emit_general(bb, bpos, occ, occp, vox, i, atlas, em, fb)
        if len(s):
            parts.append(s)
            n_gen += len(s)
    scene = concat(parts)
    if verbose:
        print("  geometry(fast): %d quads (%d full-cube, %d modelled)" % (len(scene), n_fast, n_gen))
    return scene


def load_scene(args):
    t0 = time.time()
    d = S.read_structure(args.input)
    vox = d["voxels"]
    palette = d["palette"]
    print("  %s: %s  %d blocks  palette %d  data version %d" % (
        Path(args.input).name, "x".join(map(str, d["size"])),
        int((vox != 0).sum()), len(palette), d["data_version"]))

    assets = auto_assets(d["data_version"], args.version, args.cache, args.offline,
                         verbose=not args.quiet)
    names = [p["Name"].replace("minecraft:", "") for p in palette]
    rep = assets.prefetch(names)
    if not args.quiet and rep.get("missing"):
        print("  no blockstate for %d palette entries" % len(rep["missing"]))

    resolver = ModelResolver(assets)
    t1 = time.time()
    baked = []
    special = {}
    for p in palette:
        name = p["Name"].replace("minecraft:", "")
        props = p.get("Properties") or {}
        bb = resolver.resolve_block(name, props)
        if bb.render == "invisible" or (not bb.quads and not bb.builtin):
            key = bb.builtin or "invisible"
            special[key] = special.get(key, 0) + 1
        baked.append(bb)
    print("  resolved %d palette entries in %.1fs%s" % (
        len(baked), time.time() - t1,
        ("  special: %s" % special) if special and not args.quiet else ""))

    refs = set()
    for bb in baked:
        refs |= bb.textures
    atlas = TextureAtlas(refs, assets, verbose=not args.quiet)
    # same occlusion rule as mcrender.cli.load_scene
    for bb in baked:
        if bb.is_full_cube and bb.quads:
            opaque = all(not atlas.info[t].has_zero and not atlas.info[t].has_partial
                         for t in bb.textures if t in atlas.info)
            bb.occludes = bool(opaque and bb.textures)

    t2 = time.time()
    if args.no_fast:
        scene = build_scene(vox, baked, atlas, ao=not args.no_ao, verbose=not args.quiet)
    else:
        scene = build_scene_fast(vox, baked, atlas, verbose=not args.quiet)
    print("  scene built in %.1fs (load total %.1fs)" % (time.time() - t2, time.time() - t0))
    return scene, atlas


def rasterize_dual(r, atlas):
    """Raster the projected scene onto a black and a white canvas in one pass."""
    W, H = r["W"], r["H"]
    black = [np.zeros((H, W, 3), np.uint8), np.full((H, W), 1e30, np.float32),
             np.zeros((H, W), np.uint8)]
    white = [np.full((H, W, 3), 255, np.uint8), np.full((H, W), 1e30, np.float32),
             np.zeros((H, W), np.uint8)]
    targets = (black, white)

    def run(mask, pass_id):
        if not mask.any():
            return
        arr = dict(
            vxy=np.ascontiguousarray(r["vxy"][mask]),
            vw=np.ascontiguousarray(r["vw"][mask]),
            vuv=np.ascontiguousarray(r["vuv"][mask]),
            vshade=np.ascontiguousarray(r["vshade"][mask]),
            tex=np.ascontiguousarray(r["tex"][mask]),
            tint=np.ascontiguousarray(r["tint"][mask]),
            layer=np.ascontiguousarray(r["layer"][mask]),
            glow=np.ascontiguousarray(r["glow"][mask]),
        )
        for buf in targets:
            _raster(W, H, arr["vxy"], arr["vw"], arr["vuv"], arr["vshade"],
                    arr["tex"], arr["tint"], arr["layer"], arr["glow"],
                    r["persp"], atlas.pixels, atlas.cols, buf[1], buf[0], buf[2], pass_id)

    opaque = r["layer"] < 2
    run(opaque, 0)
    trans = ~opaque
    if trans.any():
        order = np.argsort(-r["vw"].mean(axis=1))
        perm = order[trans[order]]
        run(perm, 1)
    return black, white


def finish_view(black, white, ssaa, bloom, bg):
    """Recover straight-alpha RGBA from the black/white renders."""
    H, W = black[0].shape[:2]
    w, h = max(1, W // ssaa), max(1, H // ssaa)

    def down(arr):
        return np.asarray(Image.fromarray(arr).resize((w, h), Image.LANCZOS),
                          dtype=np.float32)

    ib = down(black[0])
    iw = down(white[0])
    alpha = np.clip(1.0 - (iw - ib).mean(axis=2) / 255.0, 0.0, 1.0)
    color = np.zeros_like(ib)
    nz = alpha > 1.0 / 255.0
    color[nz] = np.clip(ib[nz] / alpha[nz][:, None], 0.0, 255.0)  # un-premultiply

    if bloom > 0 and black[2].any():
        g = Image.fromarray(black[2], "L").filter(
            ImageFilter.GaussianBlur(radius=max(1.0, 1.6 * ssaa)))
        garr = np.asarray(g.resize((w, h), Image.LANCZOS), dtype=np.float32) / 255.0
        color = np.clip(color + garr[:, :, None] * (bloom * 90.0) * nz[:, :, None],
                        0.0, 255.0)

    if bg is None:
        out = np.zeros((h, w, 4), np.uint8)
        out[:, :, :3] = color.astype(np.uint8)
        out[:, :, 3] = (alpha * 255.0 + 0.5).astype(np.uint8)
        return Image.fromarray(out, "RGBA")
    base = np.asarray(bg, np.float32)[None, None, :]
    rgb = color * alpha[:, :, None] + base * (1.0 - alpha[:, :, None])
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")


def make_sheet(paths, out, cols=2, scale=0.5):
    ims = [Image.open(p).convert("RGBA") for p in paths]
    ims = [im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                     Image.LANCZOS) for im in ims]
    title_h, pad = 26, 16
    tw = max(i.width for i in ims)
    th = max(i.height for i in ims) + title_h
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + pad) + pad),
                      (243, 244, 246))
    dr = ImageDraw.Draw(sheet)
    for k, im in enumerate(ims):
        r, c = divmod(k, cols)
        x = pad + c * (tw + pad)
        y = pad + r * (th + pad)
        canvas = Image.new("RGB", (im.width, im.height), (255, 255, 255))
        canvas.paste(im, (0, 0), im)
        sheet.paste(canvas, (x + (tw - im.width) // 2, y + title_h))
        dr.text((x + 4, y + 6), Path(paths[k]).stem, fill=(60, 64, 70))
        dr.rectangle([x, y, x + tw - 1, y + th - 1], outline=(214, 216, 220))
    sheet.save(out)
    print("  sheet -> %s (%dx%d)" % (out, sheet.width, sheet.height))


def parse_bg(text):
    if text.lower() in ("none", "transparent", "rgba"):
        return None
    parts = [int(float(v)) for v in text.split(",")]
    if len(parts) != 3:
        raise SystemExit("--bg must be none or R,G,B")
    return tuple(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Isometric multi-view renders (RGBA).")
    ap.add_argument("input", help=".schem / .litematic file")
    ap.add_argument("--out-dir", default=None, help="default: <input dir>/renders")
    ap.add_argument("--prefix", default=None, help="file name prefix (default: input stem)")
    ap.add_argument("--views", default="orbit", help="orbit,all or " + " ".join(VIEWS))
    ap.add_argument("--scale", type=float, default=3.0, help="pixels per block")
    ap.add_argument("--ssaa", type=int, default=2, help="supersampling 1-4")
    ap.add_argument("--pad", type=int, default=16, help="final margin in px")
    ap.add_argument("--bloom", type=float, default=0.5)
    ap.add_argument("--no-ao", action="store_true")
    ap.add_argument("--no-fast", action="store_true",
                    help="use engine build_scene (slow) instead of the grouped one")
    ap.add_argument("--bg", default="none", help="none -> RGBA, or R,G,B composite color")
    ap.add_argument("--sheet", action="store_true", help="also write a contact sheet")
    ap.add_argument("--sheet-scale", type=float, default=0.5)
    ap.add_argument("--version", default=None)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    inp = Path(a.input)
    if not inp.exists():
        raise SystemExit("no such file: %s" % a.input)
    out_dir = Path(a.out_dir) if a.out_dir else inp.parent / "renders"
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = a.prefix or inp.stem
    bg = parse_bg(a.bg)
    views = parse_views(a.views)

    scene, atlas = load_scene(a)
    outputs = []
    for tag, az, el in views:
        t0 = time.time()
        cam = Camera(azimuth=az, elevation=el, scale=a.scale, proj="ortho", pad=a.pad)
        r = _triangles(scene, cam, a.ssaa)
        if r is None:
            print("  view %s: nothing visible" % tag)
            continue
        black, white = rasterize_dual(r, atlas)
        del r
        gc.collect()
        img = finish_view(black, white, a.ssaa, a.bloom, bg)
        del black, white
        gc.collect()
        path = out_dir / ("%s_%s.png" % (prefix, tag))
        img.save(path)
        outputs.append(str(path))
        print("  [%d/%d] %-6s az=%g el=%g -> %s  %dx%d  %.1fs" % (
            len(outputs), len(views), tag, az, el, path.name, img.width, img.height,
            time.time() - t0))

    if a.sheet and len(outputs) > 1:
        make_sheet(outputs, out_dir / ("%s_sheet.png" % prefix), scale=a.sheet_scale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
