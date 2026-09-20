"""连接状态求解器：墙 / 栅栏 / 玻璃板 / 楼梯按邻居算出正确的 blockstate 属性。

Minecraft does **not** derive wall/fence/pane/stairs connection states from the
world when loading a schematic: the state is stored in the blockstate itself.
If you generate a structure file programmatically and forget to write those
properties, every wall becomes a lone post, every pane a lone bar and every
stair a straight stair facing north.

This module computes the properties you must write, given a neighbour lookup:

    def at(x, y, z) -> str | None      # block name without "minecraft:", or None

Example
-------
    from connect import wall_state, fence_state, pane_state, stair_state, door_pair

    props = wall_state("polished_deepslate_wall", (10, 64, 10), at)
    # {"up": "true", "north": "tall", "south": "none",
    #  "east": "low", "west": "none", "waterlogged": "false"}

    shape = stair_shape((10, 64, 10), "east", at)
    lower, upper = door_pair(facing="north", hinge="left")

Everything is pure-python and dependency-free.  ``is_solid`` may be supplied to
override the built-in "full cube" heuristic (e.g. pass the real set of full
blocks from your generator).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# block-family classification
# ---------------------------------------------------------------------------

WALLS = ("_wall",)
FENCES = ("_fence",)
GATES = ("_fence_gate",)
PANES = ("_pane",)
BARS = ("iron_bars", "copper_bars", "exposed_copper_bars", "weathered_copper_bars",
        "oxidized_copper_bars", "waxed_copper_bars", "waxed_exposed_copper_bars",
        "waxed_weathered_copper_bars", "waxed_oxidized_copper_bars")
SLABS = ("_slab",)
STAIRS = ("_stairs",)
DOORS = ("_door",)
TRAPDOORS = ("_trapdoor",)

# blocks that are definitely not "full solid" (used by the default heuristic)
NON_SOLID_SUFFIXES = (
    "_slab", "_stairs", "_wall", "_fence", "_fence_gate", "_pane", "_door",
    "_trapdoor", "_button", "_pressure_plate", "_carpet", "_banner", "_sign",
    "_torch", "_candle", "_bed", "_flower", "_sapling", "_roots", "_vines",
    "_fern", "_bush", "_grass", "_leaves",
)
NON_SOLID_NAMES = {
    "air", "cave_air", "void_air", "water", "lava", "glass", "tinted_glass",
    "iron_bars", "chain", "lantern", "soul_lantern", "end_rod", "lever",
    "grindstone", "ladder", "rail", "powered_rail", "detector_rail",
    "activator_rail", "torch", "soul_torch", "redstone_torch", "redstone_wire",
    "tripwire", "tripwire_hook", "cobweb", "scaffolding", "lily_pad",
    "sea_pickle", "snow", "moss_carpet", "hanging_roots", "glow_lichen",
    "vine", "cave_vines", "cave_vines_plant", "pointed_dripstone",
    "amethyst_cluster", "large_amethyst_bud", "medium_amethyst_bud",
    "small_amethyst_bud", "light", "barrier", "structure_void", "moving_piston",
    "flower_pot", "brewing_stand", "cauldron", "composter", "stonecutter",
    "bell", "decorated_pot", "bamboo", "sugar_cane", "kelp", "kelp_plant",
    "seagrass", "big_dripleaf", "small_dripleaf", "spore_blossom", "azalea",
    "flowering_azalea", "pink_petals", "torchflower", "pitcher_plant",
}
for _c in ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
           "gray", "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black"):
    NON_SOLID_NAMES.add(_c + "_stained_glass")
    NON_SOLID_NAMES.add(_c + "_stained_glass_pane")
    NON_SOLID_NAMES.add(_c + "_candle")


def is_full_solid(name: str | None) -> bool:
    """Default heuristic: is ``name`` a full solid cube (something fences connect to)?"""
    if not name:
        return False
    if name in NON_SOLID_NAMES:
        return False
    if name.endswith(NON_SOLID_SUFFIXES):
        return False
    return True


def _is_wall(name):
    return isinstance(name, str) and name.endswith("_wall")


def _is_fence(name):
    return isinstance(name, str) and name.endswith("_fence")


def _is_gate(name):
    return isinstance(name, str) and name.endswith("_fence_gate")


def _is_pane(name):
    return isinstance(name, str) and (name.endswith("_pane") or name in BARS)


def _is_slab(name):
    return isinstance(name, str) and name.endswith("_slab")


def _is_stairs(name):
    return isinstance(name, str) and name.endswith("_stairs")


# ---------------------------------------------------------------------------
# directions (Minecraft's horizontal order and its counter-clockwise table)
# ---------------------------------------------------------------------------

DIRS = ("north", "south", "west", "east")
DELTA = {"north": (0, 0, -1), "south": (0, 0, 1), "west": (-1, 0, 0), "east": (1, 0, 0)}
OPPOSITE = {"north": "south", "south": "north", "west": "east", "east": "west",
            "up": "down", "down": "up"}
# Minecraft Direction.getCounterClockWise() in the horizontal plane
CCW = {"north": "west", "west": "south", "south": "east", "east": "north"}
CW = {v: k for k, v in CCW.items()}


def _nbr(pos, d):
    x, y, z = pos
    dx, dy, dz = DELTA[d]
    return (x + dx, y + dy, z + dz)


def _above(pos):
    return (pos[0], pos[1] + 1, pos[2])


# ---------------------------------------------------------------------------
# 「上方压着它时墙要立中心柱」的方块名表
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def center_cover_blocks() -> frozenset:
    """哪些方块**盖住了方块顶面中心那 2×2 格**（由 ``tools/export_center_cover.py``
    从真实模型几何导出）。原版 ``WallBlock#shouldRaisePost`` 看的是上方方块的
    **shape**：盖住中心四格（压力板/火把/告示牌/花盆/栅栏/半砖…）就要立柱子。
    我们只有方块名，所以离线算成一张表。
    """
    path = Path(__file__).with_name("data") / "center_cover.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return frozenset(str(x) for x in (data.get("blocks") or ()))
    except (OSError, ValueError):
        return frozenset()


def covers_center(name) -> bool:
    """上方这个方块会不会压住墙柱顶那 2×2 中心（→ 立中心柱）？

    差不到表时退回“整方块”启发式（宁可不立柱，也不给不完整方块乱立柱）。
    另外原版点名「旗帜」也算（它的布面几何不盖中心，但游戏里立柱）。
    """
    n = str(name or "").split(":", 1)[-1]      # 表里存的是去命名空间的名字
    if not n:
        return False
    if n.endswith("_banner"):          # wiki 点名：banner 会立柱
        return True
    table = center_cover_blocks()
    if not table:
        return is_full_solid(n)
    return n in table


# ---------------------------------------------------------------------------
# wall
# ---------------------------------------------------------------------------

def wall_state(block: str, pos, at, is_solid=is_full_solid, waterlogged: str = "false",
               up: bool | None = None) -> dict:
    """Full property dict for a ``*_wall`` block at ``pos``.

    ``tall`` for a full solid neighbour or another wall, ``low`` for fences,
    gates, slabs, stairs and panes, ``none`` otherwise.

    ``up``（= 有没有中心柱）：按**原版** `WallBlock#shouldRaisePost` 的语义——

    * 上方是另一段墙 → 立柱（跟着上面那根走）；
    * 四向**都不连**（孤立、墙头墙尾）→ 立柱；
    * 否则看上方方块的 **shape 有没有压住中心 2×2 格**（整方块、压力板、火把、花盆、
      栅栏、半砖、告示牌…见 :func:`covers_center`）—— 压住就立柱；
    * **例外**：north/south 或 east/west **两侧都是 ``tall``** 时顶面已经被抬起来了，
      不立柱（原版 ``unless the wall raises its top on two opposite sides``）。

    旧实现只看“上方是不是整方块/墙”，于是“墙 + 花盆”（花盆的 6×6 底盖住中心）
    这种状态会被算成 ``up=false``：没有中心柱、而臂的内侧面在原版模型里本来就不存在
    （``template_wall_side_tall`` 只有 down/up/north/west/east）→ 看上去就像“高臂没渲染”。
    显式传 ``up`` 则按传入值。
    """
    x, y, z = pos
    above = at(x, y + 1, z)
    out = {"waterlogged": str(waterlogged).lower()}
    connected = 0
    for d in DIRS:
        n = at(*_nbr(pos, d))
        if not n:
            side = "none"
        elif is_solid(n) or _is_wall(n):
            side = "tall"
        elif _is_fence(n) or _is_gate(n) or _is_slab(n) or _is_stairs(n) or _is_pane(n):
            side = "low"
        else:
            side = "none"
        connected += side != "none"
        out[d] = side
    if up is None:
        above_wall = bool(above) and _is_wall(above)
        supported = bool(above) and (above_wall or covers_center(above) or is_solid(above))
        up = supported or connected == 0
        pair_tall = ((out["north"] == "tall" and out["south"] == "tall")
                     or (out["east"] == "tall" and out["west"] == "tall"))
        if pair_tall and not above_wall:
            up = False        # 对边都 tall：顶面已被抬起，原版不立柱
        # 注：原版源码里 shouldRaisePost 的精确分支没拿到（javadoc 只有签名），
        # 这里按 wiki 的措辞实现：被“压住中心”或“四向都不连”才立柱。
        # 因此**墙头只有一侧连接、上方又没东西**时不给柱（up=false）——若以后确认原版
        # 墙头也立柱，把上面的 `connected == 0` 改成 `connected <= 1` 即可。
    out["up"] = "true" if up else "false"
    return out


# ---------------------------------------------------------------------------
# fence / fence gate / pane
# ---------------------------------------------------------------------------

def fence_state(block: str, pos, at, is_solid=is_full_solid, waterlogged: str = "false") -> dict:
    """Full property dict for a ``*_fence`` block."""
    out = {"waterlogged": str(waterlogged).lower()}
    for d in DIRS:
        n = at(*_nbr(pos, d))
        out[d] = "true" if (n and (is_solid(n) or _is_fence(n) or _is_gate(n))) else "false"
    return out


def pane_state(block: str, pos, at, is_solid=is_full_solid, waterlogged: str = "false") -> dict:
    """Full property dict for a glass pane / iron bars block."""
    out = {"waterlogged": str(waterlogged).lower()}
    for d in DIRS:
        n = at(*_nbr(pos, d))
        out[d] = "true" if (n and (is_solid(n) or _is_pane(n))) else "false"
    return out


def fence_gate_state(facing: str, open_: bool = False, in_wall: bool = False,
                     powered: bool = False) -> dict:
    return {"facing": facing, "open": "true" if open_ else "false",
            "in_wall": "true" if in_wall else "false",
            "powered": "true" if powered else "false"}


# ---------------------------------------------------------------------------
# stairs
# ---------------------------------------------------------------------------

def stair_shape(pos, facing: str, at, facing_at=None) -> str:
    """Vanilla ``StairsBlock`` shape from the two stairs in front / behind.

    ``facing`` is the direction the stair's high side faces.  ``at`` returns a
    block name; pass ``facing_at(x,y,z) -> facing`` so the neighbour's facing
    can be read (a stair name alone does not carry its facing).
    """
    def neighbour_facing(p):
        n = at(*p)
        if not _is_stairs(n):
            return None
        if facing_at is not None:
            return str(facing_at(*p)).lower()
        return _facing_of(n)

    ff = neighbour_facing(_nbr(pos, facing))
    if ff and ff not in (facing, OPPOSITE[facing]):
        return "outer_left" if ff == CCW[facing] else "outer_right"
    bf = neighbour_facing(_nbr(pos, OPPOSITE[facing]))
    if bf and bf not in (facing, OPPOSITE[facing]):
        return "inner_left" if bf == CCW[facing] else "inner_right"
    return "straight"


def _facing_of(n) -> str | None:
    """Accept either a block name or a (name, facing) tuple from ``at``."""
    if isinstance(n, (tuple, list)) and len(n) >= 2:
        return str(n[1]).lower()
    return None


def stair_state(pos, facing: str, at, half: str = "bottom", waterlogged: str = "false",
                shape: str | None = None, facing_at=None) -> dict:
    """Full property dict for a stair block (shape auto-computed if omitted)."""
    return {"facing": facing, "half": half,
            "shape": shape or stair_shape(pos, facing, at, facing_at),
            "waterlogged": str(waterlogged).lower()}


# ---------------------------------------------------------------------------
# door / trapdoor / chain / lantern / end rod / button / lever / grindstone
# ---------------------------------------------------------------------------

def door_pair(facing: str = "north", hinge: str = "left", open_: bool = False,
              powered: bool = False) -> tuple[dict, dict]:
    """(lower, upper) property dicts for the two blocks of one door."""
    common = {"facing": facing, "hinge": hinge,
              "open": "true" if open_ else "false",
              "powered": "true" if powered else "false"}
    lower = dict(common, half="lower")
    upper = dict(common, half="upper")
    return lower, upper


def trapdoor_state(facing: str = "north", half: str = "bottom", open_: bool = False,
                   powered: bool = False, waterlogged: str = "false") -> dict:
    return {"facing": facing, "half": half,
            "open": "true" if open_ else "false",
            "powered": "true" if powered else "false",
            "waterlogged": str(waterlogged).lower()}


def chain_state(axis: str = "y", waterlogged: str = "false") -> dict:
    return {"axis": axis, "waterlogged": str(waterlogged).lower()}


def lantern_state(hanging: bool = False, waterlogged: str = "false") -> dict:
    return {"hanging": "true" if hanging else "false",
            "waterlogged": str(waterlogged).lower()}


def end_rod_state(facing: str = "up") -> dict:
    return {"facing": facing}


def button_state(face: str = "wall", facing: str = "north", powered: bool = False) -> dict:
    return {"face": face, "facing": facing, "powered": "true" if powered else "false"}


def lever_state(face: str = "wall", facing: str = "north", powered: bool = False) -> dict:
    return {"face": face, "facing": facing, "powered": "true" if powered else "false"}


def grindstone_state(face: str = "floor", facing: str = "north") -> dict:
    return {"face": face, "facing": facing}


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    # an L-shaped wall: post at origin, arms to the east and south, glass pane
    blocks = {
        (0, 0, 0): "polished_deepslate_wall",
        (1, 0, 0): "polished_deepslate_wall",
        (0, 0, 1): "polished_deepslate_wall",
        (2, 0, 0): "glass_pane",
    }

    def at(x, y, z):
        return blocks.get((x, y, z))

    print("wall  @0,0,0:", wall_state("polished_deepslate_wall", (0, 0, 0), at))
    print("pane  @2,0,0:", pane_state("glass_pane", (2, 0, 0), at))
    # stairs: an east-facing stair with a north-facing stair in front
    stairs = {(0, 0, 0): "oak_stairs", (1, 0, 0): "oak_stairs"}
    facings = {(0, 0, 0): "east", (1, 0, 0): "north"}
    print("stair @0,0,0:", stair_state((0, 0, 0), "east",
                                      lambda x, y, z: stairs.get((x, y, z)),
                                      facing_at=lambda x, y, z: facings.get((x, y, z))))
    print("door       :", door_pair("north", "left"))


if __name__ == "__main__":
    _demo()
