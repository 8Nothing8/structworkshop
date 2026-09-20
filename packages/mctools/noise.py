"""程序化噪声场（词汇表对齐 Axiom ``com/moulberry/axiom/noise/``）。

所有采样器吃**世界坐标**、吐 ``[0, 1]`` 的 float32 数组，且完全确定性：
同样的 ``(kind, seed, coords)`` 永远得到同样的值，所以编辑结果可复现、可 diff。
实现上按块（chunk）计算且与分块方式无关 —— 结果只取决于坐标本身。

Axiom 的噪声族：White / Simplex / Worley / VoronoiEdges / Metaball /
Splatter / FBM（octaves/gain/lacunarity）/ SimplexDomainWarp。
"""
from __future__ import annotations

import numpy as np

__all__ = ["KINDS", "sample", "fbm", "white", "simplex", "worley",
           "voronoi_edges", "metaball", "splatter", "permutation"]

KINDS = ("white", "simplex", "fbm", "worley", "voronoi", "metaball",
         "splatter")

_F3 = 1.0 / 3.0
_G3 = 1.0 / 6.0
_CHUNK = 1 << 20          # 每批 100 万点，控制峰值内存

_GRAD3 = np.array([[1, 1, 0], [-1, 1, 0], [1, -1, 0], [-1, -1, 0],
                   [1, 0, 1], [-1, 0, 1], [1, 0, -1], [-1, 0, -1],
                   [0, 1, 1], [0, -1, 1], [0, 1, -1], [0, -1, -1]],
                  dtype=np.float64)
# 六个四面体分支的 (i1,j1,k1) / (i2,j2,k2)
_BRANCH = {0: ((1, 0, 0), (1, 1, 0)), 1: ((1, 0, 0), (1, 0, 1)),
           2: ((0, 0, 1), (1, 0, 1)), 3: ((0, 0, 1), (0, 1, 1)),
           4: ((0, 1, 0), (0, 1, 1)), 5: ((0, 1, 0), (1, 1, 0))}
_I1 = np.array([_BRANCH[i][0][0] for i in range(6)], dtype=np.int64)
_J1 = np.array([_BRANCH[i][0][1] for i in range(6)], dtype=np.int64)
_K1 = np.array([_BRANCH[i][0][2] for i in range(6)], dtype=np.int64)
_I2 = np.array([_BRANCH[i][1][0] for i in range(6)], dtype=np.int64)
_J2 = np.array([_BRANCH[i][1][1] for i in range(6)], dtype=np.int64)
_K2 = np.array([_BRANCH[i][1][2] for i in range(6)], dtype=np.int64)


# ------------------------------------------------------------------ helpers
def _chunked(shape, fn, *arrays, chunk=_CHUNK):
    """把坐标摊平后分块调用 ``fn``，再拼回原形状。"""
    flat = [np.asarray(a, dtype=np.float64).reshape(-1) for a in arrays]
    n = flat[0].size
    out = np.empty(n, dtype=np.float32)
    for i in range(0, n, chunk):
        sl = slice(i, min(i + chunk, n))
        out[sl] = fn(*[a[sl] for a in flat])
    return out.reshape(shape)


def _hash01(ix, iy, iz, seed):
    """整数格坐标 → [0,1) 的确定性哈希（白噪声 / 抖动 / 团块共用）。"""
    ix = np.asarray(ix, dtype=np.int64)
    iy = np.asarray(iy, dtype=np.int64)
    iz = np.asarray(iz, dtype=np.int64)
    h = (ix * np.int64(374761393) + iy * np.int64(668265263) +
         iz * np.int64(2147483647) + np.int64(int(seed)) * np.int64(1013904223))
    h &= np.int64(0x7FFFFFFF)
    h = (h ^ (h >> np.int64(13))) * np.int64(1274126177)
    h &= np.int64(0x7FFFFFFF)
    h = h ^ (h >> np.int64(16))
    return (h & np.int64(0xFFFFFF)).astype(np.float64) / float(0x1000000)


def permutation(seed: int) -> np.ndarray:
    """512 长置换表（前后 256 相同，省一次取模）。"""
    p = np.random.default_rng(int(seed) & 0xFFFFFFFF).permutation(256)
    return np.concatenate([p, p]).astype(np.int64)


