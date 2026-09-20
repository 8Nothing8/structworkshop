"""方块实体的几何表（箱子/床/告示牌/旗帜/头颅/潜影盒/装饰罐/钟/导管/铜傀儡）。

数据由 ``tools/export_entity_models.js`` 从 **deepslate 的 SpecialRenderers**（MIT）
导出到 ``data/entity_models.json``：这些方块在资源包里**没有方块模型**（几何是游戏
代码按状态/NBT 现画的），所以以前 mcrender 只能给它们画一个纯色立方体。

用法::

    from mcrender.entity_models import lookup, textures
    quads = lookup("minecraft:chest[facing=south,waterlogged=false]")   # list[Quad] | None

注意版本差异：床和告示牌在 1.21.4+ 已经是普通方块模型（``block/*_bed_*`` /
``block/*_sign_rot_*``），那种情况走正常的模型路径（``has_elements``），不会用到
这张表；表里仍然保留它们，给没有模型的老版本用。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from mcrender.model import FACE_DIR, FACES, Quad, _norm_ref  # noqa: PLC0415

DATA = Path(__file__).with_name("data") / "entity_models.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    if not DATA.is_file():
        return {"models": {}, "textures": []}
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"models": {}, "textures": []}


def textures() -> list[str]:
    """这张表会用到的全部贴图 ref（``entity/...``；给预取用）。"""
    return list(_data().get("textures") or [])


def available() -> int:
    return len(_data().get("models") or {})


def _quads_for(entry: dict) -> list[Quad]:
    out: list[Quad] = []
    for q in entry.get("quads") or []:
        try:
            pos = np.asarray(q["pos"], dtype=np.float32).reshape(4, 3)
            uv = np.asarray(q["uv"], dtype=np.float32).reshape(4, 2)
        except (KeyError, ValueError):
            continue
        face = q.get("dir") or "up"
        normal = np.asarray(FACE_DIR.get(face, (0, 1, 0)), dtype=np.float32)
        out.append(Quad(pos=pos, uv=uv, normal=normal,
                        tex=_norm_ref(q.get("tex") or ""),
                        cull=-1, tint=-1, shade=True, face=face,
                        source="entity-model"))
    return out


@lru_cache(maxsize=2048)
def lookup(state_str: str) -> tuple | None:
    """按**状态字符串**取几何（如 ``minecraft:chest[facing=south,waterlogged=false]``）。

    没命中时退一步按「同方块名 + 只保留关键属性」找（导出表里属性写法可能差一个
    ``waterlogged`` 之类）；再没有就返回 ``None``。
    """
    models = _data().get("models") or {}
    entry = models.get(state_str)
    if entry is None and "[" in state_str:
        base = state_str.split("[", 1)[0]
        for key, cand in models.items():
            if key.split("[", 1)[0] == base:
                entry = cand
                break
    if entry is None:
        return None
    quads = _quads_for(entry)
    return tuple(quads) if quads else None


def face_shade_of(quad: Quad) -> float:
    """该面在导出表里的方向名（调试/测试用）。"""
    return FACE_DIR.get(quad.face, FACE_DIR["up"])[1]
