"""方块更新模拟：把 ``mckit.connect`` 的属性解算接到「体素 + 调色板」上。

Minecraft 里不完整方块的连接状态是**存在方块状态里**的：墙 / 栅栏 / 玻璃板 /
铁栏杆要按四个水平邻居写 ``north/south/west/east``（``none``/``low``/``tall``），
墙还按上方方块写 ``up``，楼梯按相邻楼梯的朝向写 ``shape``。程序化生成或手改过的
结构如果没写这些属性，进入游戏就是一排孤零零的柱子。

``mckit.connect`` 提供纯函数（"邻居查询 → 属性字典"）；本模块负责体素级的一次更新：

    n = update_volume(voxels, palette, box)          # 就地改，返回改动格数
    vox2, pal2, n = updated_copy(voxels, palette)    # 只读场景（渲染）用

一次遍历即可收敛：连接属性只依赖邻居的**方块名**，楼梯 ``shape`` 只依赖邻居的
``facing``（``facing`` 由放置者决定，本次更新不改），所以不存在级联迭代。

不模拟：栅栏门的 ``in_wall``、红石类的 ``powered``、以及方块的激活/掉落——那些是
游戏逻辑而不是状态重算。

坐标约定：``voxels`` 形状为 ``(sy, sz, sx)``，索引 ``voxels[y, z, x]``；
``box = (x0, y0, z0, x1, y1, z1)`` 闭区间，与 mcstudio / mctools 一致。
"""
from __future__ import annotations

import numpy as np

from mckit import connect as C

__all__ = ["family", "update_volume", "updated_copy", "STATE_FAMILIES"]

# 需要按邻居重算的家族（栅栏门暂不模拟 in_wall，见模块 docstring）
STATE_FAMILIES = ("wall", "fence", "pane", "stairs")
_CODE = {name: i + 1 for i, name in enumerate(STATE_FAMILIES)}

# 每个家族真正由邻居决定的属性键（其余属性原样保留）
_KEYS = {
    "wall": ("up", "north", "south", "west", "east"),
    "fence": ("north", "south", "west", "east"),
    "pane": ("north", "south", "west", "east"),
    "stairs": ("shape",),
}


def bare(name) -> str | None:
    """去掉命名空间：``minecraft:oak_wall`` → ``oak_wall``（connect 的约定）。"""
    if not isinstance(name, str) or not name:
        return None
    return name.split(":", 1)[-1]


def family(name) -> str | None:
    """这个方块名属于哪个「要按邻居重算」的家族？"""
    n = bare(name)
    if not n:
        return None
    if C._is_gate(n):            # 栅栏门：in_wall 不模拟
        return None
    if C._is_wall(n):
        return "wall"
    if C._is_fence(n):
        return "fence"
    if C._is_pane(n):            # 玻璃板 + 铁栏杆/铜栏杆
        return "pane"
    if C._is_stairs(n):
        return "stairs"
    return None


def _fam_table(palette) -> np.ndarray:
    out = np.zeros(len(palette), dtype=np.int8)
    for i, p in enumerate(palette):
        name = p.get("Name") if isinstance(p, dict) else p
        out[i] = _CODE.get(family(name) or "", 0)
    return out


def _props_of(entry) -> dict:
    if isinstance(entry, dict):
        return {k: str(v) for k, v in (entry.get("Properties") or {}).items()}
    return {}


