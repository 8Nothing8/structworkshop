"""方块实体 / 特判方块的**贴图清单**（编辑器 3D 用）。

deepslate 的 ``SpecialRenderers``（MIT）在方块自身模型没有几何时，会按方块名补一套
几何（箱子/床/告示牌/旗帜/头颅/潜影盒/装饰罐/钟/导管/铜傀儡）。这套几何要的贴图
（``entity/chest/normal`` 之类）**不在任何 blockstate/model 里**，只写在这张表里，
所以资源加载器必须单独把它们取回来 —— 否则 deepslate 会去采样图集 0 号 tile，
也就是那块品红/黑“缺贴图”棋盘（旧行为：床/告示牌/水侧面花屏）。

本模块只做两件事：

* :func:`special_kind` —— 方块名 → 特判类型（``None`` = 不特判，走正常模型）；
* :func:`special_textures` / :func:`fluid_textures` —— 该方块需要哪些贴图 ref
  （相对 ``textures/``，与 ``mcrender.assets.Assets.fetch('textures/<ref>.png')`` 一致）。

版本差异：床和告示牌在 1.21.4+ 已经是**普通方块模型**（不再用 entity 贴图），
表里照样给出 ref，但调用方应先用 ``has_elements`` 判断：有模型就不必取 entity 贴图
（那些文件在新版本里也已经 404）。取不到时渲染端会**丢掉该面**而不是画棋盘。
"""
from __future__ import annotations

#: 染料色（deepslate ``pr`` 表：床 / 旗帜 / 潜影盒）
DYE_COLORS = (
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
    "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black",
)
#: 木头种类（告示牌 / 悬挂告示牌）
WOODS = (
    "oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove",
    "cherry", "pale_oak", "bamboo", "crimson", "warped",
)

#: 箱子家族（含铜箱）→ entity/chest/<variant>
CHESTS = {
    "chest": "normal",
    "ender_chest": "ender",
    "trapped_chest": "trapped",
    "copper_chest": "copper",
    "exposed_copper_chest": "copper_exposed",
    "weathered_copper_chest": "copper_weathered",
    "oxidized_copper_chest": "copper_oxidized",
    "waxed_copper_chest": "copper",
    "waxed_exposed_copper_chest": "copper_exposed",
    "waxed_weathered_copper_chest": "copper_weathered",
    "waxed_oxidized_copper_chest": "copper_oxidized",
}

#: 头颅 / 头（deepslate headRenderer 的贴图与缩放）
HEADS = {
    "skeleton_skull": "skeleton/skeleton",
    "wither_skeleton_skull": "skeleton/wither_skeleton",
    "zombie_head": "zombie/zombie",
    "creeper_head": "creeper/creeper",
    "dragon_head": "enderdragon/dragon",
    "piglin_head": "piglin/piglin",
    "player_head": "player/wide/steve",
}

#: 铜傀儡像（deepslate copperGolemStatueRenderer）
COPPER_GOLEMS = {
    "copper_golem_statue": "copper_golem",
    "exposed_copper_golem_statue": "copper_golem_exposed",
    "weathered_copper_golem_statue": "copper_golem_weathered",
    "oxidized_copper_golem_statue": "copper_golem_oxidized",
    "waxed_copper_golem_statue": "copper_golem",
    "waxed_exposed_copper_golem_statue": "copper_golem_exposed",
    "waxed_weathered_copper_golem_statue": "copper_golem_weathered",
    "waxed_oxidized_copper_golem_statue": "copper_golem_oxidized",
}

#: 液体贴图（静止面 / 流动面）—— deepslate 内置液体模型与编辑器自建流体网格都要用
FLUIDS = {
    "water": ("block/water_still", "block/water_flow"),
    "lava": ("block/lava_still", "block/lava_flow"),
}


def _bed_color(name: str) -> str | None:
    if not name.endswith("_bed"):
        return None
    color = name[:-len("_bed")]
    return color if color in DYE_COLORS else None


def _shulker_color(name: str) -> str | None:
    # 无色潜影盒（``shulker_box``）也要认：贴图是 ``entity/shulker/shulker``
    # （旧写法只认 ``<色>_shulker_box``，于是无色的在编辑器里根本不被当特判方块 → 不画）
    if name == "shulker_box":
        return ""
    if not name.endswith("_shulker_box"):
        return None
    color = name[:-len("_shulker_box")]
    return color if color in DYE_COLORS else None


