"""工具执行器：把「工作盒 → 局部体 → 工具函数 → 统计」串起来。

设计要点（都是为了可撤销 + 可复现）：

* 工具只在**局部子体**（work box 的副本）上动手，动完写回；
  调用方在此之前压好撤销快照，因此工具永远不可能改到盒子外的格子。
* 掩码/噪声都用**世界坐标**求值，所以同一工具在任意子区域跑结果一致。
* 一切随机都走 ``np.random.default_rng(seed)``，``seed`` 由调用方给定。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from mctools import masks as M
from mctools import shapes as SH

# registry 只依赖 tools/spec（不反向依赖 engine），所以在这里直接转出很方便
from mctools.registry import get_tool  # noqa: F401

__all__ = ["ToolContext", "plan_box", "run", "apply", "build_sel",
           "ToolError", "get_tool"]


class ToolError(ValueError):
    """工具参数/状态错误（前端直接显示这条消息）。"""


# ------------------------------------------------------------------ box math
def _clamp_box(box, size):
    x0, y0, z0, x1, y1, z1 = (int(v) for v in box)
    sx, sy, sz = size
    x0, x1 = max(0, min(x0, x1)), min(sx - 1, max(x0, x1))
    y0, y1 = max(0, min(y0, y1)), min(sy - 1, max(y0, y1))
    z0, z1 = max(0, min(z0, z1)), min(sz - 1, max(z0, z1))
    if x1 < x0 or y1 < y0 or z1 < z0:
        return None
    return (x0, y0, z0, x1, y1, z1)


def _union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]),
            max(a[3], b[3]), max(a[4], b[4]), max(a[5], b[5]))


def brush_box(center, shape, radius, height=None, axis="y"):
    return SH.bbox_of(shape, center=center, radius=radius, height=height,
                      axis=axis)


def plan_box(spec, params, size, *, sel_box=None, centers=None,
             brush_shape="sphere", brush_radius=4.0, extra_points=None):
    """算出工作盒（世界坐标，闭区间）。

    ``region``：
      * ``brush`` —— 中心点集的笔刷并集（必须给 centers）
      * ``sel``   —— 用户框选；没框选则整张画布
      * ``own``   —— 工具自带几何（``spec.bbox_fn``）
    """
    sel_box = _clamp_box(sel_box, size) if sel_box else None
    if spec.region == "brush":
        pts = list(centers or []) + list(extra_points or [])
        if pts:
            box = None
            for c in pts:
                box = _union(box, brush_box(c, brush_shape, brush_radius))
        elif sel_box:
            box = sel_box            # 没涂抹 → 整块选区（WorldEdit 式用法）
        else:
            raise ToolError(f"{spec.label}：请先框选，或用笔刷在 3D 里涂抹")
    elif spec.region == "own":
        fn = spec.bbox_fn
        if fn is None:
            raise ToolError(f"{spec.label} 缺少几何定义")
        box = fn(params, size, sel_box)
        if box is None:
            raise ToolError(f"{spec.label} 需要框选或位置参数")
    else:
        box = sel_box or (0, 0, 0, size[0] - 1, size[1] - 1, size[2] - 1)
    clamp = _clamp_box(box, size)
    if clamp is None:
        raise ToolError("工作区域超出画布")
    pad = max(0, int(getattr(spec, "pad", 1)))
    return _clamp_box((clamp[0] - pad, clamp[1] - pad, clamp[2] - pad,
                       clamp[3] + pad, clamp[4] + pad, clamp[5] + pad),
                      size) or clamp


def build_sel(box, spec, params, *, size, sel_box=None, mask_expr=None,
              centers=None, brush_shape="sphere", brush_radius=4.0,
              extent=None):
    """在工作盒里构造「受影响掩码」。

    选区 ∩ （掩码表达式）∩ （笔刷并集）。没有给定选区就是全盒。
    """
    x0, y0, z0, x1, y1, z1 = box
    shape = (y1 - y0 + 1, z1 - z0 + 1, x1 - x0 + 1)
    sel = np.ones(shape, dtype=bool)
    if sel_box:
        sb = _clamp_box(sel_box, size)
        if sb:
            inside = np.zeros(shape, dtype=bool)
            ix0, iy0, iz0, ix1, iy1, iz1 = sb
            inside[iy0 - y0:iy1 - y0 + 1, iz0 - z0:iz1 - z0 + 1,
                   ix0 - x0:ix1 - x0 + 1] = True
            sel &= inside
    if spec.region == "brush" and centers:
        Y, Z, X = SH.grid(shape)
        bm = np.zeros(shape, dtype=bool)
        h = params.get("height") if isinstance(params, dict) else None
        for c in centers:
            lc = (float(c[0]) - x0, float(c[1]) - y0, float(c[2]) - z0)
            bm |= SH.shape_mask(brush_shape, X, Y, Z, center=lc,
                                radius=float(brush_radius),
                                height=(float(h) if h else None))
            if bm.all():
                break
        sel &= bm
    return sel


# ------------------------------------------------------------------ context
@dataclass
class ToolContext:
    """工具函数看到的全部环境（局部子体 + 调色板 + 参数 + 选区）。"""

    vol: np.ndarray                       # (sy, sz, sx) uint16 局部
    palette: list                         # 调色板状态字符串（可追加）
    origin: tuple = (0, 0, 0)             # 工作盒在世界里的原点
    sel: np.ndarray | None = None         # 受影响掩码（局部）
    params: dict = field(default_factory=dict)
    block: str | None = None              # 当前方块（UI 里选中的）
    block2: str | None = None             # 次方块（渐变/噪声的第二档）
    seed: int = 0
    extent: tuple | None = None           # 整张画布 (sx, sy, sz)
    new_states: list = field(default_factory=list)
    note: list = field(default_factory=list)

    def __post_init__(self):
        if self.sel is None:
            self.sel = np.ones(self.vol.shape, dtype=bool)
        if not self.block:
            # 调用方没给「当前方块」时兜底，避免工具半路炸掉
            self.block = "minecraft:stone"
        self.new_states = list(self.new_states or [])
        self.rng = np.random.default_rng(int(self.seed) & 0xFFFFFFFF)
        self._grid = None

    # -------------------------------------------------- coordinates
    @property
    def shape(self):
        return self.vol.shape

    def grid(self):
        """局部 ``(Y, Z, X)`` 浮点网格（缓存）。"""
        if self._grid is None:
            self._grid = SH.grid(self.vol.shape)
        return self._grid

    def world(self, axis: str) -> np.ndarray:
        """世界坐标数组（axis: 'x'|'y'|'z'），用于噪声/掩码。"""
        Y, Z, X = self.grid()
        o = {"x": self.origin[0], "y": self.origin[1], "z": self.origin[2]}
        return {"x": X + o["x"], "y": Y + o["y"], "z": Z + o["z"]}[axis]

    @property
    def solid(self) -> np.ndarray:
        return self.vol != 0

    # -------------------------------------------------- palette
    def idx(self, state: str, create=True) -> int:
        from mccore.schem_io import parse_state, state_str
        key = state_str(parse_state(state or "minecraft:air"))
        for i, p in enumerate(self.palette):
            if state_str(p) == key:
                return i
        if not create:
            return -1
        self.palette.append(parse_state(state))
        self.new_states.append(state)
        return len(self.palette) - 1

    def state(self, i: int) -> str:
        from mccore.schem_io import state_str
        return state_str(self.palette[int(i)]) if 0 <= int(i) < len(self.palette) \
            else "minecraft:air"

    def id_of(self, i: int) -> str:
        return self.state(i).split("[", 1)[0]

    def air(self) -> int:
        return self.idx("minecraft:air")

    # -------------------------------------------------- masks
    def mask_ctx(self) -> M.MaskContext:
        return M.MaskContext(self.vol, origin=self.origin,
                             names=[self.state(i)
                                    for i in range(len(self.palette))],
                             sel=self.sel, rng=self.rng)

    def expr(self, text) -> np.ndarray:
        """编译额外掩码表达式并与选区求交。"""
        m = M.compile_mask(text, self.mask_ctx())
        return m & self.sel

    def mask_arg(self, name, default=None):
        """取参数里的掩码表达式（可为空）。"""
        v = self.params.get(name, default)
        return self.expr(v) if v else None

    # -------------------------------------------------- read/write
    def get(self, x, y, z) -> int:
        sy, sz, sx = self.vol.shape
        if 0 <= x < sx and 0 <= y < sy and 0 <= z < sz:
            return int(self.vol[y, z, x])
        return 0

    def put(self, x, y, z, value) -> None:
        sy, sz, sx = self.vol.shape
        if 0 <= x < sx and 0 <= y < sy and 0 <= z < sz:
            self.vol[y, z, x] = (self.idx(value) if isinstance(value, str)
                                 else int(value))

    def fill(self, mask, value) -> int:
        """把掩码内的格子替换成 ``value``（state 或索引），返回改动数。"""
        i = self.idx(value) if isinstance(value, str) else int(value)
        m = mask & (self.vol != i)
        n = int(m.sum())
        if n:
            self.vol[m] = i
        return n

    def add(self, mask, value) -> int:
        """只在空气格上放（不覆盖已有方块）。"""
        return self.fill(mask & (self.vol == 0), value)

    def erase(self, mask) -> int:
        return self.fill(mask, 0)

    def param(self, name, default=None):
        v = self.params.get(name, default)
        return default if v is None or v == "" else v

    def p_int(self, name, default=0):
        try:
            return int(round(float(self.param(name, default))))
        except (TypeError, ValueError):
            return int(default)

    def p_float(self, name, default=0.0):
        try:
            return float(self.param(name, default))
        except (TypeError, ValueError):
            return float(default)

    def p_bool(self, name, default=False):
        v = self.param(name, default)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    def blocks_arg(self, name="blocks", fallback=None) -> list:
        """取方块串（``a,b`` 或列表）→ 状态字符串列表。"""
        v = self.param(name, None)
        if v is None:
            v = fallback if fallback is not None else self.block
        if isinstance(v, str):
            out = [x.strip() for x in v.replace("，", ",").split(",") if x.strip()]
        elif isinstance(v, (list, tuple)):
            out = [str(x).strip() for x in v if str(x).strip()]
        else:
            out = []
        return out or ["minecraft:stone"]


def apply(world, palette, name, params=None, *, sel_box=None, centers=None,
          brush_shape="sphere", brush_radius=4.0, seed=0, block=None,
          block2=None, mask=None, **extra) -> dict:
    """在整张画布上执行一个工具（就地改 ``world`` / ``palette``）。

    这是 mcstudio / CLI / 脚本共用的唯一入口：内部按工具的 ``region`` 算出
    工作盒，切出局部子体，跑工具，再写回。返回值里的 ``box`` 是工作盒
    （调用方据此压撤销快照与回传改动区域）。
    """
    from mccore.schem_io import state_str
    from mctools.registry import get_tool
    spec = get_tool(name)
    params = dict(params or {})
    if centers and not (params.get("_center") or params.get("at")):
        params["_center"] = [float(v) for v in centers[-1]]
    sy, sz, sx = world.shape
    size = (sx, sy, sz)
    box = plan_box(spec, params, size, sel_box=sel_box, centers=centers,
                   brush_shape=brush_shape, brush_radius=brush_radius)
    x0, y0, z0, x1, y1, z1 = box
    sub = np.ascontiguousarray(world[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1])
    sel = build_sel(box, spec, params, size=size, sel_box=sel_box,
                    centers=centers, brush_shape=brush_shape,
                    brush_radius=brush_radius)
    if mask:
        mc = M.MaskContext(sub, origin=(x0, y0, z0),
                           names=[state_str(p) for p in palette], sel=sel)
        sel = sel & M.compile_mask(mask, mc)
    res = run(name, sub, palette, params=params, origin=(x0, y0, z0), sel=sel,
              seed=seed, block=block, block2=block2, extent=size)
    world[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1] = sub
    res["box"] = [x0, y0, z0, x1, y1, z1]
    return res


# ------------------------------------------------------------------ runner
def run(name, vol, palette, *, params=None, origin=(0, 0, 0), sel=None,
        seed=0, block=None, block2=None, extent=None) -> dict:
    """在 ``vol``（局部子体，就地修改）上执行工具，返回统计。"""
    from mctools.registry import get_tool
    spec = get_tool(name)
    before = vol.copy()
    ctx = ToolContext(vol, palette, origin=origin, sel=sel, params=params or {},
                      block=block, block2=block2, seed=seed, extent=extent)
    msg = spec.fn(ctx) or ""
    diff = vol != before
    n = int(diff.sum())
    bbox = None
    if n:
        idx = np.argwhere(diff)
        lo = idx.min(axis=0)
        hi = idx.max(axis=0)
        bbox = [int(lo[2]) + origin[0], int(lo[0]) + origin[1],
                int(lo[1]) + origin[2], int(hi[2]) + origin[0],
                int(hi[0]) + origin[1], int(hi[1]) + origin[2]]
    return {"stats": msg, "changed": n, "bbox": bbox,
            "new_states": list(ctx.new_states), "notes": list(ctx.note)}
