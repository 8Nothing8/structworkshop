"""Block catalog for the web UI: search, 2D colors and 3D render flags.

Heavy mcrender imports are lazy so that headless API calls stay cheap.
Results are cached per Minecraft version under ``.cache/mcstudio/``.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from mccore.paths import repo_root

TILE = 16


from mcstudio.entity_assets import (  # noqa: PLC0415
    fluid_textures,
    special_kind,
    special_textures,
)


def _tint_hex(name: str) -> str | None:
    """群系染色的颜色（草地/树叶/水…），与 mcrender 出图用同一张表；无色返回 None。"""
    try:
        from mcrender.renderer import tint_color  # noqa: PLC0415
        rgb = tint_color(name, 0, None)
    except Exception:  # noqa: BLE001
        return None
    rgb = tuple(int(max(0, min(255, v))) for v in rgb[:3])
    if rgb == (255, 255, 255):
        return None
    return "#%02x%02x%02x" % rgb


def _render_semantics(name: str, props: dict, bb, atlas) -> dict:
    """编辑器 mesher 与 mcrender 共用的「这块怎么画」判定（单一来源）。

    ``layer``：0 opaque / 1 cutout（alpha 测试）/ 2 translucent（混合，最后画）；
    ``tint``：群系染色 [r,g,b]（None = 不染）；``liquid``；``special``（方块实体特判）；
    ``has_elements``：**方块自身模型**有没有几何（有就不跑特判，避免床/告示牌重复叠加）；
    ``entity_geometry``：几何来自 deepslate 特判表（箱子/旗帜/头颅…，渲染端自己跑特判）；
    ``ao_occluder``：算 AO 时算不算遮挡（整方块且非透明 → 树叶算、玻璃/水不算）。

    为什么 ``has_elements`` 要排掉特判表来的几何：mcrender 的解析器会把表里的
    几何直接填进 ``bb.quads``，于是旗帜/箱子也“有几何”了；但编辑器的 deepslate
    路径是**自己**跑特判渲染（且要先把 entity 贴图拉全）—— 旧写法下客户端看到
    ``has_elements=true`` 就不跑特判、也不下载 entity 贴图，结果是
    放一个橙色旗帜**什么也看不到**。
    """
    from mcrender.renderer import layer_for, tint_color  # noqa: PLC0415

    notes = getattr(bb, "notes", None) or ()
    entity_geometry = any("entity-model" in str(n) for n in notes)
    #: mcrender 的兜底：没模型时用同名贴图**捏一个立方体**（notes 标 ``texture fallback`` /
    #: ``model-by-name fallback``）。这种几何客户端拿不到（它是编辑器自己算的），
    #: 所以也要算「自身没模型」→ 客户端走特判/兜底立方体，不会变成空气。
    fabricated = any(("fallback" in str(n)) for n in notes)
    has_elements = bool(bb.quads) and not entity_geometry and not fabricated
    #: 自身没几何、特判几何也可能拿不到时（例如**无色潜影盒**：deepslate 的 shulker
    #: 渲染器只认带颜色的），给客户端一个「拿方块自己的贴图画立方体」的兜底贴图。
    fallback_texture = None
    if not has_elements and bb.textures:
        own = sorted(t for t in bb.textures if t)
        fallback_texture = own[0] if own else None
    layer = 0
    if has_elements and atlas is not None:
        layer = max(layer_for(name, q.tex, atlas) for q in bb.quads)
    tint = None
    if has_elements and any(q.tint >= 0 for q in bb.quads):
        rgb = tuple(int(max(0, min(255, v))) for v in tint_color(name, 0, props)[:3])
        if rgb != (255, 255, 255):
            tint = list(rgb)
    liquid = name if name in ("water", "lava") else None
    if liquid == "water":
        layer = max(layer, 2)
        if tint is None:                      # 水的模型没有 tintindex，但游戏里要染群系水色
            tint = [int(max(0, min(255, v))) for v in tint_color("water", 0, props)[:3]]
    kind = special_kind(name)
    render = "invisible" if bb.render == "invisible" else (
        "normal" if (has_elements or kind) else "fallback")
    core = bb.cube_textures or (bb.textures if bb.is_full_cube else set())
    core_zero = any(atlas.info[t].has_zero for t in core if atlas is not None and t in atlas.info) if atlas else False
    core_partial = any(atlas.info[t].has_partial for t in core if atlas is not None and t in atlas.info) if atlas else False
    # 水没（waterlogged=true / 海带海草这类天生含水）：渲染端要额外画水面
    waterlogged = bool(str(props.get("waterlogged", "")).lower() == "true"
                       or name in ("bubble_column", "kelp", "kelp_plant",
                                   "seagrass", "tall_seagrass"))
    return {
        "layer": int(layer),
        "tint": tint,
        "liquid": liquid,
        "special": kind,
        "has_elements": has_elements,
        "entity_geometry": entity_geometry,
        "fallback_texture": fallback_texture,
        "waterlogged": waterlogged,
        # 算 AO 时算不算遮挡：有方块体、不是混合图层、且贴图没有半透明像素
        # （树叶/铁栏杆算（cutout），玻璃/水不算）
        "ao_occluder": bool(core and layer != 2 and not core_partial),
        # 能不能开**背面剔除**：只有「正好 6 个面组成的整方块 + 贴图全不透明 + 不混合」
        # 才敢剔 —— 这些方块的面一定是朝外的，剔背面在数学上不会丢可见面。
        # 花草/树叶/楼梯/栏杆这类（薄片、异形、带透明像素）一律 False，走不剔除的桶。
        "cull_safe": bool(bb.is_full_cube and layer != 2 and render == "normal"
                          and not core_zero and not core_partial),
        "render": render,
    }


def _extra_textures(name: str, props: dict, info: dict) -> list[str]:
    """这个方块除了模型自带贴图外，**额外**要下载的 ref（特判方块 + 液体流面）。"""
    out: list[str] = []
    if info.get("special") and not info.get("has_elements"):
        out.extend(special_textures(name))
    if info.get("fallback_texture"):
        out.append(str(info["fallback_texture"]))     # 兜底立方体要用的那张
    waterlogged = str(props.get("waterlogged", "")).lower() == "true"
    if info.get("liquid") == "water" or waterlogged:
        out.extend(fluid_textures("water"))
    if info.get("liquid") == "lava":
        out.extend(fluid_textures("lava"))
    return out


def _cache_dir() -> Path:
    d = repo_root() / ".cache" / "mcstudio"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 方块分类
#: 语义分类只看**方块名 + 目录数据**，不看贴图家族：材质家族是给选色用的，
#: 拿来当分类会把「磁石」归进石砖（贴图像石头）、把「红石火把」也归进去，
#: 而 observer / crafter / piston / 铁轨 / 按钮 这些根本没有任何家族。
#: 功能方块 = 红石元件 + 功能设备（有界面/有行为的方块）。
FUNCTION_EXACT = frozenset("""
    observer crafter dispenser dropper hopper piston sticky_piston piston_head moving_piston
    note_block jukebox bell lectern target daylight_detector tripwire tripwire_hook lever
    repeater comparator
    lightning_rod lodestone beacon conduit respawn_anchor end_portal_frame chest trapped_chest
    ender_chest barrel furnace smoker brewing_stand composter beehive bee_nest decorated_pot
    chiseled_bookshelf bookshelf crafting_table stonecutter loom grindstone enchanting_table
    honey_block slime_block cake dragon_egg vault trial_spawner anvil rail shulker_box
    sculk sculk_vein sculk_catalyst sculk_shrieker