def _banner_dye(name: str) -> str | None:
    base = name[:-len("_wall_banner")] if name.endswith("_wall_banner") \
        else (name[:-len("_banner")] if name.endswith("_banner") else None)
    return base if base in DYE_COLORS else None


def special_kind(name: str) -> str | None:
    """方块名（无命名空间）→ deepslate 特判类型；不特判返回 ``None``。"""
    name = name.removeprefix("minecraft:")
    # 墙上的头颅/骷髅：``creeper_wall_head`` ≈ ``creeper_head``（几何由同一个渲染器画）
    wall_head = None
    for suffix in ("_wall_head", "_wall_skull"):
        if name.endswith(suffix):
            wall_head = name[:-len("_wall_head")] + "_head" \
                if suffix == "_wall_head" else name[:-len("_wall_skull")] + "_skull"
            break
    if wall_head in HEADS:
        return "head"
    if name in CHESTS:
        return "chest"
    if name == "decorated_pot":
        return "pot"
    if name in HEADS:
        return "head"
    if _bed_color(name):
        return "bed"
    if name in ("bell", "conduit"):
        return name
    if _shulker_color(name) is not None:      # 空串 = 无色潜影盒（也要算特判）
        return "shulker"
    if _banner_dye(name):
        return "banner"
    if name in COPPER_GOLEMS:
        return "copper_golem"
    tail = name[:-len("_wall_hanging_sign")] if name.endswith("_wall_hanging_sign") \
        else (name[:-len("_hanging_sign")] if name.endswith("_hanging_sign") else None)
    if tail in WOODS:
        return "hanging_sign"
    tail = name[:-len("_wall_sign")] if name.endswith("_wall_sign") \
        else (name[:-len("_sign")] if name.endswith("_sign") else None)
    if tail in WOODS:
        return "sign"
    return None


def special_textures(name: str) -> list[str]:
    """该特判方块需要的 ``entity/...`` 贴图 ref（按 deepslate 表 1:1）。"""
    name = name.removeprefix("minecraft:")
    kind = special_kind(name)
    if kind is None:
        return []
    if kind == "head" and name not in HEADS:
        # 墙上的头颅：贴图跟站立版一样
        for suffix in ("_wall_head", "_wall_skull"):
            if name.endswith(suffix):
                base = name[:-len("_wall_head")] + "_head" if suffix == "_wall_head" \
                    else name[:-len("_wall_skull")] + "_skull"
                if base in HEADS:
                    return [f"entity/{HEADS[base]}"]
        return []
    if kind == "chest":
        return [f"entity/chest/{CHESTS[name]}"]
    if kind == "pot":
        return ["entity/decorated_pot/decorated_pot_side",
                "entity/decorated_pot/decorated_pot_base"]
    if kind == "head":
        return [f"entity/{HEADS[name]}"]
    if kind == "bed":
        return [f"entity/bed/{_bed_color(name)}"]
    if kind == "bell":
        return ["entity/bell/bell_body"]
    if kind == "conduit":
        return ["entity/conduit/base"]
    if kind == "shulker":
        color = _shulker_color(name)
        return [f"entity/shulker/shulker_{color}" if color else "entity/shulker/shulker"]
    if kind == "banner":
        # 图案层是 entity/banner/<pattern>（由 NBT 决定，不可枚举）；底色层统一 banner_base
        return ["entity/banner/banner_base"]
    if kind == "copper_golem":
        return [f"entity/copper_golem/{COPPER_GOLEMS[name]}"]
    wood = name
    for suffix in ("_wall_hanging_sign", "_hanging_sign", "_wall_sign", "_sign"):
        if wood.endswith(suffix):
            wood = wood[:-len(suffix)]
            break
    if kind == "hanging_sign":
        return [f"entity/signs/hanging/{wood}"]
    return [f"entity/signs/{wood}"]


def fluid_textures(name: str) -> list[str]:
    """液体方块需要的贴图 ref（静止 + 流动）。"""
    name = name.removeprefix("minecraft:")
    pair = FLUIDS.get(name)
    return list(pair) if pair else []
