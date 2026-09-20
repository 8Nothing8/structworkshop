"""Axiom 式工具的实现（参数名尽量与 Axiom 6.x 同名同义）。

每个工具是一个 ``fn(ctx) -> str``：就地修改 ``ctx.vol``（局部子体），返回
一句中文统计。所有读写都经过 ``ctx``（越界自动丢弃），因此调用方只要保证
「工作盒覆盖所有改动」即可安全撤销。

分区：形状与路径 / 绘制与上色 / 形变与雕刻 / 体块运算 / 地形与重力。
"""
from __future__ import annotations

import math

import numpy as np

from mctools import noise as NZ
from mctools import shapes as SH
from mctools.spec import Param, ToolSpec

AIR = "minecraft:air"


# ------------------------------------------------------------------ 通用 helper
def _shift(a: np.ndarray, axis: int, delta: int, fill=0) -> np.ndarray:
    """沿 axis 平移（不环绕，空出来的填 fill）。"""
    out = np.full_like(a, fill)
    n = a.shape[axis]
    d = int(delta)
    if d == 0:
        return a.copy()
    if abs(d) >= n:
        return out
    if axis == 0:
        if d > 0:
            out[d:] = a[:-d]
        else:
            out[:d] = a[-d:]
    elif axis == 1:
        if d > 0:
            out[:, d:] = a[:, :-d]
        else:
            out[:, :d] = a[:, -d:]
    else:
        if d > 0:
            out[:, :, d:] = a[:, :, :-d]
        else:
            out[:, :, :d] = a[:, :, -d:]
    return out


def _neighbors(vol: np.ndarray) -> list:
    return [_shift(vol, ax, d, 0) for ax in (0, 1, 2) for d in (1, -1)]


def _face_count(solid: np.ndarray) -> np.ndarray:
    n = np.zeros(solid.shape, dtype=np.int8)
    for ax in (0, 1, 2):
        n += _shift(solid, ax, 1, False)
        n += _shift(solid, ax, -1, False)
    return n


def _dilate(mask: np.ndarray, times=1) -> np.ndarray:
    out = mask.copy()
    for _ in range(max(1, int(times))):
        m = out.copy()
        for ax in (0, 1, 2):
            m |= _shift(out, ax, 1, False) | _shift(out, ax, -1, False)
        out = m
    return out


def _erode(mask: np.ndarray, times=1) -> np.ndarray:
    out = mask.copy()
    for _ in range(max(1, int(times))):
        m = out.copy()
        for ax in (0, 1, 2):
            m &= _shift(out, ax, 1, False) & _shift(out, ax, -1, False)
        out = m
    return out


def _dir_index(axis: str) -> int:
    return {"x": 2, "y": 0, "z": 1}[axis]


def _neighbor_vote(ctx, mask):
    """在 ``mask`` 为真的格子上，从 6 邻域投票选出现次数最多的方块索引。"""
    vol = ctx.vol
    shifts = _neighbors(vol)
    cand = set()
    for sh in shifts:
        for v in np.unique(sh[mask]):
            v = int(v)
            if v:
                cand.add(v)
    if not cand:
        return None
    best = np.zeros(vol.shape, dtype=np.int8)
    pick = np.zeros(vol.shape, dtype=np.uint16)
    for i in cand:
        c = np.zeros(vol.shape, dtype=np.int8)
        for sh in shifts:
            c += (sh == i)
        upd = (c > best) & mask
        best[upd] = c[upd]
        pick[upd] = i
    return np.where(mask, pick, 0).astype(np.uint16)


def _parse_blocks(entries) -> list:
    """``["stone*3", "andesite"]`` → ``[(state, weight)]``（``*`` 后跟权重）。"""
    out = []
    for e in entries:
        s = str(e).strip()
        if not s:
            continue
        w = 1.0
        head, star, tail = s.rpartition("*")
        if star and head and "]" not in tail and "=" not in tail:
            try:
                w = max(0.0, float(tail))
                s = head
            except ValueError:
                pass
        out.append((s, w))
    return out or [("minecraft:stone", 1.0)]


def _finalize(ctx, pick) -> int:
    """把邻域投票结果写进体素（跳过没投出结果的格子）。

    ``pick`` 可能是 ``None``（空选区）/ 元组（旧调用返回值）/ 标量 —— 都必须安全退化成"不改动"。
    """
    if pick is None:
        return 0
    if isinstance(pick, tuple):          # 兼容 (_pick, _best) 形式的旧调用
        pick = pick[0]
        if pick is None:
            return 0
    pick = np.asarray(pick)
    if pick.ndim == 0:                   # 标量：没有可写区域
        return 0
    good = pick != 0
    n = int(good.sum())
    if n:
        ctx.vol[good] = pick[good]
    return n


def _smooth_core(ctx, mode="stable", strength=1, ratio=0.5,
                 fix_edges=True) -> int:
    """形态学平滑核心（t_smooth / t_rock / t_sculpt 共用）。"""
    total = 0
    for _ in range(max(1, int(strength))):
        solid = ctx.vol != 0
        cnt = _face_count(solid)
        add = np.zeros(ctx.vol.shape, dtype=bool)
        rm = np.zeros(ctx.vol.shape, dtype=bool)
        if mode in ("grow", "stable"):
            add = ctx.sel & ~solid & (cnt >= 4)
        if mode in ("melt", "stable"):
            rm = ctx.sel & solid & (cnt <= 2)
        if fix_edges and add.any():
            # 只在“有支撑”的地方长：下方或四周已有实体（避免凭空出现）
            support = _shift(solid, 0, -1, False) | _shift(solid, 1, 1, False) | \
                _shift(solid, 1, -1, False) | _shift(solid, 2, 1, False) | \
                _shift(solid, 2, -1, False)
            add &= support
        if add.any():
            pick = _neighbor_vote(ctx, add)
            if pick is not None:
                keep = ctx.rng.random(ctx.vol.shape) < ratio
                src = np.where(keep, ctx.vol, pick).astype(np.uint16)
                good = add & (pick != 0)
                ctx.vol[good] = src[good]
                total += int(good.sum())
        if rm.any():
            total += ctx.fill(rm, AIR)
    return total


# ==================================================================== 形状与路径
def t_shape(ctx) -> str:
    """基本体：球/立方/圆柱/锥/环/超椭球… 一次成型。"""
    kind = str(ctx.param("kind", "sphere"))
    c = ctx.param("_center") or ctx.param("at") or [0, 0, 0]
    if isinstance(c, str):
        c = [float(v) for v in c.replace(" ", "").split(",")]
    cx, cy, cz = (float(c[0]) - ctx.origin[0], float(c[1]) - ctx.origin[1],
                  float(c[2]) - ctx.origin[2])
    radius = max(0.5, ctx.p_float("radius", 6.0))
    height = ctx.p_float("height", 0.0) or None
    size = ctx.param("size")
    if isinstance(size, str):
        size = [float(v) for v in size.replace(" ", "").split(",") if v] or None
    Y, Z, X = ctx.grid()
    m = SH.shape_mask(kind, X, Y, Z, center=(cx, cy, cz), radius=radius,
                      height=height, thickness=ctx.p_float("thickness", 0.0),
                      size=size, exponent=ctx.p_float("exponent", 2.0),
                      segments=ctx.p_int("segments", 6),
                      axis=str(ctx.param("axis", "y")),
                      hollow=ctx.p_bool("hollow"))
    mode = str(ctx.param("mode", "add"))
    if mode == "fill":
        n = ctx.fill(m, ctx.block)
    elif mode == "replace":
        n = ctx.fill(m & ctx.solid, ctx.block)
    else:
        n = ctx.add(m, ctx.block)
    return f"{kind} @({cx:.0f},{cy:.0f},{cz:.0f}) → {n:,} 格"


def bbox_shape(params, size, sel_box):
    c = params.get("_center") or params.get("at")
    if not c:
        return None
    if isinstance(c, str):
        c = [float(v) for v in c.replace(" ", "").split(",")]
    sz = params.get("size")
    if isinstance(sz, str):
        sz = [float(v) for v in sz.replace(" ", "").split(",") if v] or None
    return SH.bbox_of(str(params.get("kind") or "sphere"), center=c,
                      radius=float(params.get("radius") or 6.0),
                      height=float(params.get("height") or 0) or None,
                      thickness=float(params.get("thickness") or 0),
                      size=sz, axis=str(params.get("axis") or "y"))