""".split())
FUNCTION_SUFFIX = (
    "_button", "_pressure_plate", "_rail", "_table", "_anvil", "_cauldron", "_furnace",
    "_campfire", "_bulb", "_sensor", "_spawner", "_bed", "_shulker_box", "_shelf",
    "_lightning_rod",
)
#: 植物：家族只有 leaves/flowers/coral，树苗·藤·农作物·菌类这些都没家族
PLANT_EXACT = frozenset("""
    bamboo cactus vine moss_block moss_carpet pale_moss_block pale_moss_carpet azalea
    flowering_azalea spore_blossom glow_lichen hanging_roots pale_hanging_moss wildflowers
    leaf_litter chorus_plant chorus_flower sugar_cane sea_pickle torchflower pitcher_plant
    pitcher_crop open_eyeblossom closed_eyeblossom dead_bush wheat carrots potatoes beetroots
    big_dripleaf small_dripleaf fern kelp kelp_plant seagrass lily_pad bush firefly_bush
    nether_wart nether_wart_block cocoa melon_stem pumpkin_stem attached_melon_stem
    attached_pumpkin_stem mushroom_stem big_dripleaf_stem
""".split())
PLANT_SUFFIX = (
    "_sapling", "_leaves", "_flower", "_bush", "_vines", "_vines_plant", "_roots",
    "_mushroom", "_mushroom_block", "_fungus", "_lily_pad", "_dripleaf", "_propagule",
    "_petals", "_sprouts", "_grass", "_fern", "_kelp", "_seagrass", "_berry_bush",
    "_crop",
)
#: 盆栽（potted_*）算植物装饰
PLANT_PREFIX = ("potted_",)
SIGN_SUFFIX = ("_banner", "_wall_banner", "_sign")
DOOR_SUFFIX = ("_door", "_trapdoor", "_fence_gate")
#: 木材：材质家族没收进 logs 的（下界菌柄/菌核、竹块）
WOOD_EXACT = frozenset("""
    crimson_stem warped_stem stripped_crimson_stem stripped_warped_stem
    crimson_hyphae warped_hyphae stripped_crimson_hyphae stripped_warped_hyphae
    bamboo_block stripped_bamboo_block bamboo_mosaic bamboo_planks
