"""笔刷形状与参数化基本体（对齐 Axiom ``brush_shapes/`` 的 19 个类）。

一个 ``mask`` = 布尔数组；坐标用**工作盒局部整数格**（格 i 的中心即 i），
``center`` 用浮点（可以落在格之间，方便沿曲线连续盖章）。
"""
from __future__ import annotations

import numpy as np

__all__ = ["BRUSH_SHAPES", "SHAPE_KINDS", "bbox_of", "shape_mask", "grid"]

# 笔刷可选形状（Axiom brush_shapes：Sphere/Cube/Cylinder/Cone/Capsule/
# Octahedron/FlatDisk/SinglePoint + 变体）
BRUSH_SHAPES = ("sphere", "cube", "cuboid", "cylinder", "cone", "capsule",
                "octahedron", "disk", "point", "supersphere")

# shape 工具的参数化基本体（Axiom Shape 工具的常用子集）
SHAPE_KINDS = ("sphere", "sphere_hollow", "ball", "cuboid", "cube",
               "cylinder", "cylinder_hollow", "tube", "cone", "pyramid",
               "torus", "capsule", "octahedron", "supersphere", "ellipsoid",
               "prism", "disk", "plane", "point")


def grid(shape):
    """``(Y, Z, X)`` 三个 int 数组（与体素数组同序：(sy, sz, sx)）。"""
    sy, sz, sx = shape
    y, z, x = np.indices((sy, sz, sx), dtype=np.float64)
    return y, z, x


def _axis_offsets(X, Y, Z, center, axis):
    """按 ``axis`` 把三轴偏移重排成 ``(axial, u, v)``（axial = 拉伸方向）。"""
    dx, dy, dz = X - center[0], Y - center[1], Z - center[2]
    if axis == "x":
        return dx, dy, dz
    if axis == "z":
        return dz, dx, dy
    return dy, dx, dz          # 默认 y


def _radius_at(shape, r, height, thickness, a):
    """按拉伸坐标 ``a`` 求该层的等效半径（锥形/球形衰减都走这里）。"""
    h = max(1e-6, height)
    t = (a + h / 2.0) / h                       # 0 (底) → 1 (顶)
    if shape in ("cone", "pyramid"):
        return r * (1.0 - np.clip(t, 0.0, 1.0))
    return r


