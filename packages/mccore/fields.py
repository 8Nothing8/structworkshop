"""标量场 / 隐式曲面：表达式 → 体素。

数学对象在这套工坊里的通用入口：**一个公式就是一座建筑**。

    python -m mccore.fields --expr "sin(x/4)*cos(z/4) - y/6" \
        --size 48,32,48 --mode iso --shell 2 --ascii --out /tmp/wave.schem

约定
----
* 表达式是一元标量场 ``f(x, y, z)``，坐标是**世界坐标**（可为负，中心在 0）。
* 采样点是**格子中心**（整数坐标）。所以体素化误差 ~ 半格，可用
  ``field_check`` 度量（解析式 vs 实际占位）。
* 三种用法：
  - ``mask``  ``f <= t`` 求占位（实心）
  - ``shell``  占位减腐蚀 = 等值面壳体（大件必须走壳，否则爆内存）
  - ``bands``  把 ``f`` 分级映射到一串方块（颜色 = 场值），可抖动

安全性：表达式走 ``ast`` 白名单求值，无 ``eval``；名字表固定，
不允许属性访问 / 下标 / lambda / 导入。
"""
from __future__ import annotations

import argparse
import ast
import math
import sys
from pathlib import Path

import numpy as np

__all__ = ["FieldError", "compile_expr", "sample", "mask_of", "shell_of",
           "erode", "dilate", "stratify", "ascii_slice", "FUNCS", "CONSTS"]

# ------------------------------------------------------------------ 白名单
CONSTS = {
    "pi": math.pi, "e": math.e, "tau": math.tau, "phi": (1 + math.sqrt(5)) / 2,
    "inf": float("inf"),
}


def _fbm(X, Y, Z, scale=1.0, octaves=4, gain=0.5, lacunarity=2.0, seed=0.0):
    """确定性值噪声 fBm（不依赖 mctools，便于数学层单独用）。"""
    out = np.zeros_like(np.asarray(X, dtype=np.float64))
    amp, sc = 1.0, float(scale)
    norm = 0.0
    for o in range(max(1, int(octaves))):
        out += amp * _value_noise(X / sc, Y / sc, Z / sc, seed + 17.0 * o)
        norm += abs(amp)
        amp *= float(gain)
        sc /= max(1e-6, float(lacunarity))
    return out / max(1e-9, norm)


def _hash3(X, Y, Z, seed=0.0):
    """三维整数哈希 → [0,1)。"""
    ix = np.floor(X).astype(np.int64)
    iy = np.floor(Y).astype(np.int64)
    iz = np.floor(Z).astype(np.int64)
    h = (ix * 73856093) ^ (iy * 19349663) ^ (iz * 83492791) ^ (int(seed) * 2654435761)
    h = (h ^ (h >> 13)) * 1274126177
    h = h ^ (h >> 16)
    return (h & 0xFFFFFF).astype(np.float64) / float(0x1000000)


def _value_noise(X, Y, Z, seed=0.0):
    x0, y0, z0 = np.floor(X), np.floor(Y), np.floor(Z)
    fx, fy, fz = X - x0, Y - y0, Z - z0
    sx = fx * fx * (3 - 2 * fx)
    sy = fy * fy * (3 - 2 * fy)
    sz = fz * fz * (3 - 2 * fz)
    c = {}
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                c[(dx, dy, dz)] = _hash3(x0 + dx, y0 + dy, z0 + dz, seed)
    x00 = c[(0, 0, 0)] + (c[(1, 0, 0)] - c[(0, 0, 0)]) * sx
    x10 = c[(0, 1, 0)] + (c[(1, 1, 0)] - c[(0, 1, 0)]) * sx
    x01 = c[(0, 0, 1)] + (c[(1, 0, 1)] - c[(0, 0, 1)]) * sx
    x11 = c[(0, 1, 1)] + (c[(1, 1, 1)] - c[(0, 1, 1)]) * sx
    y0v = x00 + (x10 - x00) * sy
    y1v = x01 + (x11 - x01) * sy
    return y0v + (y1v - y0v) * sz