def _curve(points, curve, samples, sag=0.35):
    """把控制点采样成折线：line / bezier / catmull / catenary。"""
    pts = [np.asarray(p, dtype=float) for p in points]
    if len(pts) < 2:
        return np.array(pts) if pts else np.zeros((0, 3))
    samples = max(8, int(samples))
    if curve == "bezier":
        out = []
        n = len(pts) - 1
        for t in np.linspace(0.0, 1.0, samples):
            v = np.zeros(3)
            for i, q in enumerate(pts):
                v += math.comb(n, i) * (1 - t) ** (n - i) * t ** i * q
            out.append(v)
        return np.array(out)
    if curve == "catmull":
        ext = [pts[0] + (pts[0] - pts[1])] + pts + [pts[-1] + (pts[-1] - pts[-2])]
        per = max(4, samples // max(1, len(pts) - 1))
        out = []
        for i in range(1, len(ext) - 2):
            p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
            for t in np.linspace(0.0, 1.0, per, endpoint=False):
                out.append(0.5 * ((2 * p1) + (-p0 + p2) * t +
                                  (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t +
                                  (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
        out.append(pts[-1])
        return np.array(out)
    if curve == "catenary":
        a, b = pts[0], pts[-1]
        span = b - a
        horiz = float(np.hypot(span[0], span[2])) or 1.0
        s = max(0.0, float(sag)) * horiz
        out = []
        for t in np.linspace(0.0, 1.0, samples):
            v = a + span * t
            v = v.copy()
            v[1] -= s * (1.0 - (2.0 * t - 1.0) ** 2)
            out.append(v)
        return np.array(out)
    per = max(2, samples // max(1, len(pts) - 1))
    out = [np.linspace(pts[i], pts[i + 1], per, endpoint=False)
           for i in range(len(pts) - 1)]
    out.append(np.array([pts[-1]]))
    return np.vstack(out)


def _points_of(ctx) -> list:
    pts = ctx.param("points") or []
    if isinstance(pts, str):
        pts = [[float(v) for v in q.split(",")] for q in pts.split(";") if q]
    pts = [[float(v) for v in q] for q in pts if len(q) == 3]
    if not pts:
        c = ctx.param("_center")
        if c:
            pts = [[float(v) for v in c]]
    return pts


def t_path(ctx) -> str:
    """沿曲线铺管道/道路（直线 / 贝塞尔 / Catmull-Rom / 悬链线）。"""
    pts = _points_of(ctx)
    if not pts:
        raise ValueError("路径需要至少一个点（在 3D 里点一下或多点点选）")
    radius = max(0.5, ctx.p_float("radius", 2.5))
    hollow = ctx.p_bool("hollow")
    curve = str(ctx.param("curve", "line"))
    line = _curve(pts, curve, ctx.p_int("samples", max(16, len(pts) * 24)),
                  ctx.p_float("sag", 0.35))
    Y, Z, X = ctx.grid()
    sy, sz, sx = ctx.vol.shape
    m = np.zeros(ctx.vol.shape, dtype=bool)
    inner = np.zeros(ctx.vol.shape, dtype=bool)
    rin = max(0.3, radius - max(0.5, ctx.p_float("wall", 1.0)))
    for q in line:
        lc = (q[0] - ctx.origin[0], q[1] - ctx.origin[1], q[2] - ctx.origin[2])
        if not (-radius - 2 <= lc[0] <= sx + radius + 2 and
                -radius - 2 <= lc[1] <= sy + radius + 2 and
                -radius - 2 <= lc[2] <= sz + radius + 2):
            continue
        m |= SH.shape_mask("sphere", X, Y, Z, center=lc, radius=radius)
        if hollow:
            inner |= SH.shape_mask("sphere", X, Y, Z, center=lc, radius=rin)
    if hollow:
        m &= ~inner
    n = (ctx.fill(m & ctx.sel, ctx.block) if ctx.p_bool("replace")
         else ctx.add(m & ctx.sel, ctx.block))
    if ctx.p_bool("use_stairs_and_slabs") and n:
        k = _stairize(ctx, m, line)
        ctx.note.append(
            f"楼梯/台阶模式：{k:,} 格换成台阶（朝向按路径切线，shape 按相邻台阶）"
            if k else "楼梯/台阶模式：没有对应的台阶方块，已跳过")
    return f"{curve} 路径（{len(line)} 段，r={radius:g}）→ {n:,} 格"


def _path_facings(line, cells) -> list:
    """每个格子取「最近路径采样点处的切线方向」→ Minecraft 水平朝向名。

    ``line`` 是世界坐标采样点（``_curve`` 的输出），``cells`` 是 (n, 3) 世界坐标。
    切线用中心差分（端点退化为单侧差分），按 MC 约定映射：
    +X=east、-X=west、+Z=south、-Z=north；纯竖直切线退回 north。
    """
    from mckit import connect as C
    pts = np.asarray(line, dtype=np.float64).reshape(-1, 3)
    c = np.asarray(cells, dtype=np.float64).reshape(-1, 3)
    if not len(c):
        return []
    if len(pts) == 1:
        tang = np.zeros((1, 3))
    else:
        i = np.arange(len(pts))
        tang = pts[np.minimum(i + 1, len(pts) - 1)] - pts[np.maximum(i - 1, 0)]
    dx, dz = tang[:, 0], tang[:, 2]
    horiz = (np.abs(dx) > 1e-9) | (np.abs(dz) > 1e-9)
    idx = np.where(np.abs(dx) >= np.abs(dz), np.where(dx > 0, 3, 2),
                   np.where(dz > 0, 1, 0)).astype(np.int8)
    idx = np.where(horiz, idx, 0)                 # 纯竖直段：兜底 north
    best = np.full(len(c), np.inf)
    out = np.zeros(len(c), dtype=np.int8)
    for i in range(len(pts)):
        d2 = ((c[:, 0] - pts[i, 0]) ** 2 + (c[:, 1] - pts[i, 1]) ** 2 +
              (c[:, 2] - pts[i, 2]) ** 2)
        hit = d2 < best
        if hit.any():
            best[hit] = d2[hit]
            out[hit] = idx[i]
    return [C.DIRS[int(k)] for k in out]


def _stairize(ctx, mask, line=None) -> int:
    """把外壳里朝上的方块换成台阶，返回改动格数。

    朝向取**路径切线**（``_path_facings``），``shape`` 交给
    ``mckit.connect.stair_shape`` 按相邻台阶的朝向算——转角处的
    ``outer_left/outer_right/inner_*`` 由它推出，不再写死 straight。
    ``line`` 为空时退回 ``north``（老行为）。
    """
    from mccore.schem_io import parse_state, state_str
    from mckit import connect as C

    def bare(n):
        return n.split(":", 1)[-1] if isinstance(n, str) and ":" in n else n

    try:
        base = parse_state(ctx.block)
    except Exception:                                    # noqa: BLE001
        return 0
    name = base.get("Name", "")
    stair = f"{name}_stairs"
    if ctx.idx(stair, create=False) >= 0:
        return 0
    if ctx.idx(f"{name}_slab", create=False) >= 0:
        return 0
    top = mask & (ctx.vol != 0) & ~_shift(ctx.vol != 0, 0, 1, False)
    if not top.any():
        return 0
    ox, oy, oz = ctx.origin
    rows = np.argwhere(top)                               # (y, z, x)
    cells = np.column_stack([rows[:, 2] + ox, rows[:, 0] + oy, rows[:, 1] + oz])
    facings = (_path_facings(line, cells) if line is not None
               else ["north"] * len(cells))
    assigned = {(int(x), int(y), int(z)): f for (x, y, z), f in zip(cells, facings)}

    def at(x, y, z):
        """邻居方块名（connect 约定：不带命名空间）。"""
        i = ctx.get(x - ox, y - oy, z - oz)
        return bare(ctx.id_of(i)) if i else None

    def facing_at(x, y, z):
        """邻居台阶的朝向：本轮已定的优先，否则读已有方块的 facing 属性。"""
        key = (int(x), int(y), int(z))
        if key in assigned:
            return assigned[key]
        i = ctx.get(x - ox, y - oy, z - oz)
        if not i or i >= len(ctx.palette):
            return None
        e = ctx.palette[i]
        props = (e.get("Properties") or {}) if isinstance(e, dict) else {}
        f = props.get("facing")
        return str(f).lower() if f else None

    groups: dict = {}
    for pos, f in assigned.items():
        sh = C.stair_shape(pos, f, at, facing_at=facing_at)
        groups.setdefault((f, sh), []).append(pos)
    n = 0
    for (f, sh), ps in groups.items():
        sub = np.zeros(ctx.vol.shape, dtype=bool)
        for (x, y, z) in ps:
            sub[y - oy, z - oz, x - ox] = True
        n += ctx.fill(sub, state_str({"Name": stair, "Properties": {
            "facing": f, "half": "bottom", "shape": sh,
            "waterlogged": "false"}}))
    return n


def bbox_path(params, size, sel_box):
    pts = params.get("points") or []
    if isinstance(pts, str):
        pts = [[float(v) for v in q.split(",")] for q in pts.split(";") if q]
    if not pts and params.get("_center"):
        pts = [params["_center"]]
    if not pts:
        return sel_box
    r = float(params.get("radius") or 2.5) + 2
    xs = [float(q[0]) for q in pts]
    ys = [float(q[1]) for q in pts]
    zs = [float(q[2]) for q in pts]
    sag = float(params.get("sag") or 0.35) * (
        math.hypot(xs[-1] - xs[0], zs[-1] - zs[0]) or 1.0)
    return (int(min(xs) - r), int(min(ys) - r - sag) - 2, int(min(zs) - r),
            int(max(xs) + r), int(max(ys) + r) + 2, int(max(zs) + r))


# ==================================================================== 绘制与上色
def t_noise_painter(ctx) -> str:
    """按噪声把选区里的方块换成一组方块（按分布分位映射，不会糊成噪点）。"""
    kind = str(ctx.param("noise", "fbm"))
    scale = max(0.5, ctx.p_float("scale", 12.0))
    blocks = _parse_blocks(ctx.blocks_arg("blocks"))
    density = min(1.0, max(0.0, ctx.p_float("probability_density", 1.0)))
    mode = str(ctx.param("mode", "replace"))
    nz = NZ.sample(kind, ctx.world("x"), ctx.world("y"), ctx.world("z"),
                   scale=scale, seed=ctx.p_int("seed", 0),
                   octaves=max(1, ctx.p_int("octaves", 4)),
                   gain=ctx.p_float("gain", 0.5),
                   lacunarity=ctx.p_float("lacunarity", 2.0),
                   warp=ctx.p_float("warp", 0.0),
                   aniso=(ctx.p_float("aniso_x", 1.0),
                          ctx.p_float("aniso_y", 1.0),
                          ctx.p_float("aniso_z", 1.0)))
    m = ctx.sel.copy()
    if mode == "add":
        m &= ~ctx.solid
    elif ctx.p_bool("only_existing", True):
        m &= ctx.solid
    if density < 1.0:
        m &= ctx.rng.random(ctx.vol.shape) < density
    if not m.any():
        return "噪声绘制：选区内没有可绘制格"
    vals = nz[m]
    weights = np.array([w for _, w in blocks], dtype=float)
    weights = weights / max(1e-9, weights.sum())
    edges = np.cumsum(weights)[:-1]
    thr = np.quantile(vals, edges) if edges.size else np.array([])
    band = np.searchsorted(thr, nz, side="right")
    n = 0
    for i, (state, _w) in enumerate(blocks):
        n += ctx.fill(m & (band == i) & (ctx.vol != ctx.idx(state)), state)
    return f"噪声绘制 {kind}（尺度 {scale:g}）→ {n:,} 格 / {len(blocks)} 种"


def t_gradient_painter(ctx) -> str:
    """按位置渐变上色（线性 / 径向 / 球向 / 平面），可加噪声抖动去色带。"""
    shape = str(ctx.param("gradient_shape", "linear"))
    blocks = _parse_blocks(ctx.blocks_arg("blocks"))
    axis = str(ctx.param("axis", "y"))
    dither = max(0.0, ctx.p_float("dither", 0.35))
    if shape == "linear":
        v = ctx.world(axis).astype(np.float64)
        t = (v - float(v.min())) / max(1e-9, float(v.max() - v.min()))
    else:
        c = ctx.param("_center") or [
            (ctx.vol.shape[2] - 1) / 2 + ctx.origin[0],
            (ctx.vol.shape[0] - 1) / 2 + ctx.origin[1],
            (ctx.vol.shape[1] - 1) / 2 + ctx.origin[2]]
        dx = ctx.world("x") - float(c[0])
        dy = ctx.world("y") - float(c[1])
        dz = ctx.world("z") - float(c[2])
        if shape == "spherical":
            d = np.sqrt(dx * dx + dy * dy + dz * dz)
        elif shape == "radial_xz":
            d = np.sqrt(dx * dx + dz * dz)
        else:
            d = np.abs({"x": dx, "y": dy, "z": dz}[axis])
        t = (d - float(d.min())) / max(1e-9, float(d.max() - d.min()))
    if ctx.p_bool("invert"):
        t = 1.0 - t
    if dither > 0:
        t = np.clip(t + (NZ.sample("simplex", ctx.world("x"), ctx.world("y"),
                                   ctx.world("z"), scale=2.0,
                                   seed=ctx.p_int("seed", 7))
                        .astype(np.float64) - 0.5) * dither, 0.0, 1.0)
    n_b = len(blocks)
    band = np.clip((t * n_b).astype(np.int32), 0, n_b - 1)
    m = ctx.sel & ctx.solid if ctx.p_bool("only_existing", True) else ctx.sel
    n = 0
    for i, (state, _w) in enumerate(blocks):
        n += ctx.fill(m & (band == i) & (ctx.vol != ctx.idx(state)), state)
    return f"渐变上色（{shape}）→ {n:,} 格 / {n_b} 档"


def t_painter(ctx) -> str:
    """把选区涂成当前方块，边缘可按概率“虚化”（soft edge）。"""
    chance = min(1.0, max(0.0, ctx.p_float("chance", 1.0)))
    soft = min(1.0, max(0.0, ctx.p_float("soft_edge", 0.0)))
    m = ctx.sel.copy()
    if ctx.p_bool("only_existing", True):
        m &= ctx.solid
    if soft > 0 and m.any():
        edge = m & ~_erode(m, 1)
        drop = edge & (ctx.rng.random(ctx.vol.shape) > (1.0 - soft))
        m &= ~drop
    if chance < 1.0:
        m &= ctx.rng.random(ctx.vol.shape) < chance
    n = ctx.fill(m, ctx.block)
    return f"绘制 {ctx.block} → {n:,} 格"


def t_floodfill(ctx) -> str:
    """从起点做连通填充（同种方块内部）：灌满房间 / 替换连片材质。"""
    c = ctx.param("_center")
    if not c:
        raise ValueError("连通填充需要起点（在 3D 里点一下）")
    x = int(round(float(c[0]) - ctx.origin[0]))
    y = int(round(float(c[1]) - ctx.origin[1]))
    z = int(round(float(c[2]) - ctx.origin[2]))
    sy, sz, sx = ctx.vol.shape
    if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
        raise ValueError("起点在工作区之外")
    target = int(ctx.vol[y, z, x])
    tgt_idx = ctx.idx(ctx.block)
    if target == tgt_idx:
        return "起点已经是目标方块"
    limit = max(1, ctx.p_int("limit", 200000))
    same = (ctx.vol == target) & ctx.sel
    seen = np.zeros(ctx.vol.shape, dtype=bool)
    stack = [(y, z, x)]
    seen[y, z, x] = True
    count = 0
    while stack and count < limit:
        cy, cz, cx = stack.pop()
        count += 1
        for dy, dz, dx in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
                           (0, 0, 1), (0, 0, -1)):
            ny, nz, nx = cy + dy, cz + dz, cx + dx
            if (0 <= ny < sy and 0 <= nz < sz and 0 <= nx < sx and
                    not seen[ny, nz, nx] and same[ny, nz, nx]):
                seen[ny, nz, nx] = True
                stack.append((ny, nz, nx))
    n = ctx.fill(seen, tgt_idx)
    more = "（已达上限）" if stack else ""
    return f"连通填充 {n:,} 格{more}"


# ==================================================================== 形变与雕刻
def t_smooth(ctx) -> str:
    """形态学平滑：grow 只长 / melt 只融 / stable 双向（Axiom SmoothTool）。"""
    mode = str(ctx.param("mode", "stable"))
    strength = max(1, ctx.p_int("strength", 2))
    ratio = min(1.0, max(0.0, ctx.p_float("block_ratio", 0.5)))
    n = _smooth_core(ctx, mode, strength, ratio, ctx.p_bool("fix_edges", True))
    return f"平滑（{mode} ×{strength}）→ 改动 {n:,} 格"


def t_rock(ctx) -> str:
    """噪声位移 + 平滑，把方块堆变自然岩石（Axiom RockTool）。"""
    noisiness = ctx.p_float("noisiness", 0.5)
    radius = max(0.5, ctx.p_float("noise_radius", 6.0))
    seed = ctx.p_int("noise_field_seed", ctx.p_int("seed", 0))
    meld = min(1.0, max(0.0, ctx.p_float("meld_strength", 0.5)))
    smooth_std = max(0, ctx.p_int("smoothing_stddev", 2))
    nz = NZ.sample("fbm", ctx.world("x"), ctx.world("y"), ctx.world("z"),
                   scale=radius, seed=seed, octaves=3, gain=0.55)
    solid = ctx.vol != 0
    if not ctx.sel.any():
        return "岩石化：选区是空的"
    thr = float(np.quantile(nz[ctx.sel], 0.5))
    band = max(0.0, 1.0 - noisiness) * 0.5
    grow = ctx.sel & ~solid & (nz > thr + band) & _dilate(solid, 1)
    eat = ctx.sel & solid & (nz < thr - band)
    n_add = _finalize(ctx, _neighbor_vote(ctx, grow))
    n_rm = ctx.fill(eat, AIR)
    if meld > 0 and n_add:
        # 只留下「有支撑」的新块：(1-meld) 的概率删掉孤立的
        added = grow & (ctx.vol != 0) & (ctx.vol != 0)
        cnt = _face_count(ctx.vol != 0)
        lonely = added & (cnt <= 1) & (ctx.rng.random(ctx.vol.shape) > meld)
        n_rm += ctx.fill(lonely, AIR)
    total_smooth = _smooth_core(ctx, "stable", max(0, smooth_std), 0.7, False) \
        if smooth_std else 0
    return (f"岩石化：+{n_add:,} / -{n_rm:,}（噪声 {radius:g}，"
            f"平滑 {smooth_std} 次改 {total_smooth:,}）")


def t_melt(ctx) -> str:
    """融化：没支撑的方块往下淌（strength 步，stretch 保持连通）。"""
    strength = max(1, ctx.p_int("strength", 4))
    chance = min(1.0, max(0.05, ctx.p_float("chance", 0.6)))
    moved = 0
    for _ in range(strength):
        solid = ctx.vol != 0
        below_air = solid & ~_shift(solid, 0, -1, False)
        # 至少要连着上面一点东西才淌，否则整根掉下去
        attached = (_shift(solid, 0, 1, False) | _shift(solid, 0, 2, False) |
                    _shift(solid, 0, 3, False) | _shift(solid, 0, 4, False))
        m = ctx.sel & below_air & attached & \
            (ctx.rng.random(ctx.vol.shape) < chance)
        if not m.any():
            break
        src = ctx.vol.copy()
        drop = _shift(src, 0, -1, False)
        tgt = _shift(m, 0, -1, False)
        ctx.vol[m] = 0
        ctx.vol[tgt] = drop[tgt]
        moved += int(tgt.sum())
    return f"融化下淌 {moved:,} 格（{strength} 步）"


def t_roughen(ctx) -> str:
    """表面粗糙化：按面积概率增删方块（min_faces 控制只在“正面”动手）。"""
    ratio = min(1.0, max(0.0, ctx.p_float("ratio", 0.4)))
    min_faces = max(1, min(6, ctx.p_int("min_faces", 2)))
    mode = str(ctx.param("mode", "both"))
    solid = ctx.vol != 0
    cnt_solid = _face_count(solid)
    cnt_air = 6 - cnt_solid
    out = 0
    if mode in ("both", "add"):
        add = ctx.sel & ~solid & (cnt_solid >= min_faces) & \
            (ctx.rng.random(ctx.vol.shape) < ratio)
        out += _finalize(ctx, _neighbor_vote(ctx, add))
    if mode in ("both", "remove"):
        rm = ctx.sel & solid & (cnt_air >= min_faces) & \
            (ctx.rng.random(ctx.vol.shape) < ratio)
        out += ctx.fill(rm, AIR)
    return f"粗糙化（ratio={ratio:g}，min_faces={min_faces}）→ 改动 {out:,} 格"


def t_distort(ctx) -> str:
    """噪声向量场位移（Axiom DistortTool）：整块体素沿噪声方向搬走。"""
    dist = (ctx.p_float("distance_x", 3.0), ctx.p_float("distance_y", 1.0),
            ctx.p_float("distance_z", 3.0))
    scale = max(1.0, ctx.p_float("scale", 14.0))
    iters = max(1, ctx.p_int("iterations", 1))
    seed = ctx.p_int("seed", 0)
    sy, sz, sx = ctx.vol.shape
    Y, Z, X = ctx.grid()
    moved = 0
    for it in range(iters):
        ch = [NZ.sample("simplex", ctx.world("x"), ctx.world("y"),
                        ctx.world("z"), scale=scale,
                        seed=seed + 31 * it + k).astype(np.float64) - 0.5
              for k in range(3)]
        tx = np.clip(np.round(X + ch[0] * dist[0]), 0, sx - 1).astype(np.int32)
        ty = np.clip(np.round(Y + ch[1] * dist[1]), 0, sy - 1).astype(np.int32)
        tz = np.clip(np.round(Z + ch[2] * dist[2]), 0, sz - 1).astype(np.int32)
        src = ctx.vol.copy()
        m = ctx.sel & (src != 0) & ((ty != Y) | (tz != Z) | (tx != X))
        out = ctx.vol.copy()
        out[m] = 0
        out[ty[m], tz[m], tx[m]] = src[m]
        ctx.vol[:] = out
        moved = int(m.sum())
    return f"噪声位移 {moved:,} 格（scale={scale:g} × {iters}）"


def t_weld(ctx) -> str:
    """焊接：把缝隙里的空气格按邻域多数方块补上（strength 步）。"""
    strength = max(1, ctx.p_int("strength", 2))
    threshold = max(2, min(6, ctx.p_int("threshold", 4)))
    out = 0
    for _ in range(strength):
        solid = ctx.vol != 0
        gap = ctx.sel & ~solid & (_face_count(solid) >= threshold)
        if not gap.any():
            break
        out += _finalize(ctx, _neighbor_vote(ctx, gap))
    return f"焊接缝隙 {out:,} 格（阈值 {threshold}）"


def t_blend(ctx) -> str:
    """材质混合：接触面的两侧按 warp 概率互相渗透（做过渡色带）。"""
    spread = max(1, ctx.p_int("spread", 2))
    warp = min(1.0, max(0.0, ctx.p_float("warp", 0.5)))
    n = 0
    vol = ctx.vol
    for _ in range(spread):
        if not (vol != 0).any():
            break
        # 每个方向：邻居是「另一种实心方块」→ 按 warp/6 概率染过去
        share = warp / 6.0
        for ax in (0, 1, 2):
            for d in (1, -1):
                nb = _shift(vol, ax, d, 0)
                m = ctx.sel & (vol != 0) & (nb != 0) & (nb != vol) & \
                    (ctx.rng.random(vol.shape) < share)
                if m.any():
                    vol[m] = nb[m]
                    n += int(m.sum())
    return f"材质混合 {n:,} 格（warp={warp:g}）"


def t_shatter(ctx) -> str:
    """破碎：沿轴按 width 切片错位（3d = 逐格噪声位移），可缝隙填实。"""
    axis = str(ctx.param("axis", "y"))
    scale = ctx.p_float("scale", 3.0)
    width = max(1, ctx.p_int("width", 3))
    fill_gaps = ctx.p_bool("use_active_block")
    seed = ctx.p_int("seed", 0)
    sy, sz, sx = ctx.vol.shape
    n_ax = {"x": sx, "y": sy, "z": sz}[axis]
    src = ctx.vol.copy()
    out = src.copy()
    out[ctx.sel] = 0
    n = 0
    if axis == "3d":
        Y, Z, X = ctx.grid()
        nz = NZ.sample("fbm", ctx.world("x"), ctx.world("y"), ctx.world("z"),
                       scale=max(2.0, scale * 2), seed=seed,
                       octaves=2).astype(np.float64)
        ty = np.clip(np.round(Y + (nz - 0.5) * scale * 2), 0,
                     sy - 1).astype(np.int32)
        tz = Z.astype(np.int32)
        tx = X.astype(np.int32)
        m = ctx.sel & (src != 0) & (ty != Y)
        out[ty[m], tz[m], tx[m]] = src[m]
        n = int(m.sum())
    else:
        ax = _dir_index(axis)
        for a0 in range(0, n_ax, width):
            a1 = min(n_ax, a0 + width)
            d = int(round((float(NZ.sample("white", a0 / width, 0, 0,
                                           seed=seed)) - 0.5) * scale * 2))
            if d == 0:
                continue
            dst = np.arange(a0 + d, a1 + d)
            dst = dst[(dst >= 0) & (dst < n_ax)]
            if dst.size == 0:
                continue
            s2 = [slice(None)] * 3
            d2 = [slice(None)] * 3
            s2[ax] = dst - d
            d2[ax] = dst
            block = src[tuple(s2)]
            sm = ctx.sel[tuple(d2)]
            cur = out[tuple(d2)]
            real = sm & (cur != block)
            cur[real] = block[real]
            n += int(real.sum())
    if fill_gaps:
        gap = ctx.sel & (out == 0) & (_face_count(out != 0) >= 2)
        out[gap] = ctx.idx(ctx.block)
    ctx.vol[:] = np.where(ctx.sel, out, src)
    return f"破碎（{axis}，width={width}，scale={scale:g}）→ 位移 {n:,} 格"


def t_sculpt(ctx) -> str:
    """雕刻笔刷：表面沿法线增/减材料（add / remove / smooth）。"""
    mode = str(ctx.param("mode", "add"))
    strength = max(1, ctx.p_int("strength", 1))
    total = 0
    if mode == "smooth":
        total = _smooth_core(ctx, "stable", strength, 1.0, False)
    elif mode == "remove":
        for _ in range(strength):
            m = ctx.sel & (ctx.vol != 0) & (_face_count(ctx.vol != 0) <= 3)
            total += ctx.fill(m, AIR)
    else:
        for _ in range(strength):
            m = ctx.sel & (ctx.vol == 0) & (_face_count(ctx.vol != 0) >= 1)
            total += _finalize(ctx, _neighbor_vote(ctx, m))
    return f"雕刻（{mode} ×{strength}）→ {total:,} 格"


# ==================================================================== 体块运算
def t_fill(ctx) -> str:
    """把选区（∩掩码）填成当前方块。"""
    m = ctx.sel if not ctx.params.get("mask") else ctx.mask_arg("mask")
    if ctx.p_bool("only_air"):
        m = m & (ctx.vol == 0)
    n = ctx.fill(m, ctx.block)
    return f"填充 {ctx.block} → {n:,} 格"


def t_replace(ctx) -> str:
    """替换：把选区里匹配的方块换成当前方块（可保留同名属性）。"""
    m = ctx.sel if not ctx.params.get("mask") else ctx.mask_arg("mask")
    if ctx.param("from"):
        mm = np.zeros(ctx.vol.shape, dtype=bool)
        for st in ctx.blocks_arg("from"):
            if "*" in st:
                mm |= ctx.mask_ctx().name_mask(st)
            else:
                i = ctx.idx(st, create=False)
                if i >= 0:
                    mm |= ctx.vol == i
        m = m & mm
    if ctx.p_bool("only_existing", True):
        m = m & (ctx.vol != 0)
    n = 0
    if ctx.p_bool("keep_props"):
        from mccore.schem_io import parse_state, state_str
        tgt = parse_state(ctx.block)
        tp = dict(tgt.get("Properties") or {})
        for i in np.unique(ctx.vol[m]):
            i = int(i)
            if not i:
                continue
            props = dict(parse_state(ctx.state(i)).get("Properties") or {})
            merged = {k: v for k, v in props.items() if k in tp}
            merged.update(tp)
            st2 = state_str({"Name": tgt["Name"], "Properties": merged})
            n += ctx.fill(m & (ctx.vol == i), st2)
    else:
        n = ctx.fill(m, ctx.block)
    return f"替换 → {n:,} 格"


def t_hollow(ctx) -> str:
    """掏空：保留 thickness 层外壳，其余挖空（Axiom HollowOperation）。"""
    t = max(1, ctx.p_int("thickness", 1))
    solid = (ctx.vol != 0) & ctx.sel
    inner = _erode(solid, t)
    if ctx.p_bool("open_top"):
        top = np.zeros(ctx.vol.shape, dtype=bool)
        top[-1] = True
        inner &= ~top
    n = ctx.fill(inner, AIR)
    return f"掏空（壁厚 {t}）→ 挖掉 {n:,} 格"


def t_grow(ctx) -> str:
    """膨胀（+N）/ 腐蚀（-N）体块，用于加粗构件或瘦身。"""
    amount = ctx.p_int("amount", 1)
    placed = 0
    if amount >= 0:
        for _ in range(max(1, amount)):
            cur = ctx.vol != 0
            reach = _dilate(cur, 1) & ~cur
            m = ctx.sel & reach
            if not m.any():
                break
            pick = _neighbor_vote(ctx, m)
            if pick is not None:
                keep = m & (pick != 0)
                ctx.vol[keep] = pick[keep]
                placed += int(keep.sum())
    else:
        for _ in range(max(1, -amount)):
            cur = ctx.vol != 0
            shell = cur & ~_erode(cur, 1)
            placed += ctx.fill(ctx.sel & shell, AIR)
    return f"体块{'膨胀' if amount >= 0 else '腐蚀'} {abs(amount)} 格 → {placed:,}"


def t_gravity(ctx) -> str:
    """重力：选区内的方块落到底（逐列压实）。"""
    sel = ctx.sel
    sy, sz, sx = ctx.vol.shape
    n = 0
    for z in range(sz):
        for x in range(sx):
            col = np.flatnonzero(sel[:, z, x])
            if col.size == 0:
                continue
            ys = np.flatnonzero(ctx.vol[:, z, x])
            ys = ys[(ys >= col[0]) & (ys <= col[-1])]
            if ys.size == 0:
                continue
            vals = ctx.vol[ys, z, x].copy()
            ctx.vol[ys, z, x] = 0
            y = int(ys[0])
            while y > 0 and ctx.vol[y - 1, z, x] == 0:
                y -= 1
            for v, y0 in zip(vals, ys):
                while y < sy and ctx.vol[y, z, x] != 0:
                    y += 1
                if y >= sy:
                    break
                ctx.vol[y, z, x] = v
                if y != int(y0):
                    n += 1
                y += 1
    return f"重力沉降 {n:,} 格"


def t_drain(ctx) -> str:
    """排液：清掉选区里的水 / 岩浆。"""
    water = np.zeros(ctx.vol.shape, dtype=bool)
    for st in ("minecraft:water", "minecraft:lava", "minecraft:bubble_column",
               "water", "lava"):
        i = ctx.idx(st, create=False)
        if i >= 0:
            water |= ctx.vol == i
    n = ctx.fill(ctx.sel & water, AIR)
    return f"排液 → {n:,} 格"


def t_autoshade(ctx) -> str:
    """按高度/深度自动明暗：高处用浅色方块，低处用深色（做体积感）。"""
    blocks = _parse_blocks(ctx.blocks_arg("shade_blocks"))
    axis = str(ctx.param("axis", "y"))
    invert = ctx.p_bool("invert")
    m = ctx.sel & ctx.solid if ctx.p_bool("only_existing", True) else ctx.sel
    if not m.any():
        return "自动明暗：选区内没有方块"
    v = ctx.world(axis).astype(np.float64)
    lo, hi = float(v[m].min()), float(v[m].max())
    t = (v - lo) / max(1e-9, hi - lo)
    if invert:
        t = 1.0 - t
    n_b = len(blocks)
    band = np.clip((t * n_b).astype(np.int32), 0, n_b - 1)
    n = 0
    for i, (state, _w) in enumerate(blocks):
        n += ctx.fill(m & (band == i) & (ctx.vol != ctx.idx(state)), state)
    return f"自动明暗 → {n:,} 格 / {n_b} 档"


# ==================================================================== 地形
def t_flatten(ctx) -> str:
    """削平 / 填平：把选区表面压到指定高度（世界 Y）。"""
    level = ctx.p_int("level", ctx.origin[1] + ctx.vol.shape[0] // 2)
    mode = str(ctx.param("mode", "flatten"))
    sel = ctx.sel
    sy = ctx.vol.shape[0]
    n = 0
    for z in range(ctx.vol.shape[1]):
        for x in range(ctx.vol.shape[2]):
            col = np.flatnonzero(sel[:, z, x])
            if col.size == 0:
                continue
            nz = np.flatnonzero(ctx.vol[:, z, x])
            nz = nz[(nz >= col[0]) & (nz <= col[-1])]
            top = int(nz[-1]) if nz.size else col[0] - 1
            top_world = top + ctx.origin[1]
            if mode in ("flatten", "both") and top_world > level:
                y0 = max(col[0], level + 1 - ctx.origin[1])
                if top >= y0:
                    ctx.vol[y0:top + 1, z, x] = 0
                    n += top - y0 + 1
            if mode in ("fill", "both") and top_world < level:
                y0 = top + 1
                y1 = min(sy - 1, level - ctx.origin[1])
                if y1 >= y0:
                    ctx.vol[y0:y1 + 1, z, x] = ctx.idx(ctx.block)
                    n += y1 - y0 + 1
    return f"削平 → {n:,} 格（目标 Y={level}）"


def t_slope(ctx) -> str:
    """坡度化：把选区内表面拟合成沿某轴的均匀斜坡（做屋顶 / 坡道）。"""
    axis = str(ctx.param("axis", "x"))
    mode = str(ctx.param("mode", "both"))
    sel = ctx.sel
    sz, sx = ctx.vol.shape[1], ctx.vol.shape[2]
    heights = np.full((sz, sx), -1, dtype=np.int32)
    for z in range(sz):
        for x in range(sx):
            col = np.flatnonzero(sel[:, z, x])
            if col.size == 0:
                continue
            nz = np.flatnonzero(ctx.vol[:, z, x])
            nz = nz[(nz >= col[0]) & (nz <= col[-1])]
            if nz.size:
                heights[z, x] = int(nz[-1])
    if not (heights >= 0).any():
        return "坡度化：选区内没有表面"
    prof = heights.max(axis=0) if axis == "x" else heights.max(axis=1)
    valid = np.flatnonzero(prof >= 0)
    if valid.size < 2:
        return "坡度化：样本不足"
    tgt = np.interp(np.arange(prof.size), valid, prof[valid].astype(float))
    n = 0
    for z in range(sz):
        for x in range(sx):
            if heights[z, x] < 0:
                continue
            t = int(round(float(tgt[x if axis == "x" else z])))
            cur = int(heights[z, x])
            if t > cur and mode in ("both", "fill"):
                ctx.vol[cur + 1:t + 1, z, x] = ctx.idx(ctx.block)
                n += t - cur
            elif t < cur and mode in ("both", "flatten"):
                ctx.vol[t + 1:cur + 1, z, x] = 0
                n += cur - t
    return f"坡度化（{axis}）→ 改动 {n:,} 格"


def t_elevation(ctx) -> str:
    """笔刷抬升 / 压低 / 抹平地形（Axiom ElevationTool）。"""
    mode = str(ctx.param("mode", "raise"))
    amount = max(1, ctx.p_int("amount", 2))
    sel = ctx.sel
    sy, sz, sx = ctx.vol.shape
    n = 0
    for z in range(sz):
        for x in range(sx):
            col = np.flatnonzero(sel[:, z, x])
            if col.size == 0:
                continue
            nz = np.flatnonzero(ctx.vol[:, z, x])
            nz = nz[(nz >= col[0]) & (nz <= col[-1])]
            top = int(nz[-1]) if nz.size else int(col[0]) - 1
            if mode == "lower":
                if top >= int(col[0]):
                    y0 = max(int(col[0]), top - amount + 1)
                    ctx.vol[y0:top + 1, z, x] = 0
                    n += top - y0 + 1
            elif mode == "flatten":
                t = int(col[0]) + amount
                if top > t:
                    ctx.vol[t + 1:top + 1, z, x] = 0
                    n += top - t
                elif top < t:
                    ctx.vol[top + 1:t + 1, z, x] = ctx.idx(ctx.block)
                    n += t - top
            else:
                y0 = top + 1
                y1 = min(sy - 1, top + amount)
                if y1 >= y0:
                    ctx.vol[y0:y1 + 1, z, x] = ctx.idx(ctx.block)
                    n += y1 - y0 + 1
    return f"地形 {mode} → {n:,} 格"


def t_extrude(ctx) -> str:
    """挤出：按每列顶面/底面把形状沿竖直方向复制 amount 层。"""
    amount = ctx.p_int("amount", 3)
    if amount == 0:
        return "amount=0，无改动"
    sel = ctx.sel
    sy, sz, sx = ctx.vol.shape
    n = 0
    for z in range(sz):
        for x in range(sx):
            col = np.flatnonzero(sel[:, z, x])
            if col.size == 0:
                continue
            nz = np.flatnonzero(ctx.vol[:, z, x])
            nz = nz[(nz >= col[0]) & (nz <= col[-1])]
            if nz.size == 0:
                continue
            if amount > 0:
                y = int(nz[-1])
                blk = ctx.vol[y, z, x]
                for k in range(1, amount + 1):
                    yy = y + k
                    if yy >= sy or not sel[yy, z, x]:
                        break
                    ctx.vol[yy, z, x] = blk
                    n += 1
            else:
                y = int(nz[0])
                blk = ctx.vol[y, z, x]
                for k in range(1, -amount + 1):
                    yy = y - k
                    if yy < 0 or not sel[yy, z, x]:
                        break
                    ctx.vol[yy, z, x] = blk
                    n += 1
    return f"挤出 {amount:+d} 层 → {n:,} 格"


def t_stamp(ctx) -> str:
    """盖章：把模块 / 蓝图按概率散布在选区表面（间距 + 随机朝向）。"""
    from mccore import library as LB
    from mccore import structure_io as SIO
    bid = ctx.param("blueprint")
    if not bid:
        raise ValueError("盖章需要选择模块（蓝图）")
    path = LB.module_path(str(bid))
    if path is None:
        raise ValueError(f"没有模块 {bid}")
    d = SIO.read_structure(str(path))
    from mccore.schem_io import state_str
    lut = np.zeros(len(d["palette"]), dtype=np.uint16)
    for i, entry in enumerate(d["palette"]):
        lut[i] = ctx.idx(state_str(entry))
    vox = np.ascontiguousarray(d["voxels"], dtype=np.uint16)
    chance = min(1.0, max(0.0, ctx.p_float("chance", 0.5)))
    spacing = max(1, ctx.p_int("min_spacing", 6))
    random_yaw = ctx.p_bool("random_yaw", True)
    random_flip = ctx.p_bool("random_xz_flip", False)
    sel = ctx.sel
    sy, sz, sx = ctx.vol.shape
    n = 0
    placed = []
    for z in range(0, sz, spacing):
        for x in range(0, sx, spacing):
            col = np.flatnonzero(sel[:, z, x])
            if col.size == 0 or ctx.rng.random() > chance:
                continue
            if any((z - q[1]) ** 2 + (x - q[0]) ** 2 < spacing ** 2
                   for q in placed):
                continue
            nz = np.flatnonzero(ctx.vol[:, z, x])
            nz = nz[(nz >= col[0]) & (nz <= col[-1])]
            if nz.size == 0:
                continue
            top = int(nz[-1])
            v = vox
            if random_yaw:
                k = int(ctx.rng.integers(0, 4))
                if k:
                    v = np.ascontiguousarray(np.rot90(v, k, axes=(1, 2)))
            if random_flip and ctx.rng.random() < 0.5:
                v = np.ascontiguousarray(v[:, :, ::-1])
            vy, vz, vx = v.shape
            # 站在表面上：模块底 = 表面上一格；放不下就往回收，再不行就裁着放
            oy = top + 1
            if oy + vy > sy:
                oy = sy - vy
            if oy < 0:
                oy = 0
            ox = int(np.clip(x - vx // 2, 0, max(0, sx - 1)))
            oz = int(np.clip(z - vz // 2, 0, max(0, sz - 1)))
            vy2 = min(vy, sy - oy)
            vz2 = min(vz, sz - oz)
            vx2 = min(vx, sx - ox)
            if vy2 <= 0 or vz2 <= 0 or vx2 <= 0:
                continue
            sv = v[:vy2, :vz2, :vx2]
            sl = (slice(oy, oy + vy2), slice(oz, oz + vz2),
                  slice(ox, ox + vx2))
            m = (sv != 0) & sel[sl]
            if not m.any():
                continue
            tgt = ctx.vol[sl]
            tgt[m] = lut[sv[m]]
            n += int(m.sum())
            placed.append((x, z))
    return f"盖章 {len(placed)} 处 / {n:,} 格"


# ==================================================================== 注册表
BRUSH_SHAPES = ("sphere", "cube", "cuboid", "cylinder", "cone", "capsule",
                "octahedron", "disk", "point", "supersphere")

SPECS: list[ToolSpec] = [
    # ---------------------------------------------------------- 形状与路径
    ToolSpec(
        id="shape", label="基本体", group="shape", fn=t_shape,
        hint="在点击处（或参数 at）生成球/圆柱/锥/环等；own = 自带几何，不受框选限制",
        region="own", bbox_fn=bbox_shape, needs_block=True, pad=1,
        params=(
            Param("kind", "形状", "enum", "sphere",
                  options=("sphere", "sphere_hollow", "ball", "cube", "cuboid",
                           "cylinder", "cylinder_hollow", "tube", "cone",
                           "pyramid", "torus", "capsule", "octahedron",
                           "supersphere", "ellipsoid", "prism", "disk",
                           "plane", "point"),
                  label_of={"sphere": "球（实心）", "sphere_hollow": "球（空心）",
                            "ball": "球", "cube": "立方", "cuboid": "长方体",
                            "cylinder": "圆柱", "cylinder_hollow": "圆柱（空心）",
                            "tube": "圆管", "cone": "圆锥", "pyramid": "方锥",
                            "torus": "圆环", "capsule": "胶囊",
                            "octahedron": "八面体", "supersphere": "超椭球",
                            "ellipsoid": "椭球", "prism": "棱柱",
                            "disk": "圆盘", "plane": "平面", "point": "单格"}),
            Param("radius", "半径", "float", 6.0, 0.5, 200, 0.5),
            Param("height", "高", "float", 0, 0, 400, 1,
                  hint="0 = 用 2×半径"),
            Param("size", "尺寸 a,b,c", "text", "",
                  hint="长方体/椭球/平面用：10,8,6 或 12,9"),
            Param("thickness", "壁厚/管径", "float", 1.0, 0, 32, 0.5),
            Param("hollow", "空心", "bool", False),
            Param("exponent", "超椭球指数", "float", 2.5, 0.2, 12, 0.1),
            Param("segments", "棱柱边数", "int", 6, 3, 64, 1),
            Param("axis", "轴向", "enum", "y", options=("x", "y", "z")),
            Param("mode", "写入方式", "enum", "add",
                  options=("add", "fill", "replace"),
                  label_of={"add": "只填空气", "fill": "整体覆盖",
                            "replace": "只换已有方块"}),
        )),
    ToolSpec(
        id="path", label="路径 / 管道", group="shape", fn=t_path,
        hint="3D 里连续点几个点 → 曲线铺设；支持悬链线（吊桥/线缆）与贝塞尔",
        region="own", bbox_fn=bbox_path, grabbable=True, needs_block=True,
        params=(
            Param("curve", "曲线", "enum", "line",
                  options=("line", "bezier", "catmull", "catenary"),
                  label_of={"line": "直线折线", "bezier": "贝塞尔",
                            "catmull": "Catmull-Rom（平滑穿过点）",
                            "catenary": "悬链线（下垂）"}),
            Param("radius", "半径", "float", 2.5, 0.5, 64, 0.5),
            Param("hollow", "空心管", "bool", False),
            Param("wall", "管壁厚", "float", 1.0, 0.5, 16, 0.5),
            Param("sag", "下垂量", "float", 0.35, 0, 2, 0.05,
                  hint="悬链线：×水平跨度"),
            Param("samples", "采样段数", "int", 64, 4, 2000, 4),
            Param("replace", "覆盖已有方块", "bool", False),
            Param("use_stairs_and_slabs", "用台阶收边", "bool", False),
        )),
    # ---------------------------------------------------------- 绘制与上色
    ToolSpec(
        id="noise_painter", label="噪声绘制", group="paint", fn=t_noise_painter,
        hint="按噪声把选区/笔刷里的方块换成一组方块（按分布分位，不会有色斑）",
        region="brush", needs_block=True,
        params=(
            Param("noise", "噪声", "enum", "fbm",
                  options=NZ.KINDS,
                  label_of={"white": "白噪声", "simplex": "Simplex",
                            "fbm": "FBM 分形", "worley": "Worley 细胞",
                            "voronoi": "Voronoi 边界", "metaball": "Metaball 团块",
                            "splatter": "Splatter 溅点"}),
            Param("blocks", "方块（可带权重）", "blocks", "minecraft:stone,minecraft:andesite*0.5",
                  hint="逗号分隔；name*3 表示权重 3"),
            Param("scale", "特征尺度", "float", 12.0, 0.5, 256, 0.5),
            Param("octaves", "层数", "int", 4, 1, 10, 1),
            Param("gain", "增益", "float", 0.5, 0.05, 1.0, 0.05),
            Param("lacunarity", "频率倍率", "float", 2.0, 1.1, 4, 0.1),
            Param("warp", "域扭曲", "float", 0.0, 0, 3, 0.1),
            Param("aniso_x", "各向异性 X", "float", 1.0, 0.1, 8, 0.1),
            Param("aniso_y", "各向异性 Y", "float", 1.0, 0.1, 8, 0.1),
            Param("aniso_z", "各向异性 Z", "float", 1.0, 0.1, 8, 0.1),
            Param("mode", "模式", "enum", "replace",
                  options=("replace", "add"),
                  label_of={"replace": "改已有方块", "add": "只在空气处加"}),
            Param("only_existing", "只改已有方块", "bool", True),
            Param("probability_density", "覆盖密度", "float", 1.0, 0.05, 1, 0.05),
            Param("seed", "随机种子", "int", 0, 0, 999999, 1),
        )),
    ToolSpec(
        id="gradient_painter", label="渐变上色", group="paint", fn=t_gradient_painter,
        hint="按高度/半径做材质渐变（幕墙、夜灯、地形分层）",
        region="brush", needs_block=True,
        params=(
            Param("gradient_shape", "渐变形状", "enum", "linear",
                  options=("linear", "spherical", "radial_xz", "plane"),
                  label_of={"linear": "沿轴线性", "spherical": "球向",
                            "radial_xz": "水平径向", "plane": "离轴平面距离"}),
            Param("axis", "轴", "enum", "y", options=("x", "y", "z")),
            Param("blocks", "方块序列", "blocks",
                  "minecraft:deepslate,minecraft:stone,minecraft:calcite",
                  hint="从低到高依次渐变"),
            Param("dither", "抖动", "float", 0.35, 0, 1, 0.05),
            Param("invert", "反向", "bool", False),
            Param("only_existing", "只改已有方块", "bool", True),
            Param("seed", "种子", "int", 7, 0, 999999, 1),
        )),
    ToolSpec(
        id="painter", label="实心绘制", group="paint", fn=t_painter,
        hint="把选区/笔刷涂成当前方块，边缘随机虚化",
        region="brush", needs_block=True,
        params=(
            Param("chance", "命中概率", "float", 1.0, 0.05, 1, 0.05),
            Param("soft_edge", "边缘虚化", "float", 0.0, 0, 1, 0.05),
            Param("only_existing", "只改已有方块", "bool", True),
        )),
    ToolSpec(
        id="floodfill", label="连通填充", group="paint", fn=t_floodfill,
        hint="从点击处灌满连通的同种方块（房间内壁、水体）",
        region="brush", needs_block=True,
        params=(
            Param("limit", "格子上限", "int", 200000, 100, 5000000, 1000),
        )),
    # ---------------------------------------------------------- 形变
    ToolSpec(
        id="smooth", label="平滑", group="deform", fn=t_smooth,
        hint="grow 长合缝隙 / melt 磨圆棱角 / stable 双向——去噪点最常用的工具",
        region="brush", needs_block=False,
        params=(
            Param("mode", "模式", "enum", "stable",
                  options=("stable", "grow", "melt"),
                  label_of={"stable": "双向", "grow": "只长", "melt": "只融"}),
            Param("strength", "强度（次数）", "int", 2, 1, 20, 1),
            Param("block_ratio", "保留原方块比例", "float", 0.5, 0, 1, 0.05),
            Param("fix_edges", "贴合已有实体", "bool", True),
        )),
    ToolSpec(
        id="rock", label="岩石化", group="deform", fn=t_rock,
        hint="噪声位移 + 平滑，把方块堆变自然岩体（地形/废墟表面）",
        region="brush", needs_block=False,
        params=(
            Param("noisiness", "噪声强度", "float", 0.5, 0.05, 1, 0.05),
            Param("noise_radius", "噪声尺度", "float", 6.0, 0.5, 64, 0.5),
            Param("noise_field_seed", "噪声种子", "int", 0, 0, 999999, 1),
            Param("meld_strength", "贴合强度", "float", 0.5, 0, 1, 0.05),
            Param("smoothing_stddev", "平滑次数", "int", 2, 0, 10, 1),
        )),
    ToolSpec(
        id="shatter", label="破碎", group="deform", fn=t_shatter,
        hint="沿轴切片错位（地震/爆炸感），3d = 逐格噪声位移",
        region="sel", needs_block=True,
        params=(
            Param("axis", "轴", "enum", "y",
                  options=("x", "y", "z", "3d"),
                  label_of={"3d": "3D（逐格）"}),
            Param("scale", "位移量", "float", 3.0, 0.5, 64, 0.5),
            Param("width", "切片厚度", "int", 3, 1, 32, 1),
            Param("use_active_block", "缝隙填当前方块", "bool", False),
            Param("seed", "种子", "int", 0, 0, 999999, 1),
        )),
    ToolSpec(
        id="melt", label="融化", group="deform", fn=t_melt,
        hint="没支撑的方块往下淌（钟乳石、蜡烛、融雪）",
        region="brush", needs_block=False,
        params=(
            Param("strength", "步数", "int", 4, 1, 64, 1),
            Param("chance", "概率", "float", 0.6, 0.05, 1, 0.05),
        )),
    ToolSpec(
        id="roughen", label="粗糙化", group="deform", fn=t_roughen,
        hint="表面随机增删方块（做风化/苔痕/碎石边缘）",
        region="brush", needs_block=False,
        params=(
            Param("ratio", "比例", "float", 0.4, 0.05, 1, 0.05),
            Param("min_faces", "最少相邻面", "int", 2, 1, 6, 1),
            Param("mode", "模式", "enum", "both",
                  options=("both", "add", "remove"),
                  label_of={"both": "增删都做", "add": "只加", "remove": "只删"}),
        )),
    ToolSpec(
        id="distort", label="扭曲", group="deform", fn=t_distort,
        hint="噪声向量场位移整块体素（做扭曲的塔身/异形）",
        region="sel", needs_block=False,
        params=(
            Param("distance_x", "位移 X", "float", 3.0, 0, 64, 0.5),
            Param("distance_y", "位移 Y", "float", 1.0, 0, 64, 0.5),
            Param("distance_z", "位移 Z", "float", 3.0, 0, 64, 0.5),
            Param("scale", "噪声尺度", "float", 14.0, 2, 128, 1),
            Param("iterations", "迭代", "int", 1, 1, 10, 1),
            Param("seed", "种子", "int", 0, 0, 999999, 1),
        )),
    ToolSpec(
        id="weld", label="焊接", group="deform", fn=t_weld,
        hint="把模块之间/裂缝里的空气按邻域多数方块补上",
        region="brush", needs_block=False,
        params=(
            Param("strength", "步数", "int", 2, 1, 10, 1),
            Param("threshold", "最少相邻面", "int", 4, 2, 6, 1),
        )),
    ToolSpec(
        id="blend", label="材质混合", group="deform", fn=t_blend,
        hint="接触面两侧材质按概率互相渗透（过渡色带）",
        region="brush", needs_block=False,
        params=(
            Param("spread", "步数", "int", 2, 1, 10, 1),
            Param("warp", "渗透概率", "float", 0.5, 0.05, 1, 0.05),
        )),
    ToolSpec(
        id="sculpt", label="雕刻", group="deform", fn=t_sculpt,
        hint="笔刷沿表面增/减材料（堆料、削平、揉圆）",
        region="brush", needs_block=False,
        params=(
            Param("mode", "模式", "enum", "add",
                  options=("add", "remove", "smooth"),
                  label_of={"add": "堆料", "remove": "削料", "smooth": "揉圆"}),
            Param("strength", "强度", "int", 1, 1, 20, 1),
        )),
    # ---------------------------------------------------------- 体块运算
    ToolSpec(
        id="fill", label="填充", group="solid", fn=t_fill,
        hint="把框选区域填成当前方块（可加掩码只填部分）",
        region="sel", needs_block=True,
        params=(
            Param("only_air", "只填空格", "bool", False),
        )),
    ToolSpec(
        id="replace", label="替换", group="solid", fn=t_replace,
        hint="把选区里某几种方块换成当前方块（支持 oak* 通配）",
        region="sel", needs_block=True,
        params=(
            Param("from", "被替换方块", "blocks", "",
                  hint="逗号分隔，支持 stone*, oak_log；留空 = 全部"),
            Param("keep_props", "保留属性", "bool", False,
                  hint="保留朝向/半砖等同名属性"),
            Param("only_existing", "只改已有方块", "bool", True),
        )),
    ToolSpec(
        id="hollow", label="掏空", group="solid", fn=t_hollow,
        hint="保留外壳掏空内部（做房子/穹顶）",
        region="sel", needs_block=False,
        params=(
            Param("thickness", "壁厚", "int", 1, 1, 16, 1),
            Param("open_top", "顶部开口", "bool", False),
        )),
    ToolSpec(
        id="grow", label="膨胀 / 腐蚀", group="solid", fn=t_grow,
        hint="正数加粗体块，负数瘦身（结构加厚、去毛边）",
        region="sel", needs_block=False,
        params=(
            Param("amount", "层数（负=腐蚀）", "int", 1, -16, 16, 1),
        )),
    ToolSpec(
        id="gravity", label="重力", group="solid", fn=t_gravity,
        hint="选区内方块落到底（塌方、碎石堆）",
        region="sel", needs_block=False,
        params=()),
    ToolSpec(
        id="drain", label="排液", group="solid", fn=t_drain,
        hint="清掉选区里的水与岩浆",
        region="sel", needs_block=False, params=()),
    ToolSpec(
        id="autoshade", label="自动明暗", group="solid", fn=t_autoshade,
        hint="按高度铺一套明暗渐变的方块（给建筑加体积感）",
        region="sel", needs_block=False,
        params=(
            Param("shade_blocks", "明→暗方块序列", "blocks",
                  "minecraft:calcite,minecraft:diorite,minecraft:deepslate"),
            Param("axis", "轴", "enum", "y", options=("x", "y", "z")),
            Param("invert", "反向（高=深色）", "bool", False),
            Param("only_existing", "只改已有方块", "bool", True),
        )),
    # ---------------------------------------------------------- 地形
    ToolSpec(
        id="elevation", label="地形升降", group="world", fn=t_elevation,
        hint="笔刷抬升/压低/抹平地形",
        region="brush", needs_block=True,
        params=(
            Param("mode", "模式", "enum", "raise",
                  options=("raise", "lower", "flatten"),
                  label_of={"raise": "抬升", "lower": "压低", "flatten": "抹平"}),
            Param("amount", "高度", "int", 2, 1, 64, 1),
        )),
    ToolSpec(
        id="flatten", label="削平", group="world", fn=t_flatten,
        hint="把选区表面压到指定高度（场地平整、地基开挖）",
        region="sel", needs_block=True,
        params=(
            Param("level", "目标高度 Y", "int", 64, -64, 512, 1),
            Param("mode", "模式", "enum", "flatten",
                  options=("flatten", "fill", "both"),
                  label_of={"flatten": "只削", "fill": "只填", "both": "削+填"}),
        )),
    ToolSpec(
        id="slope", label="坡度化", group="world", fn=t_slope,
        hint="把表面拟合成均匀斜坡（屋顶、坡道、护坡）",
        region="sel", needs_block=True,
        params=(
            Param("axis", "坡向轴", "enum", "x", options=("x", "z")),
            Param("mode", "模式", "enum", "both",
                  options=("both", "fill", "flatten")),
        )),
    ToolSpec(
        id="extrude", label="挤出", group="world", fn=t_extrude,
        hint="按每列顶面把形状竖起 amount 层（加高墙体、做岩柱）",
        region="sel", needs_block=False,
        params=(
            Param("amount", "层数（负=向下）", "int", 3, -64, 64, 1),
        )),
    ToolSpec(
        id="stamp", label="盖章散布", group="world", fn=t_stamp,
        hint="把资产包里的模块按概率散布到选区表面（树林、摊位、灯柱）",
        region="sel", needs_block=False,
        params=(
            Param("blueprint", "模块 / 蓝图", "text", "",
                  hint="模块 id，例如 tree_basic"),
            Param("chance", "出现概率", "float", 0.5, 0.05, 1, 0.05),
            Param("min_spacing", "最小间距", "int", 6, 1, 64, 1),
            Param("random_yaw", "随机朝向", "bool", True),
            Param("random_xz_flip", "随机镜像", "bool", False),
        )),
]

from mctools.expressive import SPECS_EXTRA  # noqa: E402

SPECS.extend(SPECS_EXTRA)

_CATALOG_META = {
    "shape": ("形状与路径", "基本体与曲线铺设"),
    "paint": ("绘制与上色", "噪声/渐变/连通填充"),
    "deform": ("形变与雕刻", "平滑、岩石化、破碎、扭曲"),
    "solid": ("体块运算", "填充、掏空、重力、排液"),
    "world": ("地形与重力", "升降、削平、坡道、盖章"),
}