# ------------------------------------------------------------------ primitives
def _simplex3(x, y, z, perm):
    """经典 3D simplex（Gustavson 版），返回值约 [-1, 1]。"""
    s = (x + y + z) * _F3
    i = np.floor(x + s)
    j = np.floor(y + s)
    k = np.floor(z + s)
    t = (i + j + k) * _G3
    x0, y0, z0 = x - (i - t), y - (j - t), z - (k - t)

    c1 = x0 >= y0
    c2 = y0 >= z0
    c3 = x0 >= z0
    br = np.where(c1, np.where(c2, 0, np.where(c3, 1, 2)),
                  np.where(~c2, 3, np.where(~c3, 4, 5)))
    i1, j1, k1 = _I1[br], _J1[br], _K1[br]
    i2, j2, k2 = _I2[br], _J2[br], _K2[br]

    x1, y1, z1 = x0 - i1 + _G3, y0 - j1 + _G3, z0 - k1 + _G3
    x2, y2, z2 = x0 - i2 + 2 * _G3, y0 - j2 + 2 * _G3, z0 - k2 + 2 * _G3
    x3, y3, z3 = x0 - 1 + 3 * _G3, y0 - 1 + 3 * _G3, z0 - 1 + 3 * _G3

    ii = i.astype(np.int64) & 255
    jj = j.astype(np.int64) & 255
    kk = k.astype(np.int64) & 255
    g0 = perm[ii + perm[jj + perm[kk]]] % 12
    g1 = perm[ii + i1 + perm[jj + j1 + perm[kk + k1]]] % 12
    g2 = perm[ii + i2 + perm[jj + j2 + perm[kk + k2]]] % 12
    g3 = perm[ii + 1 + perm[jj + 1 + perm[kk + 1]]] % 12

    def corner(dx, dy, dz, g):
        tt = np.maximum(0.6 - dx * dx - dy * dy - dz * dz, 0.0)
        gr = _GRAD3[g]
        return tt ** 4 * (gr[:, 0] * dx + gr[:, 1] * dy + gr[:, 2] * dz)

    n = (corner(x0, y0, z0, g0) + corner(x1, y1, z1, g1) +
         corner(x2, y2, z2, g2) + corner(x3, y3, z3, g3))
    return 32.0 * n


