"""从下载的方块映射表生成机器可读的方块索引（属性 / 默认值 / 不完整方块）。

Outputs (default: the skill's ``data/`` directory):

* ``all_blocks.json``        -- every block: legal properties + default state
* ``incomplete_blocks.json`` -- non-full blocks only, with resolved geometry
  (bbox, quad count, full-cube flag, textures) so an AI/generator can see what
  a block actually occupies.

    python -m mcrender.block_index                     # current version (26.2)
    python -m mcrender.block_index --data-version 4903
    python -m mcrender.block_index --all --out .cache/mcassets
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

from mccore.paths import repo_root  # noqa: E402
from mcrender.assets import auto_assets  # noqa: E402
from mcrender.model import ModelResolver  # noqa: E402

SKILL_DATA = (repo_root() / "skills" /
              "minecraft-block-models" / "data")

# families that are (or contain) non-full blocks
INCOMPLETE_SUFFIXES = (
    "_slab", "_stairs", "_wall", "_fence", "_fence_gate", "_pane", "_door",
    "_trapdoor", "_button", "_pressure_plate", "_carpet", "_candle", "_bed",
    "_torch", "_sign", "_hanging_sign", "_banner", "_flower", "_sapling",
    "_roots", "_fern", "_bush", "_grass", "_vine", "_coral", "_coral_fan",
    "_coral_wall_fan", "_bud", "_cluster", "_budding", "_egg", "_pickle",
    "_pot", "_rod", "_ladder", "_rail", "_wire", "_hook", "_repeater",
    "_comparator", "_dust", "_lamp", "_sensor", "_speleothem",
)
INCOMPLETE_NAMES = {
    "air", "cave_air", "void_air", "water", "lava", "glass", "tinted_glass",
    "iron_bars", "chain", "lantern", "soul_lantern", "end_rod", "lever",
    "grindstone", "ladder", "rail", "powered_rail", "detector_rail",
    "activator_rail", "torch", "soul_torch", "redstone_torch", "redstone_wire",
    "tripwire", "tripwire_hook", "cobweb", "scaffolding", "lily_pad",
    "sea_pickle", "snow", "moss_carpet", "hanging_roots", "glow_lichen",
    "vine", "cave_vines", "cave_vines_plant", "pointed_dripstone",
    "amethyst_cluster", "large_amethyst_bud",
    "medium_amethyst_bud", "small_amethyst_bud", "flower_pot",
    "brewing_stand", "cauldron", "water_cauldron", "lava_cauldron",
    "powder_snow_cauldron", "composter", "stonecutter", "bell",
    "decorated_pot", "bamboo", "sugar_cane", "kelp", "kelp_plant", "seagrass",
    "big_dripleaf", "small_dripleaf", "spore_blossom", "azalea",
    "flowering_azalea", "pink_petals", "torchflower", "pitcher_plant",
    "sunflower", "lilac", "rose_bush", "peony", "tall_grass", "large_fern",
    "grass", "fern", "dead_bush", "dandelion", "poppy", "blue_orchid",
    "allium", "azure_bluet", "red_tulip", "orange_tulip", "white_tulip",
    "pink_tulip", "oxeye_daisy", "cornflower", "lily_of_the_valley",
    "wither_rose", "sweet_berry_bush", "cocoa", "chorus_flower",
    "chorus_plant", "structure_void", "light", "barrier", "moving_piston",
    "end_portal", "end_gateway", "nether_portal", "frosted_ice",
    "turtle_egg", "sniffer_egg", "frogspawn", "moss_carpet", "glow_berries",
    "cave_vines_plant", "pale_moss_carpet", "leaf_litter", "wildflowers",
    "cactus_flower", "resin_clump", "dried_ghast", "firefly_bush",
    "short_grass", "tall_dry_grass", "bush",
}
for _c in ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
           "gray", "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black"):
    INCOMPLETE_NAMES.add(_c + "_stained_glass")
    INCOMPLETE_NAMES.add(_c + "_stained_glass_pane")
    INCOMPLETE_NAMES.add(_c + "_candle")
    INCOMPLETE_NAMES.add(_c + "_banner")
    INCOMPLETE_NAMES.add(_c + "_wall_banner")
    INCOMPLETE_NAMES.add(_c + "_bed")
    INCOMPLETE_NAMES.add(_c + "_shulker_box")


def is_incomplete(name: str) -> bool:
    if name in INCOMPLETE_NAMES:
        return True
    return any(name.endswith(s) for s in INCOMPLETE_SUFFIXES if s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version")
    ap.add_argument("--data-version", type=int, default=4903)
    ap.add_argument("--cache")
    ap.add_argument("--out", default=None, help="output dir (default: skill data/)")
    ap.add_argument("--all", action="store_true", help="also resolve every block (slow)")
    a = ap.parse_args(argv)

    assets = auto_assets(a.data_version, a.version, a.cache)
    summary = assets.blocks_summary()
    if not summary:
        raise SystemExit("no mapping table available (offline?)")
    resolver = ModelResolver(assets)
    out_dir = Path(a.out) if a.out else SKILL_DATA
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. all blocks: properties + defaults only (compact)
    all_blocks = {}
    for name, entry in sorted(summary.items()):
        props = entry[0] if entry else {}
        default = entry[1] if len(entry) > 1 else {}
        all_blocks[name] = {"properties": props, "default": default}
    (out_dir / "all_blocks.json").write_text(
        json.dumps(all_blocks, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
    print("wrote %s (%d blocks)" % (out_dir / "all_blocks.json", len(all_blocks)))

    # 2. incomplete blocks with resolved geometry
    names = [n for n in summary if is_incomplete(n)]
    assets.prefetch(names)
    out = {}
    for name in sorted(names):
        entry = summary[name]
        default = entry[1] if len(entry) > 1 else {}
        bb = resolver.resolve_block(name, default)
        out[name] = {
            "properties": entry[0] if entry else {},
            "default": default,
            "quads": len(bb.quads),
            "is_full_cube": bb.is_full_cube,
            "ambientocclusion": bb.ao,
            "render": bb.render if not bb.builtin else bb.builtin,
            "textures": sorted(bb.textures),
            "bbox": ([round(float(x), 4) for x in bb.bbox[0]],
                     [round(float(x), 4) for x in bb.bbox[1]]) if bb.bbox else None,
            "notes": bb.notes,
        }
    path = out_dir / "incomplete_blocks.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    missing = [n for n, v in out.items() if v["render"] == "fallback"]
    print("wrote %s (%d blocks, %d fallback)" % (path, len(out), len(missing)))
    if missing:
        print("  fallback (no model): %s" % ", ".join(missing[:30]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
