"""流体渲染冒烟（mcrender）：水/岩浆按 level 高度、侧用 *_flow、水染水色、waterlogged 补水面。

旧行为：水/岩浆是**整块 6 面立方体**、用 ``water_still`` 贴单面、且**不染色** ——
预览图里的水是灰白实心方块。现在：源方块 8/9 高、流动按 level、角点取相邻最大、
侧面用 ``block/*_flow``、贴图锚在液面、水染 ``#3F76E4``。

覆盖：
1. 源方块液面高度 = 8/9（不是 1.0）；
2. 流动 level 越大液面越低（1..7 单调下降）；
3. 侧面用的是 flow 贴图（与顶面 still 不是同一张）；
4. 水染水色、岩浆不染；
5. 同流体内部面被剔除（3×3 水池：顶面 9 个而不是 54 个）；
6. waterlogged 方块（楼梯）会额外补一层水面；
7. 真渲染一张小图：水像素发蓝（b > r）。

Usage:  python tests/fluid_smoke.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

sys.path.insert(0, str(Path(__file__).resolve().parent))  # noqa: E402
import _support  # noqa: E402

from mcrender.assets import auto_assets  # noqa: E402
from mcrender.model import LIQUID_FLOW, LIQUIDS, ModelResolver  # noqa: E402
from mcrender.renderer import (Camera, TextureAtlas, build_scene,  # noqa: E402
                               render_view)

AIR = {"Name": "minecraft:air"}
STONE = {"Name": "minecraft:stone"}
OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))


def water_state(level: int) -> dict:
    return {"Name": "minecraft:water", "Properties": {"level": str(level)}}


def build(palette, vox):
    assets = auto_assets(4903, offline=True, verbose=False)
    res = ModelResolver(assets, verbose=False)
    baked = [res.resolve_block(p["Name"].replace("minecraft:", ""),
                               p.get("Properties") or {}) for p in palette]
    refs = set()
    for bb in baked:
        refs |= bb.all_textures          # 含液体/水没的 *_still + *_flow
    atlas = TextureAtlas(refs, assets, verbose=False)
    # 流体贴图必须真的进图集（否则采样到 0 号“缺贴图”tile → 品红/黑棋盘）
    for ref in (LIQUIDS["water"], LIQUID_FLOW["water"]):
        assert ref in atlas.info, f"流体贴图没进图集: {ref}"
    for bb in baked:
        core = bb.cube_textures or (bb.textures if bb.is_full_cube else set())
        if core:
            bb.occludes = bool(all(not atlas.info[t].has_zero
                                   and not atlas.info[t].has_partial
                                   for t in core if t in atlas.info))
    scene = build_scene(vox, baked, atlas, ao=True, verbose=False)
    return scene, atlas


def fluid_faces(scene, atlas, kind):
    """（顶面, 底面, 侧面）的四边形索引：按贴图 tile 分。"""
    still = atlas.tile_of(LIQUIDS[kind])
    flow = atlas.tile_of(LIQUID_FLOW[kind])
    top, bottom, side = [], [], []
    normals = scene.normal
    for i, t in enumerate(scene.tex):
        if t == still:
            (top if normals[i][1] > 0.5 else bottom).append(i)
        elif t == flow:
            side.append(i)
    return top, bottom, side


def ys_of(scene, idxs):
    return sorted({round(float(scene.pos[i, :, 1].max()), 4) for i in idxs})


def main() -> int:
    # 离线资源缓存（.cache/mcassets）没热就跳过：渲染类冒烟走 offline=True，
    # 全新 clone / CI 上缓存是空的 —— 缺了要报“缺缓存”，不能变成看不懂的断言失败。
    _support.need_mc_assets(4903, script="fluid_smoke.py",
                            blocks=("stone", "water", "lava", "oak_stairs"))
    print("\n== 1. 源方块高度 8/9、同流体内部面剔除 ==")
    pal = [AIR, STONE, water_state(0)]
    vox = np.zeros((3, 5, 5), dtype=np.uint16)
    vox[0, :, :] = 1
    vox[1, 1:4, 1:4] = 2                     # 3×3 水源
    scene, atlas = build(pal, vox)
    top, bottom, side = fluid_faces(scene, atlas, "water")
    check("水面高度 = 8/9（不是整块 1.0）", ys_of(scene, top) == [1.8889], ys_of(scene, top))
    check("3×3 水池只有 9 个顶面（内部面被剔除）", len(top) == 9, f"top={len(top)}")
    check("底面被石头挡住 → 不画", len(bottom) == 0, f"bottom={len(bottom)}")
    check("侧面 12 个（池子四边）", len(side) == 12, f"side={len(side)}")

    print("\n== 2. 流动 level → 液面高度单调 ==")
    heights = []
    for level in (1, 4, 7):
        pal = [AIR, STONE, water_state(level)]
        vox = np.zeros((3, 5, 5), dtype=np.uint16)
        vox[0, :, :] = 1
        vox[1, 1:4, 1:4] = 2
        scene, atlas = build(pal, vox)
        top, _, _ = fluid_faces(scene, atlas, "water")
        heights.append(ys_of(scene, top)[0])
    check("level 1 > 4 > 7 的液面高度", heights[0] > heights[1] > heights[2], heights)
    check("level 7 = 1/9 高", abs(heights[2] - (1 + 1 / 9)) < 0.002, heights[2])

    print("\n== 3. 贴图与染色 ==")
    pal = [AIR, STONE, water_state(0), {"Name": "minecraft:lava", "Properties": {"level": "0"}}]
    vox = np.zeros((3, 5, 5), dtype=np.uint16)
    vox[0, :, :] = 1
    vox[1, 1:4, 1:4] = 2
    vox[1, 4, 4] = 3
    scene, atlas = build(pal, vox)
    check("侧面用 flow 贴图（与顶面 still 不同）",
          atlas.tile_of(LIQUIDS["water"]) != atlas.tile_of(LIQUID_FLOW["water"]))
    water_top, _, water_side = fluid_faces(scene, atlas, "water")
    check("水染水色 #3F76E4",
          tuple(int(v) for v in scene.tint[water_top[0]]) == (0x3F, 0x76, 0xE4),
          tuple(int(v) for v in scene.tint[water_top[0]]))
    lava_top, _, _ = fluid_faces(scene, atlas, "lava")
    check("岩浆不染（贴图本身有色）",
          lava_top and tuple(int(v) for v in scene.tint[lava_top[0]]) == (255, 255, 255),
          tuple(int(v) for v in scene.tint[lava_top[0]]) if lava_top else None)
    check("水走透明通道（layer=2），岩浆不透明（layer=0）",
          int(scene.layer[water_top[0]]) == 2 and int(scene.layer[lava_top[0]]) == 0)

    print("\n== 4. waterlogged 方块补水面 ==")
    stairs = {"Name": "minecraft:oak_stairs",
              "Properties": {"facing": "north", "half": "bottom",
                             "shape": "straight", "waterlogged": "true"}}
    pal = [AIR, STONE, stairs]
    vox = np.zeros((3, 5, 5), dtype=np.uint16)
    vox[0, :, :] = 1
    vox[1, 2, 2] = 2
    scene, atlas = build(pal, vox)
    top, _, side = fluid_faces(scene, atlas, "water")
    check("水没楼梯也画出水面（8/9 高）",
          len(top) == 1 and ys_of(scene, top) == [1.8889],
          f"top={len(top)} y={ys_of(scene, top)}")
    check("水没楼梯有 4 个侧面", len(side) >= 4, f"side={len(side)}")

    print("\n== 5. 真渲染：水像素发蓝 ==")
    pal = [AIR, STONE, water_state(0)]
    vox = np.zeros((3, 6, 6), dtype=np.uint16)
    vox[0, :, :] = 1
    vox[1, 1:5, 1:5] = 2
    scene, atlas = build(pal, vox)
    from PIL import Image  # noqa: PLC0415
    img = render_view(scene, Camera(azimuth=45, elevation=35, scale=20), atlas,
                      ssaa=2, background="dark", bloom=0.0, verbose=False)
    arr = np.asarray(img.convert("RGB")).astype(int)
    blue = ((arr[:, :, 2] > arr[:, :, 0] + 20) & (arr[:, :, 2] > 80)).sum()
    check("渲染图里存在蓝色水像素（不是灰白）", blue > 100, f"blue={blue}")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    check("PNG 可编码（渲染产品无异常）", len(buf.getvalue()) > 500, len(buf.getvalue()))

    print(f"\n{'ALL PASS' if not FAIL else 'FAILED'}  "
          f"({len(OK)} passed, {len(FAIL)} failed)")
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