def _voronoi(x, y, z, seed, edges=False):
    """F1 / (F2-F1) 距离（Worley / VoronoiEdges）。"""
    ix = np.floor(x).astype(np.int64)
    iy = np.floor(y).astype(np.int64)
    iz = np.floor(z).astype(np.int64)
    d1 = np.full(x.shape, np.inf)
    d2 = np.full(x.shape, np.inf)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                cx, cy, cz = ix + dx, iy + dy, iz + dz
                px = cx + _hash01(cx, cy, cz, seed)
                py = cy + _hash01(cx, cy, cz, seed + 7)
                pz = cz + _hash01(cx, cy, cz, seed + 13)
                d = (px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2
                nearer = d < d1
                d2 = np.where(nearer, d1, np.minimum(d2, d))
                d1 = np.where(nearer, d, d1)
    f1 = np.sqrt(d1)
    if not edges:
        return np.clip(f1 / 1.1, 0.0, 1.0)
    return np.clip((np.sqrt(d2) - f1) / 0.6, 0.0, 1.0)


def _blobs(x, y, z, seed, cell, sigma_blocks):
    """抖动格点上的高斯团块之和（Metaball / Splatter），与分块方式无关。"""
    cell = max(1e-6, float(cell))
    sigma = max(1e-6, float(sigma_blocks))
    ix = np.floor(x / cell).astype(np.int64)
    iy = np.floor(y / cell).astype(np.int64)
    iz = np.floor(z / cell).astype(np.int64)
    out = np.zeros(x.shape, dtype=np.float64)
    k = 1.0 / (2.0 * sigma * sigma)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                cx, cy, cz = ix + dx, iy + dy, iz + dz
                px = (cx + _hash01(cx, cy, cz, seed)) * cell
                py = (cy + _hash01(cx, cy, cz, seed + 7)) * cell
                pz = (cz + _hash01(cx, cy, cz, seed + 13)) * cell
                d2 = (px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2
                out += np.exp(-d2 * k)
    return np.clip(out, 0.0, 1.0)


# ------------------------------------------------------------------ samplers
def white(x, y, z, *, seed=0):
    return _hash01(np.floor(x), np.floor(y), np.floor(z), seed).astype(np.float64)


def simplex(x, y, z, *, seed=0):
    return (_simplex3(x, y, z, permutation(seed)) + 1.0) * 0.5


def fbm(x, y, z, *, seed=0, octaves=4, gain=0.5, lacunarity=2.0):
    """分形叠加：``octaves`` 层，每层频率 ×lacunarity、幅度 ×gain。"""
    perm = permutation(seed)
    total = np.zeros(x.shape, dtype=np.float64)
    amp, freq, norm = 1.0, 1.0, 0.0
    for o in range(max(1, int(octaves))):
        total += amp * (_simplex3(x * freq, y * freq, z * freq, perm) + 1.0) * 0.5
        norm += amp
        amp *= float(gain)
        freq *= float(lacunarity)
    return total / max(norm, 1e-9)


def worley(x, y, z, *, seed=0):
    return _voronoi(x, y, z, seed, edges=False)


def voronoi_edges(x, y, z, *, seed=0):
    return _voronoi(x, y, z, seed, edges=True)


def metaball(x, y, z, *, seed=0, cell=8.0, radius=3.0):
    return _blobs(x, y, z, seed, cell, radius)


def splatter(x, y, z, *, seed=0, cell=12.0, radius=1.2):
    return _blobs(x, y, z, seed, cell, radius)


# ------------------------------------------------------------------ entry
def sample(kind: str, x, y, z, *, scale=1.0, seed=0, octaves=4, gain=0.5,
           lacunarity=2.0, warp=0.0, aniso=(1.0, 1.0, 1.0)) -> np.ndarray:
    """统一入口：按世界坐标采样噪声，返回 ``[0,1]``。

    ``scale``：特征尺度（格），越大越平缓。
    ``warp`` ：>0 时先做域扭曲（SimplexDomainWarp，单位 = 1/scale）。
    ``aniso``：三轴各向异性拉伸（>1 = 沿该轴拉长特征）。
    ``octaves`` / ``gain`` / ``lacunarity``：FBM 参数；metaball/splatter
    复用 ``octaves`` 当团块数量、``gain`` 当团块半径系数。
    """
    kind = (kind or "simplex").lower()
    if kind not in KINDS:
        raise ValueError(f"未知噪声 {kind!r}（可选 {', '.join(KINDS)}）")
    shape = np.shape(x)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    ax, ay, az = (max(1e-6, float(v)) for v in aniso)
    freq = 1.0 / max(1e-6, float(scale))
    xs, ys, zs = x * freq / ax, y * freq / ay, z * freq / az

    if warp and float(warp) > 0:
        w = float(warp)
        xw = _chunked(shape, lambda a, b, c: _simplex3(a, b, c,
                                                      permutation(seed + 101)),
                      xs, ys, zs)
        yw = _chunked(shape, lambda a, b, c: _simplex3(a, b, c,
                                                      permutation(seed + 202)),
                      xs, ys, zs)
        zw = _chunked(shape, lambda a, b, c: _simplex3(a, b, c,
                                                      permutation(seed + 303)),
                      xs, ys, zs)
        xs, ys, zs = xs + xw * w, ys + yw * w, zs + zw * w

    def fn(a, b, c):
        if kind == "white":
            return white(a, b, c, seed=seed)
        if kind == "simplex":
            return simplex(a, b, c, seed=seed)
        if kind == "fbm":
            return fbm(a, b, c, seed=seed, octaves=octaves, gain=gain,
                       lacunarity=lacunarity)
        if kind == "worley":
            return worley(a, b, c, seed=seed)
        if kind == "voronoi":
            return voronoi_edges(a, b, c, seed=seed)
        # 团块尺度按「特征尺度」归一：cell ≈ 2 个特征、半径由 gain 调
        g = max(0.05, float(gain))
        if kind == "metaball":
            return metaball(a, b, c, seed=seed, cell=2.0,
                            radius=0.35 + 0.5 * g)
        return splatter(a, b, c, seed=seed, cell=2.4,
                        radius=0.3 + 1.0 * g)

    return _chunked(shape, fn, xs, ys, zs)
