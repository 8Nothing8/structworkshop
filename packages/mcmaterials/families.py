# -*- coding: utf-8 -*-
"""Curated block families used by the material charts.

Families are grouped by what a builder needs, not by registry names:
walls / stone / roof / wood / metal / glass / interior / retrofit.
"""
from __future__ import annotations

FAMILIES: dict[str, list[str]] = {
    # 外墙抹灰/混凝土：赫鲁晓夫楼主体
    "concrete": [
        "white_concrete", "light_gray_concrete", "gray_concrete", "black_concrete",
    ],
    # 石材：做旧、板缝、勒脚、渐变的下半段
    "stone": [
        "stone", "smooth_stone", "cobblestone", "mossy_cobblestone",
        "stone_bricks", "cracked_stone_bricks", "mossy_stone_bricks", "chiseled_stone_bricks",
        "andesite", "polished_andesite", "granite", "polished_granite",
        "diorite", "polished_diorite", "tuff", "polished_tuff",
        "deepslate", "cobbled_deepslate", "polished_deepslate", "deepslate_tiles",
        "calcite", "dripstone_block",
    ],
    # 屋面/烟囱/垃圾道
    "terracotta": [
        "white_terracotta", "light_gray_terracotta", "gray_terracotta", "brown_terracotta",
    ],
    "roof": [
        "bricks", "mud_bricks", "packed_mud", "deepslate_tiles", "deepslate_bricks",
        "stone_bricks", "smooth_stone", "polished_andesite", "gray_concrete",
    ],
    # 门窗/地板/家具
    "wood": [
        "oak_planks", "spruce_planks", "birch_planks", "dark_oak_planks",
        "stripped_oak_log", "stripped_spruce_log", "stripped_birch_log",
        "bookshelf", "crafting_table",
    ],
    # 金属/工业：管道、通风、供电、空调、卫星天线
    "metal": [
        "iron_block", "iron_bars", "iron_trapdoor", "iron_door",
        "copper_block", "cut_copper", "exposed_copper", "weathered_copper", "oxidized_copper",
        "lightning_rod", "iron_chain", "lantern", "cauldron", "hopper", "barrel",
    ],
    # 玻璃/窗
    "glass": [
        "glass", "glass_pane", "tinted_glass", "gray_stained_glass", "light_gray_stained_glass",
        "white_stained_glass", "light_blue_stained_glass", "black_stained_glass",
    ],

    # 细节/不完整方块（薄块、小件）：阳光传感器、中继器、火把、锁链、营火...
    "detail": [
        "daylight_detector", "repeater", "comparator", "lever", "stone_button",
        "iron_chain", "tripwire_hook", "scaffolding", "bell", "hopper",
        "iron_trapdoor", "iron_bars", "glass_pane", "lantern", "end_rod",
        "lightning_rod", "torch", "redstone_torch", "campfire", "soul_campfire",
        "white_candle", "flower_pot", "potted_fern", "sea_pickle",
    ],
    # 现代化改装件：空调/通风/管网/供电/天线
    "retrofit": [
        "iron_block", "cut_copper", "exposed_copper", "weathered_copper", "oxidized_copper",
        "lightning_rod", "iron_chain", "hopper", "cauldron", "barrel",
        "daylight_detector", "comparator", "repeater", "lever", "stone_button",
        "iron_trapdoor", "iron_door", "scaffolding",
    ],
    # 室内面层
    "interior": [
        "oak_planks", "birch_planks", "white_terracotta", "white_concrete",
        "smooth_quartz", "polished_andesite", "red_carpet", "brown_carpet",
        "light_gray_carpet", "white_carpet", "bricks",
    ],
}

# 做旧/渐变建议（基色 -> 邻近可混的方块，按明度排序在 chart 里核对）
WEATHERING = {
    "panel-wall": ["light_gray_concrete", "stone", "smooth_stone", "gray_concrete"],
    "plinth": ["stone", "cobblestone", "andesite", "polished_andesite", "deepslate"],
    "roof": ["smooth_stone", "polished_andesite", "gray_concrete", "stone_bricks"],
    "chimney": ["bricks", "mud_bricks", "packed_mud", "deepslate_tiles"],
    "retrofit-metal": ["iron_block", "cut_copper", "weathered_copper", "iron_bars"],
}


# ---------------------------------------------------------------- auto families
import re as _re  # noqa: E402

