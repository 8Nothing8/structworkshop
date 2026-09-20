"""模块装配引擎：按接口（port）把结构模块摆到一起，含兼容判定、吸附与冲突报告。

Two modes:
  1. explicit  -- plan gives every instance a pos/rot, connections validated
  2. auto      -- plan gives a connection graph ("A:west <-> B:east"); the
                  engine greedily snaps ports: rotation search + collision
                  check, then writes the assembled .schem

Ports are declared in a module spec (JSON sidecar `<stem>.module.json` or the
litematic Metadata tag `ModuleSpec`). See the skill
`skills/minecraft-modular-building/` for the spec format.

Usage:
  python -m mccore.assemble plan.json --out build.schem
  python -m mccore.assemble plan.json --out build.schem --auto
  python -m mccore.assemble --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from mccore import structure_io as S  # noqa: E402
from mccore.module_lib import load_spec, find_module, port_bbox, port_from_bbox  # noqa: E402
from mccore.paths import write_text_lf  # noqa: E402

FACES = ("west", "east", "north", "south", "up", "down")
OPP = {"west": "east", "east": "west", "north": "south", "south": "north",
       "up": "down", "down": "up"}
ROT_FACE = [
    {"west": "west", "east": "east", "north": "north", "south": "south",
     "up": "up", "down": "down"},
    {"west": "north", "north": "east", "east": "south", "south": "west",
     "up": "up", "down": "down"},
    {"west": "east", "east": "west", "north": "south", "south": "north",
     "up": "up", "down": "down"},
    {"west": "south", "south": "east", "east": "north", "north": "west",
     "up": "up", "down": "down"},
]
PASSAGE_TYPES = {"passage", "door", "stair_up", "stair_down"}
#: ``interface`` = 通用接口：**与任何类型都能接**（只看面贴合 + 面内重叠 + 尺寸），
#: 用于柱子分段、实心构件对接这种「接口里是什么都行」的场合。
ANY_INTERFACE = "interface"
#: 已知（内置推荐）类型集合——用于判断一个 type 是不是「自写的」。
KNOWN_TYPES = frozenset(
    "passage door stair_up stair_down redstone_in redstone_out fluid_in "
    "fluid_out item_in item_out power_in power_out shaft anchor light "
    "vent window interface".split())
COMPAT = {
    "redstone_in": {"redstone_out"}, "redstone_out": {"redstone_in"},
    "fluid_in": {"fluid_out"}, "fluid_out": {"fluid_in"},
    "item_in": {"item_out"}, "item_out": {"item_in"},
    "power_in": {"power_out"}, "power_out": {"power_in"},
    "shaft": {"shaft"}, "anchor": {"anchor"},
}


def types_compatible(a: str, b: str) -> bool:
    """两个接口的类型能不能接？

    * 任一边是 ``interface`` → 可以（通用接口，只按几何配）；
    * 通道家族（passage/door/stair_up/stair_down）彼此可接；
    * 同名同类（passage/shaft/anchor）可接；
    * 其余看 ``COMPAT`` 的成对表（红石/流体/物流/电力的 in↔out）；
    * **自写类型**（不在 :data:`KNOWN_TYPES` 里）→ **同名即可接**，且自动认后缀：
      ``foo_in`` ↔ ``foo_out``、``foo_up`` ↔ ``foo_down`` 也当一对（x_in_↔out 的泛化）。

    **不看体素实心空心**：柱子分段对接的实心面与风道的空心口走同一条路。
    """
    if a == ANY_INTERFACE or b == ANY_INTERFACE:
        return True
    if a in PASSAGE_TYPES and b in PASSAGE_TYPES:
        return True
    if a == b and a in ("passage", "shaft", "anchor"):
        return True
    if b in COMPAT.get(a, set()) or a in COMPAT.get(b, set()):
        return True
    # 自写类型：同名可接；`*_in`/`*_out`、`*_up`/`*_down` 成对可接
    if a not in KNOWN_TYPES or b not in KNOWN_TYPES:
        if a == b:
            return True
        for lo, hi in (("_in", "_out"), ("_out", "_in"),
                       ("_up", "_down"), ("_down", "_up")):
            if a.endswith(lo) and b == a[: -len(lo)] + hi:
                return True
    return False


def rot_dims(sx: int, sy: int, sz: int, r: int) -> tuple[int, int, int]:
    return (sx, sy, sz) if r % 2 == 0 else (sz, sy, sx)


def rot_point(x: int, y: int, z: int, sx: int, sy: int, sz: int, r: int):
    """Local point -> local point inside the rotated bounding box."""
    if r == 0:
        return (x, y, z)
    if r == 1:
        return (sz - 1 - z, y, x)
    if r == 2:
        return (sx - 1 - x, y, sz - 1 - z)
    return (z, y, sx - 1 - x)


def rot_voxels(v: np.ndarray, r: int) -> np.ndarray:
    """Rotate region array (sy, sz, sx) clockwise viewed from above."""
    if r == 0:
        return v.copy()
    if r == 1:
        return np.transpose(v, (0, 2, 1))[:, :, ::-1]
    if r == 2:
        return v[:, ::-1, ::-1]
    return np.transpose(v, (0, 2, 1))[:, ::-1, :]


def rot_axis90(v: np.ndarray, axis: int, times: int = 1) -> np.ndarray:
    """绕 ``axis``(0=x,1=y,2=z) 旋转 ``times`` 个 90°（数组布局 (sy, sz, sx)）。

    axis=1 与 :func:`rot_voxels` 同向，保证旧数据的 yaw 语义不变。
    """
    out = v
    for _ in range(int(times) % 4):
        if axis == 0:        # about X: Y <-> Z
            out = np.rot90(out, 1, axes=(0, 1))
        elif axis == 1:      # about Y: X <-> Z
            out = np.transpose(out, (0, 2, 1))[:, :, ::-1]
        else:                # about Z: X <-> Y
            out = np.rot90(out, 1, axes=(0, 2))
    return np.ascontiguousarray(out)


def scale_voxels(v: np.ndarray, f: int) -> np.ndarray:
    """整数倍数缩放（数组布局 ``(sy, sz, sx)``）。

    ``f > 1`` 用重复采样**放大**（保持体素风格、不插值），把倒模的小枪变成
    建筑级大枪；``f < 0`` 用块采样**缩小** ``|f|`` 倍。
    """
    f = int(f)
    if f == 1:
        return v.copy()
    if f > 1:
        return np.ascontiguousarray(np.repeat(np.repeat(np.repeat(v, f, 0), f, 1), f, 2))
    k = -f
    sy, sz, sx = v.shape
    ny, nz, nx = sy // k, sz // k, sx // k
    if ny == 0 or nz == 0 or nx == 0:
        raise ValueError(f"缩小倍数 {k} 过大（尺寸 {sx}x{sy}x{sz}）")
    sub = v[:ny * k, :nz * k, :nx * k]
    return np.ascontiguousarray(sub.reshape(ny, k, nz, k, nx, k)[:, 0, :, 0, :, 0])


def scale_dims(dims, f: int) -> list[int]:
    """尺寸 ``[sx, sy, sz]`` 随 :func:`scale_voxels` 变化。"""
    f = int(f)
    k = f if f > 0 else 1.0 / abs(f)
    return [max(1, int(round(int(d) * k))) for d in dims]


def rot_axis_dims(dims, axis: int, times: int = 1) -> list[int]:
    """尺寸 [sx, sy, sz] 随 :func:`rot_axis90` 的置换。"""
    sx, sy, sz = (int(v) for v in dims)
    for _ in range(int(times) % 4):
        if axis == 0:
            sy, sz = sz, sy
        elif axis == 1:
            sx, sz = sz, sx
        else:
            sx, sy = sy, sx
    return [sx, sy, sz]


def port_anchor3d(port: dict, sx: int, sy: int, sz: int) -> tuple[int, int, int]:
    """3D anchor (min corner of the opening) on the module boundary.

    圆形接口存的是「圆心 + 直径」，先化成外接矩形再取角（见
    :func:`mccore.module_lib.port_bbox`）。
    """
    face = port["face"]
    o, s = port_bbox(port)
    if face == "west":
        return (0, o[0], o[1])
    if face == "east":
        return (sx - 1, o[0], o[1])
    if face == "north":
        return (o[1], o[0], 0)
    if face == "south":
        return (o[1], o[0], sz - 1)
    if face == "up":
        return (o[0], sy - 1, o[1])
    return (o[0], 0, o[1])


def transform_port(port: dict, sx: int, sy: int, sz: int, r: int):
    """Port spec -> port spec after a 90-degree step rotation.

    Both corners of the opening are rotated and re-minned, because the
    in-face axis direction flips under rotation. 圆形接口先化成外接矩形、
    转完再还原成圆心（形状/直径不变，圆在 90° 旋转下还是同一个圆）。
    """
    face = port["face"]
    o, s = port_bbox(port)
    if face in ("west", "east"):
        c0 = (0, o[0], o[1]) if face == "west" else (sx - 1, o[0], o[1])
        c1 = (c0[0], c0[1] + s[0] - 1, c0[2] + s[1] - 1)
        n0, n1 = rot_point(*c0, sx, sy, sz, r), rot_point(*c1, sx, sy, sz, r)
        nface = ROT_FACE[r][face]
        if nface in ("west", "east"):
            org = [min(n0[1], n1[1]), min(n0[2], n1[2])]
        else:
            org = [min(n0[1], n1[1]), min(n0[0], n1[0])]
    elif face in ("north", "south"):
        c0 = (o[1], o[0], 0) if face == "north" else (o[1], o[0], sz - 1)
        c1 = (c0[0] + s[1] - 1, c0[1] + s[0] - 1, c0[2])
        n0, n1 = rot_point(*c0, sx, sy, sz, r), rot_point(*c1, sx, sy, sz, r)
        nface = ROT_FACE[r][face]
        if nface in ("west", "east"):
            org = [min(n0[1], n1[1]), min(n0[2], n1[2])]
        else:
            org = [min(n0[1], n1[1]), min(n0[0], n1[0])]
    else:  # up / down
        y = sy - 1 if face == "up" else 0
        c0 = (o[0], y, o[1])
        c1 = (c0[0] + s[0] - 1, y, c0[2] + s[1] - 1)
        n0, n1 = rot_point(*c0, sx, sy, sz, r), rot_point(*c1, sx, sy, sz, r)
        nface = ROT_FACE[r][face]
        org = [min(n0[0], n1[0]), min(n0[2], n1[2])]
        s = [s[1], s[0]] if r % 2 else list(s)
    out = {"id": port["id"], "type": port["type"], "face": nface,
           "origin": org, "size": s, "tags": port.get("tags", [])}
    if port.get("shape"):
        out["shape"] = port["shape"]
    return port_from_bbox(out, org, s)


class Assembler:
    def __init__(self, region: tuple[int, int, int] | None = None,
                 *, overlap: bool = False):
        """``overlap=True`` 时**允许模块堆叠**（互相压着放）。

        默认 ``False``（兼容旧行为）：显式 ``place`` 碰到已有方块就报冲突。
        需要「把模块摞在一起」（生成器/AI 的拼装）时开它：后放的模块
        在重叠区**盖过**先放的（与工作台里的装配语义一致）。
        """
        self.overlap = bool(overlap)
        self.region = list(region or (128, 128, 128))
        self.origin = [0, 0, 0]  # world coords of grid corner (can go negative)
        self.world = np.zeros((self.region[1], self.region[2], self.region[0]),
                              dtype=np.uint16)
        self.palette = [{"Name": "minecraft:air"}]
        self.key2idx = {"minecraft:air|{}": 0}
        self.instances: list[dict] = []
        self.module_cache: dict = {}

    def _palette_idx(self, entry: dict) -> int:
        key = entry["Name"] + "|" + json.dumps(
            dict(sorted(entry.get("Properties", {}).items())))
        if key not in self.key2idx:
            self.palette.append(entry)
            self.key2idx[key] = len(self.palette) - 1
        return self.key2idx[key]

    def _grow(self, p0, p1) -> None:
        """Ensure the grid covers [p0, p1) in world coordinates."""
        o0 = self.origin
        o1 = [o0[i] + self.region[i] for i in range(3)]
        n0 = [min(o0[i], p0[i]) for i in range(3)]
        n1 = [max(o1[i], p1[i]) for i in range(3)]
        if n0 == o0 and n1 == o1:
            return
        nreg = [n1[i] - n0[i] for i in range(3)]
        w = np.zeros((nreg[1], nreg[2], nreg[0]), dtype=np.uint16)
        ox = o0[0] - n0[0]
        oy = o0[1] - n0[1]
        oz = o0[2] - n0[2]
        w[oy:oy + self.region[1], oz:oz + self.region[2],
          ox:ox + self.region[0]] = self.world
        self.world = w
        self.region = nreg
        self.origin = n0

    def _slab(self, pos, shape):
        sx, sy, sz = shape[2], shape[0], shape[1]
        ox, oy, oz = self.origin
        return self.world[pos[1] - oy:pos[1] - oy + sy,
                          pos[2] - oz:pos[2] - oz + sz,
                          pos[0] - ox:pos[0] - ox + sx]

    def can_place(self, vrot: np.ndarray, pos: tuple[int, int, int]) -> bool:
        """能不能放在 ``pos``（只看重叠；``overlap=True`` 时永远能）。"""
        if self.overlap:
            return True
        sy, sz, sx = vrot.shape
        p1 = (pos[0] + sx, pos[1] + sy, pos[2] + sz)
        self._grow(list(pos), list(p1))
        slab = self._slab(pos, vrot.shape)
        return not ((slab != 0) & (vrot != 0)).any()

    def place(self, ref: str, module: dict, pos: tuple[int, int, int],
              rot: int, check: bool | None = None) -> dict:
        """放一个模块实例。

        ``check=None``（默认）看 :attr:`overlap`：
        不开重叠就报冲突；开了就在重叠区让**新模块盖过旧的**（堆叠）。
        """
        if check is None:
            check = not self.overlap
        spec = module["spec"]
        sx, sy, sz = spec["grid"]["size"]
        vrot = rot_voxels(module["voxels"], rot)
        rx, ry, rz = rot_dims(sx, sy, sz, rot)
        if check and not self.can_place(vrot, pos):
            raise ValueError(f"{ref}: 位置 {pos} 与已放置方块冲突")
        p1 = (pos[0] + rx, pos[1] + ry, pos[2] + rz)
        self._grow(list(pos), list(p1))
        remap = {i: self._palette_idx(e)
                 for i, e in enumerate(module["palette"])}
        mapped = np.zeros_like(vrot)
        for i, j in remap.items():
            mapped[vrot == i] = j
        slab = self._slab(pos, vrot.shape)
        if self.overlap:
            # 堆叠：新模块盖过已有方块（与 mcstudio 装配合成顺序一致）
            slab[mapped != 0] = mapped[mapped != 0]
        else:
            slab[(mapped != 0) & (slab == 0)] = mapped[(mapped != 0) & (slab == 0)]
        inst = {"ref": ref, "module": spec["id"], "pos": list(pos),
                "rot": rot, "bbox": [list(pos), list(p1)]}
        self.instances.append(inst)
        return inst

    def port_world(self, inst: dict, port: dict):
        spec = self.module_cache[inst["module"]]["spec"]
        sx, sy, sz = spec["grid"]["size"]
        tp = transform_port(port, sx, sy, sz, inst["rot"])
        ax, ay, az = port_anchor3d(tp, *rot_dims(sx, sy, sz, inst["rot"]))
        wx = inst["pos"][0] + ax
        wy = inst["pos"][1] + ay
        wz = inst["pos"][2] + az
        face = tp["face"]
        o, s = tp["origin"], tp["size"]
        if face in ("west", "east"):
            return {"face": face, "x": wx, "y": wy, "z": wz,
                    "y0": wy, "u0": wz, "h": s[0], "w": s[1]}
        if face in ("north", "south"):
            return {"face": face, "x": wx, "y": wy, "z": wz,
                    "y0": wy, "u0": wx, "h": s[0], "w": s[1]}
        return {"face": face, "x": wx, "y": wy, "z": wz,
                "y0": wy, "u0": wx, "v0": wz, "h": s[1], "w": s[0]}

    def snap(self, anchor: dict, module: dict, port: dict):
        """Candidate placements of (module, port) against a world port anchor."""
        spec = module["spec"]
        sx, sy, sz = spec["grid"]["size"]
        cands = []
        for r in range(4):
            tp = transform_port(port, sx, sy, sz, r)
            if OPP[tp["face"]] != anchor["face"]:
                continue
            rx, ry, rz = rot_dims(sx, sy, sz, r)
            if anchor["face"] == "east":
                pos = (anchor["x"] + 1, anchor["y0"] - tp["origin"][0],
                       anchor["u0"] - tp["origin"][1])
            elif anchor["face"] == "west":
                pos = (anchor["x"] - rx, anchor["y0"] - tp["origin"][0],
                       anchor["u0"] - tp["origin"][1])
            elif anchor["face"] == "north":
                pos = (anchor["u0"] - tp["origin"][1],
                       anchor["y0"] - tp["origin"][0], anchor["z"] - rz)
            elif anchor["face"] == "south":
                pos = (anchor["u0"] - tp["origin"][1],
                       anchor["y0"] - tp["origin"][0], anchor["z"] + 1)
            elif anchor["face"] == "up":
                pos = (anchor["u0"] - tp["origin"][0],
                       anchor["y"] + 1, anchor["v0"] - tp["origin"][1])
            else:
                pos = (anchor["u0"] - tp["origin"][0],
                       anchor["y"] - ry, anchor["v0"] - tp["origin"][1])
            vrot = rot_voxels(module["voxels"], r)
            if not self.can_place(vrot, pos):
                continue
            # alignment is guaranteed by the snap formula; only penalise
            # opening-size mismatch
            score = 0
            if tp["size"][0] != anchor["h"]:
                score += 1
            if anchor["face"] in ("west", "east", "north", "south") and                     tp["size"][1] != anchor["w"]:
                score += 1
            cands.append((r, pos, score))
        # ties prefer identity rotation, then smaller coords
        cands.sort(key=lambda c: (c[2], 0 if c[0] == 0 else 1, sum(c[1])))
        return cands


def load_module(name: str, cache: dict | None = None):
    if cache is not None and name in cache:
        return cache[name]
    path = find_module(name)
    if path is None:
        raise SystemExit(f"模块不存在: {name}(先用 module_lib.py scan)")
    spec = load_spec(path)
    d = S.read_structure(str(path))
    m = {"path": path, "spec": spec, "voxels": d["voxels"],
         "palette": d["palette"], "size": d["size"]}
    if cache is not None:
        cache[name] = m
    return m


def parse_plan(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_plan(plan: dict, asm: Assembler, auto: bool) -> dict:
    """Place all instances; return a report dict."""
    asm.module_cache = {}
    nodes = {n["ref"]: n for n in plan["modules"]}
    # resume support: refs already placed in asm.instances are kept
    placed: dict[str, dict] = {i["ref"]: i for i in asm.instances}
    report: dict = {"instances": [], "connections": [], "conflicts": []}

    def place_node(ref: str, pos, rot):
        if ref in placed:
            return placed[ref]
        n = nodes[ref]
        m = load_module(n["module"], asm.module_cache)
        try:
            inst = asm.place(ref, m, tuple(pos), rot)
        except ValueError as e:
            report["conflicts"].append(str(e))
            return None
        placed[ref] = inst
        report["instances"].append(
            {"ref": ref, "module": n["module"], "pos": list(pos),
             "rot": rot, "bbox": inst["bbox"]})
        return inst

    for p in plan.get("place", []):
        if p["ref"] not in placed:
            place_node(p["ref"], p["pos"], p.get("rot", 0))

    def port_of(ref_port: str):
        ref, pid = ref_port.split(":", 1)
        n = nodes[ref]
        m = load_module(n["module"], asm.module_cache)
        for p in m["spec"].get("ports", []):
            if p["id"] == pid:
                return ref, p
        raise SystemExit(f"接口不存在: {ref_port}")

    conns = plan.get("connections", [])
    conns = [c if isinstance(c, dict) else {"a": c[0], "b": c[1]}
             for c in conns]

    if auto:
        queue = list(placed.keys())
        while queue:
            ref = queue.pop(0)
            for c in conns:
                other = None
                if c["a"].startswith(ref + ":") and \
                        c["b"].split(":", 1)[0] not in placed:
                    anchor_ref, anchor_p = port_of(c["a"])
                    free_ref, free_p = port_of(c["b"])
                    other = free_ref
                elif c["b"].startswith(ref + ":") and \
                        c["a"].split(":", 1)[0] not in placed:
                    anchor_ref, anchor_p = port_of(c["b"])
                    free_ref, free_p = port_of(c["a"])
                    other = free_ref
                if other is None:
                    continue
                m = load_module(nodes[other]["module"], asm.module_cache)
                anchor_w = asm.port_world(placed[anchor_ref], anchor_p)
                cands = asm.snap(anchor_w, m, free_p)
                if not cands:
                    report["conflicts"].append(
                        f"{other}: 无法吸附到 {anchor_ref}:{anchor_p['id']}"
                        f"(4 个旋转均失败/冲突)")
                    continue
                r, pos, score = cands[0]
                inst = place_node(other, pos, r)
                if inst is not None:
                    queue.append(other)
                    break

    for c in conns:
        ra, pa = port_of(c["a"])
        rb, pb = port_of(c["b"])
        status = {"conn": c, "a": ra, "b": rb}
        if ra not in placed or rb not in placed:
            status["ok"] = False
            status["why"] = "端点未放置"
        else:
            wa = asm.port_world(placed[ra], pa)
            wb = asm.port_world(placed[rb], pb)
            ok = OPP[wa["face"]] == wb["face"]
            if not types_compatible(pa["type"], pb["type"]):
                ok = False
                status["why"] = f"类型不兼容 {pa['type']} vs {pb['type']}"
            if ok:
                adj = False
                if wa["face"] in ("east", "west"):
                    adj = abs(wa["x"] - wb["x"]) == 1
                elif wa["face"] in ("north", "south"):
                    adj = abs(wa["z"] - wb["z"]) == 1
                else:
                    adj = abs(wa["y"] - wb["y"]) == 1
                if not adj:
                    ok = False
                    status["why"] = (f"面不贴合 {wa['face']}@"
                                     f"({wa['x']},{wa['y']},{wa['z']}) vs "
                                     f"{wb['face']}@({wb['x']},{wb['y']},{wb['z']})")
            if ok:
                if wa["face"] in ("up", "down"):
                    ox = min(wa["u0"] + wa["w"], wb["u0"] + wb["w"]) \
                        - max(wa["u0"], wb["u0"])
                    oz = min(wa["v0"] + wa["h"], wb["v0"] + wb["h"]) \
                        - max(wa["v0"], wb["v0"])
                    ov = ox > 0 and oz > 0
                else:
                    oy = min(wa["y0"] + wa["h"], wb["y0"] + wb["h"]) \
                        - max(wa["y0"], wb["y0"])
                    ou = min(wa["u0"] + wa["w"], wb["u0"] + wb["w"]) \
                        - max(wa["u0"], wb["u0"])
                    ov = oy > 0 and ou > 0
                if not ov:
                    ok = False
                    status["why"] = "开口没有重叠"
            status["ok"] = ok
            status["faces"] = [wa["face"], wb["face"]]
            status["pos"] = [list(placed[ra]["pos"]), list(placed[rb]["pos"])]
        report["connections"].append(status)

    for ref in nodes:
        if ref not in placed:
            report["conflicts"].append(
                f"{ref}: 未放置(没有连接到任何已放置节点)")
    return report


def write_assembly(asm: Assembler, out: str, name: str, meta: dict) -> str:
    xs = [i["bbox"][0][0] for i in asm.instances]
    ys = [i["bbox"][0][1] for i in asm.instances]
    zs = [i["bbox"][0][2] for i in asm.instances]
    xe = [i["bbox"][1][0] for i in asm.instances]
    ye = [i["bbox"][1][1] for i in asm.instances]
    ze = [i["bbox"][1][2] for i in asm.instances]
    x0, y0, z0 = min(xs), min(ys), min(zs)
    x1, y1, z1 = max(xe), max(ye), max(ze)
    ox, oy, oz = asm.origin
    v = asm.world[y0 - oy:y1 - oy, z0 - oz:z1 - oz, x0 - ox:x1 - ox]
    S.write_structure(
        out, v, asm.palette, (x0, y0, z0), (x1 - x0, y1 - y0, z1 - z0),
        metadata={"Name": name, "Author": "assemble.py",
                  "Description": json.dumps(meta, ensure_ascii=False)[:2000]},
        name=name)
    print(f"写 {out}: {x1-x0}x{y1-y0}x{z1-z0}, 方块 {(v != 0).sum()}, "
          f"palette {len(asm.palette)}")
    return out


def ascii_map(asm: Assembler, report: dict) -> str:
    insts = report["instances"]
    if not insts:
        return ""
    out = []
    bands = sorted({i["pos"][1] for i in insts})
    for y in bands:
        out.append(f"--- 层 y={y} ---")
        at = [i for i in insts if i["pos"][1] == y]
        x0 = min(i["bbox"][0][0] for i in at)
        x1 = max(i["bbox"][1][0] for i in at)
        z0 = min(i["bbox"][0][2] for i in at)
        z1 = max(i["bbox"][1][2] for i in at)
        grid = {}
        for i in at:
            for zz in range(i["bbox"][0][2], i["bbox"][1][2]):
                for xx in range(i["bbox"][0][0], i["bbox"][1][0]):
                    grid[(xx, zz)] = i["ref"]
        for zz in range(z0, z1):
            out.append("".join(grid.get((xx, zz), " ")[0]
                               for xx in range(x0, x1)))
    return "\n".join(out)


def selftest() -> None:
    # rotation: west port of a 5x4x4 module -> north face after r=1
    p = {"id": "w", "type": "passage", "face": "west", "origin": [1, 1],
         "size": [2, 2]}
    t = transform_port(p, 5, 4, 4, 1)
    assert t["face"] == "north" and t["origin"] == [1, 1], t
    t = transform_port(p, 5, 4, 4, 2)
    assert t["face"] == "east" and t["origin"] == [1, 1], t
    p2 = {"id": "s", "type": "passage", "face": "south", "origin": [1, 1],
          "size": [2, 2]}
    t2 = transform_port(p2, 5, 4, 4, 1)
    assert t2["face"] == "west" and t2["origin"] == [1, 1], t2
    p3 = {"id": "u", "type": "shaft", "face": "up", "origin": [1, 1],
          "size": [3, 2]}
    t3 = transform_port(p3, 5, 4, 4, 1)
    assert t3["face"] == "up" and t3["size"] == [2, 3], t3
    rng = np.random.default_rng(0)
    v = (rng.random((3, 5, 4)) * 4).astype(np.uint16)
    for r in range(4):
        vr = rot_voxels(rot_voxels(v, r), (4 - r) % 4)
        assert (vr == v).all(), r
    # snap: A east port; corridor west port snaps to x=10, y=0, z=1
    a = Assembler((64, 64, 64))
    mod = {"spec": {"grid": {"size": [5, 4, 4]}, "id": "corridor_x",
                    "ports": [p]},
           "voxels": np.zeros((4, 4, 5), dtype=np.uint16)}
    anchor = {"face": "east", "x": 9, "y": 1, "z": 1, "y0": 1, "u0": 1,
              "h": 2, "w": 2}
    cands = a.snap(anchor, mod, p)
    assert cands and cands[0][1] == (10, 0, 0) and cands[0][2] == 0, cands
    # 堆叠：默认冲突报错；overlap=True 时后放的盖过先放的
    solid = {"spec": {"grid": {"size": [2, 2, 2]}, "id": "blk", "ports": []},
             "voxels": np.ones((2, 2, 2), dtype=np.uint16),
             "palette": [{"Name": "minecraft:air"}, {"Name": "minecraft:stone"}]}
    b = Assembler((16, 16, 16))
    b.place("a", solid, (0, 0, 0), 0)
    try:
        b.place("b", solid, (1, 1, 1), 0)
        raise AssertionError("默认应该报冲突")
    except ValueError as e:
        assert "冲突" in str(e), e
    c = Assembler((16, 16, 16), overlap=True)
    c.place("a", solid, (0, 0, 0), 0)
    c.place("b", solid, (1, 1, 1), 0)      # 不报错：堆叠
    assert len(c.instances) == 2, c.instances
    print("selftest OK")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plan", nargs="?", help="装配计划 JSON")
    ap.add_argument("--out", default="assembled.schem")
    ap.add_argument("--auto", action="store_true",
                    help="按连接图自动摆放(默认显式摆放+校验)")
    ap.add_argument("--region", default=None, help="世界网格 x,y,z")
    ap.add_argument("--overlap", action="store_true",
                    help="允许模块**堆叠**（互相压着放；后放的盖过先放的）——"
                         "计划里也可以写 \"overlap\": true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    if not a.plan:
        raise SystemExit("需要 plan.json")
    plan = parse_plan(a.plan)
    region = tuple(int(v) for v in a.region.split(",")) if a.region else \
        tuple(plan.get("region") or (128, 128, 128))
    asm = Assembler(region, overlap=bool(a.overlap or plan.get("overlap")))
    report = run_plan(plan, asm, a.auto or plan.get("auto", False))
    if not a.quiet:
        print(ascii_map(asm, report))
        ok = sum(1 for c in report["connections"] if c.get("ok"))
        print(f"\n连接: {ok}/{len(report['connections'])} OK"
              + (f", 冲突 {len(report['conflicts'])}"
                 if report["conflicts"] else ""))
        for c in report["connections"]:
            if not c.get("ok"):
                print(f"  x {c['a']} <-> {c['b']}: {c.get('why', '')}")
        for m in report["conflicts"]:
            print("  ! " + m)
    write_text_lf(Path(a.out).with_suffix(".layout.json"),
        json.dumps(report, ensure_ascii=False, indent=1))
    write_assembly(asm, a.out, plan.get("name", "assembly"),
                   {"plan": Path(a.plan).name, "auto": bool(a.auto)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
