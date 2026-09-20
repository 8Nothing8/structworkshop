"""方块模型 → 带贴图的四边形（blockstate → 模型 → 贴图 的解析）。

Faithful Python implementation of the vanilla **block model** format, so that
non-full blocks (slabs, stairs, walls, fences, panes, doors, trapdoors, chains,
lanterns, ...) render with their real geometry instead of a fake cube.

Covered:

* ``blockstates``: ``variants`` (property key matching, most-specific wins) and
  ``multipart`` (``when`` conditions incl. ``OR`` / ``AND`` lists);
* model inheritance: ``parent`` chains, texture-variable merge, ``elements``
  override, ``ambientocclusion``;
* variant rotation ``x`` / ``y`` / ``z`` around the block centre (negated, as
  Minecraft does) + ``uvlock`` best-effort;
* element ``rotation`` (axis / angle / origin / rescale) and ``shade``;
* per-face ``uv`` (explicit or Minecraft's default per direction), UV rotation,
  ``cullface`` and ``tintindex``;
* texture variables ``#all`` / ``#side`` ... resolved to ``block/...`` refs;
* special blocks: water / lava / air / light / barrier / structure_void.

The vertex order, default UVs and rotation conventions follow vanilla's
``FaceBakery`` (cross-checked against PrismarineJS ``prismarine-viewer``).

Usage::

    from mcrender.assets import auto_assets
    from mcmodel import ModelResolver

    assets = auto_assets(data_version=4903)
    res = ModelResolver(assets)
    bb = res.resolve_block("oak_stairs", {"facing": "east", "half": "bottom",
                                          "shape": "straight", "waterlogged": "false"})
    for q in bb.quads:
        print(q.tex, q.pos.tolist(), q.uv.tolist(), q.cull, q.tint)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from mcrender.assets import Assets, _norm_ref

# ---------------------------------------------------------------------------
# Geometry tables (vanilla FaceBakery vertex order / UV selectors)
# ---------------------------------------------------------------------------

FACES = ("down", "up", "north", "south", "west", "east")
FACE_INDEX = {f: i for i, f in enumerate(FACES)}
FACE_DIR = {
    "down": (0, -1, 0),
    "up": (0, 1, 0),
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
    "east": (1, 0, 0),
}
FACE_OPPOSITE = {
    "down": "up", "up": "down", "north": "south",
    "south": "north", "west": "east", "east": "west",
}

# Each corner: (x_sel, y_sel, z_sel, u_sel, v_sel); 1 -> "to", 0 -> "from".
FACE_CORNERS = {
    "up":    ((0, 1, 1, 0, 1), (1, 1, 1, 1, 1), (0, 1, 0, 0, 0), (1, 1, 0, 1, 0)),
    "down":  ((1, 0, 1, 0, 1), (0, 0, 1, 1, 1), (1, 0, 0, 0, 0), (0, 0, 0, 1, 0)),
    "east":  ((1, 1, 1, 0, 0), (1, 0, 1, 0, 1), (1, 1, 0, 1, 0), (1, 0, 0, 1, 1)),
    "west":  ((0, 1, 0, 0, 0), (0, 0, 0, 0, 1), (0, 1, 1, 1, 0), (0, 0, 1, 1, 1)),
    "north": ((1, 0, 0, 0, 1), (0, 0, 0, 1, 1), (1, 1, 0, 0, 0), (0, 1, 0, 1, 0)),
    "south": ((0, 0, 1, 0, 1), (1, 0, 1, 1, 1), (0, 1, 1, 0, 0), (1, 1, 1, 1, 0)),
}

# Axis-aligned face normal shading, Minecraft's fixed light values.
FACE_SHADE = {"up": 1.00, "down": 0.50, "north": 0.80, "south": 0.80, "west": 0.60, "east": 0.60}

INVISIBLE_BLOCKS = {
    "air", "cave_air", "void_air", "light", "barrier", "structure_void",
    "moving_piston", "bubble_column", "end_portal", "end_gateway",
    "nether_portal",
    # 注：tripwire / redstone_wire **不在这里** —— 它们有真正的平面模型
    #（``redstone_dust_*`` / ``tripwire_*``，y=0.25/16 的扁平元素），
    # 旧实现把它们当“不可见”直接返回 → 面板里放了也看不见。
}
LIQUIDS = {"water": "block/water_still", "lava": "block/lava_still"}
#: 流体侧面（流动）贴图；静止贴图用上面的 LIQUIDS
LIQUID_FLOW = {"water": "block/water_flow", "lava": "block/lava_flow"}
#: 水没（waterlogged=true / 天生含水）也算流体面，tint 同水
WATERLIKE = ("bubble_column", "kelp", "kelp_plant", "seagrass", "tall_seagrass")
FULLBRIGHT_BLOCKS = {
    "glowstone", "sea_lantern", "shroomlight", "ochre_froglight",
    "pearlescent_froglight", "verdant_froglight", "magma_block", "lava",
    "campfire", "soul_campfire", "torch", "wall_torch", "soul_torch",
    "soul_wall_torch", "lantern", "soul_lantern", "end_rod", "beacon",
    "conduit", "crying_obsidian", "respawn_anchor", "froglight",
    "waxed_copper_bulb", "copper_bulb", "exposed_copper_bulb",
    "weathered_copper_bulb", "oxidized_copper_bulb", "fire", "soul_fire",
    "jack_o_lantern", "redstone_lamp", "redstone_torch", "cave_vines",
    "cave_vines_plant", "glow_lichen",
}

# Blocks that vanilla draws with a translucent (blended) render layer.
TRANSLUCENT_BLOCKS = {
    # 注意：普通玻璃板也得在这里（只靠贴图 alpha 扫描会判成 cutout，
    # 于是玻璃板不混合 → 挡住后面的水）
    "glass", "glass_pane",
    "white_stained_glass", "orange_stained_glass", "magenta_stained_glass",
    "light_blue_stained_glass", "yellow_stained_glass", "lime_stained_glass",
    "pink_stained_glass", "gray_stained_glass", "light_gray_stained_glass",
    "cyan_stained_glass", "purple_stained_glass", "blue_stained_glass",
    "brown_stained_glass", "green_stained_glass", "red_stained_glass",
    "black_stained_glass", "tinted_glass", "slime_block", "honey_block",
    "water", "bubble_column", "ice", "packed_ice", "blue_ice", "frosted_ice",
    "beacon", "conduit", "nether_portal", "end_gateway",
    "white_stained_glass_pane", "orange_stained_glass_pane", "magenta_stained_glass_pane",
    "light_blue_stained_glass_pane", "yellow_stained_glass_pane", "lime_stained_glass_pane",
    "pink_stained_glass_pane", "gray_stained_glass_pane", "light_gray_stained_glass_pane",
    "cyan_stained_glass_pane", "purple_stained_glass_pane", "blue_stained_glass_pane",
    "brown_stained_glass_pane", "green_stained_glass_pane", "red_stained_glass_pane",
    "black_stained_glass_pane",
}

# Cutout (alpha-tested) blocks whose textures may contain holes.
CUTOUT_HINT_BLOCKS = {
    "glass_pane", "iron_bars", "chain", "lantern", "soul_lantern", "torch",
    "wall_torch", "soul_torch", "soul_wall_torch", "redstone_torch",
    "oak_leaves", "spruce_leaves", "birch_leaves", "jungle_leaves",
    "acacia_leaves", "dark_oak_leaves", "mangrove_leaves", "cherry_leaves",
    "azalea_leaves", "flowering_azalea_leaves", "pale_oak_leaves",
    "vine", "glow_lichen", "sculk_vein", "hanging_roots", "moss_carpet",
    "lily_pad", "sugar_cane", "bamboo", "cactus", "ladder", "rail",
    "powered_rail", "detector_rail", "activator_rail", "lever", "tripwire",
    "tripwire_hook", "flower_pot", "brewing_stand", "cauldron", "composter",
    "grindstone", "stonecutter", "anvil", "bell", "campfire", "soul_campfire",
    "candle", "white_candle", "orange_candle", "magenta_candle",
    "light_blue_candle", "yellow_candle", "lime_candle", "pink_candle",
    "gray_candle", "light_gray_candle", "cyan_candle", "purple_candle",
    "blue_candle", "brown_candle", "green_candle", "red_candle",
    "black_candle", "end_rod", "lightning_rod", "decorated_pot", "sign",
    "oak_sign", "spruce_sign", "birch_sign", "jungle_sign", "acacia_sign",
    "dark_oak_sign", "mangrove_sign", "cherry_sign", "pale_oak_sign",
    "bamboo_sign", "crimson_sign", "warped_sign", "hanging_sign",
    "oak_hanging_sign", "spruce_hanging_sign", "birch_hanging_sign",
    "jungle_hanging_sign", "acacia_hanging_sign", "dark_oak_hanging_sign",
    "mangrove_hanging_sign", "cherry_hanging_sign", "pale_oak_hanging_sign",
    "bamboo_hanging_sign", "crimson_hanging_sign", "warped_hanging_sign",
    "pointed_dripstone", "amethyst_cluster", "large_amethyst_bud",
    "medium_amethyst_bud", "small_amethyst_bud", "budding_amethyst",
    "sculk_sensor", "calibrated_sculk_sensor", "cave_vines", "cave_vines_plant",
    "big_dripleaf", "small_dripleaf", "spore_blossom", "azalea",
    "flowering_azalea", "pink_petals", "torchflower", "pitcher_plant",
    "sunflower", "lilac", "rose_bush", "peony", "tall_grass", "large_fern",
    "grass", "fern", "dead_bush", "dandelion", "poppy", "blue_orchid",
    "allium", "azure_bluet", "red_tulip", "orange_tulip", "white_tulip",
    "pink_tulip", "oxeye_daisy", "cornflower", "lily_of_the_valley",
    "wither_rose", "sweet_berry_bush", "cocoa", "kelp", "kelp_plant",
    "seagrass", "sea_pickle", "tube_coral", "brain_coral", "bubble_coral",
    "fire_coral", "horn_coral", "dead_tube_coral", "dead_brain_coral",
    "dead_bubble_coral", "dead_fire_coral", "dead_horn_coral",
    "cobweb", "scaffolding", "chain_command_block", "repeating_command_block",
    "command_block", "structure_block", "jigsaw", "lectern", "grindstone",
}


# ---------------------------------------------------------------------------
# Small vector helpers
# ---------------------------------------------------------------------------

def rotation_matrix(axis: str, degrees: float) -> np.ndarray:
    """Vanilla model rotation matrix (angle sign is the caller's job)."""
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    a = "xyz".index(axis)
    m = np.eye(3, dtype=np.float64)
    b, d = (a + 1) % 3, (a + 2) % 3
    m[b, b] = c
    m[b, d] = -s
    m[d, b] = s
    m[d, d] = c
    return m


def _snap_face(v: np.ndarray) -> str | None:
    """Nearest axis-aligned direction name, or None if the vector is diagonal."""
    ax = int(np.argmax(np.abs(v)))
    if abs(abs(v[ax]) - np.linalg.norm(v)) > 1e-3:
        return None
    name = ("xyz"[ax], "+" if v[ax] > 0 else "-")
    return {
        ("y", "+"): "up", ("y", "-"): "down",
        ("z", "+"): "south", ("z", "-"): "north",
        ("x", "+"): "east", ("x", "-"): "west",
    }[name]


def _default_uv(face: str, frm, to):
    x0, y0, z0 = frm
    x1, y1, z1 = to
    if face == "down":
        return (x0, 16 - z1, x1, 16 - z0)
    if face == "up":
        return (x0, z0, x1, z1)
    if face == "north":
        return (16 - x1, 16 - y1, 16 - x0, 16 - y0)
    if face == "south":
        return (x0, 16 - y1, x1, 16 - y0)
    if face == "west":
        return (z0, 16 - y1, z1, 16 - y0)
    if face == "east":
        return (16 - z1, 16 - y1, 16 - z0, 16 - y0)
    raise ValueError(face)


@dataclass
class Quad:
    """One textured, single-sided quad in block-local space (0..1)."""

    pos: np.ndarray          # (4, 3) float32
    uv: np.ndarray           # (4, 2) float32, texture space 0..1
    normal: np.ndarray       # (3,) float32
    tex: str                 # "block/oak_planks"
    cull: int = -1           # FACE_INDEX or -1
    tint: int = -1           # tintindex or -1
    shade: bool = True       # model element "shade"
    face: str = ""           # source direction name (debug / shading)
    source: str = ""         # model ref (debug)

    def bbox(self):
        return self.pos.min(axis=0), self.pos.max(axis=0)


@dataclass
class BakedBlock:
    """Everything the renderer needs for one palette entry."""

    name: str
    props: dict
    quads: list = field(default_factory=list)
    is_full_cube: bool = False
    liquid: str | None = None        # "water" / "lava"：几何在渲染器里按邻居逐格生成
    waterlogged: bool = False        # waterlogged=true 的方块要额外画一层水面
    cube_textures: set = field(default_factory=set)   # 整方块体自身的贴图（不含 overlay）
    occludes: bool = False           # simple cube, all textures fully opaque
    cull_same: bool = False          # hide faces shared with an identical block
    ao: bool = True                  # ambient occlusion enabled for this model
    render: str = "normal"           # normal | invisible
    builtin: str | None = None       # builtin/entity ...
    missing: bool = False            # no blockstate and no fallback
    notes: list = field(default_factory=list)
    textures: set = field(default_factory=set)
    bbox: tuple | None = None

    @property
    def extra_textures(self) -> set:
        """几何之外还需要进图集的贴图：液体/水没方块的 ``*_still`` + ``*_flow``。"""
        out: set[str] = set()
        kind = self.liquid or ("water" if self.waterlogged else None)
        if kind:
            out.add(LIQUIDS[kind])
            out.add(LIQUID_FLOW[kind])
        return out

    @property
    def all_textures(self) -> set:
        """这个方块渲染时真正会用到的全部贴图（调用方建图集时用这个）。"""
        return set(self.textures) | self.extra_textures

    @property
    def quad_count(self) -> int:
        return len(self.quads)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class ModelResolver:
    """Resolve ``name + properties`` into a :class:`BakedBlock`."""

    def __init__(self, assets: Assets, verbose: bool = False) -> None:
        self.assets = assets
        self.verbose = verbose
        self._model_cache: dict[str, dict] = {}
        self._block_cache: dict[tuple, BakedBlock] = {}
        self._summary = None
        self._warned: set[str] = set()

    # ---------------------------------------------------------------- utils
    def _warn(self, msg: str) -> None:
        if msg not in self._warned:
            self._warned.add(msg)
            import sys
            sys.stderr.write("  ! " + msg + "\n")

    def defaults(self, name: str) -> dict:
        """Default property values from the mapping table."""
        if self._summary is None:
            self._summary = self.assets.blocks_summary() or {}
        entry = self._summary.get(_norm_ref(name))
        if not entry or len(entry) < 2:
            return {}
        return dict(entry[1])

    # ------------------------------------------------------------ model load
    def load_model(self, ref: str, _stack: tuple = ()) -> dict:
        """Load a model and merge its whole ``parent`` chain."""
        ref = _norm_ref(ref)
        if ref in self._model_cache:
            return self._model_cache[ref]
        if ref in _stack:
            self._warn("model parent cycle: %s" % " -> ".join(_stack + (ref,)))
            return {"elements": None, "textures": {}, "_cycle": True}

        raw = self.assets.model(ref)
        if raw is None:
            merged = {"elements": None, "textures": {}, "_missing": True, "_ref": ref}
            self._model_cache[ref] = merged
            return merged

        parent_ref = raw.get("parent")
        if parent_ref:
            parent_ref = _norm_ref(parent_ref)
            if parent_ref.startswith("builtin/"):
                merged = {"elements": None, "textures": {}, "_builtin": parent_ref}
            else:
                pm = self.load_model(parent_ref, _stack + (ref,))
                merged = {
                    "elements": pm.get("elements"),
                    "textures": dict(pm.get("textures") or {}),
                    "ambientocclusion": pm.get("ambientocclusion", True),
                    "shade": pm.get("shade", True),
                    "_builtin": pm.get("_builtin"),
                    "_missing": pm.get("_missing", False),
                    "_ref": ref,
                }
        else:
            merged = {"elements": None, "textures": {}, "ambientocclusion": True, "_ref": ref}

        if "textures" in raw and isinstance(raw["textures"], dict):
            merged["textures"].update(raw["textures"])
        if "elements" in raw:
            merged["elements"] = raw["elements"]
        for k in ("ambientocclusion", "shade", "gui_light", "render_type", "display"):
            if k in raw:
                merged[k] = raw[k]
        if "parent" in raw:
            merged["parent"] = raw["parent"]
        self._model_cache[ref] = merged
        return merged

    def resolve_texture(self, ref, textures: dict, _depth: int = 0) -> str | None:
        """Resolve ``#var`` chains to a concrete ``block/...`` texture ref.

        Modern (1.21.4+) models may use texture *objects*::

            "edge": {"sprite": "minecraft:block/glass_pane_top",
                     "force_translucent": true}
        """
        if ref is None or _depth > 16:
            return None
        if isinstance(ref, dict):
            ref = ref.get("sprite") or ref.get("texture")
            if ref is None:
                return None
        if not isinstance(ref, str):
            return None
        if ref.startswith("#"):
            var = ref[1:]
            target = textures.get(var)
            if target is None:
                return None
            return self.resolve_texture(target, textures, _depth + 1)
        out = _norm_ref(ref)
        if "/" not in out:
            out = "block/" + out
        return out

    # --------------------------------------------------------- state matching
    @staticmethod
    def _match_variant_key(key: str, props: dict) -> int:
        """Return how many properties the key pins down, or -1 if no match."""
        if key == "":
            return 0
        n = 0
        for part in key.split(","):
            part = part.strip()
            if not part:
                continue
            k, _, v = part.partition("=")
            if k not in props:
                return -1
            if str(props[k]).lower() != v.lower():
                return -1
            n += 1
        return n

    @staticmethod
    def _match_when(when, props: dict) -> bool:
        if when is None:
            return True
        if isinstance(when, list):
            return any(ModelResolver._match_when(w, props) for w in when)
        if not isinstance(when, dict):
            return True
        if "OR" in when:
            return any(ModelResolver._match_when(w, props) for w in when["OR"])
        if "AND" in when:
            return all(ModelResolver._match_when(w, props) for w in when["AND"])
        for k, v in when.items():
            if k in ("OR", "AND"):
                continue
            actual = props.get(k)
            if actual is None:
                return False
            allowed = v if isinstance(v, list) else [v]
            if not any(str(actual).lower() == str(x).lower() for x in allowed):
                return False
        return True

    def select_variants(self, bs: dict, props: dict) -> list[dict]:
        """Return the list of variant dicts (model/x/y/uvlock/weight) to draw."""
        out: list[dict] = []
        if "variants" in bs:
            best_n, best = -1, None
            for key, val in bs["variants"].items():
                n = self._match_variant_key(key, props)
                if n > best_n:
                    best_n, best = n, val
            if best is None:
                return out
            # a list means weighted random variants (stone, dirt, grass...):
            # vanilla picks one per block position, we pick the first
            items = best if isinstance(best, list) else [best]
            for it in items[:1]:
                if isinstance(it, dict) and it.get("model"):
                    out.append(it)
            return out
        if "multipart" in bs:
            for part in bs["multipart"]:
                if not self._match_when(part.get("when"), props):
                    continue
                apply = part.get("apply")
                items = apply if isinstance(apply, list) else [apply]
                for it in items[:1]:
                    if isinstance(it, dict) and it.get("model"):
                        out.append(it)
        return out

    # ------------------------------------------------------------- baking
    def _variant_transform(self, variant: dict):
        """Global rotation for a variant (x/y/z), applied around block centre."""
        m = None
        for axis in ("x", "y", "z"):
            if axis in variant:
                ang = float(variant[axis]) % 360.0
                if ang:
                    r = rotation_matrix(axis, -ang)  # vanilla negates
                    m = r if m is None else r @ m
        if m is None:
            return None, None
        origin = np.array([8.0, 8.0, 8.0])
        shift = origin - m @ origin
        return m, shift

    def _bake_element(self, el: dict, textures: dict, g_m, g_shift, out: list, src: str) -> None:
        if not isinstance(el, dict):
            return
        frm = el.get("from")
        to = el.get("to")
        if frm is None or to is None:
            return
        frm = np.asarray(frm, dtype=np.float64)
        to = np.asarray(to, dtype=np.float64)
        faces = el.get("faces") or {}
        if not faces:
            return
        shade = bool(el.get("shade", True))

        l_m = None
        l_shift = None
        rot = el.get("rotation")
        if rot:
            axis = rot.get("axis", "y")
            angle = float(rot.get("angle", 0.0))
            origin = np.asarray(rot.get("origin", [8, 8, 8]), dtype=np.float64)
            if angle:
                m = rotation_matrix(axis, angle)
                if rot.get("rescale"):
                    s = 1.0
                    a = abs(angle) % 360.0
                    if abs(a - 22.5) < 1e-6:
                        s = 0.5
                    elif abs(a - 45.0) < 1e-6:
                        s = math.sqrt(0.5)
                    if s != 1.0:
                        ai = "xyz".index(axis)
                        sc = np.eye(3)
                        sc[(ai + 1) % 3, (ai + 1) % 3] = s
                        sc[(ai + 2) % 3, (ai + 2) % 3] = s
                        m = m @ sc
                l_m = m
                l_shift = origin - m @ origin

        for face_name, face in faces.items():
            if face_name not in FACE_CORNERS or not isinstance(face, dict):
                continue
            tex = self.resolve_texture(face.get("texture"), textures)
            if tex is None:
                continue
            uv = face.get("uv")
            if uv is None:
                uv = _default_uv(face_name, frm, to)
            uv = np.asarray(uv, dtype=np.float64)
            u1, v1, u2, v2 = uv
            urot = float(face.get("rotation", 0) or 0)
            cull_name = face.get("cullface")
            tint = int(face.get("tintindex", -1))

            pts = np.empty((4, 3), dtype=np.float64)
            uvs = np.empty((4, 2), dtype=np.float64)
            for i, (xs, ys, zs, us, vs) in enumerate(FACE_CORNERS[face_name]):
                p = np.array([
                    to[0] if xs else frm[0],
                    to[1] if ys else frm[1],
                    to[2] if zs else frm[2],
                ])
                if l_m is not None:
                    p = l_m @ p + l_shift
                if g_m is not None:
                    p = g_m @ p + g_shift
                pts[i] = p / 16.0

                bu, bv = float(us), float(vs)
                if urot:
                    r = math.radians(urot)
                    c, s = math.cos(r), math.sin(r)
                    du, dv = bu - 0.5, bv - 0.5
                    bu = du * c + dv * s + 0.5
                    bv = -du * s + dv * c + 0.5
                uvs[i, 0] = (u1 + bu * (u2 - u1)) / 16.0
                uvs[i, 1] = (v1 + bv * (v2 - v1)) / 16.0

            n = np.asarray(FACE_DIR[face_name], dtype=np.float64)
            if l_m is not None:
                n = l_m @ n
            if g_m is not None:
                n = g_m @ n
            ln = np.linalg.norm(n)
            if ln > 1e-9:
                n = n / ln
            world_face = _snap_face(n)

            # cullface is transformed by the same rotations as the geometry
            cull = -1
            if cull_name in FACE_DIR:
                cv = np.asarray(FACE_DIR[cull_name], dtype=np.float64)
                if l_m is not None:
                    cv = l_m @ cv
                if g_m is not None:
                    cv = g_m @ cv
                snapped = _snap_face(cv)
                cull = FACE_INDEX[snapped] if snapped else -1

            out.append(Quad(
                pos=pts.astype(np.float32),
                uv=uvs.astype(np.float32),
                normal=n.astype(np.float32),
                tex=tex,
                cull=cull,
                tint=tint,
                shade=shade,
                face=world_face or face_name,
                source=src,
            ))

    def bake_model(self, model: dict, variant: dict, src: str) -> tuple[list[Quad], bool]:
        """Bake one resolved model into quads. Returns (quads, ambientocclusion)."""
        out: list[Quad] = []
        elements = model.get("elements")
        if not elements:
            return out, bool(model.get("ambientocclusion", True))
        textures = model.get("textures") or {}
        g_m, g_shift = self._variant_transform(variant)
        for el in elements:
            self._bake_element(el, textures, g_m, g_shift, out, src)
        return out, bool(model.get("ambientocclusion", True))

    # ------------------------------------------------------------ full block
    @staticmethod
    def _detect_full_cube(quads: list[Quad]) -> bool:
        """恰好 6 个整方块面（严格版：用于整方块快速路径）。"""
        return len(ModelResolver._full_cube_faces(quads)) == 6 and len(quads) == 6

    @staticmethod
    def _full_cube_faces(quads: list[Quad]) -> list[Quad]:
        """挑出「方块体」的 6 个整面，**允许模型另有 overlay 等附加面**。

        草方块就是典型：6 个整面（dirt/side/top）+ 4 个侧面 overlay。
        旧实现要求 quads == 6，于是草方块既不算遮挡（邻面不剔除）也不吃 AO ——
        一块草地上每格都会画出全部 6 面，还更平。这里按面识别方块体，
        overlay 之类附加面不影响 ``cube_textures``。
        """
        seen: dict[str, Quad] = {}
        for q in quads:
            if q.cull < 0:
                continue
            d = FACES[q.cull]
            if d in seen:
                continue
            axis = {"down": 1, "up": 1, "north": 2, "south": 2, "west": 0, "east": 0}[d]
            val = 0.0 if d in ("down", "north", "west") else 1.0
            # every corner must sit on the correct face plane ...
            if not np.allclose(q.pos[:, axis], val, atol=1e-4):
                continue
            # ... and the quad must span the whole face in the other two axes
            inplane = [a for a in (0, 1, 2) if a != axis]
            lo, hi = q.bbox()
            if np.abs(lo[inplane]).max() > 1e-4 or np.abs(hi[inplane] - 1.0).max() > 1e-4:
                continue
            seen[d] = q
        return [seen[d] for d in FACES if d in seen] if len(seen) == 6 else []

    # --------------------------------------------------------------- public
    def resolve_block(self, name: str, props: dict | None = None) -> BakedBlock:
        """Resolve one block state (``name`` + ``Properties``) into geometry."""
        name = _norm_ref(name)
        props = {str(k): (str(v).lower() if isinstance(v, bool) else str(v))
                 for k, v in (props or {}).items()}
        key = (name, tuple(sorted(props.items())))
        if key in self._block_cache:
            return self._block_cache[key]

        bb = BakedBlock(name=name, props=dict(props))
        short = name

        if str((props or {}).get("waterlogged", "")).lower() == "true" or name in WATERLIKE:
            bb.waterlogged = True
        if name in INVISIBLE_BLOCKS or name.endswith("_air"):
            bb.render = "invisible"
            self._block_cache[key] = bb
            return bb

        # merge defaults for properties the palette may omit
        defaults = self.defaults(name)
        if defaults:
            merged = dict(defaults)
            merged.update(props)
            props = merged
            bb.props = dict(props)

        if name in LIQUIDS:
            # 流体不用静态几何：高度/流动面/角点要按邻居算（见 renderer.build_scene 的流体通道）
            bb.liquid = name
            bb.textures = {LIQUIDS[name], LIQUID_FLOW[name]}
            bb.ao = True
            bb.notes.append("liquid")
            self._block_cache[key] = bb
            return bb

        bs = self.assets.blockstate(name)
        if bs is not None:
            variants = self.select_variants(bs, props)
            ao = True
            for variant in variants:
                ref = _norm_ref(variant["model"])
                model = self.load_model(ref)
                if model.get("_builtin"):
                    bb.builtin = model["_builtin"]
                    continue
                if model.get("_missing"):
                    continue
                quads, vao = self.bake_model(model, variant, ref)
                bb.quads.extend(quads)
                ao = ao and vao
            if bb.quads:
                bb.ao = ao
                cube = self._full_cube_faces(bb.quads)
                bb.is_full_cube = len(cube) == 6 and len(bb.quads) == 6
                bb.cube_textures = {q.tex for q in cube}
                bb.textures = {q.tex for q in bb.quads}
                bb.bbox = self._union_bbox(bb.quads)
                self._block_cache[key] = bb
                return bb

        # ---- fallback 1: model with the same name as the block
        model = self.load_model("block/%s" % name)
        if model.get("elements"):
            quads, vao = self.bake_model(model, {}, "block/%s" % name)
            if quads:
                bb.quads = quads
                bb.ao = vao
                cube = self._full_cube_faces(quads)
                bb.is_full_cube = len(cube) == 6 and len(quads) == 6
                bb.cube_textures = {q.tex for q in cube}
                bb.textures = {q.tex for q in quads}
                bb.bbox = self._union_bbox(quads)
                bb.notes.append("model-by-name fallback")
                self._block_cache[key] = bb
                return bb

        # ---- fallback 2: 方块实体几何（箱子/告示牌/旗帜/头颅/潜影盒/罐/钟/导管…）
        #      这些方块在资源包里没有模型（几何在游戏代码里），表由 deepslate 导出
        for key in ("minecraft:%s%s" % (name, "[" + ",".join(
                "%s=%s" % kv for kv in sorted(bb.props.items())) + "]"
                if bb.props else ""),):
            try:
                from mcrender.entity_models import lookup  # noqa: PLC0415
                ent = lookup(key)
            except Exception:  # noqa: BLE001
                ent = None
            if ent:
                bb.quads = list(ent)
                bb.is_full_cube = False
                bb.occludes = False
                bb.textures = {q.tex for q in bb.quads}
                bb.bbox = self._union_bbox(bb.quads)
                if any(t in (LIQUIDS["water"], LIQUID_FLOW["water"])
                       for t in bb.textures):
                    bb.waterlogged = False      # 表里已经把水画进去了，别再来一层
                bb.notes.append("entity-model")
                self._block_cache[key] = bb
                return bb
        # 兜底：导出表里没写全的（比如别人的名字带命名空间）再按方块名找一次
        try:
            from mcrender.entity_models import lookup  # noqa: PLC0415
            ent = lookup("minecraft:%s" % name)
        except Exception:  # noqa: BLE001
            ent = None
        if ent:
            bb.quads = list(ent)
            bb.is_full_cube = False
            bb.occludes = False
            bb.textures = {q.tex for q in bb.quads}
            bb.bbox = self._union_bbox(bb.quads)
            if any(t in (LIQUIDS["water"], LIQUID_FLOW["water"])
                   for t in bb.textures):
                bb.waterlogged = False
            bb.notes.append("entity-model")
            self._block_cache[key] = bb
            return bb

        # ---- fallback 3: direct texture with the block's name
        for cand in ("block/%s" % name, "block/%s_top" % name, "block/%s_side" % name):
            if self.assets.texture_exists(cand):
                bb.quads = self._cube_quads(cand)
                bb.is_full_cube = True
                bb.cube_textures = {cand}
                bb.occludes = False
                bb.textures = {cand}
                bb.notes.append("texture fallback")
                self._block_cache[key] = bb
                return bb

        bb.missing = True
        bb.render = "fallback"
        self._block_cache[key] = bb
        self._warn("no model for %s%s" % (name, props or ""))
        return bb

    @staticmethod
    def _union_bbox(quads: list[Quad]):
        lo = np.min([q.bbox()[0] for q in quads], axis=0)
        hi = np.max([q.bbox()[1] for q in quads], axis=0)
        return (lo.astype(np.float32), hi.astype(np.float32))

    @staticmethod
    def _cube_quads(tex: str) -> list[Quad]:
        """A plain full cube with one texture on all faces."""
        out = []
        for face in FACES:
            pts = np.empty((4, 3), dtype=np.float32)
            uvs = np.empty((4, 2), dtype=np.float32)
            for i, (xs, ys, zs, us, vs) in enumerate(FACE_CORNERS[face]):
                pts[i] = (xs, ys, zs)
                uvs[i] = (us, vs)
            out.append(Quad(pos=pts, uv=uvs,
                            normal=np.asarray(FACE_DIR[face], dtype=np.float32),
                            tex=tex, cull=FACE_INDEX[face], tint=-1, shade=True,
                            face=face, source="fallback"))
        return out

    # ------------------------------------------------------------- helpers
    def all_textures(self, baked: list[BakedBlock]) -> set[str]:
        out: set[str] = set()
        for b in baked:
            out |= b.textures
        return out


# ---------------------------------------------------------------------------
# CLI: dump the geometry of one block state (debugging / verification)
# ---------------------------------------------------------------------------

def _main(argv=None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Dump baked geometry for a block state")
    ap.add_argument("block", help="e.g. oak_stairs")
    ap.add_argument("props", nargs="*", help="key=value ...")
    ap.add_argument("--data-version", type=int, default=4903)
    ap.add_argument("--version")
    ap.add_argument("--cache")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    from mcrender.assets import auto_assets
    assets = auto_assets(a.data_version, a.version, a.cache)
    res = ModelResolver(assets)
    props = dict(p.split("=", 1) for p in a.props)
    bb = res.resolve_block(a.block, props)
    print("block      :", bb.name, bb.props)
    print("render     :", bb.render, "full_cube:", bb.is_full_cube,
          "quads:", len(bb.quads), "builtin:", bb.builtin, "notes:", bb.notes)
    print("textures   :", sorted(bb.textures))
    if bb.bbox:
        print("bbox       :", bb.bbox[0].tolist(), "->", bb.bbox[1].tolist())
    if a.json:
        print(json.dumps([{
            "tex": q.tex, "cull": q.cull, "tint": q.tint, "face": q.face,
            "pos": np.round(q.pos, 4).tolist(), "uv": np.round(q.uv, 4).tolist(),
            "normal": np.round(q.normal, 3).tolist(),
        } for q in bb.quads], indent=1))
    else:
        for q in bb.quads:
            lo, hi = q.bbox()
            print("  %-6s %-28s cull=%-5s tint=%2d  [%s] -> [%s]" % (
                q.face, q.tex, FACES[q.cull] if q.cull >= 0 else "-", q.tint,
                np.round(lo, 3).tolist(), np.round(hi, 3).tolist()))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
