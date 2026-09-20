"""Accurate structure (.schem/.schem) -> PNG renderer (real block models + real textures).

Unlike a flat voxel renderer, this bakes each palette entry through
:mod:`mcmodel` (blockstates -> models -> element boxes) and rasterises the
resulting textured quads with a numba software rasteriser:

* orthographic *or* perspective camera, any azimuth / elevation;
* per-face textures sampled from a packed atlas (vanilla 16x16 textures);
* cutout (leaves, bars, panes, fences) and translucent (glass, water, ice)
  render layers, blended in a depth-sorted second pass;
* per-vertex ambient occlusion for full cubes;
* block tinting (grass / foliage / water / redstone), emissive blocks + bloom;
* supersampling (SSAA) for clean edges.

Usage::

    python -m mcrender.cli build.schem --views iso,front,top
    python -m mcrender.cli file.schem --azimuth 35 --elevation 25 --scale 3
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numba import njit
from PIL import Image, ImageFilter

from mcrender.assets import Assets
from mcrender.model import (
    CUTOUT_HINT_BLOCKS,
    FACES,
    LIQUID_FLOW,
    LIQUIDS,
    FACE_CORNERS,
    FACE_DIR,
    FACE_SHADE,
    FULLBRIGHT_BLOCKS,
    TRANSLUCENT_BLOCKS,
    ModelResolver,
    Quad,
)

TILE = 16
AO_LEVELS = np.array([0.45, 0.62, 0.80, 1.00], dtype=np.float32)


# ---------------------------------------------------------------------------
# texture atlas
# ---------------------------------------------------------------------------

def _decode_texture(data: bytes) -> np.ndarray | None:
    """PNG bytes -> (16,16,4) uint8, first frame of animated strips."""
    import io

    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    w, h = img.size
    if h > w and h % w == 0:
        img = img.crop((0, 0, w, w))
    if img.size != (TILE, TILE):
        img = img.resize((TILE, TILE), Image.NEAREST)
    return np.asarray(img, dtype=np.uint8)


def _missing_tile() -> np.ndarray:
    a = np.zeros((TILE, TILE, 4), dtype=np.uint8)
    a[..., 0] = 255
    a[..., 3] = 255
    a[::4, :, 1] = 255
    a[:, ::4, 1] = 255
    return a


@dataclass
class TextureInfo:
    tile: int = 0
    has_zero: bool = False
    has_partial: bool = False
    missing: bool = False


class TextureAtlas:
    """Packs all needed block textures into one square RGBA atlas."""

    def __init__(self, refs, assets: Assets, verbose: bool = True):
        self.refs: list[str] = sorted(set(refs))
        n = max(1, len(self.refs))
        self.cols = int(math.ceil(math.sqrt(n)))
        self.rows = int(math.ceil(n / self.cols))
        self.size = self.cols * TILE
        self.pixels = np.zeros((self.size, self.size, 4), dtype=np.uint8)
        self.info: dict[str, TextureInfo] = {}
        missing = 0
        for i, ref in enumerate(self.refs):
            data = assets.texture(ref)
            arr = _decode_texture(data) if data else None
            if arr is None:
                arr = _missing_tile()
                missing += 1
            self.info[ref] = TextureInfo(
                tile=i,
                has_zero=bool((arr[..., 3] == 0).any()),
                has_partial=bool(((arr[..., 3] > 0) & (arr[..., 3] < 255)).any()),
                missing=data is None,
            )
            cx = (i % self.cols) * TILE
            cy = (i // self.cols) * TILE
            self.pixels[cy:cy + TILE, cx:cx + TILE] = arr
        self._solid: dict[tuple, int] = {}
        if verbose:
            print("  atlas: %d textures%s" % (len(self.refs), (", %d missing (magenta)" % missing) if missing else ""))

    def add_solid(self, rgb) -> int:
        key = tuple(int(c) for c in rgb)
        if key in self._solid:
            return self._solid[key]
        i = len(self.refs)
        if i + 1 > self.cols * self.rows:
            self._grow()
        name = "__solid_%d_%d_%d" % key
        self.refs.append(name)
        self.info[name] = TextureInfo(tile=i, has_zero=False, has_partial=False)
        cx = (i % self.cols) * TILE
        cy = (i // self.cols) * TILE
        self.pixels[cy:cy + TILE, cx:cx + TILE, 0] = key[0]
        self.pixels[cy:cy + TILE, cx:cx + TILE, 1] = key[1]
        self.pixels[cy:cy + TILE, cx:cx + TILE, 2] = key[2]
        self.pixels[cy:cy + TILE, cx:cx + TILE, 3] = 255
        self._solid[key] = i
        return i

    def _grow(self) -> None:
        old_cols = self.cols
        old = self.pixels
        self.cols += 1
        self.rows = int(math.ceil((len(self.refs) + 1) / self.cols))
        new_size = self.cols * TILE
        if new_size == self.size:
            return
        new = np.zeros((new_size, new_size, 4), dtype=np.uint8)
        # re-pack existing tiles into the wider grid
        for i in range(len(self.refs)):
            ox = (i % old_cols) * TILE
            oy = (i // old_cols) * TILE
            nx = (i % self.cols) * TILE
            ny = (i // self.cols) * TILE
            new[ny:ny + TILE, nx:nx + TILE] = old[oy:oy + TILE, ox:ox + TILE]
        self.pixels = new
        self.size = new_size

    def layer_of(self, ref: str) -> int:
        info = self.info.get(ref)
        if info is None:
            return 0
        if info.has_partial:
            return 2
        if info.has_zero:
            return 1
        return 0

    def tile_of(self, ref: str) -> int:
        info = self.info.get(ref)
        return info.tile if info else 0


def layer_for(name: str, tex: str, atlas: TextureAtlas) -> int:
    l = atlas.layer_of(tex)
    if name in TRANSLUCENT_BLOCKS:
        l = max(l, 2)
    elif name in CUTOUT_HINT_BLOCKS:
        l = max(l, 1)
    return l


# ---------------------------------------------------------------------------
# tint
# ---------------------------------------------------------------------------

def _rgb(hexval: int):
    return ((hexval >> 16) & 255, (hexval >> 8) & 255, hexval & 255)


GRASS = _rgb(0x79C05A)
FOLIAGE = _rgb(0x59AE30)
WATER = _rgb(0x3F76E4)
LEAF_FIXED = {"spruce_leaves": _rgb(0x619961), "birch_leaves": _rgb(0x80A755)}
TINT_GRASS = {
    "grass_block", "grass", "fern", "tall_grass", "large_fern", "potted_fern",
    "sugar_cane", "vine", "lily_pad", "attached_melon_stem",
    "attached_pumpkin_stem", "melon_stem", "pumpkin_stem", "cactus", "bush",
}
TINT_FOLIAGE = {
    "oak_leaves", "jungle_leaves", "acacia_leaves", "dark_oak_leaves",
    "mangrove_leaves", "azalea_leaves", "flowering_azalea_leaves",
    "pale_oak_leaves", "azalea", "flowering_azalea", "mangrove_roots",
    "mangrove_propagule",
}


def tint_color(block: str, tintindex: int, props: dict | None = None):
    if tintindex < 0:
        return (255, 255, 255)
    if block == "redstone_wire":
        power = int((props or {}).get("power", 0))
        t = power / 15.0
        return (int(0x30 + (0xFF - 0x30) * t), 0, 0)
    if block in ("water", "bubble_column", "water_cauldron"):
        return WATER
    if block in LEAF_FIXED:
        return LEAF_FIXED[block]
    if block in TINT_FOLIAGE or block.endswith("_leaves"):
        return FOLIAGE
    if block in TINT_GRASS or block.endswith("_grass"):
        return GRASS
    return (255, 255, 255)


# ---------------------------------------------------------------------------
# scene
# ---------------------------------------------------------------------------

@dataclass
class Scene:
    pos: np.ndarray        # (N,4,3) float32 world
    uv: np.ndarray         # (N,4,2) float32 texture space
    normal: np.ndarray     # (N,3) float32
    shade: np.ndarray      # (N,4) float32
    tex: np.ndarray        # (N,) int32 atlas tile
    tint: np.ndarray       # (N,3) uint8
    layer: np.ndarray      # (N,) int8
    glow: np.ndarray       # (N,) uint8
    flip: np.ndarray       # (N,) bool

    def __len__(self):
        return int(self.pos.shape[0])


EMPTY = Scene(
    np.zeros((0, 4, 3), np.float32), np.zeros((0, 4, 2), np.float32),
    np.zeros((0, 3), np.float32), np.zeros((0, 4), np.float32),
    np.zeros(0, np.int32), np.zeros((0, 3), np.uint8),
    np.zeros(0, np.int8), np.zeros(0, np.uint8), np.zeros(0, bool),
)


def concat(parts) -> Scene:
    parts = [p for p in parts if len(p)]
    if not parts:
        return EMPTY
    if len(parts) == 1:
        return parts[0]
    cat = lambda k: np.concatenate([getattr(p, k) for p in parts])  # noqa: E731
    return Scene(cat("pos"), cat("uv"), cat("normal"), cat("shade"), cat("tex"),
                 cat("tint"), cat("layer"), cat("glow"), cat("flip"))


def _shift_axis_index(d):
    """FACE_DIR (x,y,z) -> (dy,dz,dx) for a (y,z,x) voxel array."""
    return d[1], d[2], d[0]


def _neighbor(arr: np.ndarray, ys, zs, xs, d):
    """Gather arr[y+dy, z+dz, x+dx]; out of bounds -> 0/False."""
    dy, dz, dx = _shift_axis_index(d)
    ny, nz, nx = arr.shape
    y2 = ys + dy
    z2 = zs + dz
    x2 = xs + dx
    ok = (y2 >= 0) & (y2 < ny) & (z2 >= 0) & (z2 < nz) & (x2 >= 0) & (x2 < nx)
    out = np.zeros(len(ys), dtype=arr.dtype)
    if ok.any():
        out[ok] = arr[y2[ok], z2[ok], x2[ok]]
    return out


def _ao_shade(occp, ys, zs, xs, quad: Quad, base: float):
    """Per-vertex AO for an axis-aligned full-cube face -> (shade (N,4), flip)."""
    d = np.asarray(FACE_DIR[quad.face], dtype=np.int64)
    axis = int(np.argmax(np.abs(d)))
    inplane = [a for a in (0, 1, 2) if a != axis]
    iy = ys + 1
    iz = zs + 1
    ix = xs + 1
    n = len(ys)
    ao = np.zeros((n, 4), dtype=np.int8)
    for k in range(4):
        sel = quad.pos[k]
        s1 = 1 if sel[inplane[0]] > 0.5 else -1
        s2 = 1 if sel[inplane[1]] > 0.5 else -1
        o1 = d.copy(); o1[inplane[0]] += s1
        o2 = d.copy(); o2[inplane[1]] += s2
        oc = d.copy(); oc[inplane[0]] += s1; oc[inplane[1]] += s2
        a1 = occp[iy + o1[1], iz + o1[2], ix + o1[0]]
        a2 = occp[iy + o2[1], iz + o2[2], ix + o2[0]]
        ac = occp[iy + oc[1], iz + oc[2], ix + oc[0]]
        ao[:, k] = np.where(a1 & a2, 0, 3 - (a1.astype(np.int8) + a2.astype(np.int8) + ac.astype(np.int8)))
    shade = (base * AO_LEVELS[ao]).astype(np.float32)
    flip = (ao[:, 0].astype(np.int16) + ao[:, 3] >= ao[:, 1] + ao[:, 2])
    return shade, flip


def _emit_fast(bb, qbf, ys, zs, xs, occ, occp, vox, pid, atlas, emissive, fullbright):
    """Vectorised full-cube faces for one palette entry."""
    out = []
    for face in FACES:
        q = qbf.get(face)
        if q is None:
            continue
        keep = ~_neighbor(occ, ys, zs, xs, FACE_DIR[face])
        if bb.cull_same:
            keep &= (_neighbor(vox, ys, zs, xs, FACE_DIR[face]) != pid)
        if not keep.any():
            continue
        yy, zz, xx = ys[keep], zs[keep], xs[keep]
        m = len(yy)
        pos = np.empty((m, 4, 3), dtype=np.float32)
        pos[..., 0] = xx[:, None] + q.pos[None, :, 0]
        pos[..., 1] = yy[:, None] + q.pos[None, :, 1]
        pos[..., 2] = zz[:, None] + q.pos[None, :, 2]
        uv = np.broadcast_to(q.uv[None], (m, 4, 2)).astype(np.float32)
        normal = np.broadcast_to(q.normal[None], (m, 3)).astype(np.float32)
        base = 1.0 if (not q.shade or fullbright) else float(FACE_SHADE.get(q.face, 0.8))
        if bb.ao and not fullbright:
            shade, flip = _ao_shade(occp, yy, zz, xx, q, base)
        else:
            shade = np.full((m, 4), base, dtype=np.float32)
            flip = np.zeros(m, dtype=bool)
        tint = np.broadcast_to(np.asarray(tint_color(bb.name, q.tint, bb.props), np.uint8)[None], (m, 3))
        layer = np.full(m, layer_for(bb.name, q.tex, atlas), dtype=np.int8)
        glow = np.full(m, 255 if fullbright else (180 if emissive else 0), dtype=np.uint8)
        out.append(Scene(pos, uv, normal, shade,
                         np.full(m, atlas.tile_of(q.tex), np.int32),
                         tint.copy(), layer, glow, flip))
    return out


def _emit_general(bb, bpos, occ, occp, vox, pid, atlas, emissive, fullbright) -> Scene:
    """Expand a non-full block into quads (vectorised over block positions)."""
    quads = bb.quads
    m = len(bpos)
    if m == 0 or not quads:
        return EMPTY
    qpos = np.stack([q.pos for q in quads]).astype(np.float32)
    quv = np.stack([q.uv for q in quads]).astype(np.float32)
    qnrm = np.stack([q.normal for q in quads]).astype(np.float32)
    qtex = np.array([atlas.tile_of(q.tex) for q in quads], np.int32)
    qtint = np.array([tint_color(bb.name, q.tint, bb.props) for q in quads], np.uint8)
    qbase = np.array([1.0 if (not q.shade or fullbright) else float(FACE_SHADE.get(q.face, 0.8))
                      for q in quads], np.float32)
    qlayer = np.array([layer_for(bb.name, q.tex, atlas) for q in quads], np.int8)
    qglow = np.full(len(quads), 255 if fullbright else (180 if emissive else 0), np.uint8)
    qcull = np.array([q.cull for q in quads], np.int8)

    keep = np.ones((m, len(quads)), dtype=bool)
    for qi, q in enumerate(quads):
        if q.cull < 0:
            continue
        keep[:, qi] = ~_neighbor(occ, bpos[:, 0], bpos[:, 1], bpos[:, 2], FACE_DIR[FACES[q.cull]])
    if bb.cull_same:
        for qi, q in enumerate(quads):
            if q.cull < 0:
                continue
            nb = _neighbor(vox, bpos[:, 0], bpos[:, 1], bpos[:, 2], FACE_DIR[FACES[q.cull]])
            keep[:, qi] &= (nb != pid)

    bi, qi = np.nonzero(keep)
    total = len(bi)
    if total == 0:
        return EMPTY
    base = bpos[bi]
    pos = np.empty((total, 4, 3), np.float32)
    pos[..., 0] = base[:, None, 2] + qpos[qi, :, 0]
    pos[..., 1] = base[:, None, 0] + qpos[qi, :, 1]
    pos[..., 2] = base[:, None, 1] + qpos[qi, :, 2]
    shade = np.broadcast_to(qbase[qi][:, None], (total, 4)).astype(np.float32)
    return Scene(pos, quv[qi].copy(), qnrm[qi].copy(), shade,
                 qtex[qi].copy(), qtint[qi].copy(), qlayer[qi].copy(),
                 qglow[qi].copy(), np.zeros(total, bool))


def _emit_fallback_cube(mask, occ, tile) -> Scene:
    ys, zs, xs = np.nonzero(mask)
    if len(ys) == 0:
        return EMPTY
    parts = []
    for face in FACES:
        keep = ~_neighbor(occ, ys, zs, xs, FACE_DIR[face])
        if not keep.any():
            continue
        yy, zz, xx = ys[keep], zs[keep], xs[keep]
        m = len(yy)
        p, uv, n = _UNIT_QUAD[face]
        pos = np.empty((m, 4, 3), np.float32)
        pos[..., 0] = xx[:, None] + p[None, :, 0]
        pos[..., 1] = yy[:, None] + p[None, :, 1]
        pos[..., 2] = zz[:, None] + p[None, :, 2]
        parts.append(Scene(pos,
                           np.broadcast_to(uv[None], (m, 4, 2)).astype(np.float32),
                           np.broadcast_to(n[None], (m, 3)).astype(np.float32),
                           np.full((m, 4), FACE_SHADE[face], np.float32),
                           np.full(m, tile, np.int32),
                           np.full((m, 3), 255, np.uint8),
                           np.zeros(m, np.int8), np.zeros(m, np.uint8), np.zeros(m, bool)))
    return concat(parts)


_UNIT_QUAD = {}
for _f in FACES:
    _p = np.array([[xs, ys, zs] for (xs, ys, zs, _, _) in FACE_CORNERS[_f]], dtype=np.float32)
    _uv = np.array([[us, vs] for (_, _, _, us, vs) in FACE_CORNERS[_f]], dtype=np.float32)
    _UNIT_QUAD[_f] = (_p, _uv, np.asarray(FACE_DIR[_f], dtype=np.float32))


def _fallback_color(name: str):
    h = 0
    for c in name:
        h = (h * 131 + ord(c)) & 0xFFFFFF
    return (90 + (h & 0x7F), 90 + ((h >> 8) & 0x7F), 90 + ((h >> 16) & 0x7F))


def _is_fullbright(name: str, props: dict) -> bool:
    if name not in FULLBRIGHT_BLOCKS:
        return False
    if name in ("waxed_copper_bulb", "copper_bulb", "exposed_copper_bulb",
                "weathered_copper_bulb", "oxidized_copper_bulb", "redstone_lamp",
                "cave_vines", "cave_vines_plant"):
        return str(props.get("lit", props.get("berries", "true"))) == "true"
    return True


def _is_emissive(name: str) -> bool:
    return name in ("lantern", "soul_lantern", "sea_lantern", "glowstone",
                    "shroomlight", "ochre_froglight", "pearlescent_froglight",
                    "verdant_froglight", "magma_block", "crying_obsidian",
                    "end_rod", "redstone_torch", "torch", "wall_torch",
                    "soul_torch", "soul_wall_torch")


# ---------------------------------------------------------------------------
# fluid (water / lava): 高度按 level + 角点取最大，侧面用 *_flow
# ---------------------------------------------------------------------------

#: 各流体「格高度」的倒数档（vanilla：源 8/9，流动 level 1..7 → 7/9..1/9，下落 = 1）
_FLUID_MIN_H = 1.0 / 9.0


def _fluid_cells(vox: np.ndarray, baked: list, kind: str,
                 waterlogged: bool = True) -> np.ndarray:
    """该类流体在本图里的格子掩码：液体本身（+ 可选：waterlogged 方块）。

    高度/剔除用**含 waterlogged 的**掩码（水没方块也是水，邻格角点要接上）；
    而“该谁发射几何”要分开：液体格子在通用通道画、水没格子随它那个方块画，
    否则水没处会重复出水。
    """
    mask = np.zeros(vox.shape, dtype=bool)
    for i, bb in enumerate(baked):
        if bb.liquid == kind or (waterlogged and kind == "water" and bb.waterlogged):
            mask |= (vox == i)
    return mask


def _fluid_heights(vox, baked, mask, kind) -> np.ndarray:
    """每格液面高度（0..1）；不是该类流体的格 = 1/9（vanilla 的“薄边”）。"""
    above = np.zeros_like(mask)
    above[:-1, :, :] = mask[1:, :, :]
    h = np.full(mask.shape, _FLUID_MIN_H, dtype=np.float32)
    # 源方块（level 0）：上方还是同类 → 1.0，否则 8/9
    src = np.full(mask.shape, 8.0 / 9.0, dtype=np.float32)
    src = np.where(above, np.float32(1.0), src)
    h = np.where(mask, src, h)
    # 流动方块：level 1..7 → (8-level)/9；level>=8（下落）→ 1.0
    for i, bb in enumerate(baked):
        if bb.liquid != kind:
            continue
        try:
            level = int(str((bb.props or {}).get("level", "0")))
        except ValueError:
            level = 0
        if level <= 0:
            continue
        val = 1.0 if level >= 8 else (8 - level) / 9.0
        h = np.where(vox == i, np.float32(val), h)
    return h


def _corner_heights(h: np.ndarray) -> tuple:
    """四个角点高度 = 该角四周 4 格高度的最大值（与 prismarine-viewer 同法）。"""
    up = np.zeros_like(h)
    up[:, 1:, :] = h[:, :-1, :]           # z-1
    dn = np.zeros_like(h)
    dn[:, :-1, :] = h[:, 1:, :]           # z+1
    lf = np.zeros_like(h)
    lf[:, :, 1:] = h[:, :, :-1]           # x-1
    rt = np.zeros_like(h)
    rt[:, :, :-1] = h[:, :, 1:]           # x+1
    ul = np.zeros_like(h)
    ul[:, 1:, 1:] = h[:, :-1, :-1]        # x-1,z-1
    ur = np.zeros_like(h)
    ur[:, 1:, :-1] = h[:, :-1, 1:]        # x+1,z-1
    dl = np.zeros_like(h)
    dl[:, :-1, 1:] = h[:, 1:, :-1]        # x-1,z+1
    dr = np.zeros_like(h)
    dr[:, :-1, :-1] = h[:, 1:, 1:]        # x+1,z+1
    c00 = np.maximum(np.maximum(ul, up), np.maximum(lf, h))   # (x=0,z=0)
    c10 = np.maximum(np.maximum(up, ur), np.maximum(h, rt))   # (x=1,z=0)
    c01 = np.maximum(np.maximum(lf, h), np.maximum(dl, dn))   # (x=0,z=1)
    c11 = np.maximum(np.maximum(h, rt), np.maximum(dn, dr))   # (x=1,z=1)
    return c00, c10, c01, c11


def _emit_fluid(bb, vox, baked, atlas, kind: str, occ: np.ndarray,
                only_mask=None) -> Scene:
    """把某类流体的可见面展成四边形（顶/底/四个侧面）。

    - 同流体格之间不画面（内部面）；邻居是不透明整方块也不画；
    - 顶/底用 ``*_still``，侧面用 ``*_flow``，且侧面贴图锚在液面上（不拉伸）；
    - 水染群系水色（tint 0），岩浆不染（贴图本身有色）。
    """
    hmask = _fluid_cells(vox, baked, kind)      # 高度/剔除用：含水（含 waterlogged）
    if only_mask is not None:
        mask = hmask & only_mask               # 水没方块：只画它自己那一格
    else:
        mask = hmask & _fluid_cells(vox, baked, kind, waterlogged=False)
    if not mask.any():
        return EMPTY
    h = _fluid_heights(vox, baked, hmask, kind)
    c00, c10, c01, c11 = _corner_heights(h)
    still = atlas.tile_of(LIQUIDS[kind])
    flow = atlas.tile_of(LIQUID_FLOW[kind])
    tint = np.asarray(tint_color(kind, 0, None), dtype=np.uint8)
    layer = 2 if kind == "water" else 0
    glow = 255 if kind == "lava" else 0
    ys, zs, xs = np.nonzero(mask)
    # 按格子取出角点高度（后面按 keep 子集再筛）
    C00 = c00[ys, zs, xs]
    C10 = c10[ys, zs, xs]
    C01 = c01[ys, zs, xs]
    C11 = c11[ys, zs, xs]
    nx, ny, nz = vox.shape[2], vox.shape[0], vox.shape[1]

    def neighbor(m, axis: int, delta: int):
        out = np.zeros(len(ys), dtype=bool)
        idx = [ys, zs, xs]
        shifted = [ys.copy(), zs.copy(), xs.copy()]
        shifted[axis] += delta
        ok = ((shifted[0] >= 0) & (shifted[0] < ny) & (shifted[1] >= 0) &
              (shifted[1] < nz) & (shifted[2] >= 0) & (shifted[2] < nx))
        if ok.any():
            out[ok] = m[shifted[0][ok], shifted[1][ok], shifted[2][ok]]
        return out

    above_occ = neighbor(occ, 0, 1)
    below_occ = neighbor(occ, 0, -1)
    parts: list[Scene] = []
    # ---- 顶面：上方不是同类含水格（不管上方多高：角点差会自然形成台阶）
    keep = ~neighbor(hmask, 0, 1)
    if keep.any():
        parts.append(_fluid_quad(
            xs[keep], ys[keep], zs[keep], still, tint, layer, glow,
            corners=[(0, C00[keep], 0), (1, C10[keep], 0),
                     (1, C11[keep], 1), (0, C01[keep], 1)],
            uv=[(0, 0), (1, 0), (1, 1), (0, 1)], face="up"))
    # ---- 底面：下方不是同类流体且不透明
    keep = ~neighbor(hmask, 0, -1) & ~below_occ
    if keep.any():
        parts.append(_fluid_quad(
            xs[keep], ys[keep], zs[keep], still, tint, layer, glow,
            corners=[(0, 0, 1), (1, 0, 1), (1, 0, 0), (0, 0, 0)],
            uv=[(0, 1), (1, 1), (1, 0), (0, 0)], face="down"))
    # ---- 四个侧面：邻居不是同类流体且不遮挡；v 锚在液面
    sides = (
        ("west", 2, -1, [(0, C00, 0), (0, C01, 1), (0, 0, 1), (0, 0, 0)],
         [(0, 1), (1, 1), (1, 0), (0, 0)], (True, True, False, False)),
        ("east", 2, 1, [(1, C10, 0), (1, 0, 0), (1, 0, 1), (1, C11, 1)],
         [(0, 1), (1, 1), (1, 0), (0, 0)], (True, False, False, True)),
        ("north", 1, -1, [(0, C00, 0), (1, C10, 0), (1, 0, 0), (0, 0, 0)],
         [(0, 1), (1, 1), (1, 0), (0, 0)], (True, True, False, False)),
        ("south", 1, 1, [(0, C01, 1), (0, 0, 1), (1, 0, 1), (1, C11, 1)],
         [(0, 1), (1, 1), (1, 0), (0, 0)], (True, False, False, True)),
    )
    for face, axis, delta, corners, uvs, top_flags in sides:
        keep = ~neighbor(hmask, axis, delta) & ~neighbor(occ, axis, delta)
        if not keep.any():
            continue
        cs, uvs2 = [], []
        for (lx, ly, lz), (tu, tv), is_top in zip(corners, uvs, top_flags):
            if isinstance(ly, np.ndarray):
                cs.append((lx, ly[keep], lz))
            else:
                cs.append((lx, ly, lz))
            if is_top:
                # 上顶点：贴图从底部算起只看到 1-h（不拉伸）
                hh = ly[keep] if isinstance(ly, np.ndarray) else np.float32(ly)
                uvs2.append((tu, 1 - np.asarray(hh)))
            else:
                uvs2.append((tu, 1))
        parts.append(_fluid_quad(
            xs[keep], ys[keep], zs[keep], flow, tint, layer, glow,
            corners=cs, uv=uvs2, face=face))
    return concat(parts)


def build_scene(
    vox: np.ndarray,
    baked: list,
    atlas: TextureAtlas,
    ao: bool = True,
    verbose: bool = True,
) -> Scene:
    """Expand a palette-indexed voxel array into world-space textured quads."""
    occ = np.zeros(vox.shape, dtype=bool)
    for i, bb in enumerate(baked):
        # bb.occludes 由调用方按「方块体自身贴图」判定（草方块带 overlay 也算遮挡）
        if bb.occludes:
            occ |= (vox == i)


def _fluid_quad(xs, ys, zs, tile: int, tint, layer: int, glow: int,
                corners, uv, face: str) -> Scene:
    """把 (x,y,z) 格子数组 + 局部角点（可为标量或数组）组包成 Scene。"""
    m = len(xs)
    if m == 0:
        return EMPTY
    pos = np.empty((m, 4, 3), dtype=np.float32)
    for k, (lx, ly, lz) in enumerate(corners):
        pos[:, k, 0] = xs + (lx if np.isscalar(lx) else lx)
        pos[:, k, 1] = ys + (ly if np.isscalar(ly) else ly)
        pos[:, k, 2] = zs + (lz if np.isscalar(lz) else lz)
    uvarr = np.empty((m, 4, 2), dtype=np.float32)
    for k, (tu, tv) in enumerate(uv):
        uvarr[:, k, 0] = tu if np.isscalar(tu) else tu
        uvarr[:, k, 1] = tv if np.isscalar(tv) else np.asarray(tv)
    normal = np.broadcast_to(np.asarray(FACE_DIR[face], dtype=np.float32),
                             (m, 3)).copy()
    shade = np.full((m, 4), FACE_SHADE[face], dtype=np.float32)
    return Scene(pos, uvarr, normal, shade,
                 np.full(m, tile, np.int32),
                 np.broadcast_to(np.asarray(tint, dtype=np.uint8), (m, 3)).copy(),
                 np.full(m, layer, np.int8), np.full(m, glow, np.uint8),
                 np.zeros(m, bool))


def build_scene(
    vox: np.ndarray,
    baked: list,
    atlas: TextureAtlas,
    ao: bool = True,
    verbose: bool = True,
) -> Scene:
    """Expand a palette-indexed voxel array into world-space textured quads."""
    occ = np.zeros(vox.shape, dtype=bool)
    for i, bb in enumerate(baked):
        # bb.occludes 由调用方按「方块体自身贴图」判定（草方块带 overlay 也算遮挡）
        if bb.occludes:
            occ |= (vox == i)
    occp = np.zeros((vox.shape[0] + 2, vox.shape[1] + 2, vox.shape[2] + 2), dtype=bool)
    occp[1:-1, 1:-1, 1:-1] = occ

    parts: list[Scene] = []
    n_fast = n_gen = n_fluid = 0
    # 流体先画：几何按邻居算（高度/角点/流动面），不赊在静态四边形里
    for kind in ("water", "lava"):
        fs = _emit_fluid(None, vox, baked, atlas, kind, occ)
        if len(fs):
            parts.append(fs)
            n_fluid += len(fs)
    for i, bb in enumerate(baked):
        mask = (vox == i)
        if not mask.any():
            continue
        if bb.render == "invisible":
            continue
        fb = _is_fullbright(bb.name, bb.props)
        em = _is_emissive(bb.name)
        if bb.liquid is not None:
            continue                      # 已在流体通道里画过
        if not bb.quads:
            tile = atlas.add_solid(_fallback_color(bb.name))
            s = _emit_fallback_cube(mask, occ, tile)
            if len(s):
                parts.append(s)
                n_gen += len(s)
            continue
        if bb.is_full_cube and not fb:
            qbf = {q.face: q for q in bb.quads}
            ys, zs, xs = np.nonzero(mask)
            got = _emit_fast(bb, qbf, ys, zs, xs, occ, occp, vox, i, atlas, em, fb)
            if got:
                parts.extend(got)
                n_fast += sum(len(s) for s in got)
            continue
        bpos = np.argwhere(mask)
        s = _emit_general(bb, bpos, occ, occp, vox, i, atlas, em, fb)
        if len(s):
            parts.append(s)
            n_gen += len(s)
        # waterlogged 方块：额外补一层水面（8/9 高，与邻格角点取平）
        if bb.waterlogged:
            fs = _emit_fluid(bb, vox, baked, atlas, "water", occ,
                             only_mask=(vox == i))
            if len(fs):
                parts.append(fs)
                n_fluid += len(fs)
    scene = concat(parts)
    if verbose:
        print("  geometry: %d quads (%d full-cube, %d modelled, %d fluid)"
              % (len(scene), n_fast, n_gen, n_fluid))
    return scene


# ---------------------------------------------------------------------------
# camera
# ---------------------------------------------------------------------------

@dataclass
class Camera:
    azimuth: float = 45.0
    elevation: float = 30.0
    scale: float = 3.0
    proj: str = "ortho"
    center: np.ndarray | None = None
    dist_factor: float = 2.8
    pad: int = 12

    def basis(self):
        az = math.radians(self.azimuth)
        el = math.radians(min(max(self.elevation, -89.5), 89.5))
        d = np.array([math.sin(az) * math.cos(el), -math.sin(el), math.cos(az) * math.cos(el)])
        d = d / (np.linalg.norm(d) + 1e-12)
        if abs(d[1]) > 0.9999:
            right = np.array([1.0, 0.0, 0.0])
            up = np.array([0.0, 0.0, 1.0])
        else:
            right = np.cross(np.array([0.0, 1.0, 0.0]), d)
            right = right / (np.linalg.norm(right) + 1e-12)
            up = np.cross(d, right)
            up = up / (np.linalg.norm(up) + 1e-12)
        return d, right, up


# ---------------------------------------------------------------------------
# rasteriser
# ---------------------------------------------------------------------------

@njit(cache=True, fastmath=True, boundscheck=False)
def _raster(W, H, vxy, vw, vuv, vshade, tex, tint, layer, glow, persp,
            atlas, tile_cols, zbuf, img, gbuf, pass_id):
    ntri = vxy.shape[0]
    tile_px = 16
    for t in range(ntri):
        ax = vxy[t, 0, 0]; ay = vxy[t, 0, 1]
        bx = vxy[t, 1, 0]; by = vxy[t, 1, 1]
        cx = vxy[t, 2, 0]; cy = vxy[t, 2, 1]
        area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if -1e-6 < area < 1e-6:
            continue
        inv_area = 1.0 / area

        minx = int(math.floor(min(ax, bx, cx)))
        maxx = int(math.ceil(max(ax, bx, cx)))
        miny = int(math.floor(min(ay, by, cy)))
        maxy = int(math.ceil(max(ay, by, cy)))
        if minx < 0:
            minx = 0
        if miny < 0:
            miny = 0
        if maxx > W - 1:
            maxx = W - 1
        if maxy > H - 1:
            maxy = H - 1
        if minx > maxx or miny > maxy:
            continue

        w0 = vw[t, 0]; w1 = vw[t, 1]; w2 = vw[t, 2]
        if persp:
            iw0 = 1.0 / w0; iw1 = 1.0 / w1; iw2 = 1.0 / w2
        else:
            iw0 = 1.0; iw1 = 1.0; iw2 = 1.0

        u0 = vuv[t, 0, 0]; v0 = vuv[t, 0, 1]
        u1 = vuv[t, 1, 0]; v1 = vuv[t, 1, 1]
        u2 = vuv[t, 2, 0]; v2 = vuv[t, 2, 1]
        s0 = vshade[t, 0]; s1 = vshade[t, 1]; s2 = vshade[t, 2]
        ti = tex[t]
        tr = tint[t, 0] / 255.0
        tg = tint[t, 1] / 255.0
        tb = tint[t, 2] / 255.0
        lay = layer[t]
        glo = glow[t]
        tx0 = (ti % tile_cols) * tile_px
        ty0 = (ti // tile_cols) * tile_px

        for py in range(miny, maxy + 1):
            fy = py + 0.5
            for px in range(minx, maxx + 1):
                fx = px + 0.5
                e0 = (cx - bx) * (fy - by) - (cy - by) * (fx - bx)
                e1 = (ax - cx) * (fy - cy) - (ay - cy) * (fx - cx)
                e2 = (bx - ax) * (fy - ay) - (by - ay) * (fx - ax)
                l0 = e0 * inv_area
                l1 = e1 * inv_area
                l2 = e2 * inv_area
                if l0 < 0.0 or l1 < 0.0 or l2 < 0.0:
                    continue

                if persp:
                    iw = l0 * iw0 + l1 * iw1 + l2 * iw2
                    if iw <= 0.0:
                        continue
                    depth = 1.0 / iw
                    u = (l0 * u0 * iw0 + l1 * u1 * iw1 + l2 * u2 * iw2) / iw
                    v = (l0 * v0 * iw0 + l1 * v1 * iw1 + l2 * v2 * iw2) / iw
                else:
                    depth = l0 * w0 + l1 * w1 + l2 * w2
                    u = l0 * u0 + l1 * u1 + l2 * u2
                    v = l0 * v0 + l1 * v1 + l2 * v2

                if depth >= zbuf[py, px]:
                    continue

                tu = int(u * tile_px)
                tv = int(v * tile_px)
                if tu < 0:
                    tu = 0
                elif tu > tile_px - 1:
                    tu = tile_px - 1
                if tv < 0:
                    tv = 0
                elif tv > tile_px - 1:
                    tv = tile_px - 1
                sx = tx0 + tu
                sy = ty0 + tv
                a = atlas[sy, sx, 3]

                if pass_id == 0:
                    if a < 128:
                        continue
                    sh = l0 * s0 + l1 * s1 + l2 * s2
                    r = atlas[sy, sx, 0] * sh * tr
                    g = atlas[sy, sx, 1] * sh * tg
                    b = atlas[sy, sx, 2] * sh * tb
                    zbuf[py, px] = depth
                    if r > 255.0:
                        r = 255.0
                    if g > 255.0:
                        g = 255.0
                    if b > 255.0:
                        b = 255.0
                    img[py, px, 0] = int(r)
                    img[py, px, 1] = int(g)
                    img[py, px, 2] = int(b)
                    if glo > gbuf[py, px]:
                        gbuf[py, px] = glo
                else:
                    if a == 0:
                        continue
                    alpha = a / 255.0
                    sh = l0 * s0 + l1 * s1 + l2 * s2
                    r = atlas[sy, sx, 0] * sh * tr
                    g = atlas[sy, sx, 1] * sh * tg
                    b = atlas[sy, sx, 2] * sh * tb
                    ia = 1.0 - alpha
                    img[py, px, 0] = int(img[py, px, 0] * ia + r * alpha)
                    img[py, px, 1] = int(img[py, px, 1] * ia + g * alpha)
                    img[py, px, 2] = int(img[py, px, 2] * ia + b * alpha)
                    gl = int(glo * alpha)
                    if gl > gbuf[py, px]:
                        gbuf[py, px] = gl


def _triangles(scene: Scene, cam: Camera, ssaa: int):
    """Project + backface-cull + triangulate -> raster arrays."""
    if len(scene) == 0:
        return None
    d, right, up = cam.basis()
    pos = scene.pos.reshape(-1, 3)
    lo = pos.min(axis=0)
    hi = pos.max(axis=0)
    center = (lo + hi) * 0.5 if cam.center is None else np.asarray(cam.center, dtype=np.float64)
    radius = float(np.linalg.norm(hi - lo)) * 0.5 + 1e-6

    if cam.proj == "ortho":
        rel = pos - center
        sx = rel @ right
        sy = rel @ up
        depth = rel @ d
        pad = cam.pad * ssaa
        x0, x1 = float(sx.min()), float(sx.max())
        y0, y1 = float(sy.min()), float(sy.max())
        W = int(math.ceil((x1 - x0) * cam.scale * ssaa)) + 2 * pad
        H = int(math.ceil((y1 - y0) * cam.scale * ssaa)) + 2 * pad
        px = (sx - x0) * cam.scale * ssaa + pad
        py = H - ((sy - y0) * cam.scale * ssaa + pad)
        w = depth
        persp = 0
        view = np.broadcast_to(d[None], scene.normal.shape)
    else:
        cam_dist = radius * cam.dist_factor
        cam_pos = center - d * cam_dist
        focal = cam.scale * ssaa * cam_dist
        rel = pos - cam_pos
        w = np.maximum(rel @ d, 1e-4)
        sx = (rel @ right) / w
        sy = (rel @ up) / w
        px = sx * focal
        py = -sy * focal
        pad = cam.pad * ssaa
        W = int(math.ceil(px.max() - px.min())) + 2 * pad
        H = int(math.ceil(py.max() - py.min())) + 2 * pad
        px = px - px.min() + pad
        py = py - py.min() + pad
        persp = 1
        fcenter = scene.pos.mean(axis=1) - cam_pos
        view = fcenter / (np.linalg.norm(fcenter, axis=1, keepdims=True) + 1e-12)

    px = px.reshape(-1, 4)
    py = py.reshape(-1, 4)
    w = w.reshape(-1, 4)

    visible = (scene.normal * view).sum(axis=1) < -1e-6
    idx = np.nonzero(visible)[0]
    if len(idx) == 0:
        return None
    flip = scene.flip[idx]
    # FACE_CORNERS uses vanilla's bow-tie vertex order (0,1,2,3), whose two
    # diagonals are 0-3 and 1-2.  Triangulate accordingly.
    tri = np.empty((len(idx), 2, 3), dtype=np.int64)
    tri[~flip, 0] = (0, 1, 2)
    tri[~flip, 1] = (2, 1, 3)
    tri[flip, 0] = (0, 1, 3)
    tri[flip, 1] = (0, 3, 2)
    tri = tri.reshape(-1, 3)
    q = np.repeat(idx, 2)
    return {
        "W": W, "H": H, "persp": persp,
        "vxy": np.stack([px[q[:, None], tri], py[q[:, None], tri]], axis=-1).astype(np.float32),
        "vw": w[q[:, None], tri].astype(np.float32),
        "vuv": scene.uv[q[:, None], tri].astype(np.float32),
        "vshade": scene.shade[q[:, None], tri].astype(np.float32),
        "tex": scene.tex[q].astype(np.int32),
        "tint": scene.tint[q],
        "layer": scene.layer[q],
        "glow": scene.glow[q],
    }


def _background(W: int, H: int, mode: str) -> np.ndarray:
    """背景填充：transparent / black / white / dark（深灰）/ sky（渐变）。"""
    if mode == "transparent":
        return np.zeros((H, W, 3), dtype=np.uint8)
    if mode == "black":
        return np.zeros((H, W, 3), dtype=np.uint8)
    if mode == "white":
        return np.full((H, W, 3), 255, dtype=np.uint8)
    if mode == "dark":
        return np.full((H, W, 3), 26, dtype=np.uint8)
    top = np.array([202, 222, 245], dtype=np.float64)
    bot = np.array([247, 249, 251], dtype=np.float64)
    t = np.linspace(0, 1, H)[:, None, None]
    return np.repeat((top * (1 - t) + bot * t).astype(np.uint8), W, axis=1)


def render_view(scene: Scene, cam: Camera, atlas: TextureAtlas, ssaa: int = 2,
                background: str = "dark", bloom: float = 0.5, verbose: bool = True):
    r = _triangles(scene, cam, ssaa)
    if r is None:
        return Image.new("RGB", (16, 16), (240, 240, 240))
    W, H = r["W"], r["H"]
    img = _background(W, H, background)
    zbuf = np.full((H, W), 1e30, dtype=np.float32)
    gbuf = np.zeros((H, W), dtype=np.uint8)

    def run(mask, pass_id):
        if not mask.any():
            return
        _raster(W, H,
                np.ascontiguousarray(r["vxy"][mask]), np.ascontiguousarray(r["vw"][mask]),
                np.ascontiguousarray(r["vuv"][mask]), np.ascontiguousarray(r["vshade"][mask]),
                np.ascontiguousarray(r["tex"][mask]), np.ascontiguousarray(r["tint"][mask]),
                np.ascontiguousarray(r["layer"][mask]), np.ascontiguousarray(r["glow"][mask]),
                r["persp"], atlas.pixels, atlas.cols, zbuf, img, gbuf, pass_id)

    opaque = r["layer"] < 2
    run(opaque, 0)
    trans = ~opaque
    if trans.any():
        order = np.argsort(-r["vw"].mean(axis=1))
        perm = order[trans[order]]
        run(perm, 1)

    out = Image.fromarray(img)
    if bloom > 0 and gbuf.any():
        g = Image.fromarray(gbuf, "L").filter(ImageFilter.GaussianBlur(radius=max(1.0, 1.6 * ssaa)))
        garr = np.asarray(g, dtype=np.float32) / 255.0
        base = np.asarray(out, dtype=np.float32)
        out = Image.fromarray(np.clip(base + garr[:, :, None] * (bloom * 90.0), 0, 255).astype(np.uint8))
    if background == "transparent":
        # 覆盖遮罩：光栅没写过的像素（zbuf 未动）才是真透明
        alpha = np.where(zbuf < 1e29, 255, 0).astype(np.uint8)
        out = _with_alpha(out, alpha, ssaa)
    elif ssaa > 1:
        out = out.resize((max(1, W // ssaa), max(1, H // ssaa)), Image.LANCZOS)
    if verbose:
        print("  view az=%.0f el=%.0f %s -> %dx%d" % (cam.azimuth, cam.elevation, cam.proj, out.width, out.height))
    return out


def _with_alpha(out: "Image.Image", alpha: np.ndarray, ssaa: int) -> "Image.Image":
    """把覆盖遮罩变成 RGBA。缩放用预乘 alpha，否则透明边缘会发黑。"""
    W, H = out.width, out.height
    if ssaa <= 1:
        return Image.fromarray(np.dstack([np.asarray(out, np.uint8), alpha]), "RGBA")
    nw, nh = max(1, W // ssaa), max(1, H // ssaa)
    rgb = np.asarray(out, dtype=np.float32)
    af = (alpha.astype(np.float32) / 255.0)[:, :, None]
    prem = Image.fromarray(np.clip(rgb * af, 0, 255).astype(np.uint8))
    prem = np.asarray(prem.resize((nw, nh), Image.LANCZOS), dtype=np.float32)
    al = np.asarray(Image.fromarray(alpha, "L").resize((nw, nh), Image.LANCZOS),
                    dtype=np.float32)
    rgb2 = prem / np.maximum(al / 255.0, 1e-6)[:, :, None]
    return Image.fromarray(np.dstack([np.clip(rgb2, 0, 255).astype(np.uint8),
                                      al.astype(np.uint8)]), "RGBA")
