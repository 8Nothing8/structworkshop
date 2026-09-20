"""Voxel brush toolkit — 程序化雕塑/枪械建模的基本体与笔刷。

约定（与 mccore 全流程一致）：

* 体素数组形状 ``(sy, sz, sx)``，``voxels[y, z, x]``，``dtype=uint16`` 调色板索引；
* ``palette[0]`` 永远是 air；其余项是 ``{"Name": "minecraft:xxx"}``（可带 ``Properties``）；
* 世界坐标 = 数组坐标 + ``origin``（部件可先在自己的局部盒里造，再 ``stamp`` 到主网格）。

设计要点：

* **SDF 壳体**：``sdf_shell`` 用隐函数求值 + 内外分层，一次得到「表面材质 / 内核材质」，
  体积雕塑靠它保持轮廓锐利、同时把内部填成便宜的石材（省文件体积）。
* **笔刷**：``box / sphere / ellipsoid / capsule / cylinder / torus / tube``，
  都有 ``hollow`` 参数（做管、做环、做枪管）。
* **装配**：``stamp`` 支持 90° 步进旋转与镜像（``rot`` = 0/1/2/3 绕 Y 轴，``mirror_z``），
  生成器先把零件造在局部坐标里，再摆到世界位置。

用法::

    g = Grid(64, 48, 64)
    steel = g.mat("minecraft:polished_deepslate")
    g.box(4, 4, 4, 40, 10, 12, steel)
    g.cyl("x", (20, 8, 8), 5, 30, steel, hollow=2)
    g.trim()
"""
from __future__ import annotations

import math

import numpy as np

AIR = {"Name": "minecraft:air"}
MC = "minecraft:"


def _props(kind: str) -> dict:
    return {"Name": MC + kind}