def _clamp(v, lo, hi):
    return np.minimum(np.maximum(v, lo), hi)


def _smoothstep(a, b, x):
    t = _clamp((x - a) / np.where(b == a, 1e-9, (b - a)), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _mod(a, b):
    return np.mod(a, b)


FUNCS = {
    # 三角 / 双曲
    "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
    "atan2": np.arctan2, "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh,
    "degrees": np.degrees, "radians": np.radians,
    # 指数 / 对数
    "exp": np.exp, "log": np.log, "log2": np.log2, "log10": np.log10,
    "sqrt": np.sqrt, "cbrt": np.cbrt, "pow": np.power, "hypot": np.hypot,
    # 取整 / 符号
    "abs": np.abs, "sign": np.sign, "floor": np.floor, "ceil": np.ceil,
    "round": np.round, "trunc": np.trunc, "fract": lambda v: v - np.floor(v),
    "mod": _mod, "min": np.minimum, "max": np.maximum,
    "clamp": _clamp, "mix": lambda a, b, t: a + (b - a) * t,
    "smoothstep": _smoothstep,
    "step": lambda e, x: np.where(x < e, 0.0, 1.0),
    # 距离 / 范数
    "length": lambda a, b=0.0, c=0.0: np.sqrt(a * a + b * b + c * c),
    "max3": lambda a, b, c: np.maximum(np.maximum(a, b), c),
    "chebyshev": lambda a, b=0.0, c=0.0: np.maximum(np.maximum(np.abs(a), np.abs(b)), np.abs(c)),
    # 噪声
    "noise": lambda x, y, z, seed=0.0: _value_noise(x, y, z, seed),
    "hash": lambda x, y, z, seed=0.0: _hash3(x, y, z, seed),
    "fbm": _fbm,
}
FUNCS["ln"] = np.log
FUNCS["len"] = FUNCS["length"]


class FieldError(ValueError):
    """表达式不合法（未知函数 / 变量 / 语法糖越界）。"""


_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load,
    ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Pow, ast.USub, ast.UAdd, ast.Compare, ast.Lt, ast.LtE, ast.Gt,
    ast.GtE, ast.Eq, ast.NotEq, ast.BoolOp, ast.And, ast.Or, ast.IfExp,
    ast.Tuple,
)
_VARS = ("x", "y", "z")


