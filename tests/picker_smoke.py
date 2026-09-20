"""方块面板（/api/blocks/picker 的数据源）分类冒烟 —— 直接读真实目录，只读、不写任何东西。

回归的是用户报的那几件事：

- **材质家族 ≠ 分类**：家族是按贴图算的，所以「磁石」会被归进 stone、「红石火把」进
  stone/lights；而「侦测器 / 合成器 / 铁轨 / 按钮」这类**根本没有家族**，以前只能在「全部」里翻。
- 新增「**功能方块**」（红石元件 + 功能设备：侦测器、合成器、高炉、活塞、铁轨、按钮、
  压力板、容器、工作台、床、潜影盒…）。
- 「**细节家具**」芯片已下线（用户要求删掉）。
- 「**灯具发光**」要含全部 17 种蜡烛（普通 + 16 色）。
- **每个方块**（完整的与不完整的）都至少属于一个芯片；「全部」= 目录里所有方块。

Usage:  python tests/picker_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from mcstudio.blocks import BlockCatalog  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail=None) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name}" + (f"  {detail}" if detail is not None else ""))
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def main() -> int:
    pk = BlockCatalog().picker()
    if not pk.get("available"):
        print("SKIP: 没有目录数据（skills/minecraft-material-lab/data/）")
        return 0
    groups = {g[0]: g for g in pk["groups"]}
    labels = {g[0]: g[1] for g in pk["groups"]}
    idx2gid: dict[int, str] = {}
    for gid, _label, idxs in pk["groups"]:
        for i in idxs:
            idx2gid[i] = gid
    rows = pk["blocks"]
    by = {r[0]: r for r in rows}

    def chips(name: str) -> set[str]:
        r = by.get(name)
        if r is None:
            return set()
        return {idx2gid[i] for i in r[3] if i in idx2gid}

    print(f"== 目录：{len(rows)} 个方块 / {len(pk['groups'])} 个分类芯片 ==")

    # 1) 芯片表：功能方块在位、家具下线、其他兜底
    check("芯片含「功能方块」与「其他」，且**没有**「细节家具」",
          "function" in groups and "other" in groups and "detail" not in groups,
          [labels[g[0]] for g in pk["groups"]])
    check("每个芯片都有成员（不会出现空芯片假「全部」）",
          all(g[0] == "all" or bool(g[2]) for g in pk["groups"]),
          {labels[g[0]]: len(g[2]) for g in pk["groups"] if g[0] != "all"})

    # 2) 覆盖：每个方块至少一个芯片；「全部」就是全部
    no_chip = [r[0] for r in rows if not r[3]]
    check("每个方块都至少属于一个芯片（完整的/不完整的都算）", not no_chip,
          f"{len(rows)} 个方块，无分类 {len(no_chip)} 个" + (f"：{no_chip[:8]}" if no_chip else ""))

    # 3) 用户点名的误分类：磁石 / 红石火把 不该在「石砖」
    check("磁石在「功能方块」、不在「石砖」",
          chips("lodestone") == {"function"}, sorted(chips("lodestone")))
    check("红石火把在「功能方块 + 灯具发光」、不在「石砖」",
          {"function", "light"} <= chips("redstone_torch")
          and "stone" not in chips("redstone_torch"), sorted(chips("redstone_torch")))
    check("萤石不在「石砖」（发光块有自己的芯片）",
          "stone" not in chips("glowstone") and "light" in chips("glowstone"),
          sorted(chips("glowstone")))

    # 4) 以前完全没有家族、只能在「全部」里找的那些，现在都在「功能方块」
    lost = [n for n in ("observer", "crafter", "blast_furnace", "smoker", "piston",
                        "sticky_piston", "note_block", "jukebox", "bell", "rail",
                        "powered_rail", "activator_rail", "detector_rail", "anvil",
                        "hopper", "dispenser", "dropper", "crafter", "target", "lectern",
                        "daylight_detector", "tripwire_hook", "lever", "stone_button",
                        "oak_button", "oak_pressure_plate", "chest", "barrel", "furnace",
                        "brewing_stand", "shulker_box", "beacon", "lodestone", "lightning_rod",
                        "waxed_oxidized_lightning_rod", "crafting_table", "stonecutter",
                        "loom", "grindstone", "smithing_table", "cartography_table",
                        "fletching_table", "enchanting_table", "composter", "beehive",
                        "honey_block", "slime_block", "redstone_block", "redstone_lamp",
                        "redstone_wire", "repeater", "comparator", "sculk_sensor")
           if "function" not in chips(n)]
    check("功能方块覆盖：红石元件 + 功能设备（52 个点名）", not lost, lost)

    # 5) 灯具发光：17 种蜡烛一个不少 + 是真发光的
    candles = sorted(n for n in by if n == "candle" or n.endswith("_candle"))
    miss_candle = [n for n in candles if "light" not in chips(n)]
    check(f"灯具发光含全部蜡烛（{len(candles)} 种：普通 + 16 色）",
          len(candles) == 17 and not miss_candle, f"{len(candles)} 种，缺 {miss_candle}")
    light_n = sum(1 for r in rows if "light" in chips(r[0]))
    check("灯具发光规模合理（≥ 110 个发光方块）", light_n >= 110, f"{light_n} 个")

    # 6) 材质芯片别混进功能/发光方块
    bad_stone = [r[0] for r in rows
                 if "stone" in chips(r[0]) and ({"function", "light"} & chips(r[0]))]
    check("「石砖」里没有功能/发光方块", not bad_stone, bad_stone[:10])

    # 7) 其它芯片的常见误分（上一版把木架当植物、把下界菌柄当植物）
    check("木架/书架算功能方块（不是植物）",
          "plant" not in chips("bamboo_shelf") and "function" in chips("bamboo_shelf"),
          sorted(chips("bamboo_shelf")))
    check("下界菌柄算木材（不是植物）",
          chips("crimson_stem") == {"wood"} or "wood" in chips("crimson_stem"),
          sorted(chips("crimson_stem")))
    check("矿石/原矿块算石质",
          "stone" in chips("coal_ore") and "stone" in chips("raw_iron_block"),
          sorted(chips("coal_ore")))
    check("旗帜在「告示牌旗帜」、床在「功能方块」",
          "sign" in chips("black_banner") and "function" in chips("red_bed"),
          [sorted(chips("black_banner")), sorted(chips("red_bed"))])

    # 8) 完整/不完整都在：不完整方块不能因为「不是整方块」被丢掉
    partial = [r[0] for r in rows if r[2] == 0]
    check("不完整方块也在（半砖/楼梯/栏杆/花盆…）",
          len(partial) > 400 and "oak_stairs" in partial and "glass_pane" in partial,
          f"{len(partial)} 个不完整方块")

    # 9) 能放的水/岩浆/火/刷怪笼/头颅不再被当成 “technical” 丢掉
    wanted = ("water", "lava", "fire", "soul_fire", "spawner", "trial_spawner", "vault",
              "creeper_head", "zombie_wall_head", "skeleton_skull", "player_head")
    miss = [n for n in wanted if n not in by]
    check("水/岩浆/火/刷怪笼/宝库/头颅都在面板里", not miss, miss)
    banned = ("air", "cave_air", "light", "barrier", "structure_void", "end_portal",
              "nether_portal", "command_block", "moving_piston", "jigsaw")
    bad = [n for n in banned if n in by]
    check("空气/屏障/传送门/命令块仍然不列", not bad, bad)

    # 10) 中文搜索别名：面板只认 id，中文用户搜「烟熏炉」原本一无所获
    def hit(query: str) -> list[str]:
        return [r[0] for r in rows
                if query.lower() in r[0].lower()
                or query.lower() in str(r[6] if len(r) > 6 else "").lower()]
    cases = {"烟熏炉": "smoker", "高炉": "blast_furnace", "侦测器": "observer",
             "合成器": "crafter", "楼梯": "oak_stairs", "旗帜": "orange_banner",
             "蜡烛": "red_candle", "头颅": "creeper_head", "红石火把": "redstone_torch",
             "磁石": "lodestone", "栅栏": "oak_fence", "半砖": "oak_slab",
             "潜影盒": "shulker_box", "铁砧": "anvil", "刷怪笼": "spawner"}
    misses = {q: want for q, want in cases.items() if want not in hit(q)}
    check(f"中文别名搜得到（{len(cases)} 个点名）", not misses, misses)

    # 11) 调色块：旗帜/头颅不能拿 catalog 里的占位贴图（橡木木板 / 灵魂沙）
    ob = by.get("orange_banner")
    check("橙色旗帜的调色块 = 染料色 + entity 贴图 + 染色",
          bool(ob) and ob[1].lower() == "#f9801d" and ob[4] == "entity/banner/banner_base"
          and str(ob[5]).lower() == "#f9801d", ob)
    ch = by.get("creeper_head")
    check("头颅的调色块 = entity/<mob> 贴图（不是灵魂沙）",
          bool(ch) and str(ch[4]).startswith("entity/creeper/"), ch)
    check("普通方块不受影响（stone 的贴图还是 block/stone）",
          by["stone"][4] == "block/stone" and by["stone"][5] is None, by["stone"])

    print(f"\n{'ALL PASS' if not FAIL else 'FAILED'}: 方块面板分类  ({PASS} passed, {FAIL} failed)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