""".split())
#: 石质：矿石 / 原矿块 / 紫水晶（都是石头货，但没进 stone 家族）
STONE_SUFFIX = ("_ore",)
STONE_EXACT = frozenset("""
    ancient_debris raw_iron_block raw_copper_block raw_gold_block
    amethyst_block budding_amethyst amethyst_cluster
    large_amethyst_bud medium_amethyst_bud small_amethyst_bud
""".split())
#: 归到这些材质芯片的家族/伪家族：功能/发光方块不该混进去
MATERIAL_CHIP_FAMILIES = ("stone", "bricks", "sandstone", "quartz", "sand", "::stone")


def _semantic_kinds(name: str, entry: dict) -> set[str]:
    """方块 → 语义分类名集合（function / light / plant / sign / door / wood / stone）。

    ``light`` 用目录里的 ``emissive``（覆盖全部 17 种蜡烛）+ 家族 ``lights``；
    ``function`` 见 :data:`FUNCTION_EXACT` / :data:`FUNCTION_SUFFIX`（红石元件与功能设备）。
    """
    kinds: set[str] = set()
    is_func = (name in FUNCTION_EXACT or name.endswith(FUNCTION_SUFFIX)
               or (name.startswith("redstone_") and not name.endswith("_ore"))
               or name.startswith("sculk"))
    if is_func:
        kinds.add("function")
    if entry.get("emissive"):
        kinds.add("light")
    if (name in PLANT_EXACT or name.endswith(PLANT_SUFFIX)
            or name.startswith(PLANT_PREFIX)):
        kinds.add("plant")
    if name in WOOD_EXACT:
        kinds.add("wood")
    if name in STONE_EXACT or name.endswith(STONE_SUFFIX):
        kinds.add("stone")
    if name.endswith(SIGN_SUFFIX):
        kinds.add("sign")
    if name.endswith(DOOR_SUFFIX):
        kinds.add("door")
    return kinds


#: 方块颜色/渲染语义缓存的结构版本（加字段就要 +1，旧缓存自动失效）
CACHE_SCHEMA = 7   # +1：语义多了 cull_safe（背面剔除）→ 旧缓存整份重算



#: 面板里**不列**的技术方块（编辑器里放了也看不见/没意义）：空气系、屏障、光、传送门、命令块、拼图块
#: 其余原本被标 ``technical`` 的方块（水/岩浆/火/刷怪笼/宝库/各类头颅）都**要列**——
#: 它们是真能放的方块，旧写法一律跳过 → 面板“缺方块”。
PICKER_EXCLUDE = frozenset("""
    air cave_air void_air light barrier structure_void moving_piston piston_head
    bubble_column end_portal end_gateway nether_portal jigsaw
    command_block chain_command_block repeating_command_block
