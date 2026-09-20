"""Edit sessions: open a structure, apply voxel ops, undo/redo, save.

One session owns a numpy ``(sy, sz, sx)`` uint16 palette-index array; the
browser keeps a mirror and patches it from the returned changed region.
All ops are applied under a lock; undo/redo keeps compact region copies.

Assembly: a session can additionally hold **module placements** (instances of
library modules placed at ``pos`` with a 90-degree ``rot``). The visible grid
is always ``base`` + placements composited in order, so a module can be moved
or removed without leaving ghosts behind. The placement manifest is persisted
to the ``<name>.layout.json`` sidecar (same convention as
``mccore.assemble``) and, for ``.schem``, also into the Sponge ``Metadata``
compound as a JSON string.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path

import numpy as np

from mccore import structure_io as S
from mccore.assemble import rot_axis90, rot_axis_dims, rot_dims, rot_voxels
from mccore.paths import repo_root, write_text_lf
from mccore.schem_io import parse_state, state_str

AIR = {"Name": "minecraft:air"}

#: 装配清单写进 Sponge ``Metadata`` 时用的键（改名后新键）。
LAYOUT_KEY = "StructworkshopModules"
#: 改名（mcforge → structworkshop）前写下的旧键：仍然读，兼容存量 .schem。
LEGACY_LAYOUT_KEYS = ("McForgeModules",)

#: 装配清单里「整幅投影」自包装实例的伪包名（`detach_base` 没有 mid 时写它）。
#: 它不是资产包模块：没有 spec / 接口，也不该去 `LB.module_path()` 里找。
#: 重开文件时据此把整幅体素**重新包一层**，于是「打开即实例、重开还是实例」。
SELF_PACK = "@self"


def read_layout_meta(meta: dict | None) -> list | None:
    """从结构 ``Metadata`` 里取装配清单：新键优先，旧键（改名前的产物）兜底。

    取不到或 JSON 坏了返回 None（调用方继续回落 sidecar / 空清单）。
    """
    for key in (LAYOUT_KEY, *LEGACY_LAYOUT_KEYS):
        raw = (meta or {}).get(key)
        if not raw:
            continue
        try:
            return json.loads(str(raw))
        except json.JSONDecodeError:
            continue
    return None


def _clamp_box(box, size):
    x0, y0, z0, x1, y1, z1 = (int(v) for v in box)
    sx, sy, sz = size
    x0, x1 = max(0, min(x0, x1)), min(sx - 1, max(x0, x1))
    y0, y1 = max(0, min(y0, y1)), min(sy - 1, max(y0, y1))
    z0, z1 = max(0, min(z0, z1)), min(sz - 1, max(z0, z1))
    if x1 < x0 or y1 < y0 or z1 < z0:
        raise ValueError("空选区")
    return x0, y0, z0, x1, y1, z1


def _bbox_union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]),
            max(a[3], b[3]), max(a[4], b[4]), max(a[5], b[5]))


def _ops_label(ops: list[dict]) -> str:
    """操作日志里的一行名字：绘制/擦除/填充/替换/移动选区/复制选区 + 格数。"""
    n = len(ops)
    kinds = {str(op.get("type", "op")) for op in ops}
    if kinds == {"set"}:
        air = all(str(op.get("state", "")).endswith("air") for op in ops)
        return f"{'擦除' if air else '绘制'} {n} 格"
    if kinds == {"region"} and n == 1:
        op = ops[0]
        b = [int(v) for v in (op.get("box") or (0, 0, 0, 0, 0, 0))]
        cells = (b[3] - b[0] + 1) * (b[4] - b[1] + 1) * (b[5] - b[2] + 1)
        verb = "移动" if str(op.get("mode", "move")) == "move" else "复制"
        return f"{verb}选区 {cells:,} 格"
    name = {"fill": "填充", "replace": "替换", "set": "绘制",
            "region": "选区变换"}.get(
        str(ops[0].get("type", "op")), str(ops[0].get("type", "op")))
    return f"{name} {n} 个操作" if len(kinds) > 1 else f"{name} {n} 格"


class StructureSession:
    def __init__(self, sid: str, path: Path | None, data: dict,
                 *, version: str | None = None, fmt: str | None = None):
        self.sid = sid
        self.path = Path(path) if path else None
        self.name = (self.path.stem if self.path else
                     str(data.get("metadata", {}).get("Name") or "untitled"))
        self.voxels = np.ascontiguousarray(data["voxels"], dtype=np.uint16)
        self.base = self.voxels.copy()          # 基地层（不含装配模块）
        # 「画布框」= 逻辑画布（保存时按它裁剪）。**数据范围可以比框大**：
        # 框外的东西仍然在数据里、还能编辑/放置，只是保存时不会写进文件。
        sy0, sz0, sx0 = self.voxels.shape
        self.frame = [int(sx0), int(sy0), int(sz0)]
        self.placements: list[dict] = []        # 装配实例（模块 id/pos/rot/体素）
        self._rcache: dict[str, dict] = {}      # pid -> 旋转后的体素缓存
        self._pid_seq = 0
        self.module_warnings: list[str] = []
        self.palette = [dict(p) for p in data["palette"]]
        self.position = tuple(int(v) for v in data.get("position", (0, 0, 0)))
        self.metadata = dict(data.get("metadata") or {})
        self.data_version = int(data.get("data_version") or 0)
        self.version = version
        self.fmt = fmt or (self.path.suffix.lower() if self.path else
                           S.PRIMARY_SUFFIX)
        self.region_name = data.get("region_name", "Schematic")
        # 方块实体（箱子内容/告示牌文字/刷怪笼…）：读进来就原样留着，保存时写回去——
        # 旧版本读结构时不读 BlockEntities、写盘时传 None，保存一次就把它们静默丢了。
        self.block_entities: list[dict] = [
            e for e in (data.get("block_entities") or []) if e.get("tag") is not None
        ]
        self._be_index: dict[tuple, list[dict]] = {}
        for e in self.block_entities:
            self._be_index.setdefault(tuple(e["pos"][:3]), []).append(e)
        self.dirty = False
        self.undo: list[dict] = []
        self.redo: list[dict] = []

        self.lock = threading.RLock()
        self.opened_at = time.time()

    # ------------------------------------------------------------ basics
    @property
    def size(self) -> tuple[int, int, int]:
        sy, sz, sx = self.voxels.shape
        return int(sx), int(sy), int(sz)

    def palette_states(self) -> list[str]:
        return [state_str(p) for p in self.palette]

    def index_of(self, state: str) -> int:
        key = state_str(parse_state(state))
        for i, p in enumerate(self.palette):
            if state_str(p) == key:
                return i
        self.palette.append(parse_state(state))
        return len(self.palette) - 1

    def _rel_path(self) -> str | None:
        if not self.path:
            return None
        try:
            return str(self.path.relative_to(repo_root())).replace("\\", "/")
        except ValueError:
            return str(self.path)

    def payload(self) -> dict:
        sx, sy, sz = self.size
        fx, fy, fz = self.frame_size()
        return {
            "sid": self.sid,
            "name": self.name,
            "path": self._rel_path(),
            "abs_path": str(self.path) if self.path else None,
            "size": [sx, sy, sz],
            "frame": [fx, fy, fz],
            "outside": self.outside_cells(),
            "palette": self.palette_states(),
            "position": list(self.position),
            "format": self.fmt,
            "data_version": self.data_version,
            "version": self.version,
            "dirty": self.dirty,
            "can_undo": bool(self.undo),
            "can_redo": bool(self.redo),
            "undo_steps": len(self.undo),
            "redo_steps": len(self.redo),
            "blocks": int((self.voxels != 0).sum()),
            "cells": int(sx * sy * sz),
            "placements": len(self.placements),
            "block_entities": len(self.block_entities),
        }

    def stats(self) -> dict:
        flat = self.voxels.reshape(-1).astype(np.int64)
        counts = np.bincount(flat, minlength=len(self.palette))
        names = self.palette_states()
        out = {}
        for i, n in enumerate(counts[:len(names)]):
            if n:
                out[names[i]] = int(n)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def voxel_bytes(self, y0: int | None = None, y1: int | None = None) -> bytes:
        with self.lock:
            v = self.voxels
            if y0 is not None or y1 is not None:
                sy = v.shape[0]
                y0 = max(0, int(y0 if y0 is not None else 0))
                y1 = min(sy, int(y1 if y1 is not None else sy))
                v = v[y0:y1]
            return np.ascontiguousarray(v.reshape(-1)).tobytes()

    def layer_bytes(self, y: int) -> bytes:
        with self.lock:
            sy, sz, sx = self.voxels.shape
            y = max(0, min(sy - 1, int(y)))
            return np.ascontiguousarray(self.voxels[y]).tobytes()

    def region_b64(self, box) -> str:
        x0, y0, z0, x1, y1, z1 = box
        with self.lock:
            v = self.voxels[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
            return base64.b64encode(np.ascontiguousarray(v).tobytes()).decode()

    # ------------------------------------------------------------ ops
    def _box_of(self, op) -> tuple:
        sx, sy, sz = self.size
        t = op.get("type")
        if t == "region":
            # 源 ∪ 目标：撤销快照与回传区域两处都要顾到
            src, tgt = self._region_targets(op)
            return _clamp_box((min(src[0], tgt[0]), min(src[1], tgt[1]),
                               min(src[2], tgt[2]), max(src[3], tgt[3]),
                               max(src[4], tgt[4]), max(src[5], tgt[5])), self.size)
        if t == "replace":
            b = op.get("box")
            return _clamp_box(b, self.size) if b else (0, 0, 0, sx - 1, sy - 1,
                                                       sz - 1)
        if t == "set":
            p = (int(op["x"]), int(op["y"]), int(op["z"]))
            if min(p) < 0:
                # 负坐标不是「空选区」：画布原点固定在 (0,0,0)，只能向 +X/+Y/+Z 扩。
                axes = "/".join(c for c, v in zip("XYZ", p) if v < 0)
                raise ValueError(
                    f"坐标越界 {p}：画布原点固定在 (0,0,0)，不能向 −{axes} 方向绘制")
            return _clamp_box((p[0], p[1], p[2], p[0], p[1], p[2]), self.size)
        a = op.get("from") or (0, 0, 0)
        b = op.get("to") or (sx - 1, sy - 1, sz - 1)
        return _clamp_box((a[0], a[1], a[2], b[0], b[1], b[2]), self.size)

    def _raw_box_of(self, op) -> tuple:
        """op 的未裁剪包围盒（画笔越界时用来扩容）。"""
        t = op.get("type")
        if t == "region":
            src, tgt = self._region_targets(op)
            return (min(src[0], tgt[0]), min(src[1], tgt[1]), min(src[2], tgt[2]),
                    max(src[3], tgt[3]), max(src[4], tgt[4]), max(src[5], tgt[5]))
        if t == "set":
            x, y, z = int(op["x"]), int(op["y"]), int(op["z"])
            return (x, y, z, x, y, z)
        if t == "replace" and op.get("box"):
            b = [int(v) for v in op["box"]]
            return (b[0], b[1], b[2], b[3], b[4], b[5])
        a = [int(v) for v in (op.get("from") or (0, 0, 0))]
        b = [int(v) for v in (op.get("to") or self.size)]
        return (a[0], a[1], a[2], b[0], b[1], b[2])

    def _region_targets(self, op) -> tuple:
        """``region`` op → (源 box, 目标 box)——都按**未裁剪**坐标算。"""
        b = [int(v) for v in (op.get("box") or ())]
        d = [int(v) for v in (op.get("delta") or (0, 0, 0))]
        if len(b) != 6 or len(d) != 3:
            raise ValueError(
                "region 需要 box=[x0,y0,z0,x1,y1,z1] 与 delta=[dx,dy,dz]")
        src = (b[0], b[1], b[2], b[3], b[4], b[5])
        tgt = (b[0] + d[0], b[1] + d[1], b[2] + d[2],
               b[3] + d[0], b[4] + d[1], b[5] + d[2])
        return src, tgt

    def _region_clip_axes(self, op) -> list:
        """region op 里哪几个方向会被画布边界裁掉（只用来给用户提示）。"""
        src, tgt = self._region_targets(op)
        sx, sy, sz = self.size
        out = []
        for lo, hi, tlo, thi, n, pos, neg in (
                (src[0], src[3], tgt[0], tgt[3], sx, "+X", "−X"),
                (src[1], src[4], tgt[1], tgt[4], sy, "+Y", "−Y"),
                (src[2], src[5], tgt[2], tgt[5], sz, "+Z", "−Z")):
            if tlo < 0 <= hi:
                out.append(neg)
            elif thi >= n > lo:
                out.append(pos)
        return out

    def _apply_region(self, op, v: np.ndarray) -> None:
        """把一段体素整体**移动**或**复制** ``delta`` 格（只动基地层）。

        三个要点：

        * 先把源片拷成副本再动手 —— 所以 ``move`` 在源/目标重叠时也不会自己吃自己；
        * 目标超出画布的部分**直接不写**（原点固定在 (0,0,0)，−X/−Y/−Z 侧没有格子），
          响应里的 ``warnings`` 会说明哪几个方向被裁了（``_region_clip_axes``）；
        * **模块（placements）不跟着走**：与画笔一致，选区变换只作用于基地层，
          模块重合成时依旧盖在上面（要连模块一起动，直接拖模块手柄）。
        """
        src, _tgt = self._region_targets(op)
        sx, sy, sz = self.size
        dx, dy, dz = (int(q) for q in (op.get("delta") or (0, 0, 0)))
        cx0, cy0, cz0 = max(0, src[0]), max(0, src[1]), max(0, src[2])
        cx1 = min(sx - 1, src[3])
        cy1 = min(sy - 1, src[4])
        cz1 = min(sz - 1, src[5])
        if cx1 < cx0 or cy1 < cy0 or cz1 < cz0:
            return                                   # 源整体在画布外
        sub = np.array(v[cy0:cy1 + 1, cz0:cz1 + 1, cx0:cx1 + 1], copy=True)
        if str(op.get("mode", "move")) == "move":
            v[cy0:cy1 + 1, cz0:cz1 + 1, cx0:cx1 + 1] = 0
        th, td, tw = sub.shape                       # (y, z, x)
        tx0, ty0, tz0 = cx0 + dx, cy0 + dy, cz0 + dz
        ax0, ay0, az0 = max(0, tx0), max(0, ty0), max(0, tz0)
        ax1 = min(sx - 1, tx0 + tw - 1)
        ay1 = min(sy - 1, ty0 + th - 1)
        az1 = min(sz - 1, tz0 + td - 1)
        if ax1 < ax0 or ay1 < ay0 or az1 < az0:
            return                                   # 目标整体在画布外
        oy, oz, ox = ay0 - ty0, az0 - tz0, ax0 - tx0
        piece = sub[oy:oy + (ay1 - ay0 + 1), oz:oz + (az1 - az0 + 1),
                    ox:ox + (ax1 - ax0 + 1)]
        dest = v[ay0:ay1 + 1, az0:az1 + 1, ax0:ax1 + 1]
        m = piece != 0
        dest[m] = piece[m]

    def _apply_one(self, op, v: np.ndarray) -> None:
        t = op.get("type")
        if t == "region":
            self._apply_region(op, v)
            return
        if t == "set":
            x, y, z = int(op["x"]), int(op["y"]), int(op["z"])
            sx, sy, sz = self.size
            if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
                raise ValueError(f"坐标越界 ({x},{y},{z})")
            v[y, z, x] = self.index_of(op.get("state", "minecraft:air"))
            return
        if t in ("fill", "erase"):
            x0, y0, z0, x1, y1, z1 = self._box_of(op)
            idx = 0 if t == "erase" else self.index_of(
                op.get("state", "minecraft:air"))
            v[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1] = idx
            return
        if t == "replace":
            x0, y0, z0, x1, y1, z1 = self._box_of(op)
            box = v[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
            src = op.get("from")
            dst = self.index_of(op.get("to", "minecraft:air"))
            if src in (None, "", "*"):
                mask = box != 0
            else:
                src_idx = self.index_of(src)
                mask = box == src_idx
            box[mask] = dst
            return
        raise ValueError(f"未知操作 {t!r}")

    def apply_ops(self, ops: list[dict], *, update: bool = False) -> dict:
        """应用一批绘制/擦除/替换操作。

        ``update=True``：改完按邻居重算不完整方块的连接状态（墙的
        ``north/south/east/west``/``up``、栅栏、铁栏杆/玻璃板、楼梯 ``shape``）——
        放一格墙会改变旁边那格墙的连接，所以重算范围是 ``bbox`` 外扩 1 格。
        另有一种操作 ``{"type": "update", "box": [...]}``：不画方块，只重算连接
        （编辑器里「重算连接」按钮用，省略 box 则整张画布）。
        """
        if not ops:
            return {"bbox": None}
        with self.lock:
            # 画笔点/拖到画布外时自动扩容（只向 +X/+Y/+Z 方向）
            need = [0, 0, 0]
            for op in ops:
                if op.get("type") == "update":
                    continue
                rb = self._raw_box_of(op)
                need[0] = max(need[0], rb[3] + 1)
                need[1] = max(need[1], rb[4] + 1)
                need[2] = max(need[2], rb[5] + 1)
            sx0, sy0, sz0 = self.size
            resized = self._ensure_size(max(sx0, need[0]), max(sy0, need[1]),
                                        max(sz0, need[2]))
            # 选区变换被画布边界裁掉的方向（只提示，不报错）
            clip_warns = []
            for op in ops:
                if op.get("type") != "region":
                    continue
                axes = self._region_clip_axes(op)
                if not axes:
                    continue
                verb = "移动" if str(op.get("mode", "move")) == "move" else "复制"
                clip_warns.append(
                    f"选区{verb}：{'/'.join(axes)} 方向超出画布，超出部分没有写入"
                    "（画布原点固定在 0,0,0）")
            bbox = None
            updates = []
            for op in ops:
                if op.get("type") == "update":
                    b = op.get("box")
                    updates.append(tuple(int(v) for v in b) if b else
                                   (0, 0, 0, sx0 - 1, sy0 - 1, sz0 - 1))
                    continue
                bbox = _bbox_union(bbox, self._box_of(op))
            do_update = bool(update or updates)
            if bbox is None:                       # 只有 update 操作
                sx, sy, sz = self.size
                bbox = (0, 0, 0, sx - 1, sy - 1, sz - 1)
            elif do_update:                        # 邻居也要进撤销/回传范围
                x0, y0, z0, x1, y1, z1 = bbox
                bbox = _clamp_box((x0 - 1, y0 - 1, z0 - 1, x1 + 1, y1 + 1,
                                   z1 + 1), self.size) or bbox
            x0, y0, z0, x1, y1, z1 = bbox
            # 画笔/工具写基地层；模块盖着的格子会被**就地记进模块实例**（overrides），
            # 所以「模块」与「编辑」不互斥（不先固化也能改，改动跟着模块走）
            covered = int(self._covered_mask(bbox).sum())
            base_before = (self.base[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1].copy()
                           if self.placements else None)
            pal_before = list(self.palette)
            pure_update = not any(op.get("type") != "update" for op in ops)
            redo_before = list(self.redo)
            self._push_undo(bbox, None, pal_before,
                            label="重算连接" if pure_update else _ops_label(ops))
            for op in ops:
                if op.get("type") == "update":
                    continue
                self._apply_one(op, self.base)
            updated = 0
            punched = 0
            if base_before is not None:
                punched = self._punch_module_edits(
                    bbox, base_before,
                    self.base[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1])
            if do_update:
                # 先在**合成结果**上重算连接：`self.voxels` 只由 _recomposite 维护，
                # 刚上的那笔改动（以及实例内容）必须先合成进来，否则算的是一张旧图
                self._recomposite(bbox)
                for b in (updates or [bbox]):
                    updated += self._update_states(b, margin=1)["changed"]
            if pure_update and not updated:
                # 「重算连接」但一个格都不用改：不进历史、不标脏（也不清 redo）
                self.undo.pop()
                self.redo = redo_before
                return {
                    "bbox": None,
                    "region": None,
                    "palette": self.palette_states(),
                    "dirty": self.dirty,
                    "can_undo": bool(self.undo),
                    "can_redo": bool(self.redo),
                    "module_cells": covered,
                    "module_edits": self.module_edit_count(),
                    "punched": 0,
                    "updated": 0,
                    "placements": self.placements_payload(),
                    "resized": bool(resized),
                    "size": list(self.size),
                    "frame": list(self.frame_size()),
                }
            self._recomposite(bbox)
            self._drop_block_entities(bbox)
            self.dirty = True
            return {
                "bbox": list(bbox),
                "palette": self.palette_states(),
                "region": self.region_b64(bbox),
                "dirty": True,
                "can_undo": True,
                "can_redo": False,
                "module_cells": covered,
                "module_edits": self.module_edit_count(),
                "punched": punched,
                "updated": updated,
                "placements": self.placements_payload(),
                "resized": bool(resized),
                "size": list(self.size),
                "frame": list(self.frame_size()),
                # 「保存范围（画布框）」的实时状态：框外格数随笔画变，
                # 前端不用自己扫一遍体素（也能立刻在状态栏反映）
                "outside": self.outside_cells(),
                "clipped_modules": self.frame_info()["clipped_modules"],
                "warnings": clip_warns or None,
            }

    def _update_states(self, b, *, margin: int = 1) -> dict:
        """按邻居重算连接状态：**读的是合成结果，差异写回各自那一层**。

        为什么不能只算基地层（旧写法）：编辑器里打开任意投影后它就是「整幅实例」，
        内容全在实例里、基地层是空的 —— 只算基地层的话，往墙旁边放一格，墙的
        ``east/west`` 不会更新（回归：``tests/fixtures/wall_state_probe.schem``
        那条，以及 ``tests/studio_ui_audit.js`` 里的墙用例）。

        做法：取合成结果一块（外扩 ``margin`` 格当邻居余量）跑 ``mckit.update``，
        再把差异写回：
          * 实例盖着的格子 → 记成**实例就地修改**（``_punch_module_edits``），
            跟着实例走；
          * 其余 → 写回基地层。
        返回 ``{"changed": n, "punched": m}``。调用方需要在之后 ``_recomposite``。
        """
        from mckit import update as BSU  # noqa: PLC0415
        x0, y0, z0, x1, y1, z1 = (int(v) for v in b)
        sx, sy, sz = self.size
        pad = max(0, int(margin))
        px0, py0, pz0 = max(0, x0 - pad), max(0, y0 - pad), max(0, z0 - pad)
        px1, py1, pz1 = (min(sx - 1, x1 + pad), min(sy - 1, y1 + pad),
                         min(sz - 1, z1 + pad))
        if px1 < px0 or py1 < py0 or pz1 < pz0:
            return {"changed": 0, "punched": 0}
        sl = (slice(py0, py1 + 1), slice(pz0, pz1 + 1), slice(px0, px1 + 1))
        comp = self.voxels[sl].copy()
        rep = BSU.update_volume(
            comp, self.palette,
            (x0 - px0, y0 - py0, z0 - pz0, x1 - px0, y1 - py0, z1 - pz0),
            margin=0, index_of=self._state_index)
        diff = comp != self.voxels[sl]
        if not diff.any():
            return {"changed": int(rep.get("changed") or 0), "punched": 0}
        before = self.base[sl].copy()
        after = before.copy()
        after[diff] = comp[diff]
        self.base[sl] = after
        punched = self._punch_module_edits((px0, py0, pz0, px1, py1, pz1),
                                           before, after)
        return {"changed": int(rep.get("changed") or 0), "punched": punched}

    def _state_index(self, state) -> int:
        """给 ``mckit.update`` 用的调色板分配器（接受 dict 或字符串）。"""
        if isinstance(state, str):
            from mccore.schem_io import parse_state  # noqa: PLC0415
            state = parse_state(state)
        key = state_str(state)
        for i, p in enumerate(self.palette):
            if state_str(p) == key:
                return i
        self.palette.append(dict(state))
        return len(self.palette) - 1

    def _apply_entry(self, entry: dict, reverse: bool) -> tuple:
        """交换 entry 与当前状态（不计算 region）；返回受影响的 box（画布框步骤返回 None）。"""
        if entry.get("box") is None:               # 只改画布框的步骤（数据不裁，随时可撤）
            cur = list(self.frame_size())
            box = entry.get("frame") or cur
            self.frame = [max(1, int(v)) for v in box]
            counter = {"box": None, "frame": cur, "to": list(self.frame_size()),
                       "label": entry.get("label", "保存范围"),
                       "at": time.time()}
            (self.redo if reverse else self.undo).append(counter)
            self.dirty = True
            return None
        x0, y0, z0, x1, y1, z1 = entry["box"]
        cur = self.voxels[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1].copy()
        cur_base = self.base[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1].copy()
        cur_place = list(self.placements)
        pal_cur = list(self.palette)
        self.voxels[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1] = entry["vox"]
        if "base" in entry:
            self.base[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1] = entry["base"]
        self.palette = [dict(p) for p in entry["palette"]]
        if entry.get("placements") is not None:
            self.placements = [dict(q) for q in entry["placements"]]
            self._prune_cache()
        counter = {"box": entry["box"], "vox": cur, "palette": pal_cur,
                   "base": cur_base, "label": entry.get("label", "op"),
                   "at": time.time()}
        if entry.get("placements") is not None:
            counter["placements"] = cur_place
        (self.redo if reverse else self.undo).append(counter)
        self.dirty = True
        return tuple(int(v) for v in entry["box"])

    def _restore(self, entry: dict, reverse: bool) -> dict:
        with self.lock:
            box = self._apply_entry(entry, reverse)
            out = {"bbox": list(box) if box else None,
                   "region": self.region_b64(box) if box else None,
                   "palette": self.palette_states(),
                   "size": list(self.size),
                   "dirty": True,
                   "can_undo": bool(self.undo),
                   "can_redo": bool(self.redo),
                   "undo_steps": len(self.undo),
                   "redo_steps": len(self.redo),
                   "placements": self.placements_payload()}
            return self._frame_info_merged(out)

    def undo_step(self) -> dict:
        if not self.undo:
            raise ValueError("没有可撤销的操作")
        entry = self.undo.pop()
        return self._restore(entry, reverse=True)

    def redo_step(self) -> dict:
        if not self.redo:
            raise ValueError("没有可重做的操作")
        entry = self.redo.pop()
        return self._restore(entry, reverse=False)

    # ------------------------------------------------------------ history
    def history_payload(self) -> dict:
        """操作日志（只回元信息，不回体素）：undo 栈 = 已做，redo 栈 = 已撤销。"""
        def meta(e: dict) -> dict:
            if not e.get("box"):
                return {"label": e.get("label", "保存范围"), "at": e.get("at"),
                        "box": None, "cells": 0,
                        "frame": [int(v) for v in (e.get("frame") or ())],
                        "to": [int(v) for v in (e.get("to") or ())]}
            x0, y0, z0, x1, y1, z1 = (int(v) for v in e["box"])
            return {"label": e.get("label", "op"), "at": e.get("at"),
                    "box": [x0, y0, z0, x1, y1, z1],
                    "cells": int((x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1))}
        with self.lock:
            return {"undo": [meta(e) for e in self.undo],
                    "redo": [meta(e) for e in reversed(self.redo)],
                    "cursor": len(self.undo),
                    "total": len(self.undo) + len(self.redo),
                    "can_undo": bool(self.undo),
                    "can_redo": bool(self.redo)}

    def jump_history(self, index: int) -> dict:
        """回到第 index 步（0 = 打开时的状态），向前/向后都行。"""
        with self.lock:
            want = max(0, min(int(index), len(self.undo) + len(self.redo)))
            box = None
            while len(self.undo) > want:
                box = _bbox_union(box, self._apply_entry(self.undo.pop(), True))
            while len(self.undo) < want and self.redo:
                box = _bbox_union(box, self._apply_entry(self.redo.pop(), False))
            out = {"palette": self.palette_states(), "dirty": True,
                   "can_undo": bool(self.undo), "can_redo": bool(self.redo),
                   "undo_steps": len(self.undo), "redo_steps": len(self.redo),
                   "placements": self.placements_payload(),
                   "size": list(self.size),
                   "cursor": len(self.undo), "bbox": None, "region": None}
            if box is not None:
                out["bbox"] = list(box)
                out["region"] = self.region_b64(box)
            return self._frame_info_merged(out)


    # ------------------------------------------------------------ assembly
    MAX_SIDE = 1024
    MAX_CELLS = 40_000_000

    def _ensure_size(self, x1: int, y1: int, z1: int) -> bool:
        """把画布扩到至少含 [0,x1)x[0,y1)x[0,z1)（只向正方向扩，不改原点）。"""
        sy, sz, sx = self.voxels.shape
        nsx, nsy, nsz = max(sx, int(x1)), max(sy, int(y1)), max(sz, int(z1))
        if any(v > self.MAX_SIDE for v in (nsx, nsy, nsz)) or                 nsx * nsy * nsz > self.MAX_CELLS:
            raise ValueError(
                f"数据范围上限：{nsx}×{nsy}×{nsz} 超过 "
                f"{self.MAX_SIDE}³ / {self.MAX_CELLS:,} 格；"
                "这是**数据范围**的硬上限（护住浏览器），与「保存范围（画布框）」无关——"
                "往保存范围外放置不受限制，只是数据不能拉到这么大")
        if (nsx, nsy, nsz) == (sx, sy, sz):
            return False
        with self.lock:
            for name in ("voxels", "base"):
                arr = getattr(self, name)
                new = np.zeros((nsy, nsz, nsx), dtype=np.uint16)
                new[:sy, :sz, :sx] = arr
                setattr(self, name, new)
        return True

    @staticmethod
    def _rots_of(p: dict) -> tuple[int, int, int]:
        """(rotx, roty, rotz)，各 0..3 个 90°。``rot`` 是绕 Y 的 yaw（兼容旧数据）。"""
        return (int(p.get("rotx", 0)) % 4, int(p.get("rot", 0)) % 4,
                int(p.get("rotz", 0)) % 4)

    def _rotated(self, p: dict):
        """(旋转后体素, 旋转后尺寸)。缓存按 pid+三轴角。"""
        rx, ry, rz = self._rots_of(p)
        key = (p["pid"], rx, ry, rz)
        c = self._rcache.get(p["pid"])
        if c and c["key"] == key:
            return c["vox"], c["dims"]
        v = np.ascontiguousarray(p["voxels"])
        dims = [int(x) for x in p["size"]]
        for axis, times in ((0, rx), (1, ry), (2, rz)):
            if times:
                v = rot_axis90(v, axis, times)
                dims = rot_axis_dims(dims, axis, times)
        self._rcache[p["pid"]] = {"key": key, "vox": v, "dims": dims}
        return v, dims

    def _prune_cache(self) -> None:
        ids = {q["pid"] for q in self.placements}
        self._rcache = {k: v for k, v in self._rcache.items()
                        if (k[1] if isinstance(k, tuple) else k) in ids}

    # ------------------------------------------- 模块实例上的「就地修改」（overrides）
    # 笔刷/工具改到模块盖着的格子时，除了写基地层，还在**那个模块实例**上记一笔
    # override：合成时它盖过模块自己的方块。这样「模块」与「编辑工具」不再互斥：
    # 模块里的空气照旧透出基地层，模块的实心部分也能被就地改掉（不用先「固化装配」），
    # 而且改动**跟着模块走**（移动/旋转/复制模块时一起走）。
    @staticmethod
    def _edit_key(k) -> tuple[int, int, int]:
        """override 的键：内存里是 (oy,oz,ox) 元组，JSON 里是 "oy,oz,ox" 字符串。"""
        if isinstance(k, str):
            a = k.split(",")
            return (int(a[0]), int(a[1]), int(a[2]))
        return (int(k[0]), int(k[1]), int(k[2]))

    def _rot_index(self, p: dict):
        """旋转后局部坐标 → **原始**局部扁平下标（记 overrides 用，跟着模块转）。

        每个模块只留一份（键带 pid，旋转变了重算）——否则每次转 90° 都留一份大数组。
        """
        sy, sz, sx = (int(p["size"][1]), int(p["size"][2]), int(p["size"][0]))
        rx, ry, rz = self._rots_of(p)
        key = ("idx", p["pid"])
        got = self._rcache.get(key)
        if got is not None and got.get("key") == (rx, ry, rz):
            return got["arr"]
        idx = np.arange(sy * sz * sx, dtype=np.int64).reshape(sy, sz, sx)
        for axis, times in ((0, rx), (1, ry), (2, rz)):
            if times:
                idx = rot_axis90(idx, axis, times)
        arr = np.ascontiguousarray(idx)
        self._rcache[key] = {"key": (rx, ry, rz), "arr": arr}
        return arr

    def _rot_edits(self, p: dict):
        """p 的 overrides 旋到与体素同一坐标系（int32，-1 = 没改过）；没有就 None。"""
        ed = p.get("edits")
        if not ed:
            return None
        sy, sz, sx = (int(p["size"][1]), int(p["size"][2]), int(p["size"][0]))
        ov = np.full((sy, sz, sx), -1, dtype=np.int32)
        for k, v in ed.items():
            oy, oz, ox = self._edit_key(k)
            if 0 <= oy < sy and 0 <= oz < sz and 0 <= ox < sx:
                ov[oy, oz, ox] = int(v)
        for axis, times in ((0, self._rots_of(p)[0]), (1, self._rots_of(p)[1]),
                            (2, self._rots_of(p)[2])):
            if times:
                ov = rot_axis90(ov, axis, times)
        return np.ascontiguousarray(ov)

    def _punch_module_edits(self, box, before: np.ndarray,
                            after: np.ndarray) -> int:
        """把 before→after 改掉的格子记进模块实例（模块不再把它们盖回去）。

        只记「模块自己盖着的实心格」——模块的空气格本来就透出基地层。
        返回记下的格数。调用前请先压撤销（overrides 进不了撤销栈就撤不回来）。
        """
        if not self.placements:
            return 0
        changed = before != after
        if not changed.any():
            return 0
        x0, y0, z0, x1, y1, z1 = (int(v) for v in box)
        n = 0
        for i, p in enumerate(self.placements):
            vrot, dims = self._rotated(p)
            px, py, pz = p["pos"]
            ix0 = max(x0, px); ix1 = min(x1, px + dims[0] - 1)
            iy0 = max(y0, py); iy1 = min(y1, py + dims[1] - 1)
            iz0 = max(z0, pz); iz1 = min(z1, pz + dims[2] - 1)
            if ix1 < ix0 or iy1 < iy0 or iz1 < iz0:
                continue
            sl = (slice(iy0 - y0, iy1 - y0 + 1),
                  slice(iz0 - z0, iz1 - z0 + 1),
                  slice(ix0 - x0, ix1 - x0 + 1))
            sub = vrot[iy0 - py:iy1 - py + 1, iz0 - pz:iz1 - pz + 1,
                       ix0 - px:ix1 - px + 1]
            sel = changed[sl] & (sub != 0)
            if not sel.any():
                continue
            ridx = self._rot_index(p)[iy0 - py:iy1 - py + 1,
                                      iz0 - pz:iz1 - pz + 1,
                                      ix0 - px:ix1 - px + 1]
            flats = ridx[sel].tolist()
            vals = after[sl][sel].tolist()
            sy, sz, sx = (int(p["size"][1]), int(p["size"][2]), int(p["size"][0]))
            edits = dict(p.get("edits") or {})
            for flat, val in zip(flats, vals):
                oy, oz, ox = np.unravel_index(int(flat), (sy, sz, sx))
                edits[(int(oy), int(oz), int(ox))] = int(val)
            self.placements[i] = {**p, "edits": edits}
            n += int(sel.sum())
        return n

    def module_edit_count(self) -> int:
        """一共在模块实例上就地改了多少格（状态栏/提示用）。"""
        return sum(len(p.get("edits") or {}) for p in self.placements)

    def _bbox(self, p: dict) -> tuple:
        x, y, z = p["pos"]
        rx, ry, rz = self._rotated(p)[1]
        return (x, y, z, x + rx - 1, y + ry - 1, z + rz - 1)

    def _clamp_box(self, box):
        """把装配操作的包围盒夹到当前画布内（用于 region/undo 切片）。"""
        x0, y0, z0, x1, y1, z1 = (int(v) for v in box)
        sx, sy, sz = self.size
        return (max(0, x0), max(0, y0), max(0, z0),
                min(sx - 1, x1), min(sy - 1, y1), min(sz - 1, z1))

    def _blit(self, target: np.ndarray, box, p: dict) -> tuple:
        """把 p 的（旋转后）体素写进 target（对应 box 的视图）。

        返回 (写入格数, 覆盖已有方块格数)。模块优先于底层/旧模块。
        """
        vrot, dims = self._rotated(p)
        px, py, pz = p["pos"]
        x0, y0, z0, x1, y1, z1 = box
        ix0 = max(x0, px); ix1 = min(x1, px + dims[0] - 1)
        iy0 = max(y0, py); iy1 = min(y1, py + dims[1] - 1)
        iz0 = max(z0, pz); iz1 = min(z1, pz + dims[2] - 1)
        if ix1 < ix0 or iy1 < iy0 or iz1 < iz0:
            return 0, 0
        sub = vrot[iy0 - py:iy1 - py + 1, iz0 - pz:iz1 - pz + 1,
                   ix0 - px:ix1 - px + 1]
        tx0, ty0, tz0 = ix0 - x0, iy0 - y0, iz0 - z0
        view = target[ty0:ty0 + (iy1 - iy0 + 1),
                      tz0:tz0 + (iz1 - iz0 + 1),
                      tx0:tx0 + (ix1 - ix0 + 1)]
        mask = sub != 0
        ovr = self._rot_edits(p)
        ov = None
        if ovr is not None:
            ov = ovr[iy0 - py:iy1 - py + 1, iz0 - pz:iz1 - pz + 1,
                     ix0 - px:ix1 - px + 1]
        if not mask.any() and ov is None:
            return 0, 0
        overlap = int((mask & (view != 0)).sum())
        lut = np.zeros(len(p["palette"]), dtype=np.uint16)
        for i in np.unique(sub[mask]):
            i = int(i)
            lut[i] = self.index_of(state_str(p["palette"][i]))
        view[mask] = lut[sub[mask]]
        if ov is not None:
            patched = ov >= 0
            if patched.any():
                # 就地改过的格子盖过模块自己的方块（0 = 擦掉，露出空气）
                view[patched] = ov[patched].astype(np.uint16)
        return int(mask.sum()), overlap

    def _erase_from(self, arr: np.ndarray, p: dict) -> None:
        """从 arr 中把 p 自己的非空气格清除（还原基地层用）。"""
        vrot, dims = self._rotated(p)
        px, py, pz = p["pos"]
        sy, sz, sx = arr.shape
        x1 = min(sx - 1, px + dims[0] - 1)
        y1 = min(sy - 1, py + dims[1] - 1)
        z1 = min(sz - 1, pz + dims[2] - 1)
        if x1 < px or y1 < py or z1 < pz:
            return
        sub = vrot[:y1 - py + 1, :z1 - pz + 1, :x1 - px + 1]
        t = arr[py:y1 + 1, pz:z1 + 1, px:x1 + 1]
        lut = np.zeros(len(p["palette"]), dtype=np.uint16)
        for i in np.unique(sub):
            i = int(i)
            if i:
                lut[i] = self.index_of(state_str(p["palette"][i]))
        mask = (sub != 0) & (t == lut[sub])
        ovr = self._rot_edits(p)
        if ovr is not None:
            # 被笔刷就地改过的格子：基地层里已经是用户要的值，别当成模块的贡献抹掉
            mask = mask & (ovr[:y1 - py + 1, :z1 - pz + 1, :x1 - px + 1] < 0)
        t[mask] = 0

    def _covered_mask(self, box) -> np.ndarray:
        """box 内被任一装配模块非空气格占用的 mask（画笔提示用）。"""
        x0, y0, z0, x1, y1, z1 = box
        m = np.zeros((y1 - y0 + 1, z1 - z0 + 1, x1 - x0 + 1), dtype=bool)
        for p in self.placements:
            vrot, dims = self._rotated(p)
            px, py, pz = p["pos"]
            ix0 = max(x0, px); ix1 = min(x1, px + dims[0] - 1)
            iy0 = max(y0, py); iy1 = min(y1, py + dims[1] - 1)
            iz0 = max(z0, pz); iz1 = min(z1, pz + dims[2] - 1)
            if ix1 < ix0 or iy1 < iy0 or iz1 < iz0:
                continue
            sub = vrot[iy0 - py:iy1 - py + 1, iz0 - pz:iz1 - pz + 1,
                       ix0 - px:ix1 - px + 1]
            m[iy0 - y0:iy1 - y0 + 1, iz0 - z0:iz1 - z0 + 1,
              ix0 - x0:ix1 - x0 + 1] |= sub != 0
        return m

    def _recomposite(self, box) -> None:
        """从 base 开始按放置顺序重合成 box 区域。"""
        x0, y0, z0, x1, y1, z1 = self._clamp_box(box)
        t = self.base[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1].copy()
        for p in self.placements:
            self._blit(t, (x0, y0, z0, x1, y1, z1), p)
        self.voxels[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1] = t

    # ------------------------------------------------------------ 方块实体
    def block_entities_json(self, limit: int = 4000) -> list[list]:
        """给浏览器渲染用的方块实体：``[[x,y,z,id,data],…]``。"""
        from mccore.schem_io import block_entity_json  # noqa: PLC0415
        with self.lock:
            return block_entity_json(self.block_entities, limit=limit)

    def _drop_block_entities(self, box) -> int:
        """把 box 内被抹掉（格子已经变空）的方块实体删掉，避免留在空地上。"""
        x0, y0, z0, x1, y1, z1 = self._clamp_box(box)
        if not self._be_index:
            return 0
        dropped = 0
        for y in range(y0, y1 + 1):
            for z in range(z0, z1 + 1):
                for x in range(x0, x1 + 1):
                    entries = self._be_index.get((x, y, z))
                    if not entries or self.voxels[y, z, x] != 0:
                        continue
                    for e in entries:
                        if e in self.block_entities:
                            self.block_entities.remove(e)
                            dropped += 1
                    self._be_index.pop((x, y, z), None)
        return dropped

    def _block_entities_for_save(self, size):
        """保存时的清单：只留画布框内、且格子不空的方块实体。"""
        sx, sy, sz = size
        out = []
        for e in self.block_entities:
            x, y, z = (int(v) for v in e["pos"][:3])
            if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
                continue
            if self.voxels[y, z, x] == 0:
                continue
            out.append(e)
        return out

    def _push_undo(self, box, placements_snapshot, pal_before, *, label) -> None:
        """把当前区域（voxels+base）压缩进撤销栈；改前调用。"""
        x0, y0, z0, x1, y1, z1 = self._clamp_box(box)
        self.undo.append({
            "box": [x0, y0, z0, x1, y1, z1],
            "vox": self.voxels[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1].copy(),
            "base": self.base[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1].copy(),
            "palette": pal_before,
            "placements": placements_snapshot,
            "label": label,
            "at": time.time(),
        })
        if len(self.undo) > 200:
            self.undo.pop(0)
        self.redo.clear()


    def _push_frame_undo(self, frame, *, to=None, label="保存范围") -> None:
        """画布框变化也进历史（只换框、不裁数据，所以这一步永远可撤）。"""
        entry = {"box": None, "frame": [int(v) for v in frame],
                 "label": label, "at": time.time()}
        if to is not None:
            entry["to"] = [int(v) for v in to]
        self.undo.append(entry)
        if len(self.undo) > 200:
            self.undo.pop(0)
        self.redo.clear()

    def _load_module(self, mid: str) -> dict:
        from mccore import library as LB  # noqa: PLC0415
        path = LB.module_path(mid)
        if path is None or not Path(path).is_file():
            raise ValueError(f"没有模块 {mid}")
        d = S.read_structure(str(path))
        try:
            spec = LB.load_spec(Path(path))
        except SystemExit:
            spec = {"id": mid, "grid": {"size": list(d["size"])}, "ports": []}
        sy, sz, sx = d["voxels"].shape
        size = [int(sx), int(sy), int(sz)]
        spec_size = spec.get("grid", {}).get("size") or list(d["size"])
        if list(spec_size) != size:
            self.module_warnings.append(
                f"{mid}: spec grid.size {spec_size} != 实际 {size}，按实际尺寸排布")
        entry = LB.find_entry(mid) or {}
        self._pid_seq += 1
        return {
            "pid": f"p{self._pid_seq}",
            "id": mid,
            "pack": entry.get("pack", ""),
            "category": entry.get("category", ""),
            "pos": [0, 0, 0],
            "rot": 0,
            "rotx": 0,
            "rotz": 0,
            "size": size,
            "blocks": int((d["voxels"] != 0).sum()),
            "ports": list(spec.get("ports") or []),
            "voxels": np.ascontiguousarray(d["voxels"], dtype=np.uint16),
            "palette": [dict(e) for e in d["palette"]],
        }

    def _layout_cursor(self):
        if not self.placements:
            return [0, 0, 0]
        x1 = max(self._bbox(p)[3] for p in self.placements)
        return [x1 + 1, 0, 0]

    def _module_result(self, box, *, added, resized, warnings=(), dirty=True):
        x0, y0, z0, x1, y1, z1 = self._clamp_box(box)
        return {
            "bbox": [x0, y0, z0, x1, y1, z1],
            "region": self.region_b64((x0, y0, z0, x1, y1, z1)),
            "palette": self.palette_states(),
            "resized": bool(resized),
            "size": list(self.size),
            "dirty": bool(dirty),
            "can_undo": bool(self.undo),
            "can_redo": False,
            "placements": self.placements_payload(),
            "added": [a["pid"] for a in added] or None,
            "warnings": list(warnings) or None,
        }

    def placements_payload(self) -> list[dict]:
        out = []
        for p in self.placements:
            vox, dims = self._rotated(p)
            b = self._bbox(p)
            out.append({
                "pid": p["pid"], "id": p["id"], "pack": p.get("pack", ""),
                "category": p.get("category", ""),
                "pos": list(p["pos"]), "rot": int(p["rot"]),
                "rotx": int(p.get("rotx", 0)), "rotz": int(p.get("rotz", 0)),
                "size": list(p["size"]), "dims": dims,
                "bbox": list(b), "blocks": int(p["blocks"]),
                "ports": len(p.get("ports") or []),
                # 就地改过的格数（笔刷/工具压在模块上的那部分）
                "edits": len(p.get("edits") or {}),
            })
        return out

    def provenance(self) -> list[dict]:
        """持久化用紧凑清单（id / pos / rot / 原始尺寸 + 就地修改）。

        ``@self``（整幅投影自包装实例）的 ``pos/rot`` 一律写 0：它的内容**就是**
        文件里的体素（保存时已经把位移/旋转画进去了），重开时按原样重新包一层
        即可 —— 再记一次位移就会把内容推两遍。
        """
        out = []
        for p in self.placements:
            adhoc = str(p.get("pack") or "") == SELF_PACK
            e = {"id": p["id"], "pack": p.get("pack") or "",
                 "pos": [0, 0, 0] if adhoc else list(p["pos"]),
                 "rot": 0 if adhoc else int(p["rot"]),
                 "rotx": 0 if adhoc else int(p.get("rotx", 0)),
                 "rotz": 0 if adhoc else int(p.get("rotz", 0)),
                 "size": list(p["size"]), "blocks": int(p["blocks"])}
            ed = p.get("edits")
            if ed:
                # JSON 的键必须是字符串；值 = 调色板下标（0 = 擦成空气）
                e["edits"] = {f"{self._edit_key(k)[0]},{self._edit_key(k)[1]},"
                              f"{self._edit_key(k)[2]}": int(v)
                              for k, v in ed.items()}
            out.append(e)
        return out

    def add_modules(self, ids, positions=None, *, auto=True) -> dict:
        """导入一个或多个模块。

        ``auto=True`` 时沿 +X 顺序排布（每个接在上一模块右边）；
        也可给 ``positions=[(x,y,z), …]`` 显式定位。
        """
        ids = [str(i) for i in ids if str(i)]
        if not ids:
            raise ValueError("ids 为空")
        with self.lock:
            pal_before = list(self.palette)
            snapshot = list(self.placements)
            added, warn = [], list(self.module_warnings)
            cursor = self._layout_cursor() if auto else None
            box = None
            for k, mid in enumerate(ids):
                p = self._load_module(mid)
                if positions and k < len(positions) and positions[k]:
                    pos = [int(v) for v in positions[k]]
                else:
                    pos = list(cursor or [0, 0, 0])
                if any(v < 0 for v in pos):
                    raise ValueError(f"{mid}: 坐标不能为负 {pos}")
                p["pos"] = pos
                self.placements.append(p)
                added.append(p)
                b = self._bbox(p)
                box = _bbox_union(box, b)
                if cursor is not None:
                    cursor = [b[3] + 1, 0, 0]
            resized = self._ensure_size(box[3] + 1, box[4] + 1, box[5] + 1)
            self._push_undo(box, snapshot, pal_before, label="装配导入")
            self._recomposite(box)
            self.dirty = True
            return self._module_result(box, added=added, resized=resized,
                                       warnings=warn)


    def move_placement(self, pid, *, pos=None, rot=None, rotx=None, rotz=None,
                       delta=None) -> dict:
        """移动/旋转一个装配实例。

        ``pos`` 绝对坐标,``delta`` 相对位移;``rot``=绕 Y、``rotx``=绕 X、
        ``rotz``=绕 Z（均为 90° 步进的绝对角度 0..3）。
        """
        with self.lock:
            idx = next((i for i, x in enumerate(self.placements)
                        if x["pid"] == pid), None)
            if idx is None:
                raise ValueError(f"没有装配 {pid}")
            old = self.placements[idx]
            if pos is not None:
                np_ = [int(v) for v in pos]
            elif delta is not None:
                np_ = [int(old["pos"][i]) + int(delta[i]) for i in range(3)]
            else:
                np_ = [int(v) for v in old["pos"]]
            if any(v < 0 for v in np_):
                raise ValueError("模块坐标不能为负")
            new = dict(old)
            new["pos"] = np_
            for name, val in (("rot", rot), ("rotx", rotx), ("rotz", rotz)):
                if val is not None:
                    new[name] = int(val) % 4
            old_box = self._bbox(old)
            new_box = self._bbox(new)
            box = _bbox_union(old_box, new_box)
            resized = self._ensure_size(new_box[3] + 1, new_box[4] + 1,
                                        new_box[5] + 1)
            pal_before = list(self.palette)
            snapshot = list(self.placements)
            self._push_undo(box, snapshot, pal_before, label="移动模块")
            self.placements[idx] = new
            self._prune_cache()
            self._recomposite(box)
            self.dirty = True
            return self._module_result(box, added=[new], resized=resized)

    def remove_placements(self, pids) -> dict:
        pids = set(str(x) for x in pids)
        if not pids:
            raise ValueError("pids 为空")
        with self.lock:
            gone = [x for x in self.placements if x["pid"] in pids]
            if not gone:
                raise ValueError("没有可移除的装配")
            keep = [x for x in self.placements if x["pid"] not in pids]
            box = None
            for x in gone:
                box = _bbox_union(box, self._bbox(x))
            pal_before = list(self.palette)
            snapshot = list(self.placements)
            self._push_undo(box, snapshot, pal_before, label="移除模块")
            self.placements = keep
            self._prune_cache()
            self._recomposite(box)
            self.dirty = True
            return self._module_result(box, added=[], resized=False)

    def clear_placements(self, *, bake=True) -> dict:
        """固化装配：清空清单；bake=True 把当前成品体素作为新的基地层。"""
        with self.lock:
            if not self.placements:
                raise ValueError("没有装配可固化")
            box = None
            for x in self.placements:
                box = _bbox_union(box, self._bbox(x))
            pal_before = list(self.palette)
            snapshot = list(self.placements)
            self._push_undo(box, snapshot, pal_before, label="固化装配")
            self.placements = []
            self._prune_cache()
            if bake:
                self.base = self.voxels.copy()
            else:
                self._recomposite(box)
            self.dirty = True
            return self._module_result(box, added=[], resized=False)

    def detach_base(self, mid=None, pack="", category="", ports=None, *,
                    mark_dirty: bool = True, record: bool = True) -> dict:
        """把基地层体素整体变成一个装配实例，并清空基地层。

        「在编辑器里打开模块 / 打开任意投影」用：打开后画布 = 内容尺寸，内容是
        可拖动/旋转的实例（与正常模块装配同一套逻辑），保存时按画布相对坐标写回原文件。
        基地层的方块实体（告示牌/箱子）不跟着实例走（位置不变），先留着不成问题。

        ``mid`` 给了就用**资产包里的 spec** 取接口（客户端传的 ``ports`` 只当兼容回退）：
        索引里的 ports 没有 ``origin``，拿去吸附会报 KeyError。

        ``mid`` 与 ``pack`` 都没给 = **整幅投影**：包名记 :data:`SELF_PACK`。
        打开文件时的包装用 ``mark_dirty=False, record=False`` —— 它是会话模型
        的变换（跟 :meth:`restore_placements` 同性质），文件本身没被改过，
        不该把刚打开的结构标成「未保存」，也不该在撤销栈里凭空多一步。
        """
        with self.lock:
            vox = np.asarray(self.base)
            n = int((vox != 0).sum())
            if not n:
                raise ValueError("基地层是空的，没有可转成实例的内容")
            if not mid and not pack:
                pack = SELF_PACK
            if mid:
                try:
                    from mccore import library as LB  # noqa: PLC0415
                    path = LB.module_path(str(mid))
                    if path is not None:
                        spec = LB.load_spec(Path(path))
                        ports = spec.get("ports") or []
                        if not pack:
                            pack = (LB.find_entry(str(mid)) or {}).get("pack", "")
                        if not category:
                            category = (LB.find_entry(str(mid)) or {}).get("category", "")
                except (OSError, KeyError, ValueError):
                    pass
            sy, sz, sx = vox.shape
            p = self._instance_from_voxels(
                vox, id=str(mid or self.name or "module"), pack=str(pack or ""),
                category=str(category or ""), ports=ports)
            box = (0, 0, 0, int(sx) - 1, int(sy) - 1, int(sz) - 1)
            pal_before = list(self.palette)
            snapshot = list(self.placements)
            if record:
                self._push_undo(box, snapshot, pal_before, label="转为实例")
            self.base = np.zeros_like(vox)
            self.placements.append(p)
            self._prune_cache()
            self._recomposite(box)
            self.dirty = bool(mark_dirty)
            return self._module_result(box, added=[p], resized=False,
                                       dirty=bool(mark_dirty))

    def _instance_from_voxels(self, vox, *, id, pack="", category="",
                              ports=None) -> dict:
        """把一片体素（+ 当前调色板）包成一个装配实例条目。

        :meth:`detach_base` 与 :meth:`restore_placements` 的 ``@self`` 还原共用，
        所以「打开时的那一层」与「重开文件时的那一层」永远是同一个形状。
        """
        vox = np.asarray(vox)
        sy, sz, sx = vox.shape
        self._pid_seq += 1
        return {
            "pid": f"p{self._pid_seq}",
            "id": str(id or "module"),
            "pack": str(pack or ""),
            "category": str(category or ""),
            "pos": [0, 0, 0], "rot": 0, "rotx": 0, "rotz": 0,
            "size": [int(sx), int(sy), int(sz)],
            "blocks": int((vox != 0).sum()),
            "ports": [dict(x) for x in (ports or [])],
            "voxels": np.ascontiguousarray(vox, dtype=np.uint16),
            "palette": [dict(e) for e in self.palette],
        }

    def load_provenance(self) -> list[dict]:
        """从 <name>.layout.json（优先）或 .schem Metadata 读装配清单。"""
        entries = None
        if self.path:
            side = Path(self.path).with_suffix(".layout.json")
            if side.is_file():
                try:
                    data = json.loads(side.read_text(encoding="utf-8"))
                    entries = data.get("instances")
                except (OSError, json.JSONDecodeError):
                    entries = None
        if entries is None:
            entries = read_layout_meta(self.metadata)
        return entries or []


    def snap_position(self, pid, *, pos=None, rot=None, rotx=None, rotz=None,
                      max_dist=2.5):
        """接口吸附：找一个让本模块某个接口与其它模块对侧接口对齐的位置。

        返回 (pos, rot) 或 None。只做几何对齐，不检查碰撞。
        ``max_dist`` 是磁吸半径（曼哈顿格数）：只有候选位置足够近才吸附，
        否则返回 None（保持自由摆放，避免把模块硬拽回去）。
        接口只有竖直面语义，因此仅当模块未绕 X/Z 翻转（rotx=rotz=0）时吸附。
        """
        from mccore.assemble import (  # noqa: PLC0415
            OPP, port_anchor3d, rot_dims, transform_port, types_compatible)
        idx = next((i for i, x in enumerate(self.placements)
                    if x["pid"] == pid), None)
        if idx is None:
            raise ValueError(f"没有装配 {pid}")
        s = self.placements[idx]
        rx = int(rotx) % 4 if rotx is not None else int(s.get("rotx", 0)) % 4
        rz = int(rotz) % 4 if rotz is not None else int(s.get("rotz", 0)) % 4
        if rx or rz:
            return None                      # 三轴翻转后接口语义不成立
        if rot is not None:
            rot = int(rot) % 4
        else:
            rot = int(s["rot"])
        sp = [int(v) for v in (pos if pos is not None else s["pos"])]
        sx, sy, sz = s["size"]
        sdims = rot_dims(sx, sy, sz, rot)
        ports_s = [transform_port(p, sx, sy, sz, rot)
                   for p in (s.get("ports") or [])]

        def plane_off(face, dims):
            if face in ("west", "north", "down"):
                return 0
            if face == "east":
                return dims[0]
            if face == "south":
                return dims[2]
            return dims[1]  # up

        def face_axes(face):
            if face in ("west", "east"):
                return 0, (1, 2)
            if face in ("north", "south"):
                return 2, (1, 0)
            return 1, (0, 2)

        best, best_d = None, None
        for t in self.placements:
            if t["pid"] == pid:
                continue
            tx, ty, tz = t["size"]
            tdims = rot_dims(tx, ty, tz, int(t["rot"]))
            for ps in ports_s:
                for pt in (transform_port(q, tx, ty, tz, int(t["rot"]))
                           for q in (t.get("ports") or [])):
                    if not types_compatible(ps["type"], pt["type"]):
                        continue
                    if pt["face"] != OPP.get(ps["face"]):
                        continue
                    axis, (ua, va) = face_axes(ps["face"])
                    cand = [0, 0, 0]
                    cand[axis] = (t["pos"][axis] +
                                  plane_off(pt["face"], tdims) -
                                  plane_off(ps["face"], sdims))
                    cand[ua] = t["pos"][ua] + pt["origin"][0] - ps["origin"][0]
                    cand[va] = t["pos"][va] + pt["origin"][1] - ps["origin"][1]
                    if any(int(v) < 0 for v in cand):
                        continue
                    d = sum(abs(cand[i] - sp[i]) for i in range(3))
                    if best_d is None or d < best_d:
                        best, best_d = cand, d
        if best is None or best_d is None or best_d > float(max_dist):
            return None
        return [int(v) for v in best], int(rot)

    def restore_placements(self) -> dict:
        """打开结构后尽力还原装配清单（或整幅的自包装实例）；资产包模块必须还在库里。"""
        try:
            entries = self.load_provenance()
        except Exception:  # noqa: BLE001
            entries = []
        if not entries:
            return {"restored": 0, "missing": []}
        with self.lock:
            restored, missing = [], []
            for e in entries:
                if str(e.get("pack") or "") == SELF_PACK:
                    # 整幅投影（打开时由客户端包的那一层）：文件里的体素**就是**
                    # 内容本身（位移/旋转早在保存时就画进去了），所以把读进来的
                    # 体素原样重新包一层 —— 不能再去 `LB.module_path()` 找模块，
                    # 也不能用清单里的 pos/rot 再变换一次。
                    x = self._instance_from_voxels(
                        self.voxels.copy(), id=str(e.get("id") or self.name),
                        pack=SELF_PACK)
                    ed = e.get("edits")
                    if ed:
                        x["edits"] = {self._edit_key(k): int(v)
                                      for k, v in ed.items()}
                    restored.append(x)
                    continue
                try:
                    x = self._load_module(str(e.get("id")))
                except ValueError as ex:
                    missing.append({"id": e.get("id"), "error": str(ex)})
                    continue
                x["pos"] = [max(0, int(v)) for v in (e.get("pos") or [0, 0, 0])]
                x["rot"] = int(e.get("rot") or 0) % 4
                x["rotx"] = int(e.get("rotx") or 0) % 4
                x["rotz"] = int(e.get("rotz") or 0) % 4
                ed = e.get("edits")
                if ed:
                    x["edits"] = {self._edit_key(k): int(v) for k, v in ed.items()}
                restored.append(x)
            if not restored:
                return {"restored": 0, "missing": missing}
            self.placements = restored
            box = None
            for x in restored:
                box = _bbox_union(box, self._bbox(x))
            self._ensure_size(box[3] + 1, box[4] + 1, box[5] + 1)
            # 基地层 = 当前体素 - 模块贡献；模块底下的原始方块无法还原
            base = self.voxels.copy()
            for x in reversed(self.placements):
                self._erase_from(base, x)
            self.base = base
            self._recomposite(box)
            return {"restored": len(restored), "missing": missing,
                    "warnings": list(self.module_warnings)}


    # ------------------------------------------------------------ 工具（Axiom 式）
    def apply_tool(self, name, params=None, *, sel_box=None, centers=None,
                   brush_shape="sphere", brush_radius=4.0, block=None,
                   mask=None, seed=0, update: bool = False) -> dict:
        """跑一个 mctools 工具（作用在基地层），自动压撤销 + 回传改动区域。

        工具写的格子如果被模块盖着，会**就地记进那个模块实例**（overrides，
        跟着模块走）——所以模块与工具不再互斥（不用先「固化装配」）。
        工作盒已 clamp 到画布，超出部分会被丢弃（``clipped`` 标记）。
        """
        from mctools import engine as TE  # noqa: PLC0415
        params = dict(params or {})
        if centers and not (params.get("_center") or params.get("at")):
            params["_center"] = [float(v) for v in centers[-1]]
        with self.lock:
            spec = TE.get_tool(name)
            sx, sy, sz = self.size
            box = TE.plan_box(spec, params, self.size, sel_box=sel_box,
                              centers=centers, brush_shape=brush_shape,
                              brush_radius=float(brush_radius))
            x0, y0, z0, x1, y1, z1 = box
            sel = TE.build_sel(box, spec, params, size=self.size,
                               sel_box=sel_box, centers=centers,
                               brush_shape=brush_shape,
                               brush_radius=float(brush_radius))
            if spec.mask and mask:
                from mctools import masks as MK  # noqa: PLC0415
                mc = MK.MaskContext(
                    self.base[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1],
                    origin=(x0, y0, z0), names=self.palette_states(),
                    sel=sel)
                sel = sel & MK.compile_mask(mask, mc)
            sub = np.ascontiguousarray(
                self.base[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1])
            base_before = sub.copy() if self.placements else None
            pal_before = list(self.palette)
            snapshot = list(self.placements)
            res = TE.run(name, sub, self.palette, params=params,
                         origin=(x0, y0, z0), sel=sel, seed=seed,
                         block=block, extent=self.size)
            covered = int(self._covered_mask(box).sum())
            touched = int(sel.sum())
            if not res["changed"]:
                self.palette = pal_before      # 没改动就别留垃圾调色板项
                notes = list(res["notes"])
                if touched == 0:
                    notes.append("笔刷落点不在选区范围内（或选区为空）——"
                                 "可清除选区后重试")
                return {"bbox": None, "changed": 0, "palette":
                        self.palette_states(), "stats": res["stats"],
                        "notes": notes, "box": list(box),
                        "covered": covered, "touched": touched,
                        "placements": self.placements_payload(),
                        "dirty": self.dirty}
            self._push_undo(box, snapshot, pal_before,
                            label=f"工具·{spec.label}")
            self.base[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1] = sub
            punched = 0
            if base_before is not None:
                punched = self._punch_module_edits(
                    box, base_before,
                    self.base[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1])
            updated = 0
            if update:
                # 工具可能放出墙/栏杆/台阶：按邻居补连接状态（含 bbox 外 1 格邻居）。
                # 算的是**合成结果**（内容可能在模块/整幅实例里）：先合成再算
                self._recomposite(box)
                updated = self._update_states(box, margin=1)["changed"]
            self._recomposite(box)
            self.dirty = True
            notes = list(res["notes"])
            if updated:
                notes.append(f"连接状态已按邻居重算：{updated} 格")
            if covered:
                notes.append(
                    f"有 {covered} 格压在模块上，已就地改在模块实例上（跟着模块走）")
            if punched:
                notes.append(f"模块实例上改了 {punched} 格")
            if x0 == 0 or y0 == 0 or z0 == 0 or x1 == sx - 1 or \
                    y1 == sy - 1 or z1 == sz - 1:
                notes.append("工作区贴到画布边界，超出部分已裁掉")
            out = {
                "bbox": list(box),
                "region": self.region_b64(box),
                "palette": self.palette_states(),
                "dirty": True,
                "can_undo": bool(self.undo),
                "can_redo": False,
                "placements": self.placements_payload(),
                "size": list(self.size),
                "resized": False,
                "changed": int(res["changed"]),
                "stats": res["stats"],
                "notes": notes,
                "covered": covered,
                "touched": touched,
                "module_edits": self.module_edit_count(),
                "punched": punched,
                "updated": updated,
            }
            return out

    def select_info(self, *, mask=None, at=None, connected=False,
                    limit=500000) -> dict:
        """统计掩码命中的格子并给出包围盒（框选/魔棒用，不改数据）。"""
        from mctools import masks as MK  # noqa: PLC0415
        with self.lock:
            b = self.base
            mc = MK.MaskContext(b, origin=(0, 0, 0), names=self.palette_states())
            if at is not None:
                x, y, z = (int(v) for v in at)
                sx, sy, sz = self.size
                if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
                    raise ValueError("取样点越界")
                idx = int(b[y, z, x])
                if idx == 0:
                    raise ValueError("该处是空气，没有可选的方块")
                m = b == idx
                if connected:
                    m = self._flood(b, (x, y, z), idx, limit)
            else:
                m = MK.compile_mask(mask, mc)
            n = int(m.sum())
            if not n:
                return {"count": 0, "bbox": None}
            ys, zs, xs = np.nonzero(m)
            return {"count": n,
                    "bbox": [int(xs.min()), int(ys.min()), int(zs.min()),
                             int(xs.max()), int(ys.max()), int(zs.max())]}

    @staticmethod
    def _flood(b, start, idx, limit):
        sy, sz, sx = b.shape
        same = (b == idx)
        seen = np.zeros(b.shape, dtype=bool)
        stack = [start]
        seen[start[1], start[2], start[0]] = True
        n = 0
        while stack and n < limit:
            x, y, z = stack.pop()
            n += 1
            for dy, dz, dx in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
                               (0, 0, 1), (0, 0, -1)):
                ny, nz, nx = y + dy, z + dz, x + dx
                if (0 <= ny < sy and 0 <= nz < sz and 0 <= nx < sx and
                        not seen[ny, nz, nx] and same[ny, nz, nx]):
                    seen[ny, nz, nx] = True
                    stack.append((nx, ny, nz))
        return seen

    # ------------------------------------------------------------ canvas size
    def content_bounds(self):
        """当前有效内容范围（非空气格 ∪ 装配包围盒），返回 (x1, y1, z1) 尺寸。"""
        v = self.voxels
        if not (v != 0).any():
            return [1, 1, 1]
        mask = v != 0
        xs = np.flatnonzero(mask.any(axis=(0, 1)))
        ys = np.flatnonzero(mask.any(axis=(1, 2)))
        zs = np.flatnonzero(mask.any(axis=(0, 2)))
        x1 = int(xs[-1]) + 1
        y1 = int(ys[-1]) + 1
        z1 = int(zs[-1]) + 1
        for p in self.placements:
            b = self._bbox(p)
            x1 = max(x1, b[3] + 1)
            y1 = max(y1, b[4] + 1)
            z1 = max(z1, b[5] + 1)
        return [x1, y1, z1]

    def frame_size(self) -> tuple[int, int, int]:
        """「画布框」尺寸（保存时按它裁剪）；恒 ≤ 数据范围。"""
        sx, sy, sz = self.size
        box = [1, 1, 1]
        for i, lim in enumerate((sx, sy, sz)):
            try:
                box[i] = int(self.frame[i])
            except (AttributeError, IndexError, TypeError, ValueError):
                box[i] = lim
            box[i] = max(1, min(box[i], lim))
        return (box[0], box[1], box[2])

    def outside_cells(self) -> int:
        """画布框外还有多少非空气格（保存时会被裁掉）。"""
        fx, fy, fz = self.frame_size()
        if (fx, fy, fz) == self.size:
            return 0
        v = self.voxels
        return int((v != 0).sum()) - int((v[:fy, :fz, :fx] != 0).sum())

    def frame_info(self) -> dict:
        """画布框 + 框外格数 + 被框到的模块（响应里带给前端）。"""
        fx, fy, fz = self.frame_size()
        clipped = []
        for p in self.placements:
            b = self._bbox(p)
            if b[3] + 1 > fx or b[4] + 1 > fy or b[5] + 1 > fz:
                clipped.append({"pid": p["pid"], "id": p["id"], "pos": list(p["pos"])})
        return {"frame": [fx, fy, fz], "outside": self.outside_cells(),
                "clipped_modules": clipped or None}

    def resize(self, size=None, *, fit=False) -> dict:
        """改「画布框」（逻辑画布，保存时按它裁剪）——**不裁数据，也不清撤销栈**。

        - 放大：数据范围按需扩到框内（框内空白补空气）；
        - 缩小：只改框，框外内容留在数据里（可继续看/编辑/往里放东西），保存时才裁掉；
        - 框的变化本身进历史（操作日志里一步，可撤销/重做）；
        - ``fit=True``：按内容（非空气格 ∪ 装配模块包围盒）收缩框。
        """
        with self.lock:
            if fit:
                target = [int(v) for v in self.content_bounds()]
            else:
                if not size or len(size) != 3:
                    raise ValueError("size 必须是 [x, y, z]")
                target = [int(v) for v in size]
            if any(v < 1 for v in target):
                raise ValueError("尺寸必须 ≥ 1")
            if any(v > self.MAX_SIDE for v in target):
                raise ValueError(
                    f"单边尺寸上限 {self.MAX_SIDE}（避免画布过大卡死）")
            sx, sy, sz = self.size
            storage = [max(sx, target[0]), max(sy, target[1]), max(sz, target[2])]
            cells = storage[0] * storage[1] * storage[2]
            if cells > self.MAX_CELLS:
                raise ValueError(
                    f"目标数据范围 {storage[0]}×{storage[1]}×{storage[2]} "
                    f"= {cells:,} 格，超过 {self.MAX_CELLS:,} 上限")
            old = list(self.frame_size())
            if target != old:                      # 框变了才进历史
                self._push_frame_undo(old, to=target, label="保存范围")
            resized = self._ensure_size(*storage)
            self.frame = list(target)
            self.dirty = True
            out = self._frame_info_merged({
                "bbox": None, "region": None, "palette": self.palette_states(),
                "resized": bool(resized), "size": list(self.size),
                "dirty": True, "can_undo": bool(self.undo),
                "can_redo": bool(self.redo),
                "placements": self.placements_payload(),
                "warnings": None,
            })
            if resized:                            # 数据范围变了：整幅重传
                box = (0, 0, 0, out["size"][0] - 1, out["size"][1] - 1,
                       out["size"][2] - 1)
                out["bbox"] = list(box)
                out["region"] = self.region_b64(box)
            return out

    def _frame_info_merged(self, out: dict) -> dict:
        info = self.frame_info()
        out.update(info)
        warns = list(out.get("warnings") or [])
        if info["outside"]:
            warns.append(f"画布框外还有 {info['outside']:,} 格：能继续编辑，保存时才裁掉")
        if info["clipped_modules"]:
            warns.append("有 %d 个装配模块伸到画布框外，保存会被裁到框内：%s"
                         % (len(info["clipped_modules"]),
                            "、".join(m["id"] for m in info["clipped_modules"][:3])))
        out["warnings"] = warns or None
        return out

    def _recompute_size_cache(self) -> None:
        """尺寸变化后清掉旋转缓存（体素数组已被替换）。"""
        self._rcache = {}


    # ------------------------------------------------------------ save
    def save(self, path=None, fmt: str | None = None,
             name: str | None = None) -> dict:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("没有目标路径")
        suffix = (fmt or target.suffix or S.PRIMARY_SUFFIX).lower()
        if suffix not in S.SUPPORTED_SUFFIXES:
            raise ValueError(f"不支持的格式 {suffix}")
        target = target.with_suffix(suffix)
        # 按「设置」里的备份策略（off / 保留最近 N 次 / 按天留存）给旧文件留底
        from mccore import backup as BK  # noqa: PLC0415
        existed = target.is_file()
        backup = BK.backup_file(target, "structures")
        with self.lock:
            prov = self.provenance()
            meta = dict(self.metadata or {})
            for key in (LAYOUT_KEY, *LEGACY_LAYOUT_KEYS):
                meta.pop(key, None)          # 旧键不再写；读到旧文件时照样认
            if prov and suffix == ".schem":
                blob = json.dumps(prov, ensure_ascii=False, separators=(",", ":"))
                if len(blob) <= 60000:          # 大清单交给 sidecar
                    meta[LAYOUT_KEY] = blob
            # 落盘时按「画布框」裁切：框外的东西留在会话里（可继续编辑），只是不写进文件
            fx, fy, fz = self.frame_size()
            sx, sy, sz = self.size
            vox = self.voxels if (fx, fy, fz) == (sx, sy, sz) \
                else np.ascontiguousarray(self.voxels[:fy, :fz, :fx])
            cropped = int((self.voxels != 0).sum()) - int((vox != 0).sum())
            tile_entities = self._block_entities_for_save((fx, fy, fz))
            S.write_structure(
                str(target), vox, self.palette, self.position,
                (fx, fy, fz), metadata=meta or None,
                name=name or self.name, data_version=self.data_version,
                tile_entities=tile_entities)
            side = target.with_suffix(".layout.json")
            if prov:
                side.parent.mkdir(parents=True, exist_ok=True)
                write_text_lf(side,
                    json.dumps({"generator": "mcstudio", "version": 1,
                                "name": name or self.name,
                                "size": [fx, fy, fz],
                                "frame": [fx, fy, fz],
                                "instances": prov},
                               ensure_ascii=False, indent=1))
            else:
                side.unlink(missing_ok=True)
            spec_sync = self._sync_module_spec(target, (fx, fy, fz))
            self.path = target
            self.fmt = suffix
            self.dirty = False
        if target.is_relative_to(repo_root()):
            rel = str(target.relative_to(repo_root())).replace("\\", "/")
        else:
            rel = str(target)
        return {"path": str(target), "rel": rel,
                "backup": str(backup) if backup else None,
                "backup_skipped": bool(existed and not backup),
                "size": [fx, fy, fz],
                "frame": [fx, fy, fz],
                "cropped": cropped,
                "cropped_axes": [sx - fx, sy - fy, sz - fz],
                "placements": len(prov),
                "spec": spec_sync.get("spec"),
                "spec_grid": spec_sync.get("grid"),
                "spec_grid_from": spec_sync.get("grid_from"),
                "spec_warnings": spec_sync.get("warnings") or [],
                "layout": str(side.relative_to(repo_root())).replace("\\", "/")
                if side.is_file() and side.is_relative_to(repo_root())
                else str(side)}

    def _sync_module_spec(self, target: Path, size) -> dict:
        """保存后把同名 ``*.module.json`` 的 ``grid`` 对齐到**实际写盘的尺寸**。

        改过画布尺寸再保存时，``.schem`` 按画布框裁好了、而 sidecar spec 里的
        ``grid.size`` 还是旧的 → 模块库扫描时会报
        “grid.size [1, 5, 2] != 实际 [2, 5, 2]”（卡片/抽屉里直接标红）。
        这里顺手把改小后越界的接口也报出来（老接口不动接口列表）。
        """
        from mccore import module_lib as ML                    # noqa: PLC0415
        spec_path = ML.spec_path_for(target)
        if not spec_path.is_file():
            return {}
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(spec, dict):
            return {}
        old = (spec.get("grid") or {}).get("size")
        new_size = [int(v) for v in size]
        if ML.sync_grid(spec, tuple(new_size)):
            spec["grid"]["position"] = [int(v) for v in self.position]
            from mccore.library import save_spec as _save                # noqa: PLC0415
            _save(target, spec)
        return {"spec": str(spec_path), "grid": new_size, "grid_from": old,
                "warnings": ML.validate_spec(spec, tuple(new_size))}

    def save_as_module(self, pack: str, mid: str, *, category="custom",
                       tags=(), description="", overwrite=False) -> dict:
        from mccore import library as LB  # noqa: PLC0415
        with self.lock:
            fx, fy, fz = self.frame_size()
            if (fx, fy, fz) != self.size:
                vox = np.ascontiguousarray(self.voxels[:fy, :fz, :fx])
            else:
                vox = self.voxels
            return LB.save_as_module(
                vox, self.palette, self.position, pack, mid,
                category=category, tags=tags, description=description,
                overwrite=overwrite, data_version=self.data_version or None)


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, StructureSession] = {}
        self._lock = threading.Lock()
        self._seq = 0

    def _next_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"s{self._seq}"

    def add(self, path: Path | None, data: dict, version: str | None = None,
            fmt: str | None = None) -> StructureSession:
        s = StructureSession(self._next_id(), path, data, version=version,
                             fmt=fmt)
        with self._lock:
            self._sessions[s.sid] = s
        return s

    def get(self, sid: str) -> StructureSession:
        s = self._sessions.get(sid)
        if s is None:
            raise KeyError(f"会话不存在: {sid}")
        return s

    def close(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def list(self) -> list[dict]:
        return [s.payload() for s in self._sessions.values()]