def shape_mask(kind: str, X, Y, Z, *, center, radius=4.0, height=None,
               thickness=0.0, size=None, exponent=2.0, segments=6,
               axis="y", hollow=False) -> np.ndarray:
    """生成基本体掩码（布尔数组，形状与 ``X`` 相同）。"""
    kind = (kind or "sphere").lower()
    if kind == "point":
        d = (np.abs(X - center[0]) < 0.5) & (np.abs(Y - center[1]) < 0.5) & \
            (np.abs(Z - center[2]) < 0.5)
        return d
    r = float(radius)
    h = float(height if height is not None else 2.0 * r)
    t = float(thickness or 0.0)
    a, u, v = _axis_offsets(X, Y, Z, center, axis)

    if kind in ("sphere", "ball", "sphere_hollow"):
        rr = _radius_at(kind, r, h, t, a)
        d2 = a * a + u * u + v * v
        m = d2 <= rr * rr
        if kind == "sphere_hollow" or (kind == "sphere" and hollow):
            m &= d2 >= max(0.0, rr - t) ** 2
        return m

    if kind in ("cube", "cuboid"):
        sx, sy, sz = (size if size else (2 * r, 2 * r, 2 * r))
        ax, ay, az = abs(sx) / 2.0, abs(sy) / 2.0, abs(sz) / 2.0
        m = (np.abs(X - center[0]) <= ax) & (np.abs(Y - center[1]) <= ay) & \
            (np.abs(Z - center[2]) <= az)
        if hollow and t > 0:
            m &= ~((np.abs(X - center[0]) <= max(0.0, ax - t)) &
                   (np.abs(Y - center[1]) <= max(0.0, ay - t)) &
                   (np.abs(Z - center[2]) <= max(0.0, az - t)))
        return m

    if kind in ("cylinder", "cylinder_hollow", "tube", "disk", "prism"):
        radial = np.sqrt(u * u + v * v)
        hh = 0.5 if kind == "disk" else h / 2.0
        if kind == "prism":
            n = max(3, int(segments))
            ang = np.arctan2(v, u)
            sec = np.pi / n
            # 正 n 边形：半径随角度收缩（顶点在半径 r 处）
            rad = r * np.cos(sec) / np.cos(np.mod(ang + sec, 2 * sec) - sec)
            m = (radial <= rad) & (np.abs(a) <= hh)
        else:
            m = (radial <= r) & (np.abs(a) <= hh)
        if kind in ("cylinder_hollow", "tube") and t > 0:
            m &= radial >= max(0.0, r - t)
        return m

    if kind in ("cone", "pyramid"):
        lim = np.clip((a + h / 2.0) / max(1e-6, h), 0.0, 1.0)
        rlim = r * (1.0 - lim)
        inside = (np.abs(a) <= h / 2.0)
        if kind == "cone":
            m = inside & (np.sqrt(u * u + v * v) <= rlim)
        else:
            m = inside & (np.abs(u) <= rlim) & (np.abs(v) <= rlim)
        if hollow and t > 0:
            inner = np.maximum(0.0, rlim - t)
            if kind == "cone":
                m &= ~(inside & (np.sqrt(u * u + v * v) <= inner))
            else:
                m &= ~(inside & (np.abs(u) <= inner) & (np.abs(v) <= inner))
        return m

    if kind == "torus":
        tube = max(0.25, t if t > 0 else r * 0.25)
        radial = np.sqrt(u * u + v * v)
        return (radial - r) ** 2 + a * a <= tube * tube

    if kind == "capsule":
        # 沿轴的一段圆柱 + 两个半球
        half = max(0.0, h / 2.0 - r)
        ac = np.clip(a, -half, half)
        d2 = (a - ac) ** 2 + u * u + v * v
        return d2 <= r * r

    if kind == "octahedron":
        return np.abs(a) + np.abs(u) + np.abs(v) <= r

    if kind == "supersphere":
        e = max(0.2, float(exponent))
        d = (np.abs(a) ** e + np.abs(u) ** e + np.abs(v) ** e) ** (1.0 / e)
        m = d <= r
        if hollow and t > 0:
            d2 = (np.abs(a) ** e + np.abs(u) ** e + np.abs(v) ** e) ** (1.0 / e)
            m &= d2 >= max(0.0, r - t)
        return m

    if kind == "ellipsoid":
        sx, sy, sz = (size if size else (r, r, r))
        rx, ry, rz = (max(1e-6, abs(float(q))) for q in (sx, sy, sz))
        d = ((X - center[0]) / rx) ** 2 + ((Y - center[1]) / ry) ** 2 + \
            ((Z - center[2]) / rz) ** 2
        m = d <= 1.0
        if hollow and t > 0:
            k = max(0.0, 1.0 - t / max(rx, ry, rz))
            m &= d >= k * k
        return m

    if kind == "plane":
        s = [abs(float(q)) for q in (size if size else (2 * r, 2 * r))]
        sx, sz = (s[0], s[-1])
        return (np.abs(X - center[0]) <= sx / 2.0) & \
               (np.abs(Z - center[2]) <= sz / 2.0) & \
               (np.abs(Y - center[1]) <= 0.5)

    raise ValueError(f"未知基本体 {kind!r}（可选 {', '.join(SHAPE_KINDS)}）")


def bbox_of(kind: str, *, center, radius=4.0, height=None, thickness=0.0,
            size=None, exponent=2.0, segments=6, axis="y") -> tuple:
    """形状的**保守**包围盒（局部整数格，闭区间），用于裁剪工作盒。"""
    kind = (kind or "sphere").lower()
    r = float(radius)
    h = float(height if height is not None else 2.0 * r)
    t = float(thickness or 0.0)
    if kind in ("cube", "cuboid", "ellipsoid"):
        e = [abs(float(q)) for q in (size or (2 * r, 2 * r, 2 * r))]
        ext = [e[0] / 2.0, e[1] / 2.0, e[2] / 2.0]
    elif kind == "plane":
        s = [abs(float(q)) for q in (size or (2 * r, 2 * r))]
        ext = [s[0] / 2.0, 0.5, s[-1] / 2.0]
    elif kind == "torus":
        tube = t if t > 0 else r * 0.25
        ext = [r + tube, tube, r + tube]
    elif kind in ("cylinder", "cylinder_hollow", "tube", "cone", "pyramid",
                  "capsule", "prism", "disk"):
        ext = [r, h / 2.0, r]
        if axis == "x":
            ext = [ext[1], ext[0], ext[2]]
        elif axis == "z":
            ext = [ext[2], ext[1], ext[0]]
    else:                                   # 球/八面体/超椭球/capsule…
        ext = [r, r, r]
    pad = 2
    return (int(np.floor(center[0] - ext[0])) - pad,
            int(np.floor(center[1] - ext[1])) - pad,
            int(np.floor(center[2] - ext[2])) - pad,
            int(np.ceil(center[0] + ext[0])) + pad,
            int(np.ceil(center[1] + ext[1])) + pad,
            int(np.ceil(center[2] + ext[2])) + pad)
