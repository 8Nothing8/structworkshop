"""三维模型 → 体素：把下载来的 `.glb` / `.gltf` / `.obj` 倒模成 Minecraft 结构。

流程（`voxelize_model`）::

    网格 → 归一化(居中/缩放到目标长边) → 三角面按 ~1 点/格 采样成表面体素
         → 形态学闭孔 → scipy.binary_fill_holes 灌成实心 → 最近面片标记材质

设计要点：

* **表面 + 实心**：表面体素保留原模型的细节；实心部分用「最近表面材质」传播
  （`distance_transform_edt(return_indices=True)`），所以内部不会是空白腔。
* **材质来自模型本身**：glTF 的 ``pbrMetallicRoughness.baseColorFactor`` 会被读出来，
  再交给调用方映射到 structworkshop 的色板（默认按**明度阶梯**映射，见 `mat_map_default`）。
* **缩放/旋转**由 `mckit.voxbrush` 的 `scale` / `rot90_*` 完成 —— 倒模一次，任意复用。
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import numpy as np

from mckit.voxbrush import Grid

_COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16,
         5125: np.uint32, 5126: np.float32}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9,
          "MAT4": 16}


# ------------------------------------------------------------------ glTF / GLB
def _read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    magic, _ver, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError(f"{path.name}: 不是 GLB")
    off, js, bins = 12, None, None
    while off < min(length, len(data)):
        clen, ctype = struct.unpack_from("<II", data, off)
        off += 8
        chunk = data[off:off + clen]
        off += clen
        if ctype == 0x4E4F534A:
            js = json.loads(chunk.decode("utf-8"))
        elif ctype == 0x004E4942:
            bins = chunk
    if js is None:
        raise ValueError(f"{path.name}: 缺 JSON 块")
    return js, (bins or b"")


def _accessor(g: dict, bins: bytes, idx: int) -> np.ndarray:
    acc = g["accessors"][idx]
    bv = g["bufferViews"][acc["bufferView"]]
    dt = np.dtype(_COMP[acc["componentType"]])
    n = _NCOMP[acc["type"]]
    base = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = bv.get("byteStride") or dt.itemsize * n
    count = acc["count"]
    if stride == dt.itemsize * n:
        arr = np.frombuffer(bins, dtype=dt, count=count * n, offset=base)
        arr = arr.reshape(count, n)
    else:                                   # 交错缓冲
        arr = np.empty((count, n), dtype=dt)
        for i in range(count):
            arr[i] = np.frombuffer(bins, dtype=dt, count=n,
                                   offset=base + i * stride)
    return arr


def _node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], float).reshape(4, 4).T   # 列主序
    m = np.eye(4)
    if any(k in node for k in ("scale", "rotation", "translation")):
        t = np.array(node.get("translation", [0, 0, 0]), float)
        s = np.array(node.get("scale", [1, 1, 1]), float)
        q = np.array(node.get("rotation", [0, 0, 0, 1]), float)
        x, y, z, w = q
        r = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ])
        m[:3, :3] = r * s[None, :]
        m[:3, 3] = t
    return m


def load_gltf(path) -> dict:
    """读 `.glb` / `.gltf`，返回 ``{"prims": [(verts, tris, mat)], "materials": [...]}``。

    顶点/法线按节点的世界变换烘焙好（多部件模型如整把枪都能正确落位）。
    """
    path = Path(path)
    if path.suffix.lower() == ".glb":
        g, bins = _read_glb(path)
    else:
        g = json.loads(path.read_text(encoding="utf-8"))
        uri = g.get("buffers", [{}])[0].get("uri", "")
        bins = (path.parent / uri.replace("file://", "")).read_bytes() \
            if uri and not uri.startswith("data:") else b""
    mats = []
    for m in g.get("materials", []):
        pbr = m.get("pbrMetallicRoughness", {})
        c = pbr.get("baseColorFactor", [0.7, 0.7, 0.7, 1.0])
        mats.append({"name": m.get("name", f"mat{len(mats)}"),
                     "color": [float(x) for x in c[:3]]})
    if not mats:
        mats = [{"name": "default", "color": [0.6, 0.6, 0.6]}]

    prims: list[tuple[np.ndarray, np.ndarray, int]] = []
    scene = g.get("scenes", [{}])[g.get("scene", 0)]
    stack = [(i, np.eye(4)) for i in scene.get("nodes", [])]
    seen = 0
    while stack:
        ni, parent = stack.pop()
        node = g["nodes"][ni]
        world = parent @ _node_matrix(node)
        if "mesh" in node:
            for prim in g["meshes"][node["mesh"]].get("primitives", []):
                if prim.get("mode", 4) != 4:
                    continue
                attrs = prim["attributes"]
                pos = _accessor(g, bins, attrs["POSITION"]).astype(float)
                if pos.size == 0:
                    continue
                idx = _accessor(g, bins, prim["indices"]).reshape(-1).astype(np.int64) \
                    if "indices" in prim else np.arange(len(pos), dtype=np.int64)
                tris = idx.reshape(-1, 3)
                xyz = np.concatenate([pos, np.ones((len(pos), 1))], 1) @ world.T
                prims.append((xyz[:, :3], tris, int(prim.get("material", 0))))
                seen += 1
        for ch in node.get("children", []):
            stack.append((ch, world))
    return {"prims": prims, "materials": mats}


def load_obj(path) -> dict:
    """极简 OBJ：只认 ``v`` 和 ``f``（含 ``f a/b/c`` 形式），按对象名当材质。"""
    path = Path(path)
    verts: list[list[float]] = []
    groups: dict[str, list[tuple[int, int, int]]] = {}
    cur = "default"
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("v "):
            verts.append([float(x) for x in line.split()[1:4]])
        elif line.startswith(("g ", "o ", "usemtl ")):
            cur = line.split(None, 1)[1].strip() if len(line.split()) > 1 else "default"
            groups.setdefault(cur, [])
        elif line.startswith("f "):
            idx = []
            for tok in line.split()[1:]:
                idx.append(int(tok.split("/")[0]) - 1)
            for k in range(1, len(idx) - 1):        # 三角化扇形
                groups.setdefault(cur, []).append((idx[0], idx[k], idx[k + 1]))
    if not verts:
        raise ValueError(f"{path.name}: 没有顶点")
    V = np.array(verts, float)
    prims, mats, names = [], [], []
    for name, faces in groups.items():
        if not faces:
            continue
        names.append(name)
        prims.append((V, np.array(faces, np.int64), len(names) - 1))
        mats.append({"name": name, "color": [0.6, 0.6, 0.6]})
    if not prims:
        prims = [(V, np.array([[0, 1, 2]], np.int64), 0)]
        mats = [{"name": "default", "color": [0.6, 0.6, 0.6]}]
    return {"prims": prims, "materials": mats}


def load_model(path) -> dict:
    path = Path(path)
    return load_obj(path) if path.suffix.lower() == ".obj" else load_gltf(path)


# ------------------------------------------------------------------ 体素化
def _sample_tri(p0, p1, p2, density: float = 1.0,
                cap: int = 240) -> np.ndarray:
    """在三角面上按约 ``density`` 点/格的密度取点（边采样，够密不漏）。"""
    e = max(float(np.linalg.norm(p1 - p0)), float(np.linalg.norm(p2 - p0)),
            float(np.linalg.norm(p2 - p1)))
    n = int(min(cap, max(1, math.ceil(e * density))))
    i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
    m = (i + j) <= n
    a = (i[m] / n)[:, None]
    b = (j[m] / n)[:, None]
    return p0 * (1 - a - b) + p1 * a + p2 * b


def voxelize_model(path, long_side: int = 96, density: float = 1.0,
                   solid: bool = True, pad: int = 1, max_tris: int = 400000,
                   verbose: bool = False) -> dict:
    """把模型体素化。

    返回 ``{"labels": uint8 体素(0=空), "mats": [...], "shape": (sx,sy,sz)}``：

    * ``labels[y, z, x]`` = 材质槽位 + 1（0 = 空）；
    * ``mats`` 是模型自带的材质列表（带 RGB），调用方决定映到哪些方块。
    """
    from scipy import ndimage  # noqa: PLC0415

    m = load_model(path)
    prims = m["prims"]
    if not prims:
        raise ValueError(f"{path.name}: 没有可用的三角面")
    allv = np.concatenate([p[0] for p in prims], 0)
    lo, hi = allv.min(0), allv.max(0)
    ext = hi - lo                       # (ex, ey, ez) 模型坐标
    scale = float(long_side) / max(float(ext.max()), 1e-6)
    # 数组布局约定：(sy, sz, sx) —— 与 mccore / Grid 一致
    sx = int(np.ceil(ext[0] * scale)) + 2 * pad + 2
    sy = int(np.ceil(ext[1] * scale)) + 2 * pad + 2
    sz = int(np.ceil(ext[2] * scale)) + 2 * pad + 2

    def to_vox(v):
        return (v - lo) * scale + pad + 1.0

    surf = np.zeros((sy, sz, sx), bool)
    lab = np.zeros((sy, sz, sx), np.int32)
    nt = 0
    for verts, tris, mat in prims:
        vv = to_vox(verts)
        for t in tris[:max_tris]:
            p0, p1, p2 = vv[t[0]], vv[t[1]], vv[t[2]]
            pts = _sample_tri(p0, p1, p2, density)
            ix = np.floor(pts[:, 0]).astype(np.int64)
            iy = np.floor(pts[:, 1]).astype(np.int64)
            iz = np.floor(pts[:, 2]).astype(np.int64)
            ok = (ix >= 0) & (ix < sx) & (iy >= 0) & (iy < sy) & (iz >= 0) & (iz < sz)
            ix, iy, iz = ix[ok], iy[ok], iz[ok]
            if len(ix):
                surf[iy, iz, ix] = True
                lab[iy, iz, ix] = mat + 1
            nt += 1
    # 闭孔（三角面之间的小缝）→ 灌实心
    closed = ndimage.binary_closing(surf, structure=np.ones((3, 3, 3)), iterations=1)
    if solid:
        filled = ndimage.binary_fill_holes(closed)
    else:
        filled = closed
    # 内部材质 = 最近表面体素的材质（整体一致，不出现内部空洞）
    if filled.any():
        ind = ndimage.distance_transform_edt(~closed, return_distances=False,
                                            return_indices=True)
        lab_full = lab[tuple(ind)]
        lab_full[~filled] = 0
    else:
        lab_full = lab
    # 朝向：模型 y 向上 → 体素 y 向上（数组轴 0）。glTF 常见 y-up，这里保持一致。
    labels = lab_full.astype(np.uint8)
    if verbose:
        print(f"    {Path(path).name}: {nt} 面 → {sx}x{sy}x{sz}，"
              f"实心 {int((labels > 0).sum())} 格，材质 {len(m['materials'])}")
    return {"labels": labels, "mats": m["materials"], "shape": (sx, sy, sz),
            "faces": nt}


# ------------------------------------------------------------------ 色板映射
def lum(rgb) -> float:
    r, g, b = rgb[:3]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def mat_map_default(rgb, name: str = "") -> str:
    """模型材质 → structworkshop 方块：**明度阶梯**（枪械/骨骼的单色纪律）。

    只有明显偏黄铜/金的材质会走金属色，其余一律落在黑钢→白骨这一段。
    """
    r, g, b = rgb[:3]
    L = lum(rgb)
    warm = r > 0.30 and r > b * 1.35 and g > b * 1.05 and (r - b) > 0.12
    n = (name or "").lower()
    if warm and L < 0.75:
        return "waxed_exposed_copper" if L < 0.45 else "gold_block"
    if "glass" in n or (b > r * 1.25 and b > g * 1.15 and L > 0.35):
        return "light_blue_stained_glass"
    if L < 0.06:
        return "black_concrete"
    if L < 0.16:
        return "polished_blackstone"
    if L < 0.30:
        return "deepslate_tiles"
    if L < 0.44:
        return "polished_deepslate"
    if L < 0.58:
        return "deepslate"
    if L < 0.70:
        return "gray_concrete"
    if L < 0.82:
        return "iron_block"
    if L < 0.90:
        return "bone_block"
    return "smooth_quartz"


def mat_map_bone(rgb, name: str = "") -> str:
    """骨骼：一律白骨（颌/牙用方解石提亮，眼窝等处仍由几何决定阴影）。"""
    L = lum(rgb)
    if L < 0.30:
        return "bone_block"
    if L > 0.72:
        return "smooth_quartz"
    if L > 0.55:
        return "bone_block"
    return "calcite"


def mat_map_metal(rgb, name: str = "") -> str:
    """枪械：冷灰金属明度阶梯（黄铜件保留金色）。"""
    r, _, b = rgb[:3]
    L = lum(rgb)
    if r > 0.30 and r > b * 1.35 and (r - b) > 0.12 and L < 0.7:
        return "waxed_exposed_copper"
    if L < 0.10:
        return "black_concrete"
    if L < 0.22:
        return "polished_blackstone"
    if L < 0.36:
        return "polished_deepslate"
    if L < 0.50:
        return "deepslate_tiles"
    if L < 0.64:
        return "gray_concrete"
    if L < 0.78:
        return "iron_block"
    return "light_gray_concrete"


def mat_map_brass(rgb, name: str = "") -> str:
    """弹链 / 弹壳：黄铜 + 铅灰。"""
    L = lum(rgb)
    r, _, b = rgb[:3]
    if r > 0.35 and r > b * 1.3:
        return "gold_block" if L > 0.45 else "waxed_exposed_copper"
    if L < 0.30:
        return "polished_blackstone"
    return "iron_block"


MAPS = {"auto": mat_map_default, "bone": mat_map_bone, "metal": mat_map_metal,
        "brass": mat_map_brass}


def to_grid(path, long_side: int = 96, mat_map=mat_map_default,
            fallback: str = "polished_deepslate", solid: bool = True,
            verbose: bool = False) -> Grid:
    """体素化 + 上色 → `mckit.voxbrush.Grid`（可直接 stamp 进雕塑）。"""
    out = voxelize_model(path, long_side=long_side, solid=solid, verbose=verbose)
    labels, mats = out["labels"], out["mats"]
    sx, sy, sz = out["shape"]
    g = Grid(sx, sy, sz)
    slots = {}
    for i in range(1, int(labels.max()) + 1):
        src = mats[i - 1] if i - 1 < len(mats) else {"name": "", "color": [0.5] * 3}
        slots[i] = g.mat(mat_map(src["color"], src.get("name", "")) or fallback)
    lut = np.zeros(256, np.uint16)
    for k, v in slots.items():
        lut[k] = v
    g.v = lut[labels]
    return g
