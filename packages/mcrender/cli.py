"""用真实方块模型 + 真实贴图渲染结构（.schem / .litematic）→ PNG。

Examples
--------
    # three standard views, 3 px/block
    python -m mcrender.cli build.schem --views iso,front,top --scale 3

    # one hero shot, perspective, high quality
    python -m mcrender.cli build.schem --azimuth 35 --elevation 22 \
        --proj persp --scale 4 --ssaa 3 --out hero

    # cutaway: hide everything with x < 112 (see the interior from +x)
    python -m mcrender.cli build.schem --views right --cut x=112

    # download every asset for a build once, then render offline later
    python -m mcrender.cli build.schem --prefetch
    python -m mcrender.cli build.schem --offline --views iso

    # inspect how each palette entry resolves (full cube / model / fallback)
    python -m mcrender.cli build.schem --list
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent

from mccore import structure_io as S  # noqa: E402
from mckit import update as UPD  # noqa: E402
from mcrender.assets import Assets, auto_assets  # noqa: E402
from mcrender.model import ModelResolver  # noqa: E402
from mcrender.renderer import Camera, TextureAtlas, build_scene, render_view  # noqa: E402

VIEWS = {
    "iso": (45.0, 30.0),
    "iso2": (135.0, 30.0),
    "iso3": (225.0, 30.0),
    "iso4": (315.0, 30.0),
    "front": (0.0, 12.0),
    "back": (180.0, 12.0),
    "left": (270.0, 12.0),
    "right": (90.0, 12.0),
    "top": (0.0, 89.5),
    "hero": (35.0, 22.0),
    "hero2": (145.0, 20.0),
}
# named multi-view sets
VIEW_SETS = {
    "all": "iso,front,back,left,right,top,hero,hero2",
    "orbit": "iso,iso2,iso3,iso4",
    "elevations": "front,back,left,right",
}


def parse_views(spec: str):
    if spec in VIEW_SETS:
        spec = VIEW_SETS[spec]
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
            raise SystemExit("unknown view %r (use a preset or az:el)" % part)
    return out


def apply_cuts(vox: np.ndarray, cuts: list[str]) -> np.ndarray:
    """--cut axis=coord hides blocks with coordinate < coord on that axis."""
    if not cuts:
        return vox
    v = vox.copy()
    axes = {"x": 2, "y": 0, "z": 1}  # voxel array is (y, z, x)
    for c in cuts:
        axis, _, coord = c.partition("=")
        axis = axis.strip().lower()
        if axis not in axes:
            raise SystemExit("--cut axis must be x, y or z")
        coord = int(coord)
        a = axes[axis]
        sl = [slice(None)] * 3
        sl[a] = slice(0, max(0, coord))
        v[tuple(sl)] = 0
    return v


def load_scene(args):
    t0 = time.time()
    d = S.read_structure(args.input)
    vox = d["voxels"]
    palette = d["palette"]
    # 方块更新模拟：只影响本次渲染（不改文件）——让墙/栅栏/铁栏杆连起来、
    # 楼梯 shape 正确。必须在 --cut 之前做，否则切面会被当成“没有邻居”。
    if getattr(args, "update_states", False):
        vox, palette, rep = UPD.updated_copy(vox, palette)
        if rep["changed"] and not args.quiet:
            kinds = "、".join(f"{k}×{v}"
                             for k, v in sorted(rep["families"].items()))
            print("  更新状态: %d 格（%s）" % (rep["changed"], kinds))
    if args.cut:
        vox = apply_cuts(vox, args.cut)
    print("  %s: %s  %d blocks  palette %d  data version %d"
          % (Path(args.input).name, "x".join(map(str, d["size"])),
             int((vox != 0).sum()), len(palette), d["data_version"]))

    assets = auto_assets(d["data_version"], args.version, args.cache, args.offline,
                         verbose=not args.quiet)
    resolver = ModelResolver(assets)
    baked = []
    t1 = time.time()
    # warm the cache in parallel: blockstates -> models -> textures
    names = [p["Name"].replace("minecraft:", "") for p in palette]
    rep = assets.prefetch(names)
    if not args.quiet:
        print("  prefetch: %d blockstates, %d models, %d textures%s" % (
            rep["blockstates"], rep["models"], rep["textures"],
            (" (%d without blockstate)" % len(rep["missing"])) if rep["missing"] else ""))
    for p in palette:
        name = p["Name"].replace("minecraft:", "")
        props = p.get("Properties") or {}
        baked.append(resolver.resolve_block(name, props))
    print("  resolved %d palette entries in %.1fs" % (len(baked), time.time() - t1))

    refs = set()
    for bb in baked:
        refs |= bb.all_textures          # 含液体/水没的 *_still + *_flow
    atlas = TextureAtlas(refs, assets, verbose=not args.quiet)

    # mark opaque cube bodies (texture-based)——用方块体自身贴图（overlay 不算）
    for bb in baked:
        core = bb.cube_textures or (bb.textures if bb.is_full_cube else set())
        if core:
            opaque = all(not atlas.info[t].has_zero and not atlas.info[t].has_partial
                         for t in core if t in atlas.info)
            bb.occludes = bool(opaque)

    t2 = time.time()
    scene = build_scene(vox, baked, atlas, ao=not args.no_ao, verbose=not args.quiet)
    print("  scene built in %.1fs" % (time.time() - t2))
    return d, vox, palette, baked, assets, atlas, scene, resolver


def cmd_list(baked, assets):
    print("%-44s %-6s %-5s %-6s %-6s %s" % ("block[state]", "quads", "full", "ao", "render", "textures"))
    print("-" * 130)
    for bb in baked:
        state = bb.name
        if bb.props:
            state += "[" + ",".join("%s=%s" % kv for kv in sorted(bb.props.items())) + "]"
        print("%-44s %-6d %-5s %-6s %-6s %s" % (
            state[:44], len(bb.quads), bb.is_full_cube, bb.ao,
            bb.render if not bb.builtin else bb.builtin,
            ", ".join(sorted(bb.textures))[:60]))


def cmd_index(baked, assets, path):
    """Write a machine-readable block mapping table for the palette."""
    out = {}
    for bb in baked:
        state = bb.name
        if bb.props:
            state += "[" + ",".join("%s=%s" % kv for kv in sorted(bb.props.items())) + "]"
        summ = assets.blocks_summary().get(bb.name)
        out[state] = {
            "block": bb.name,
            "properties": bb.props,
            "defaults": (summ[1] if summ and len(summ) > 1 else {}),
            "legal_values": (summ[0] if summ else {}),
            "quads": len(bb.quads),
            "is_full_cube": bb.is_full_cube,
            "ambientocclusion": bb.ao,
            "render": bb.render if not bb.builtin else bb.builtin,
            "textures": sorted(bb.textures),
            "bbox": ([round(float(x), 4) for x in bb.bbox[0]],
                     [round(float(x), 4) for x in bb.bbox[1]]) if bb.bbox else None,
            "notes": bb.notes,
        }
    Path(path).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    print("  wrote %s (%d entries)" % (path, len(out)))


def cmd_sheet(images, out, cols=3):
    from PIL import Image, ImageDraw

    ims = [Image.open(p).convert("RGB") for p in images]
    if not ims:
        return
    W = max(i.width for i in ims)
    H = max(i.height for i in ims)
    rows = int(np.ceil(len(ims) / cols))
    sheet = Image.new("RGB", (W * cols + 16 * (cols + 1), H * rows + 16 * (rows + 1)), (243, 244, 246))
    dr = ImageDraw.Draw(sheet)
    for k, (im, p) in enumerate(zip(ims, images)):
        r, c = divmod(k, cols)
        x = 16 + c * (W + 16) + (W - im.width) // 2
        y = 16 + r * (H + 16) + (H - im.height) // 2
        sheet.paste(im, (x, y))
        dr.rectangle([x - 1, y - 1, x + im.width, y + im.height], outline=(210, 212, 216))
        dr.text((x + 6, y + 6), Path(p).stem, fill=(70, 74, 80))
    sheet.save(out)
    print("  sheet -> %s (%dx%d)" % (out, sheet.width, sheet.height))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Accurate structure renderer (.schem/.schem)")
    ap.add_argument("input", help=".schem / .litematic file")
    ap.add_argument("--out", default=None, help="output prefix (default <stem>_r)")
    ap.add_argument("--views", default="iso,front,top",
                    help="iso,front,back,left,right,top,hero,hero2,all,orbit,elevations or az:el")
    ap.add_argument("--azimuth", type=float, default=None)
    ap.add_argument("--elevation", type=float, default=None)
    ap.add_argument("--scale", type=float, default=3.0, help="pixels per block (default 3)")
    ap.add_argument("--ssaa", type=int, default=2, help="supersampling (1-4, default 2)")
    ap.add_argument("--proj", choices=("ortho", "persp"), default="ortho")
    ap.add_argument("--background", default="dark",
                    choices=("sky", "dark", "black", "white", "transparent"),
                    help="背景：sky 渐变 / dark 深灰 / black 纯黑 / white 纯白 / transparent 透明")
    ap.add_argument("--bloom", type=float, default=0.5)
    ap.add_argument("--no-ao", action="store_true")
    ap.add_argument("--update-states", action="store_true",
                    help="渲染前按邻居重算不完整方块的连接状态（墙/栅栏/铁栏杆/玻璃板/楼梯 shape）；只影响本次渲染")
    ap.add_argument("--cut", action="append", default=None, metavar="AXIS=COORD",
                    help="hide blocks with coordinate < COORD (repeatable)")
    ap.add_argument("--version", default=None, help="Minecraft asset version (default: from data version)")
    ap.add_argument("--cache", default=None, help="asset cache dir (default .cache/mcassets)")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--prefetch", action="store_true", help="only download assets, no render")
    ap.add_argument("--list", action="store_true", help="list palette resolution and exit")
    ap.add_argument("--index", default=None, metavar="JSON", help="write a block mapping table and exit")
    ap.add_argument("--sheet", action="store_true", help="also write a contact sheet")
    a = ap.parse_args(argv)

    if not os.path.exists(a.input):
        raise SystemExit("no such file: %s" % a.input)
    out_prefix = a.out or (Path(a.input).stem + "_r")

    # prefetch-only mode
    if a.prefetch:
        d = S.read_structure(a.input)
        assets = auto_assets(d["data_version"], a.version, a.cache, a.offline)
        names = [p["Name"].replace("minecraft:", "") for p in d["palette"]]
        rep = assets.prefetch(names)
        print("  prefetch: %s" % {k: v for k, v in rep.items() if k != "missing"})
        if rep["missing"]:
            print("  no blockstate for: %s" % ", ".join(rep["missing"][:20]))
        return 0

    d, vox, palette, baked, assets, atlas, scene, resolver = load_scene(a)

    if a.list:
        cmd_list(baked, assets)
        return 0
    if a.index:
        cmd_index(baked, assets, a.index)
        return 0

    if a.azimuth is not None or a.elevation is not None:
        views = [("view", a.azimuth if a.azimuth is not None else 45.0,
                  a.elevation if a.elevation is not None else 30.0)]
    else:
        views = parse_views(a.views)

    outputs = []
    for tag, az, el in views:
        cam = Camera(azimuth=az, elevation=el, scale=a.scale, proj=a.proj)
        img = render_view(scene, cam, atlas, ssaa=a.ssaa, background=a.background,
                          bloom=a.bloom, verbose=not a.quiet)
        path = "%s_%s.png" % (out_prefix, tag)
        img.save(path)          # 透明模式带 alpha 通道（RGBA）
        outputs.append(path)
        print("  -> %s" % path)
    if a.sheet and len(outputs) > 1:
        cmd_sheet(outputs, "%s_sheet.png" % out_prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