def update_volume(voxels: np.ndarray, palette: list, box=None, *,
                  margin: int = 1, index_of=None) -> dict:
    """按邻居重算 ``box``（外扩 ``margin`` 格）内不完整方块的状态。

    * ``voxels`` 就地改写，``palette`` 就地追加需要的新状态。
    * ``box=None`` → 整张画布。``margin=1`` 是为了让**被改动的格子的邻居**一起
      重算（放一格墙会改变旁边那格墙的连接）。
    * ``index_of(state_str) -> int`` 可注入（mcstudio 会话用它复用调色板逻辑）；
      默认自带去重缓存。

    返回 ``{"changed": n, "cells": [[x,y,z,old,new], …], "families": {…},
    "palette_added": m}``；``cells`` 最多 200 条（给 UI 显示，不是全部）。
    """
    from mccore.schem_io import state_str

    sy, sz, sx = voxels.shape
    empty = {"changed": 0, "cells": [], "families": {}, "palette_added": 0}
    if not len(palette):
        return empty
    fam = _fam_table(palette)
    names = [bare(p.get("Name") if isinstance(p, dict) else p) or "air"
             for p in palette]

    x0, y0, z0, x1, y1, z1 = (box if box is not None
                              else (0, 0, 0, sx - 1, sy - 1, sz - 1))
    if margin:
        x0, y0, z0 = x0 - margin, y0 - margin, z0 - margin
        x1, y1, z1 = x1 + margin, y1 + margin, z1 + margin
    x0, y0, z0 = max(0, int(x0)), max(0, int(y0)), max(0, int(z0))
    x1, y1, z1 = min(sx - 1, int(x1)), min(sy - 1, int(y1)), min(sz - 1, int(z1))
    if x1 < x0 or y1 < y0 or z1 < z0:
        return empty

    def name_at(x, y, z):
        if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
            return None
        i = int(voxels[y, z, x])
        if i <= 0:
            return None
        # 调色板会随本轮更新变长，names 懒同步（index_of 注入时也一样）
        while len(names) < len(palette):
            p = palette[len(names)]
            names.append(bare(p.get("Name") if isinstance(p, dict) else p)
                         or "air")
        return names[i] if i < len(names) else None

    def props_at(x, y, z) -> dict:
        if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
            return {}
        i = int(voxels[y, z, x])
        return _props_of(palette[i]) if 0 < i < len(palette) else {}

    def facing_at(x, y, z):
        return props_at(x, y, z).get("facing")

    cache: dict = {}
    added = 0

    def alloc(base_idx: int, props: dict) -> int:
        """(原方块名 + 新属性) → 调色板索引（复用已有的，否则追加）。"""
        nonlocal added
        if index_of is not None:
            return int(index_of({"Name": palette[base_idx]["Name"],
                                 "Properties": props}))
        key = (palette[base_idx]["Name"], state_str({"Name": "x",
                                                    "Properties": props}))
        j = cache.get(key, -1)
        if j >= 0:
            return j
        for k, p in enumerate(palette):
            if (isinstance(p, dict) and p.get("Name") == palette[base_idx]["Name"]
                    and _props_of(p) == props):
                cache[key] = k
                return k
        palette.append({"Name": palette[base_idx]["Name"], "Properties": props})
        added += 1
        cache[key] = len(palette) - 1
        return len(palette) - 1

    sub = voxels[y0:y1 + 1, z0:z1 + 1, x0:x1 + 1]
    cand = fam[sub] != 0
    changed = 0
    cells: list = []
    kinds: dict = {}
    for ly, lz, lx in np.argwhere(cand):
        x, y, z = int(lx) + x0, int(ly) + y0, int(lz) + z0
        i = int(voxels[y, z, x])
        kind = STATE_FAMILIES[int(fam[i]) - 1]
        cur = _props_of(palette[i])
        name = names[i]
        pos = (x, y, z)
        if kind == "wall":
            new = C.wall_state(name, pos, name_at)
        elif kind == "fence":
            new = C.fence_state(name, pos, name_at)
        elif kind == "pane":
            new = C.pane_state(name, pos, name_at)
        else:                                    # stairs：只算 shape，不动 facing
            facing = cur.get("facing") or "north"
            new = {"shape": C.stair_shape(pos, facing, name_at,
                                          facing_at=facing_at)}
        merged = dict(cur)
        merged.update({k: str(new[k]) for k in _KEYS[kind] if k in new})
        if merged == cur:
            continue
        j = alloc(i, merged)
        voxels[y, z, x] = j
        changed += 1
        kinds[kind] = kinds.get(kind, 0) + 1
        if len(cells) < 200:
            cells.append([x, y, z, state_str(palette[i]), state_str(palette[j])])
    return {"changed": changed, "cells": cells, "families": kinds,
            "palette_added": added}


def updated_copy(voxels: np.ndarray, palette: list, box=None, *,
                 margin: int = 1) -> tuple:
    """不修改输入的版本（渲染/预览用）：返回 ``(voxels, palette, report)``。"""
    v = np.array(voxels, copy=True)
    p = [dict(e) if isinstance(e, dict) else e for e in palette]
    rep = update_volume(v, p, box, margin=margin)
    return v, p, rep


def _demo() -> None:
    pal = [{"Name": "minecraft:air"}, {"Name": "minecraft:stone"},
           {"Name": "minecraft:polished_deepslate_wall"},
           {"Name": "minecraft:iron_bars"},
           {"Name": "minecraft:oak_stairs",
            "Properties": {"facing": "east", "half": "bottom",
                           "shape": "straight", "waterlogged": "false"}}]
    v = np.zeros((3, 4, 6), dtype=np.uint16)
    v[0, 0, 0] = v[0, 0, 1] = 2          # 一排墙
    v[0, 2, 0] = 2
    v[0, 1, 4] = 3                        # 铁栏杆
    v[1, 0, 0] = 1                        # 墙上方一块石头（up=true）
    v[0, 2, 4] = 4
    v[0, 2, 5] = 4                        # 西邻朝东 → shape=inner_left
    rep = update_volume(v, pal, margin=0)
    print("改动:", rep["changed"], rep["families"], "新状态:", rep["palette_added"])
    for x, y, z, old, new in rep["cells"][:6]:
        print(f"  ({x},{y},{z}) {old} → {new}")


if __name__ == "__main__":
    _demo()