AUTO_PATTERNS: list[tuple[str, str]] = [
    ("concrete", r"^[a-z_]+_concrete$"),
    ("concrete_powder", r"_concrete_powder$"),
    ("terracotta", r"^(terracotta|[a-z_]+_terracotta)$"),
    ("glazed_terracotta", r"_glazed_terracotta$"),
    ("wool", r"_wool$"),
    ("carpet", r"_carpet$"),
    ("shulker_box", r"shulker_box$"),
    ("glass", r"glass"),
    ("copper", r"copper"),
    ("copper_chain", r"copper_chain$"),
    ("cut_copper", r"cut_copper"),
    ("coral", r"coral"),
    ("sand", r"^(sand|red_sand|suspicious_sand)$"),
    ("sandstone", r"sandstone"),
    ("bricks", r"(bricks|brick_)"),
    ("planks", r"_planks$"),
    ("logs", r"(_log$|_wood$|stripped_[a-z_]+$|hyphae$|stem$|_stem$)"),
    ("signs", r"(?<!hanging)_sign$"),
    ("hanging_signs", r"_hanging_sign$"),
    ("slabs", r"_slab$"),
    ("stairs", r"_stairs$"),
    ("buttons", r"_button$"),
    ("levers", r"^lever$"),
    ("chains", r"chain"),
    ("doors", r"_door$"),
    ("trapdoors", r"_trapdoor$"),
    ("fences", r"(_fence$|_fence_gate$|_wall$)"),
    ("lights", r"(torch|lantern|glowstone|sea_lantern|shroomlight|candle|bulb|froglight|end_rod)"),
    ("leaves", r"_leaves$"),
    ("flowers", r"(flower|tulip|rose|daisy|bluet|orchid|allium|poppy|dandelion)"),
    ("quartz", r"quartz"),
    ("stone", r"(stone|deepslate|andesite|diorite|granite|tuff|calcite|basalt|blackstone|obsidian)"),
]


def auto_families(blocks: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for fam, pat in AUTO_PATTERNS:
        rx = _re.compile(pat)
        out[fam] = sorted(b for b in blocks if rx.search(b))
    return out


FACADE_FAMILIES = ("concrete", "terracotta", "glazed_terracotta", "stone", "bricks",
                   "sandstone", "sand", "quartz", "glass", "copper", "cut_copper",
                   "planks", "logs", "wood", "metal", "roof", "wool", "interior")

NON_BUILDING = _re.compile(
    r"(ore|infested|_bud|spawner|test_|bedrock|reinforced_deepslate|suspicious|leaves|"
    r"sapling|coral|flower|fungus|roots|vine|grass|bush|plant|wheat|carrot|potato|"
    r"beetroot|seeds|stem|kelp|seagrass|moss|lichen|sculk|egg|honey|bee|amethyst|"
    r"dripstone|_pot$|candle|torch|lantern|_rail|cactus|mushroom|pumpkin|melon|"
    r"sugar_cane|nether_wart|chorus|twisting|weeping|shroomlight|_head$|banner|"
    r"sign|_door$|_trapdoor$|_button$|_plate$|pressure|lever|repeater|comparator|"
    r"observer|piston|dispenser|dropper|hopper|_bulb$|_grate$|conduit|beacon|"
    r"respawn_anchor|lodestone|_slab$|_stairs$|_wall$|_fence$|_pane$|_bars$|chain)")


def facade_blocks(blocks: list[str] | None = None) -> list[str]:
    """Full-cube, non-technical blocks (glass included) for facades/roofs.

    Uses the catalog's ``facade_safe`` flag when available (it falls back to the
    model bbox for blocks missing from the incomplete-block table).
    """
    from mcmaterials.catalog import CATALOG, all_blocks_json, incomplete_meta
    if CATALOG.exists():
        try:
            import json
            cat = json.loads(CATALOG.read_text(encoding="utf-8"))
            fams = auto_families(list(cat))
            for fam in FAMILIES:
                fams[fam] = sorted(dict.fromkeys(fams.get(fam, []) + FAMILIES[fam]))
            building = set()
            for fam in FACADE_FAMILIES:
                building.update(fams.get(fam, []))
            out = [b for b, e in cat.items()
                   if e.get("facade_safe") and b in building and not NON_BUILDING.search(b)]
            if out:
                return sorted(out)
        except Exception:  # noqa: BLE001
            pass
    names = blocks or sorted(all_blocks_json())
    inc = incomplete_meta()
    out = []
    for b in names:
        meta = inc.get(b) or {}
        if meta.get("is_full_cube") and not _re.match(
                r"^(air|water|lava|fire|light|barrier|structure_void|jigsaw|moving_piston|piston_head)$", b):
            out.append(b)
    return sorted(out)


def all_families(blocks: list[str]) -> dict[str, list[str]]:
    fams = {k: list(v) for k, v in FAMILIES.items()}
    for k, v in auto_families(blocks).items():
        if v:
            fams[k] = sorted(dict.fromkeys(fams.get(k, []) + v))   # union, never shrink
    fac = facade_blocks(blocks)
    if fac:
        fams["facade"] = fac
    return {k: sorted(dict.fromkeys(v)) for k, v in fams.items()}