class Grid:
    """一块可涂改的体素：``v[y, z, x]`` = 调色板索引。"""

    def __init__(self, sx: int, sy: int, sz: int):
        self.sx, self.sy, self.sz = int(sx), int(sy), int(sz)
        self.v = np.zeros((self.sy, self.sz, self.sx), np.uint16)
        self.pal: list[dict] = [dict(AIR)]
        self._idx: dict[str, int] = {}

    # ---------------------------------------------------------------- palette
    def mat(self, name: str, props: dict | None = None) -> int:
        """取材质索引（同 state 复用同一索引）。``name`` 可省 ``minecraft:`` 前缀。"""
        if not name.startswith(MC):
            name = MC + name
        key = name + ("|" + repr(sorted(props.items())) if props else "")
        if key not in self._idx:
            self.pal.append({"Name": name, **({"Properties": dict(props)} if props else {})})
            self._idx[key] = len(self.pal) - 1
        return self._idx[key]

    def id_of(self, name: str, props: dict | None = None) -> int:
        if not name.startswith(MC):
            name = MC + name
        key = name + ("|" + repr(sorted(props.items())) if props else "")
        return self._idx.get(key, 0)

    # ------------------------------------------------------------------ bbox
    def clamp_box(self, x0, y0, z0, x1, y1, z1):
        return (max(0, int(math.floor(x0))), max(0, int(math.floor(y0))),
                max(0, int(math.floor(z0))),
                min(self.sx - 1, int(math.ceil(x1))),
                min(self.sy - 1, int(math.ceil(y1))),
                min(self.sz - 1, int(math.ceil(z1))))

    def box(self, x0, y0, z0, x1, y1, z1, m: int) -> None:
        x0, y0, z0, x1, y1, z1 = self.clamp_box(x0, y0, z0, x1, y1, z1)
        if x1 < x0 or y1 < y0 or z1 < z0:
            return
        self.v[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1] = m

    def clear_box(self, x0, y0, z0, x1, y1, z1) -> None:
        self.box(x0, y0, z0, x1, y1, z1, 0)

    def sphere(self, cx, cy, cz, r, m: int, hollow: float = 0.0) -> None:
        r = float(r)
        a = self.clamp_box(cx - r - 1, cy - r - 1, cz - r - 1, cx + r + 1, cy + r + 1, cz + r + 1)
        x0, y0, z0, x1, y1, z1 = a
        if x1 < x0:
            return
        yy, zz, xx = np.mgrid[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2 + (zz - cz) ** 2)
        msk = d <= r
        if hollow:
            msk &= d >= r - float(hollow)
        self.v[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1][msk] = m

    def ellipsoid(self, c, r, m: int, hollow: float = 0.0,
                  rot_y: float = 0.0, rot_z: float = 0.0) -> None:
        """椭球（可绕 Y / Z 轴旋转，弧度）。"""
        (cx, cy, cz), (rx, ry, rz) = c, r
        rmax = max(rx, ry, rz) + 1
        x0, y0, z0, x1, y1, z1 = self.clamp_box(cx - rmax, cy - rmax, cz - rmax,
                                                cx + rmax, cy + rmax, cz + rmax)
        if x1 < x0:
            return
        yy, zz, xx = np.mgrid[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
        X = (xx - cx).astype(float)
        Y = (yy - cy).astype(float)
        Z = (zz - cz).astype(float)
        if rot_y:
            c_, s_ = math.cos(rot_y), math.sin(rot_y)
            X, Z = X * c_ + Z * s_, -X * s_ + Z * c_
        if rot_z:
            c_, s_ = math.cos(rot_z), math.sin(rot_z)
            X, Y = X * c_ - Y * s_, X * s_ + Y * c_
        d = np.sqrt((X / max(rx, 1e-6)) ** 2 + (Y / max(ry, 1e-6)) ** 2
                    + (Z / max(rz, 1e-6)) ** 2)
        msk = d <= 1.0
        if hollow:
            inner = 1.0 - float(hollow) / max(rx, ry, rz, 1e-6)
            msk &= d >= max(0.0, inner)
        self.v[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1][msk] = m

    def capsule(self, p0, p1, r, m: int, hollow: float = 0.0) -> None:
        (x0_, y0_, z0_), (x1_, y1_, z1_) = p0, p1
        r = float(r)
        a = self.clamp_box(min(x0_, x1_) - r - 1, min(y0_, y1_) - r - 1, min(z0_, z1_) - r - 1,
                           max(x0_, x1_) + r + 1, max(y0_, y1_) + r + 1, max(z0_, z1_) + r + 1)
        x0, y0, z0, x1, y1, z1 = a
        if x1 < x0 or y1 < y0 or z1 < z0:       # clamp 后盒子可能反向（源点在网格外）
            return
        yy, zz, xx = np.mgrid[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
        P = np.stack([xx - x0_, yy - y0_, zz - z0_], -1).astype(float)
        A = np.array([x0_, y0_, z0_], float)
        B = np.array([x1_, y1_, z1_], float)
        AB = B - A
        L2 = float(AB @ AB) or 1e-6
        t = np.clip(((P - A) @ AB) / L2, 0.0, 1.0)[..., None]
        D = np.linalg.norm(P - (A + t * AB), axis=-1)
        msk = D <= r
        if hollow:
            msk &= D >= r - float(hollow)
        self.v[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1][msk] = m

    def cyl(self, axis: str, c, r, length, m: int, hollow: float = 0.0,
            taper: float = 0.0) -> None:
        """圆柱/锥台：``axis`` = x/y/z；``c`` 是底面中心（沿轴起点）；``taper`` 末端半径增量。"""
        cx, cy, cz = c
        r = float(r)
        L = float(length)
        rr = r + abs(taper) + 1
        if axis == "x":
            a = self.clamp_box(cx - 1, cy - rr, cz - rr, cx + L + 1, cy + rr, cz + rr)
        elif axis == "y":
            a = self.clamp_box(cx - rr, cy - 1, cz - rr, cx + rr, cy + L + 1, cz + rr)
        else:
            a = self.clamp_box(cx - rr, cy - rr, cz - 1, cx + rr, cy + rr, cz + L + 1)
        x0, y0, z0, x1, y1, z1 = a
        if x1 < x0:
            return
        yy, zz, xx = np.mgrid[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
        if axis == "x":
            t = np.clip((xx - cx) / max(L, 1e-6), 0, 1)
            rad = np.sqrt((yy - cy) ** 2 + (zz - cz) ** 2)
        elif axis == "y":
            t = np.clip((yy - cy) / max(L, 1e-6), 0, 1)
            rad = np.sqrt((xx - cx) ** 2 + (zz - cz) ** 2)
        else:
            t = np.clip((zz - cz) / max(L, 1e-6), 0, 1)
            rad = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        R = r + taper * t
        inside_len = (xx - cx >= -0.5) & (xx - cx <= L) if axis == "x" else True
        if axis == "y":
            inside_len = (yy - cy >= -0.5) & (yy - cy <= L)
        if axis == "z":
            inside_len = (zz - cz >= -0.5) & (zz - cz <= L)
        msk = (rad <= R) & inside_len
        if hollow:
            msk &= rad >= (R - float(hollow))
        self.v[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1][msk] = m

    def torus(self, c, R, r, m: int, axis: str = "y", arc: float = 2 * math.pi,
              rot0: float = 0.0) -> None:
        """圆环（``axis`` 为环法线方向）—— 肋骨 / 弹链环 / 握把护弓都靠它。"""
        cx, cy, cz = c
        R, r = float(R), float(r)
        s = R + r + 1
        x0, y0, z0, x1, y1, z1 = self.clamp_box(cx - s, cy - s, cz - s, cx + s, cy + s, cz + s)
        if x1 < x0:
            return
        yy, zz, xx = np.mgrid[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
        X, Y, Z = (xx - cx).astype(float), (yy - cy).astype(float), (zz - cz).astype(float)
        if axis == "y":
            u, w = X, Z
        elif axis == "z":
            u, w = X, Y
        else:
            u, w = Y, Z
        ang = np.arctan2(w, u) - rot0
        ang = np.mod(ang, 2 * math.pi)
        if arc < 2 * math.pi - 1e-6:
            msk_arc = ang <= arc
        else:
            msk_arc = np.ones_like(ang, bool)
        # 到环心的距离（用角度还原最近点）
        ca, sa = np.cos(np.clip(ang, 0, arc)), np.sin(np.clip(ang, 0, arc))
        du, dw = u - R * ca, w - R * sa
        if axis == "y":
            d = np.sqrt(du ** 2 + dw ** 2 + Y ** 2)
        elif axis == "z":
            d = np.sqrt(du ** 2 + dw ** 2 + Z ** 2)
        else:
            d = np.sqrt(du ** 2 + dw ** 2 + X ** 2)
        msk = (d <= r) & msk_arc
        self.v[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1][msk] = m

    def tube(self, pts, r, m: int, hollow: float = 0.0, closed: bool = False) -> None:
        """沿折线扫掠（管道 / 弹链 / 线缆 / 手指）。"""
        seq = list(pts) + ([pts[0]] if closed else [])
        for a, b in zip(seq[:-1], seq[1:]):
            self.capsule(a, b, r, m, hollow=hollow)

    # ------------------------------------------------------------------ sdf
    def sdf_shell(self, sdf, bbox, surface: int, core: int, thickness: float = 2.0,
                  mask_extra=None) -> int:
        """隐函数壳体：``sdf(xx, yy, zz) <= 0`` 为实体；外层 ``thickness`` 用 ``surface``，
        内部用 ``core``。返回填充的格子数。"""
        x0, y0, z0, x1, y1, z1 = self.clamp_box(*bbox)
        if x1 < x0:
            return 0
        yy, zz, xx = np.mgrid[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
        d = sdf(xx.astype(float), yy.astype(float), zz.astype(float))
        solid = d <= 0.0
        if mask_extra is not None:
            solid &= mask_extra(xx, yy, zz)
        shell = solid & (d > -float(thickness))
        sub = self.v[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
        sub[solid] = core
        sub[shell] = surface
        return int(solid.sum())

    # ------------------------------------------------------------- 变换/装配
    def mirror_z(self, at: int | None = None) -> "Grid":
        """把 z < at 的一半镜像到另一半（``at`` 默认网格中线）。"""
        at = self.sz // 2 if at is None else int(at)
        half = self.v[:, :at + 1, :][:, ::-1, :]
        n = min(half.shape[1], self.sz - at)
        self.v[:, at:at + n, :] = half[:, :n, :]
        return self

    def flip_x(self) -> "Grid":
        self.v = np.ascontiguousarray(self.v[:, :, ::-1])
        return self

    def rot90_y(self, k: int = 1) -> "Grid":
        """绕 Y 轴旋转 90°×k（返回新网格，调色板共享）。"""
        k %= 4
        g = Grid(self.sx, self.sy, self.sz)
        g.pal = self.pal
        g._idx = dict(self._idx)
        v = self.v
        for _ in range(k):
            v = np.transpose(v, (0, 2, 1))[:, ::-1, :]
            v = np.ascontiguousarray(v)
        g.v = v
        g.sx = v.shape[2]
        g.sz = v.shape[1]
        return g

    def rot90_x(self, k: int = 1) -> "Grid":
        """绕 X 轴旋转 90°×k（头尾翻转：枪口朝上 / 朝下用这个）。"""
        k %= 4
        v = self.v
        for _ in range(k):
            v = np.ascontiguousarray(np.flip(np.transpose(v, (1, 0, 2)), 0))
        g = Grid(v.shape[2], v.shape[0], v.shape[1])
        g.pal, g._idx, g.v = self.pal, dict(self._idx), v
        return g

    def rot90_z(self, k: int = 1) -> "Grid":
        """绕 Z 轴旋转 90°×k（俯仰：枪口朝上 / 朝下）。"""
        k %= 4
        v = self.v
        for _ in range(k):
            v = np.ascontiguousarray(np.transpose(v, (1, 0, 2))[:, ::-1, :])
        g = Grid(v.shape[2], v.shape[0], v.shape[1])
        g.pal, g._idx, g.v = self.pal, dict(self._idx), v
        return g

    def scale(self, f: int) -> "Grid":
        """整数倍缩放（``f>=1`` 用重复采样放大，``f<0`` 用块平均缩 |f| 倍）。

        放大用于把倒模的小枪变成建筑级大枪（保持体素风格，不插值、不模糊）。
        """
        if f == 1:
            return self
        if f > 1:
            v = np.repeat(np.repeat(np.repeat(self.v, f, 0), f, 1), f, 2)
        else:
            k = -f
            sy, sz, sx = self.v.shape
            ny, nz, nx = sy // k, sz // k, sx // k
            if ny == 0 or nz == 0 or nx == 0:
                raise ValueError("缩小倍数过大")
            v = self.v[:ny * k, :nz * k, :nx * k].reshape(ny, k, nz, k, nx, k)
            v = np.ascontiguousarray(v[:, 0, :, 0, :, 0].astype(np.uint16))
        g = Grid(v.shape[2], v.shape[0], v.shape[1])
        g.pal, g._idx, g.v = self.pal, dict(self._idx), v
        return g

    def stamp(self, other: "Grid", at, rot: int = 0, mirror_x: bool = False,
              merge_palette: bool = True) -> None:
        """把 ``other`` 盖到本网格 ``at=(x, y, z)``（左下前角）。"""
        src = other.v
        if rot:
            for _ in range(rot % 4):
                src = np.ascontiguousarray(np.transpose(src, (0, 2, 1))[:, ::-1, :])
        if mirror_x:
            src = np.ascontiguousarray(src[:, :, ::-1])
            at = (at[0] - (src.shape[2] - 1), at[1], at[2])
        if merge_palette:
            remap = np.zeros(len(other.pal), np.uint16)
            for i, e in enumerate(other.pal):
                if e.get("Name") == "minecraft:air":
                    remap[i] = 0
                else:
                    remap[i] = self.mat(e["Name"], e.get("Properties"))
            src = remap[src]
        x, y, z = int(at[0]), int(at[1]), int(at[2])
        sy, sz, sx = src.shape
        x0, x1 = max(0, x), min(self.sx, x + sx)
        y0, y1 = max(0, y), min(self.sy, y + sy)
        z0, z1 = max(0, z), min(self.sz, z + sz)
        if x1 <= x0 or y1 <= y0 or z1 <= z0:
            return
        sub = src[y0 - y:y1 - y, z0 - z:z1 - z, x0 - x:x1 - x]
        dst = self.v[y0:y1, z0:z1, x0:x1]
        m = sub != 0
        dst[m] = sub[m]

    # ------------------------------------------------------------------ misc
    def bounds(self):
        """非空包围盒 (x0,y0,z0,x1,y1,z1)；全空返回 None。"""
        nz = np.nonzero(self.v)
        if not len(nz[0]):
            return None
        ys, zs, xs = nz
        return (int(xs.min()), int(ys.min()), int(zs.min()),
                int(xs.max()), int(ys.max()), int(zs.max()))

    def trim(self, pad: int = 0) -> "Grid":
        b = self.bounds()
        if b is None:
            return self
        x0, y0, z0, x1, y1, z1 = b
        x0, y0, z0 = max(0, x0 - pad), max(0, y0 - pad), max(0, z0 - pad)
        x1 = min(self.sx - 1, x1 + pad)
        y1 = min(self.sy - 1, y1 + pad)
        z1 = min(self.sz - 1, z1 + pad)
        g = Grid(x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1)
        g.pal = self.pal
        g._idx = dict(self._idx)
        g.v = np.ascontiguousarray(self.v[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1])
        g.origin = (x0, y0, z0)
        return g

    def count(self) -> int:
        return int((self.v != 0).sum())

    def surface_mask(self, include_floor: bool = True) -> np.ndarray:
        """表面格（六邻接里有 air 或贴地）。"""
        v = self.v
        air = v == 0
        s = np.zeros_like(air)
        s[:-1, :, :] |= air[1:, :, :]
        s[1:, :, :] |= air[:-1, :, :]
        s[:, :-1, :] |= air[:, 1:, :]
        s[:, 1:, :] |= air[:, :-1, :]
        s[:, :, :-1] |= air[:, :, 1:]
        s[:, :, 1:] |= air[:, :, :-1]
        if include_floor:
            s[0, :, :] = v[0, :, :] != 0
        return s & ~air

    def replace(self, old: int | list[int], new: int, mask: np.ndarray | None = None) -> int:
        olds = [old] if isinstance(old, int) else list(old)
        m = np.isin(self.v, olds)
        if mask is not None:
            m &= mask
        n = int(m.sum())
        self.v[m] = new
        return n

    def dither(self, a: int, b: int, t: float, mask: np.ndarray | None = None,
               mod: int = 11, scale: int = 3, seed: int = 0) -> int:
        """在 ``mask`` 内按比例 ``t`` 把 ``a`` 换成 ``b``，用斜条纹抖动（不做随机噪点）。"""
        m = (self.v == a)
        if mask is not None:
            m &= mask
        ys, zs, xs = np.mgrid[0:self.sy, 0:self.sz, 0:self.sx]
        thr = int(round(min(1.0, max(0.0, t)) * mod))
        stripe = ((xs * 3 + ys * 5 + zs * 2 + seed) % mod) < thr
        m &= stripe
        self.v[m] = b
        return int(m.sum())


def from_arrays(voxels: np.ndarray, palette: list[dict], origin=(0, 0, 0)) -> Grid:
    g = Grid(voxels.shape[2], voxels.shape[0], voxels.shape[1])
    g.v = np.ascontiguousarray(voxels.astype(np.uint16))
    g.pal = list(palette)
    g._idx = {}
    for i, e in enumerate(g.pal):
        key = e["Name"] + ("|" + repr(sorted(e.get("Properties", {}).items()))
                           if e.get("Properties") else "")
        g._idx[key] = i
    g.origin = tuple(origin)
    return g


def smoothstep(a: float, b: float, x):
    t = np.clip((x - a) / max(1e-6, (b - a)), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def sd_round_box(px, py, pz, bx, by, bz, r):
    qx = np.abs(px) - bx
    qy = np.abs(py) - by
    qz = np.abs(pz) - bz
    ox, oy, oz = np.maximum(qx, 0), np.maximum(qy, 0), np.maximum(qz, 0)
    outside = np.sqrt(ox ** 2 + oy ** 2 + oz ** 2)
    inside = np.minimum(np.maximum(qx, np.maximum(qy, qz)), 0.0)
    return outside + inside - r


def sd_ellipsoid(px, py, pz, rx, ry, rz):
    return (np.sqrt((px / rx) ** 2 + (py / ry) ** 2 + (pz / rz) ** 2) - 1.0) \
        * min(rx, ry, rz)


def sd_capsule(px, py, pz, a, b, r):
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    P = np.stack([px, py, pz], -1)
    AB = b - a
    t = np.clip(((P - a) @ AB) / max(float(AB @ AB), 1e-6), 0.0, 1.0)[..., None]
    return np.linalg.norm(P - (a + t * AB), axis=-1) - r