def compile_expr(text: str):
    """编译 ``f(x, y, z)``；返回可对 numpy 数组调用的函数。"""
    src = str(text).strip()
    if not src:
        raise FieldError("空表达式")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise FieldError(f"语法错误: {exc}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FieldError(f"不允许的语法: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
                name = getattr(node.func, "id", "?")
                raise FieldError(f"未知函数 {name}（可用: {', '.join(sorted(FUNCS))}）")
        if isinstance(node, ast.Name) and node.id not in FUNCS and \
                node.id not in CONSTS and node.id not in _VARS:
            raise FieldError(f"未知名字 {node.id}（变量只有 x/y/z）")

    code = compile(tree, "<field>", "eval")

    def fn(X, Y, Z):
        X = np.asarray(X, dtype=np.float64)
        Y = np.asarray(Y, dtype=np.float64)
        Z = np.asarray(Z, dtype=np.float64)
        env = dict(CONSTS)
        env.update(FUNCS)
        env.update({"x": X, "y": Y, "z": Z})
        try:
            out = eval(code, {"__builtins__": {}}, env)  # noqa: S307 —— AST 已白名单
        except Exception as exc:  # noqa: BLE001
            raise FieldError(f"求值失败: {exc}") from exc
        arr = np.asarray(out, dtype=np.float64)
        if arr.ndim == 0:
            arr = np.broadcast_to(arr, X.shape)
        return arr

    fn.source = src  # type: ignore[attr-defined]
    return fn


# ------------------------------------------------------------------ 采样
def sample(expr, shape: tuple[int, int, int], origin: tuple[int, int, int] = (0, 0, 0),
           step: float = 1.0, center: bool = True) -> np.ndarray:
    """在 ``shape`` 上采样标量场，返回 ``(sy, sz, sx)`` 的 float 数组。

    ``origin`` 是世界坐标里 (0,0,0) 格子的坐标；``center=True`` 时把采样点
    放在格子中心（+0.5），更接近真实体素化的最优解。
    """
    fn = compile_expr(expr) if isinstance(expr, str) else expr
    sy, sz, sx = (int(v) for v in shape)
    off = 0.5 if center else 0.0
    x = (np.arange(sx, dtype=np.float64) + off) * step + origin[0]
    y = (np.arange(sy, dtype=np.float64) + off) * step + origin[1]
    z = (np.arange(sz, dtype=np.float64) + off) * step + origin[2]
    X = np.broadcast_to(x.reshape(1, 1, sx), (sy, sz, sx))
    Y = np.broadcast_to(y.reshape(sy, 1, 1), (sy, sz, sx))
    Z = np.broadcast_to(z.reshape(1, sz, 1), (sy, sz, sx))
    return fn(X, Y, Z)


_OPS = {"<=": np.less_equal, "<": np.less, ">=": np.greater_equal,
        ">": np.greater, "==": np.equal, "!=": np.not_equal}


def mask_of(f: np.ndarray, op: str = "<=", threshold: float = 0.0) -> np.ndarray:
    """把场变成占位掩码（默认 ``f <= 0`` = 隐式曲面内部）。"""
    if op not in _OPS:
        raise FieldError(f"未知比较符 {op!r}（可用 {', '.join(_OPS)}）")
    return _OPS[op](np.asarray(f), float(threshold))


def _shift(a: np.ndarray, axis: int, delta: int, fill=False) -> np.ndarray:
    out = np.full_like(a, fill)
    n = a.shape[axis]
    d = int(delta)
    if d == 0:
        return a.copy()
    if abs(d) >= n:
        return out
    src = [slice(None)] * 3
    dst = [slice(None)] * 3
    if d > 0:
        src[axis] = slice(0, n - d)
        dst[axis] = slice(d, n)
    else:
        src[axis] = slice(-d, n)
        dst[axis] = slice(0, n + d)
    out[tuple(dst)] = a[tuple(src)]
    return out


def dilate(mask: np.ndarray, times: int = 1) -> np.ndarray:
    out = np.asarray(mask, dtype=bool).copy()
    for _ in range(max(1, int(times))):
        m = out.copy()
        for ax in (0, 1, 2):
            m |= _shift(out, ax, 1) | _shift(out, ax, -1)
        out = m
    return out


def erode(mask: np.ndarray, times: int = 1) -> np.ndarray:
    out = np.asarray(mask, dtype=bool).copy()
    for _ in range(max(1, int(times))):
        m = out.copy()
        for ax in (0, 1, 2):
            m &= _shift(out, ax, 1, True) & _shift(out, ax, -1, True)
        out = m
    return out


def shell_of(mask: np.ndarray, thickness: int = 2) -> np.ndarray:
    """等值壳：``mask - erode(mask, t)``（大件必须走这条，别灌实心）。"""
    t = max(1, int(thickness))
    return np.asarray(mask, dtype=bool) & ~erode(mask, t)


def stratify(f: np.ndarray, blocks: list[str], lo: float | None = None,
             hi: float | None = None, dither: bool = True, seed: int = 7,
             power: float = 1.0) -> tuple[np.ndarray, list[dict]]:
    """把场分级映射到方块（颜色 = 场值）。

    返回 ``(索引数组, 调色板)``；索引 0 = 空气（不参与），1..n 对应 ``blocks``。
    ``dither`` 用确定性哈希在相邻档之间抖动，避免色带断裂。
    """
    from mccore.schem_io import parse_state
    f = np.asarray(f, dtype=np.float64)
    lo = float(np.nanmin(f)) if lo is None else float(lo)
    hi = float(np.nanmax(f)) if hi is None else float(hi)
    n = max(1, len(blocks))
    span = max(1e-9, hi - lo)
    t = np.clip((f - lo) / span, 0.0, 1.0) ** float(power)
    pos = t * n - 0.5
    idx = np.floor(pos).astype(np.int64)
    if dither and n > 1:
        frac = pos - idx
        sy, sz, sx = f.shape
        X = np.broadcast_to(np.arange(sx).reshape(1, 1, sx), (sy, sz, sx))
        Y = np.broadcast_to(np.arange(sy).reshape(sy, 1, 1), (sy, sz, sx))
        Z = np.broadcast_to(np.arange(sz).reshape(1, sz, 1), (sy, sz, sx))
        h = _hash3(X.astype(np.float64), Y.astype(np.float64), Z.astype(np.float64),
                   float(seed))
        bump = (h < frac).astype(np.int64)
        idx = np.where(t >= 0.5, idx + bump, idx - bump)
    idx = np.clip(idx, 0, n - 1) + 1
    pal = [{"Name": "minecraft:air"}]
    pal += [parse_state(b) if isinstance(b, str) else dict(b) for b in blocks]
    return idx.astype(np.uint16), pal


# ------------------------------------------------------------------ 预览
_RAMP = " .:-=+*#%@"


def ascii_slice(f: np.ndarray, axis: str = "y", at: int | None = None,
                width: int = 96) -> str:
    """把一层场打成 ASCII（自检用，不用开渲染器）。"""
    a = np.asarray(f, dtype=np.float64)
    if axis == "y":
        sl = a[(a.shape[0] // 2) if at is None else at]
    elif axis == "z":
        sl = a[:, (a.shape[1] // 2) if at is None else at, :]
    else:
        sl = a[:, :, (a.shape[2] // 2) if at is None else at]
    lo, hi = float(np.nanmin(sl)), float(np.nanmax(sl))
    t = (sl - lo) / max(1e-9, hi - lo)
    step = max(1, int(math.ceil(t.shape[1] / max(8, width))))
    lines = []
    for row in t[::max(1, step // 2)]:
        lines.append("".join(_RAMP[min(len(_RAMP) - 1, int(v * (len(_RAMP) - 1)))]
                             for v in row[::step]))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--expr", required=True, help="f(x,y,z) 表达式")
    ap.add_argument("--size", default="48,32,48", help="sx,sy,sz（默认 48,32,48）")
    ap.add_argument("--origin", default="0,0,0")
    ap.add_argument("--mode", default="iso", choices=("iso", "band", "ascii"))
    ap.add_argument("--op", default="<=")
    ap.add_argument("--threshold", type=float, default=0.0)
    ap.add_argument("--shell", type=int, default=0, help=">0 时只留等值壳")
    ap.add_argument("--blocks", default="minecraft:white_concrete")
    ap.add_argument("--out", default="")
    ap.add_argument("--ascii", action="store_true")
    a = ap.parse_args()
    sx, sy, sz = (int(v) for v in a.size.replace(" ", "").split(","))
    ox, oy, oz = (int(v) for v in a.origin.replace(" ", "").split(","))
    f = sample(a.expr, (sy, sz, sx), (ox, oy, oz))
    blocks = [b.strip() for b in a.blocks.split(",") if b.strip()]
    if a.mode == "band" or len(blocks) > 1:
        field_idx, pal = stratify(f, blocks)
    else:
        from mccore.schem_io import parse_state
        m = mask_of(f, a.op, a.threshold)
        if a.shell:
            m = shell_of(m, a.shell)
        pal = [{"Name": "minecraft:air"}, parse_state(blocks[0])]
        field_idx = np.where(m, 1, 0).astype(np.uint16)
    if a.ascii:
        print(ascii_slice(f, "y"))
    print(f"场 f(x,y,z) = {a.expr}")
    print(f"  尺寸 {sx}×{sy}×{sz}  值域 [{f.min():.3f}, {f.max():.3f}]  "
          f"非空格 {int((field_idx != 0).sum()):,}")
    if a.out:
        from mccore import structure_io as S
        p = S.write_structure(a.out, field_idx, pal, (ox, oy, oz), (sx, sy, sz),
                              name=Path(a.out).stem, author="structworkshop",
                              description=f"field: {a.expr}")
        print(f"  写 {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
