"""方块实体几何冒烟（mcrender）：箱子/旗帜/头颅/潜影盒/罐 走 deepslate 导出的几何表。

旧行为：这些方块在资源包里没有方块模型 → mcrender 只能画**纯色立方体**（色相还是按名字
哈希出来的随机色）。现在用 ``tools/export_entity_models.js`` 从 deepslate SpecialRenderers
导出的 ``data/entity_models.json``（MIT）建真实几何。

覆盖：
1. 导出表存在且规模合理（状态数/贴图数）；
2. chest / ender_chest / white_banner / skeleton_skull / white_shulker_box / decorated_pot
   解析出真实四边形 + entity/* 贴图，且 render != 'fallback'；
3. 床/告示牌在 1.21.4+ 有真模型 → 走模型路径（不会被实体表抢走）；
4. 渲染不崩：箱子场景能出图，且箱子的 entity 贴图进了图集；
5. 缺贴图的容错：表里点到但不存在的贴图不会让解析崩（只是画不出来）。

Usage:  python tests/entity_models_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

sys.path.insert(0, str(Path(__file__).resolve().parent))  # noqa: E402
import _support  # noqa: E402

from mcrender.assets import auto_assets  # noqa: E402
from mcrender.entity_models import available, lookup, textures  # noqa: E402
from mcrender.model import ModelResolver  # noqa: E402
from mcrender.renderer import (Camera, TextureAtlas, build_scene,  # noqa: E402
                               render_view)

AIR = {"Name": "minecraft:air"}
STONE = {"Name": "minecraft:stone"}
OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))


def main() -> int:
    # 同上：先确保资源缓存可用（缺了就跳过并告诉人怎么预热）
    _support.need_mc_assets(4903, script="entity_models_smoke.py",
                            blocks=("chest", "ender_chest", "white_banner",
                                    "skeleton_skull", "white_shulker_box",
                                    "decorated_pot", "white_bed", "oak_sign",
                                    "bell", "stone"))
    print("\n== 1. 导出表 ==")
    n, tex = available(), textures()
    check("导出表已就位（状态 ≥100、贴图 ≥20）", n >= 100 and len(tex) >= 20,
          f"states={n} textures={len(tex)}")
    entity_tex = [t for t in tex if t.startswith("entity/")]
    check("表里的贴图都是合法 ref（entity/* 为主；水没方块会带 block/water_*）",
          all(t.startswith(("entity/", "block/")) for t in tex) and len(entity_tex) >= 20,
          f"entity={len(entity_tex)} 全部={len(tex)}")

    assets = auto_assets(4903, offline=True, verbose=False)
    res = ModelResolver(assets, verbose=False)

    print("\n== 2. 方块实体解析出真实几何 ==")
    cases = [
        ("chest", {"facing": "south", "waterlogged": "false"}, "entity/chest/normal"),
        ("ender_chest", {"facing": "south", "waterlogged": "false"}, "entity/chest/ender"),
        ("white_banner", {"rotation": "0"}, "entity/banner/banner_base"),
        ("skeleton_skull", {"rotation": "0", "powered": "false"}, "entity/skeleton/skeleton"),
        ("white_shulker_box", {"facing": "up"}, "entity/shulker/shulker_white"),
        ("decorated_pot", {"facing": "south", "cracked": "false", "waterlogged": "false"},
         "entity/decorated_pot/decorated_pot_base"),
    ]
    for name, props, want_tex in cases:
        bb = res.resolve_block(name, dict(props))
        check(f"{name}: 有几何 + 用 {want_tex}",
              len(bb.quads) > 0 and want_tex in bb.textures
              and bb.render != "fallback" and "entity-model" in bb.notes,
              f"quads={len(bb.quads)} tex={sorted(bb.textures)[:2]} render={bb.render}")

    print("\n== 3. 有真模型的方块不被实体表抢走 ==")
    for name, props, want in (
        ("white_bed", {"facing": "south", "part": "head", "occupied": "false"}, "block/"),
        ("oak_sign", {"rotation": "0", "waterlogged": "false"}, "block/"),
        ("bell", {"attachment": "floor", "facing": "south", "powered": "false",
                  "toggle": "false"}, "block/"),
    ):
        bb = res.resolve_block(name, dict(props))
        check(f"{name}: 走方块模型（贴图是 block/*）",
              bool(bb.textures) and all(t.startswith(want) for t in bb.textures),
              sorted(bb.textures)[:2])

    print("\n== 4. 真渲染：箱子场景出图 ==")
    pal = [AIR, STONE,
           {"Name": "minecraft:chest", "Properties": {"facing": "south", "waterlogged": "false"}},
           {"Name": "minecraft:white_banner", "Properties": {"rotation": "0"}}]
    vox = np.zeros((3, 4, 6), dtype=np.uint16)
    vox[0, :, :] = 1
    vox[1, 1, 1] = 2
    vox[1, 2, 4] = 3
    baked = [res.resolve_block(p["Name"].replace("minecraft:", ""),
                               p.get("Properties") or {}) for p in pal]
    refs = set()
    for bb in baked:
        refs |= bb.all_textures
    atlas = TextureAtlas(refs, assets, verbose=False)
    for bb in baked:
        core = bb.cube_textures or (bb.textures if bb.is_full_cube else set())
        if core:
            bb.occludes = bool(all(not atlas.info[t].has_zero
                                   and not atlas.info[t].has_partial
                                   for t in core if t in atlas.info))
    check("箱子的 entity 贴图进了图集",
          atlas.tile_of("entity/chest/normal") != 0, atlas.tile_of("entity/chest/normal"))
    scene = build_scene(vox, baked, atlas, ao=True, verbose=False)
    check("场景有几何（>60 个四边形）", len(scene) > 60, len(scene))
    img = render_view(scene, Camera(azimuth=45, elevation=30, scale=18), atlas,
                      ssaa=2, background="dark", bloom=0.0, verbose=False)
    arr = np.asarray(img.convert("RGB")).astype(int)
    check("渲染出图且不是全黑", arr.sum() > 20000, int(arr.sum()))

    print("\n== 5. 容错 ==")
    check("查不到的方块返回 None（不抛）", lookup("minecraft:not_a_block") is None)
    bb = res.resolve_block("definitely_missing_block", {})
    check("完全未知的方块仍然退化成纯色（render=fallback）",
          bb.render == "fallback" and not bb.quads, bb.render)

    # ---- 导出表的贴图归位：同一图集槽位被认成别人的贴图（实测彩色旗帜 6 个面写成 entity/bed/<色>）
    import json as _json  # noqa: PLC0415
    import collections as _collections  # noqa: PLC0415
    tab = _json.loads((ROOT / "packages" / "mcrender" / "data" / "entity_models.json")
                      .read_text(encoding="utf-8"))
    by_block: dict[str, set[str]] = _collections.defaultdict(set)
    for ent in tab["models"].values():
        for q in ent.get("quads") or []:
            by_block[ent["block"]].add(q.get("tex"))

    bad_banner = {b: sorted(t) for b, t in by_block.items()
                  if (b.endswith("_banner")) and any("bed" in (x or "") for x in t)}
    check("表里旗帜的面不再用床贴图（图集归位）", not bad_banner, bad_banner)

    def oxidation(name: str) -> str:
        for key in ("oxidized", "weathered", "exposed"):
            if key in name:
                return "copper_" + key
        return "copper"

    bad_chest = {b: sorted(t) for b, t in by_block.items()
                 if b.endswith("_copper_chest") and any(
                     x != f"entity/chest/{oxidation(b)}" for x in t)}
    check("铜箱的面只用自己那级氧化的贴图", not bad_chest, bad_chest)
    bad_golem = {b: sorted(t) for b, t in by_block.items()
                 if b.endswith("_copper_golem_statue") and
                 any(x != f"entity/copper_golem/copper_golem{'' if oxidation(b) == 'copper' else '_' + oxidation(b)[7:]}" for x in t)}
    check("铜像的面只用自己那级氧化的贴图", not bad_golem, bad_golem)

    # 表里引用的贴图必须在本地资产缓存里有对应文件（防“名字写错→永远画不出来”）
    tex_root = ROOT / ".cache" / "mcassets" / "26.2" / "textures"
    if tex_root.is_dir():
        allrefs = {r for t in by_block.values() for r in t if r}
        cached = {r for r in allrefs if (tex_root / (r + ".png")).is_file()}
        missing = sorted(allrefs - cached)
        # 缓存是“按需下载”的，所以只当大部分都被缓存过时才敢断言（否则就是没下过）
        if len(cached) >= len(allrefs) * 0.8:
            check("表里的贴图 ref 都能在 mcassets 里找到文件", not missing, missing[:8])
        else:
            print(f"  SKIP 贴图文件存在性（本地缓存只有 {len(cached)}/{len(allrefs)}）")

    # ---- 编辑器语义（blocks.py）：特判方块不能让客户端“跳过特判且不下载贴图”
    from mccore.schem_io import state_str  # noqa: PLC0415
    import mcstudio.blocks as B  # noqa: PLC0415
    from mcstudio import entity_assets as EA  # noqa: PLC0415
    import tempfile as _tempfile  # noqa: PLC0415
    _old = B._cache_dir
    B._cache_dir = lambda: Path(_tempfile.mkdtemp())      # 冷缓存，免得读到旧语义
    try:
        cat = B.BlockCatalog()
        states = [
            {"Name": "minecraft:orange_banner", "Properties": {"rotation": "8"}},
            {"Name": "minecraft:chest", "Properties": {"facing": "south", "type": "single", "waterlogged": "false"}},
            {"Name": "minecraft:oak_sign", "Properties": {"rotation": "8", "waterlogged": "false"}},
            {"Name": "minecraft:shulker_box", "Properties": {"facing": "up"}},
            {"Name": "minecraft:creeper_wall_head", "Properties": {"facing": "north", "powered": "false"}},
        ]
        info = cat.resolve(states, "26.2")
        extras = cat.extra_textures("26.2")
        ob = info[state_str(states[0])]
        ch = info[state_str(states[1])]
        sg = info[state_str(states[2])]
        sh = info[state_str(states[3])]
        wh = info[state_str(states[4])]
        check("旗帜/箱子：has_elements=false + entity_geometry=true（客户端才会跑特判）",
              ob["has_elements"] is False and ob["entity_geometry"] is True and
              ch["has_elements"] is False and ch["entity_geometry"] is True,
              (ob["has_elements"], ch["has_elements"]))
        check("告示牌：has_elements=true（1.21.4+ 有真模型，不跑特判、不重复叠）",
              sg["has_elements"] is True and sg["entity_geometry"] is False, sg)
        check("无色潜影盒：has_elements=false + fallback_texture（几何拿不到就画自己的立方体）",
              sh["has_elements"] is False and sh["fallback_texture"] == "block/shulker_box", sh)
        check("墙上的头颅也认成特判方块（deepslate 同一套渲染器）",
              wh["special"] == "head" and EA.special_kind("creeper_wall_head") == "head" and
              EA.special_textures("creeper_wall_head") == ["entity/creeper/creeper"], wh)
        check("entity 贴图进了 extra_textures（客户端会真下载）",
              "entity/banner/banner_base" in extras and "entity/chest/normal" in extras,
              extras[:6])
        check("无色潜影盒的兜底贴图也在 extra_textures 里",
              "block/shulker_box" in extras, [e for e in extras if "shulker" in e])
    finally:
        B._cache_dir = _old

    print(f"\n{'ALL PASS' if not FAIL else 'FAILED'}  "
          f"({len(OK)} passed, {len(FAIL)} failed)")
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