""".split())

#: 中文别名（只用于**搜索**：面板按方块 id 搜，中文用户搜「烟熏炉」搜不到 smoker）
CN_COLORS = {
    "white": "白", "orange": "橙", "magenta": "品红", "light_blue": "淡蓝",
    "yellow": "黄", "lime": "黄绿", "pink": "粉", "gray": "灰",
    "light_gray": "淡灰", "cyan": "青", "purple": "紫", "blue": "蓝",
    "brown": "棕", "green": "绿", "red": "红", "black": "黑",
}
CN_WOODS = {
    "oak": "橡木", "spruce": "云杉", "birch": "白桦", "jungle": "丛林",
    "acacia": "金合欢", "dark_oak": "深色橡木", "mangrove": "红树", "cherry": "樱花",
    "pale_oak": "苍白橡木", "bamboo": "竹", "crimson": "绯红", "warped": "诡异",
    "mangrove_": "红树",
}
#: 词尾 / 常见方块名 → 中文（空格分隔的多个同义词）
CN_FORMS = {
    "slab": "台阶 半砖", "stairs": "楼梯", "wall": "墙 围墙", "fence": "栅栏 围栏",
    "fence_gate": "栅栏门", "door": "门", "trapdoor": "活板门", "button": "按钮",
    "pressure_plate": "压力板", "sign": "告示牌", "hanging_sign": "悬挂告示牌",
    "banner": "旗帜", "bed": "床", "candle": "蜡烛", "carpet": "地毯", "wool": "羊毛",
    "leaves": "树叶 叶子", "sapling": "树苗", "planks": "木板 木板材", "log": "原木",
    "wood": "木头", "hyphae": "菌核", "stem": "菌柄", "glass": "玻璃",
    "glass_pane": "玻璃板 玻璃片", "concrete": "混凝土", "concrete_powder": "混凝土粉末",
    "terracotta": "陶瓦 陶土", "glazed_terracotta": "带釉陶瓦 釉陶",
    "bricks": "砖 砖块", "brick": "砖", "tiles": "瓦 砖瓦", "shulker_box": "潜影盒",
    "chains": "锁链", "chain": "锁链", "lantern": "灯笼", "torch": "火把",
    "campfire": "营火", "rail": "铁轨", "ladder": "梯子", "scaffolding": "脚手架",
}
#: 单个方块名 → 中文（用户真会搜的那些：设备 / 红石 / 常见地形）
CN_EXACT = {
    "smoker": "烟熏炉", "blast_furnace": "高炉", "furnace": "熔炉", "crafting_table": "工作台",
    "observer": "侦测器 观察者", "crafter": "合成器 自动合成器", "piston": "活塞",
    "sticky_piston": "黏性活塞 粘性活塞", "dispenser": "发射器", "dropper": "投掷器",
    "hopper": "漏斗", "lever": "拉杆", "tripwire_hook": "绊线钩", "tripwire": "绊线",
    "redstone_torch": "红石火把", "redstone_wall_torch": "红石火把", "redstone_lamp": "红石灯",
    "redstone_block": "红石块", "redstone_wire": "红石线 红石粉 红石中继线",
    "repeater": "中继器 红石中继器", "comparator": "比较器 红石比较器",
    "daylight_detector": "日光传感器 阳光探测器", "target": "标靶 靶子",
    "note_block": "音符盒", "jukebox": "唱片机", "bell": "钟 铃铛", "lectern": "讲台",
    "lodestone": "磁石", "beacon": "信标", "conduit": "潮涌核心 导管",
    "respawn_anchor": "重生锚", "anvil": "铁砧", "chipped_anvil": "开裂的铁砧",
    "damaged_anvil": "破损的铁砧", "enchanting_table": "附魔台", "bookshelf": "书架",
    "chiseled_bookshelf": "雕纹书架", "loom": "织布机", "stonecutter": "切石机",
    "grindstone": "砂轮", "smithing_table": "钋造台", "cartography_table": "制图台",
    "fletching_table": "制箭台", "composter": "堆肥桶", "barrel": "木桶", "chest": "箱子",
    "trapped_chest": "陷阱箱", "ender_chest": "末影箱", "brewing_stand": "酿造台",
    "cauldron": "铁锅 锅", "water_cauldron": "装有水的铁锅", "lava_cauldron": "装有岩浆的铁锅",
    "powder_snow_cauldron": "装有细雪的铁锅", "beehive": "蜂箱", "bee_nest": "蜂巢",
    "decorated_pot": "装饰罐 饰纹陶罐", "vault": "宝库 避难所", "spawner": "刷怪笼",
    "trial_spawner": "试炼刷怪笼", "lightning_rod": "避雷针", "honey_block": "蜂蜜块",
    "slime_block": "黏液块", "water": "水 水源", "lava": "岩浆 熔岩", "fire": "火",
    "soul_fire": "灵魂火", "dirt": "泥土", "grass_block": "草方块 草地", "sand": "沙子",
    "gravel": "砂砾 碎石", "clay": "黏土 粘土", "snow": "雪", "snow_block": "雪块",
    "ice": "冰", "packed_ice": "浮冰 密冰", "blue_ice": "蓝冰", "obsidian": "黑曜石",
    "crying_obsidian": "泣黑曜石", "bedrock": "基岩", "sponge": "海绵", "tnt": "TNT",
    "cobweb": "蛛网", "ladder": "梯子", "stone": "石头 石块", "cobblestone": "圆石",
    "deepslate": "深板岩", "oak_planks": "橡木木板", "book": "书", "cake": "蛋糕",
    "armor_stand": "盔甲架", "flower_pot": "花盆", "item_frame": "物品展示框",
}


#: 染料色（中文别名用）+ 染料十六进制（面板色块用）
CN_DYE_HEX = {
    "white": "#f9fffe", "orange": "#f9801d", "magenta": "#c74ebd", "light_blue": "#3ab3da",
    "yellow": "#fed83d", "lime": "#80c71f", "pink": "#f38baa", "gray": "#474f52",
    "light_gray": "#9d9d97", "cyan": "#169c9c", "purple": "#8932b8", "blue": "#3c44aa",
    "brown": "#835432", "green": "#5e7c16", "red": "#b02e26", "black": "#1d1d21",
}
#: 头颅 / 骷髅：面板贴图用 entity/<mob> 那张（catalog 里的 textures 是 soul_sand 占位符）
CN_MOBS = {
    "skeleton": "骷髅", "wither_skeleton": "凋灵骷髅", "zombie": "僵尸", "creeper": "苦力怕 爬行者",
    "dragon": "末影龙 龙", "piglin": "猪灵", "player": "玩家",
}


def _picker_swatch(name: str, refs: list, color: str) -> tuple[str, str | None, str]:
    """调色块（颜色 / 贴图 ref / 染色）：旗帜与头颅的 resource-pack 贴图是占位符，

    直接拿 catalog 里的 ref 会显示成橡木木板（旗帜）或灵魂沙（头颅）——
    这里改成游戏里真的那张（``entity/banner/banner_base`` + 染料染色；头颅用 ``entity/<mob>``）。
    """
    dye = None
    if name.endswith("_wall_banner"):
        dye = name[:-len("_wall_banner")]
    elif name.endswith("_banner"):
        dye = name[:-len("_banner")]
    if dye in CN_DYE_HEX:
        return CN_DYE_HEX[dye], "entity/banner/banner_base", CN_DYE_HEX[dye]
    mob = None
    for suffix in ("_wall_head", "_wall_skull", "_head", "_skull"):
        if name.endswith(suffix):
            mob = name[:-len(suffix)]
            break
    if mob:
        try:
            from mcstudio.entity_assets import special_textures  # noqa: PLC0415
            tex = special_textures(name)
        except Exception:  # noqa: BLE001
            tex = []
        if tex:
            return "#7d7d7d", tex[0], None
    return color, None, None


def _picker_alias(name: str) -> str:
    """方块名 → 中文搜索别名（空格分隔；词尾/颜色/材质拼接 + 少量单块表）。"""
    words: list[str] = []
    hit_color = None
    for c, cn in CN_COLORS.items():
        if name == c or name.startswith(c + "_"):
            hit_color = cn
            words.append(cn)
            break
    hit_wood = None
    for w, wn in CN_WOODS.items():
        if name == w or name.startswith(w + "_"):
            hit_wood = wn
            words.append(wn)
            break
    form = None
    for suffix, cn in CN_FORMS.items():
        if name == suffix or name.endswith("_" + suffix):
            form = cn
            words.extend(cn.split())
            break
    if name in CN_EXACT:
        words.extend(CN_EXACT[name].split())
    # 拼合词（“黄旗帜”“黄色旗帜”“橡木楼梯”），让中文整词搜索也能命中
    for color in ([hit_color] if hit_color else []):
        for f in (form.split() if form else []):
            words.extend([color + f, color + "色" + f])
    for wood in ([hit_wood] if hit_wood else []):
        for f in (form.split() if form else []):
            words.extend([wood + f])
    if name in CN_EXACT:
        words.insert(0, CN_EXACT[name].split()[0])
    # 头颅：加「头颅/头/骷髅头」+ 怪物名，免得只能搜英文
    mob = None
    for suffix in ("_wall_head", "_wall_skull", "_head", "_skull"):
        if name.endswith(suffix):
            mob = name[:-len(suffix)]
            break
    if mob:
        words.extend(["头颅", "头", "脑袋"])
        for key, cn in CN_MOBS.items():
            if mob == key:
                words.extend(cn.split())
                words.extend([cn.split()[0] + "头颅", cn.split()[0] + "头"])
                break
    seen, out = set(), []
    for w in words:
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return " ".join(out)
class BlockCatalog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._assets: dict[str, object] = {}
        self._resolver: dict[str, object] = {}
        self._summary: dict[str, dict] = {}
        self._resolved: dict[str, dict[str, dict]] = {}
        self._extra_textures: dict[str, list[str]] = {}
        self._loaded: set[str] = set()
        self._picker: dict | None = None

    # ------------------------------------------------------------ assets
    def assets(self, version: str | None = None, data_version: int | None = None):
        from mcrender.assets import auto_assets  # noqa: PLC0415
        key = version or f"dv{data_version}"
        with self._lock:
            a = self._assets.get(key)
        if a is None:
            a = auto_assets(data_version=data_version, version=version,
                            cache_dir=None, offline=False, verbose=False)
            with self._lock:
                self._assets[key] = a
                self._assets[a.version] = a
        return a

    def version_of(self, data_version: int, allow_network: bool = False) -> str:
        """Map a data version to a Minecraft version (cached manifest only
        unless *allow_network*; avoids blocking the API on first run)."""
        from mccore.paths import cache_dir  # noqa: PLC0415
        from mcrender.assets import FALLBACK_VERSION  # noqa: PLC0415
        if not (cache_dir() / "_versions.json").is_file() and not allow_network:
            return FALLBACK_VERSION
        try:
            return self.assets(data_version=data_version).version
        except Exception:  # noqa: BLE001
            return FALLBACK_VERSION

    def summary(self, version: str) -> dict:
        with self._lock:
            s = self._summary.get(version)
        if s is None:
            a = self.assets(version=version)
            s = a.blocks_summary() or {}
            with self._lock:
                self._summary[version] = s
        return s

    def search(self, q: str, version: str, limit: int = 40) -> list[dict]:
        """按名字找方块。

        相关性排序：**完全相同 → 以它开头 → 包含它**（同级按字母）；
        否则 ``limit`` 会在字母序里截断，把真正的 ``stone`` 挤出去——
        调用方（编辑器「当前方块」）拿不到精确匹配就会退成“列表里的第一个”，
        于是点 stone 变成 blackstone。
        """
        s = self.summary(version)
        q = (q or "").strip().lower()
        names = sorted(s)
        if not q:
            picked = names
        else:
            exact, prefix, other = [], [], []
            for name in names:
                nl = name.lower()
                if nl == q:
                    exact.append(name)
                elif nl.startswith(q):
                    prefix.append(name)
                elif q in nl:
                    other.append(name)
            picked = exact + prefix + other
        out = []
        for name in picked:
            props = s[name][0] if s[name] else {}
            default = s[name][1] if len(s[name]) > 1 else {}
            out.append({"name": name, "properties": props, "default": default})
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------ resolve
    def _load_cache(self, version: str) -> None:
        if version in self._loaded:
            return
        p = _cache_dir() / f"blocks_{version}.json"
        data = {}
        if p.is_file():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                # 旧缓存没有渲染语义字段 → 整份丢掉重算（__schema 不一致就当空）
                if isinstance(raw, dict) and raw.get("__schema") == CACHE_SCHEMA:
                    data = {k: v for k, v in raw.items() if k != "__schema"}
            except (json.JSONDecodeError, OSError):
                data = {}
        self._resolved[version] = data
        self._loaded.add(version)

    def _save_cache(self, version: str) -> None:
        p = _cache_dir() / f"blocks_{version}.json"
        tmp = p.with_name(p.name + ".tmp")
        payload = {"__schema": CACHE_SCHEMA}
        payload.update(self._resolved.get(version, {}))
        tmp.write_text(json.dumps(payload, ensure_ascii=False),
                       encoding="utf-8")  # lf-ok: .cache 里的方块缓存，不进 git
        tmp.replace(p)

    # ------------------------------------------------------------ picker
    #: （``::`` 开头的是**语义伪家族**，由 :func:`_semantic_kinds` 按方块名与目录数据算，
    #:   因为材质家族只描述**贴图**：磁石会被归进 stone、红石火把进 stone/lights，
    #:   而 observer / crafter / piston 这类根本没有家族——以前只能在「全部」里找）
    PICKER_GROUPS = (
        ("all", "全部", ()),
        ("function", "功能方块", ("::function",)),
        ("stone", "石砖", ("stone", "bricks", "sandstone", "quartz", "sand", "::stone")),
        ("concrete", "混凝土", ("concrete", "concrete_powder")),
        ("terracotta", "陶瓦", ("terracotta", "glazed_terracotta")),
        ("wood", "木材", ("wood", "planks", "logs", "::wood")),
        ("metal", "金属", ("metal", "copper", "cut_copper", "copper_chain")),
        ("glass", "玻璃", ("glass",)),
        ("wool", "羊毛织物", ("wool", "carpet")),
        ("light", "灯具发光", ("::light",)),
        ("plant", "植物", ("leaves", "flowers", "coral", "::plant")),
        ("slab", "半砖", ("slabs",)),
        ("stair", "楼梯", ("stairs",)),
        ("edge", "墙栅栏", ("fences",)),
        ("door", "门窗", ("doors", "trapdoors", "::door")),
        ("sign", "告示牌旗帜", ("signs", "hanging_signs", "::sign")),
        ("other", "其他", ("::other",)),
    )

    def picker(self) -> dict:
        """方块选择面板的数据：全量方块 + 颜色 + 贴图 + 材质家族。

        数据源是 ``skills/minecraft-material-lab/data/`` 下的
        ``block_catalog.json`` / ``texture_stats.json`` / ``family_index.json``
        （由 ``python -m mcmaterials catalog`` 生成；纯 JSON，加载秒级，不碰 mcassets）。

        返回 ``{"groups": [[id, 标签, [famIdx…]]…], "families": [名…],
        "blocks": [[名, "#rrggbb", is_full, [famIdx…], 贴图ref, tint|null]…]}`` ——
        面板用贴图当色块（``tint`` 非空时用 ``mix-blend-mode: multiply`` 叠一层群系染色，
        和 mcrender 出图一致），颜色作为加载失败/纹理缺失时的底；
        技术方块（空气/命令块/头颅等）与贴图缺失的方块不列。
        """
        with self._lock:
            cached = self._picker
        if cached is not None:
            return cached
        root = repo_root()
        data = root / "skills" / "minecraft-material-lab" / "data"
        cat_p, fam_p = data / "block_catalog.json", data / "family_index.json"
        tex_p = data / "texture_stats.json"
        out: dict = {"groups": [], "families": [], "blocks": [],
                     "source": str(cat_p.relative_to(root)).replace("\\", "/"),
                     "available": False}
        try:
            cat = json.loads(cat_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            with self._lock:
                self._picker = out      # 缺数据 → 缓存空面板，客户端回退到输入框
            return out
        try:
            fams = json.loads(fam_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fams = {}
        try:
            tex_stats = json.loads(tex_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            tex_stats = {}

        names = sorted(fams)
        fidx = {n: i for i, n in enumerate(names)}
        #: 语义伪家族接在材质家族后面：下标同一套，客户端协议（groups[i][2] / block[3]）不变
        SEMANTIC = ("::function", "::light", "::plant", "::sign", "::door", "::wood",
                    "::stone", "::other")
        for k, s in enumerate(SEMANTIC):
            fidx[s] = len(names) + k
        member: dict[str, list[int]] = {}
        for fname in names:
            for b in fams.get(fname) or ():
                member.setdefault(b, []).append(fidx[fname])
        #: 功能/发光方块不该待在「石砖」这类材质芯片里（磁石、红石火把、萤石）
        material = {fidx[f] for f in MATERIAL_CHIP_FAMILIES if f in fidx}
        lights_fam = fidx.get("lights")
        rows = []
        for name, e in cat.items():
            if not isinstance(e, dict) or e.get("missing"):
                continue
            if name in PICKER_EXCLUDE:      # 空气/屏障/光/传送门/命令块…不列；水岩浆火刷怪笼头颅要列
                continue
            rgb = (e.get("color") or {}).get("avg")
            if not rgb:
                continue
            color = "#%02x%02x%02x" % tuple(
                max(0, min(255, int(round(float(v))))) for v in rgb[:3])
            refs = [r for r in (e.get("textures") or []) if isinstance(r, str)]
            swatch_color, swatch_tex, swatch_tint = _picker_swatch(name, refs, color)
            if swatch_color:
                color = swatch_color
            kinds = set(member.get(name, ()))
            sem = _semantic_kinds(name, e)
            if lights_fam is not None and lights_fam in kinds:
                sem.add("light")          # 家族 lights 的成员一律算发光（蜡烛 17 色都在里面）
            if sem & {"function", "light"}:
                kinds -= material         # 磁石别在「石砖」、萤石也别在「石砖」
                sem -= {"stone", "wood"}  # 语义上的材质类也让位（紫水晶簇→只算发光）
            kinds |= {fidx["::" + s] for s in sem}
            rows.append([name, color, 1 if e.get("is_full") else 0,
                         sorted(kinds),
                         swatch_tex or self._picker_texture(name, refs, tex_stats, rgb),
                         swatch_tint if swatch_tex else _tint_hex(name),
                         _picker_alias(name)])      # 第 7 项：中文搜索别名（旧客户端忽略）
        # 按「首个分组」聚簇，同类方块排在一起（无分组的排最后）
        rows.sort(key=lambda r: (r[3][0] if r[3] else 10 ** 6, r[0]))
        # 「其他」= 没被任何芯片覆盖的方块：保证**每个**方块都能按分类找到，不再只靠「全部」
        other = fidx["::other"]
        for r in rows:
            if not r[3]:
                r[3] = [other]
        out["families"] = names + list(SEMANTIC)
        out["groups"] = [[gid, label, [fidx[f] for f in fs if f in fidx]]
                         for gid, label, fs in self.PICKER_GROUPS]
        out["blocks"] = rows
        out["available"] = True
        with self._lock:
            self._picker = out
        return out

    @staticmethod
    def _picker_texture(name: str, refs: list, tex_stats: dict, block_rgb) -> str | None:
        """挑一张能代表这个方块的贴图（面板色块用）。

        优先同名 → 同前缀（如 grass_block → block/grass_block_top）→ 颜色最接近。
        """
        if not refs:
            return None
        for ref in refs:
            if ref.split("/")[-1] == name:
                return ref
        for ref in refs:
            short = ref.split("/")[-1]
            if short.startswith(name) or name.startswith(short):
                return ref
        best, best_d = refs[0], None
        for ref in refs:
            rgb = ((tex_stats.get(ref) or {}).get("color") or {}).get("avg")
            if not rgb or not block_rgb:
                continue
            d = sum((float(a) - float(b)) ** 2 for a, b in zip(rgb[:3], block_rgb[:3]))
            if best_d is None or d < best_d:
                best, best_d = ref, d
        return best

    def resolve(self, states: list[dict], version: str) -> dict:
        """Return ``{state_str: info}``（info 里含颜色 + 渲染语义，二者同源）。"""
        from mccore.schem_io import state_str  # noqa: PLC0415
        from mcrender.model import ModelResolver  # noqa: PLC0415
        from mcrender.renderer import TextureAtlas  # noqa: PLC0415

        self._load_cache(version)
        cache = self._resolved[version]
        keys = [state_str(s) for s in states]
        states_by_key = {state_str(s): s for s in states}
        todo = [s for s, k in zip(states, keys) if k not in cache]
        if todo:
            assets = self.assets(version=version)
            resolver = self._resolver.get(version)
            if resolver is None:
                resolver = ModelResolver(assets)
                self._resolver[version] = resolver
            baked = {}
            for s in todo:
                name = s["Name"].removeprefix("minecraft:")
                try:
                    baked[state_str(s)] = resolver.resolve_block(
                        name, s.get("Properties") or {})
                except Exception:  # noqa: BLE001
                    baked[state_str(s)] = None
            refs = {t for bb in baked.values() if bb for t in bb.all_textures}
            atlas = TextureAtlas(sorted(refs), assets, verbose=False) if refs else None
            extra: list[str] = []
            for k, bb in baked.items():
                info = {"color": "#888888", "opaque": False,
                        "semi_transparent": False, "self_culling": False,
                        "layer": 0, "tint": None, "liquid": None,
                        "special": None, "has_elements": False,
                        "ao_occluder": False, "render": "normal"}
                state = states_by_key.get(k) or {}
                props = state.get("Properties") or {}
                block_name = str(state.get("Name", "")).removeprefix("minecraft:") or bb.name
                if bb is not None and atlas is not None:
                    colors = []
                    for ref in sorted(bb.textures):
                        ti = atlas.info.get(ref)
                        if ti is None:
                            continue
                        col, row = ti.tile % atlas.cols, ti.tile // atlas.cols
                        tile = atlas.pixels[row * TILE:(row + 1) * TILE,
                                            col * TILE:(col + 1) * TILE]
                        mask = tile[..., 3] > 0
                        if mask.any():
                            colors.append(tile[mask][:, :3].mean(axis=0))
                    if colors:
                        import numpy as np  # noqa: PLC0415
                        c = np.mean(colors, axis=0)
                        info["color"] = "#%02x%02x%02x" % tuple(
                            int(max(0, min(255, v))) for v in c)
                    core = bb.cube_textures or (bb.textures if bb.is_full_cube else set())
                    core_trans = [atlas.info[t].has_zero or atlas.info[t].has_partial
                                  for t in core if t in atlas.info]
                    # opaque/self_culling 看「方块体」；overlay（草方块侧面）不算
                    info["opaque"] = bool(core and core_trans
                                          and not any(core_trans))
                    info["self_culling"] = bool(core and any(core_trans))
                    # semi_transparent 稍后按 layer 定（cutout 走 alpha 测试，不进混合）
                if bb is not None:
                    info.update(_render_semantics(block_name, props, bb, atlas))
                    # 只有真正需要混合（layer 2）的方块进透明通道；
                    # cutout（树叶/草方块/铁栏杆）走透明测试，否则会被混成半透明
                    info["semi_transparent"] = bool(info["layer"] == 2)
                cache[k] = info
            self._save_cache(version)
        # 额外贴图按**最终 info** 重算（缓存命中与新解析走同一条路）。
        # 不能只写在 if todo 里：缓存一热 todo 为空就直接返回的话，
        # extra_textures() 会是空表 → 编辑器取不到 entity/* 与 *_flow 贴图
        # （水侧面退回图集 0 号 tile）。_extra_textures 只在内存里，不进缓存。
        extra: list[str] = []
        for k in keys:
            info = cache.get(k)
            if not info:
                continue
            state = states_by_key.get(k) or {}
            block_name = str(state.get("Name", "")).removeprefix("minecraft:")
            if not block_name:
                continue
            extra.extend(_extra_textures(block_name,
                                         state.get("Properties") or {}, info))
        prev = set(self._extra_textures.get(version) or [])
        self._extra_textures[version] = sorted(prev | set(extra))
        return {k: cache[k] for k in keys if k in cache}

    def extra_textures(self, version: str) -> list[str]:
        """上一次 :meth:`resolve` 统计出的额外贴图（特判方块 entity/* + 液体 flow）。"""
        return list(self._extra_textures.get(version) or [])
