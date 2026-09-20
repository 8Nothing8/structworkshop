"""Sponge Schematic v2（.schem）读写 —— 本仓库的**存储格式**。

Storage format of structworkshop: writers emit ``.schem`` (Sponge Schematic v2),
readers accept v2 **and** v3 (WorldEdit 7.3+ / FAWE). The returned dict is
identical in shape to :func:`mccore.litematic_io.read_litematic`, so every
consumer (renderer / QA / slice / assemble / module_lib) is format-agnostic::

    {"voxels": uint16 (sy, sz, sx), "palette": [{"Name":…, "Properties":…}],
     "size": (sx, sy, sz), "position": (x, y, z), "metadata": {...},
     "region_name": "Schematic", "data_version": int, "root": NBTFile}

Layout notes (Sponge v2):

* index order is YZX, i.e. ``y * Width * Length + z * Width + x`` — the same
  local layout that Litematica uses, so no voxel reordering is needed;
* ``BlockData`` is a byte array of unsigned LEB128 varints (one per cell,
  full bounding box, air included);
* dims are ``short``; palette is ``state -> index``; every cell has an entry;
* the engine invariant ``palette[0] == minecraft:air`` is enforced on read
  (missing air is prepended and indices are remapped).
"""
from __future__ import annotations

import time

import numpy as np
from nbt.nbt import (  # noqa: F401
    NBTFile,
    TAG_Byte,
    TAG_Byte_Array,
    TAG_Compound,
    TAG_Int,
    TAG_Int_Array,
    TAG_List,
    TAG_Long,
    TAG_Short,
    TAG_String,
)

DEFAULT_DATA_VERSION = 4903          # same default the compositions use
AIR = {"Name": "minecraft:air"}


# ------------------------------------------------------------------ states
def _is_air(entry: dict) -> bool:
    name = entry.get("Name", "")
    return name in ("minecraft:air", "air") and not entry.get("Properties")


def state_str(entry: dict) -> str:
    """Canonical Sponge block state string: ``minecraft:oak_stairs[facing=n]``."""
    name = entry["Name"]
    props = entry.get("Properties") or {}
    if not props:
        return name
    body = ",".join(f"{k}={props[k]}" for k in sorted(props))
    return f"{name}[{body}]"


def parse_state(text: str) -> dict:
    """Inverse of :func:`state_str` (tolerates missing ``minecraft:`` prefix)."""
    text = text.strip()
    if "[" in text and text.endswith("]"):
        name, body = text[:-1].split("[", 1)
        props: dict[str, str] = {}
        for pair in body.split(","):
            if not pair.strip():
                continue
            k, _, v = pair.partition("=")
            props[k.strip()] = v.strip()
        entry: dict = {"Name": name.strip()}
        if props:
            entry["Properties"] = props
        return entry
    return {"Name": text}


# ------------------------------------------------------------------ varint
def _varint_encode(flat: np.ndarray) -> bytes:
    """LEB128-encode palette indices (fast path when every index < 128)."""
    v = flat.astype(np.uint64)
    if v.size and int(v.max()) < 0x80:
        return v.astype(np.uint8).tobytes()
    out = bytearray()
    for x in v.tolist():
        while True:
            b = x & 0x7F
            x >>= 7
            if x:
                out.append(b | 0x80)
            else:
                out.append(b)
                break
    return bytes(out)


def _varint_decode(data: bytes | bytearray, count: int) -> np.ndarray:
    """Decode ``count`` LEB128 varints into a uint16 array."""
    buf = bytes(data)
    raw = np.frombuffer(buf, dtype=np.uint8)
    if raw.size == count and not (raw & 0x80).any():
        return raw.astype(np.uint16)
    out = np.zeros(count, dtype=np.uint16)
    value = 0
    shift = 0
    i = 0
    for byte in buf:
        value |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            continue
        if i < count:
            out[i] = value
        i += 1
        if i >= count:
            break
        value = 0
        shift = 0
    return out


# ------------------------------------------------------------------ read
def _tag_value(tag):
    value = tag.value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return value


def _tag_ints(tag) -> list[int]:
    """取一个整型列表（TAG_List 的元素在 ``.tags`` 里，新版 nbt 里 ``.value`` 是 None）。"""
    items = getattr(tag, "tags", None)
    if items is None or isinstance(items, dict):
        items = getattr(tag, "value", None)
    out: list[int] = []
    for it in (items or []):
        v = getattr(it, "value", it)
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            return []
    return out


def _read_block_entities(top) -> list[dict]:
    """读 ``BlockEntities``（v2/v3 都在顶层；有些工具写进 ``Blocks``）。

    保留**原始 TAG_Compound**，不转 Python 值：写回时原样落盘，不丢任何字段
    （箱子里的物品、告示牌文字、刷怪笼数据…）。渲染端要 JSON 时另走
    :func:`block_entity_json`。
    """
    tag = None
    if "BlockEntities" in top:
        tag = top["BlockEntities"]
    elif "Blocks" in top and "BlockEntities" in top["Blocks"]:
        tag = top["Blocks"]["BlockEntities"]
    out: list[dict] = []
    for be in (getattr(tag, "tags", None) or []):
        if not isinstance(be, TAG_Compound):
            continue
        pos = None
        if "Pos" in be:
            vals = _tag_ints(be["Pos"])
            if len(vals) >= 3:
                pos = tuple(vals[:3])
        elif all(k in be for k in ("x", "y", "z")):
            try:
                pos = (int(be["x"].value), int(be["y"].value),
                       int(be["z"].value))
            except (TypeError, ValueError):
                pos = None
        if pos is None:
            continue
        out.append({"pos": pos, "tag": be})
    return out


def _tag_simple(tag):
    """递归转 JSON 可序列化的 Python 值（bytes → hex 字符串）。

    注意 ``nbt`` 库里 TAG_Compound.tags 是**带名字的 TAG 列表**（不是 dict），
    所以按子标签有没有名字来判断是「对象」还是「数组」。
    """
    value = getattr(tag, "value", None)
    tags = getattr(tag, "tags", None)
    if isinstance(tags, list):
        if isinstance(tag, TAG_Compound):
            return {t.name: _tag_simple(t) for t in tags if getattr(t, "name", None)}
        return [_tag_simple(t) for t in tags]
    if isinstance(tags, dict):
        return {k: _tag_simple(v) for k, v in tags.items()}
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return [int(v) for v in value]
    except Exception:  # noqa: BLE001
        return None


#: 方块实体里只传这几个字段给浏览器（渲染用；物品清单太大且渲染用不到）
_BE_JSON_KEYS = (
    ("patterns", "patterns"), ("Patterns", "patterns"),
    ("front_text", "front_text"), ("back_text", "back_text"),
    ("color", "color"), ("Color", "color"),
    ("SkullOwner", "SkullOwner"), ("profile", "profile"),
    ("note", "note"), ("instrument", "instrument"),
)


def block_entity_json(entries, *, limit: int = 4000) -> list[list]:
    """方块实体 → 浏览器可用的 JSON：``[[x,y,z,id,data],…]``。"""
    out: list[list] = []
    for e in (entries or [])[:limit]:
        tag = e.get("tag")
        if tag is None:
            continue
        eid = ""
        for key in ("id", "Id"):
            if key in tag:
                try:
                    eid = str(tag[key].value)
                except Exception:  # noqa: BLE001
                    eid = ""
                break
        data: dict = {}
        for key, out_key in _BE_JSON_KEYS:
            if key not in tag:
                continue
            try:
                data[out_key] = _tag_simple(tag[key])
            except Exception:  # noqa: BLE001
                continue
        try:
            pos = [int(v) for v in e["pos"][:3]]
        except Exception:  # noqa: BLE001
            continue
        out.append([pos[0], pos[1], pos[2], eid, data])
    return out


def _find_position(top, meta: dict) -> tuple[int, int, int]:
    if "Offset" in top:
        off = list(top["Offset"].value)
        if len(off) == 3:
            return int(off[0]), int(off[1]), int(off[2])
    if all(k in meta for k in ("WEOffsetX", "WEOffsetY", "WEOffsetZ")):
        return (int(meta["WEOffsetX"]), int(meta["WEOffsetY"]),
                int(meta["WEOffsetZ"]))
    if "WEOffset" in meta:
        off = list(meta["WEOffset"])
        if len(off) == 3:
            return int(off[0]), int(off[1]), int(off[2])
    return 0, 0, 0


def read_schem(path: str) -> dict:
    """Read a Sponge ``.schem`` (v2 or v3) into the structworkshop structure dict."""
    root = NBTFile(path, "rb")
    top = root["Schematic"] if "Schematic" in root else root
    blocks = top["Blocks"] if "Blocks" in top else top      # v3 nests blocks
    if "Width" not in top:
        raise ValueError(f"{path}: 不是 Sponge schematic(缺 Width/Height/Length)")
    sx = abs(int(top["Width"].value))
    sy = abs(int(top["Height"].value))
    sz = abs(int(top["Length"].value))
    version = int(top["Version"].value) if "Version" in top else 2

    palette_tag = blocks["Palette"] if "Palette" in blocks else None
    if palette_tag is None:
        raise ValueError(f"{path}: schematic 缺 Palette")
    file_pal: dict[int, dict] = {}
    for tag in palette_tag.tags:
        file_pal[int(tag.value)] = parse_state(tag.name)

    data_tag = blocks["BlockData"] if "BlockData" in blocks \
        else blocks["Data"]
    flat = _varint_decode(data_tag.value, sx * sy * sz)

    # normalise to the engine invariant palette[0] == air
    palette: list[dict] = [AIR]
    max_idx = max([max(file_pal) if file_pal else 0,
                   int(flat.max()) if flat.size else 0])
    lut = np.zeros(max_idx + 1, dtype=np.uint16)
    air_seen = False
    for idx in sorted(file_pal):
        entry = file_pal[idx]
        if _is_air(entry):
            lut[idx] = 0
            air_seen = True
            continue
        lut[idx] = len(palette)
        palette.append(entry)
    if not air_seen:
        # file stored an air-free palette (v3 style): 未列出的索引视为空气
        pass
    voxels = lut[flat] if file_pal else np.zeros(sx * sy * sz, dtype=np.uint16)

    meta_tag = top["Metadata"] if "Metadata" in top else None
    metadata = {}
    if meta_tag is not None:
        metadata = {k: _tag_value(t) for k, t in meta_tag.items()}
    data_version = int(top["DataVersion"].value) if "DataVersion" in top \
        else DEFAULT_DATA_VERSION
    position = _find_position(top, metadata)

    return {
        "voxels": voxels.reshape(sy, sz, sx),
        "palette": palette,
        "size": (sx, sy, sz),
        "position": position,
        "metadata": metadata,
        "region_name": "Schematic",
        "data_version": data_version,
        "version": version,
        "sub_version": 0,
        "block_entities": _read_block_entities(top),
        "root": root,
    }


# ------------------------------------------------------------------ write
def _metadata_tags(metadata: dict | None, name: str, author: str,
                   description: str) -> list:
    merged: dict = dict(metadata or {})
    merged.pop("TimeCreated", None)
    merged.pop("TimeModified", None)
    merged.setdefault("Name", name)
    merged.setdefault("Author", author)
    merged.setdefault("Description", description)
    md = TAG_Compound(name="Metadata")
    for key, value in merged.items():
        if isinstance(value, bool):
            md.tags.append(TAG_Byte(name=key, value=int(value)))
        elif isinstance(value, (int, np.integer)):
            if -2**31 <= int(value) < 2**31:
                md.tags.append(TAG_Int(name=key, value=int(value)))
            else:
                md.tags.append(TAG_Long(name=key, value=int(value)))
        elif isinstance(value, float):
            from nbt.nbt import TAG_Float  # noqa: PLC0415
            md.tags.append(TAG_Float(name=key, value=float(value)))
        else:
            md.tags.append(TAG_String(name=key, value=str(value)))
    return md


def _canonical_palette(palette: list[dict],
                       flat: np.ndarray) -> tuple[list[dict], np.ndarray]:
    """Collapse duplicate states (same name+properties) and remap indices.

    Source data may carry the same block state twice (e.g. generated builds
    appending a palette entry per pass); a Sponge ``Palette`` compound is keyed
    by state string, so duplicates must be merged before writing.
    """
    seen: dict[tuple, int] = {}
    lut = np.zeros(len(palette), dtype=np.uint16)
    out: list[dict] = []
    for i, entry in enumerate(palette):
        key = (entry.get("Name"), tuple(sorted((entry.get("Properties")
                                                or {}).items())))
        idx = seen.get(key)
        if idx is None:
            idx = len(out)
            seen[key] = idx
            out.append(entry)
        lut[i] = idx
    return out, lut[flat]


def write_schem(
    path: str,
    voxels: np.ndarray,
    palette: list[dict],
    position: tuple,
    size: tuple,
    metadata: dict | None = None,
    name: str = "Unnamed",
    author: str = "",
    description: str = "",
    data_version: int = DEFAULT_DATA_VERSION,
    tile_entities: TAG_List | None = None,
    entities: TAG_List | None = None,
) -> None:
    """Write a region array (uint16 palette indices) as a Sponge v2 ``.schem``."""
    sy, sz, sx = voxels.shape
    assert (sx, sy, sz) == tuple(abs(int(v)) for v in size), (voxels.shape, size)
    palette, flat = _canonical_palette(palette,
                                       np.ascontiguousarray(voxels.reshape(-1)))

    root = NBTFile()
    root.name = "Schematic"
    root.tags.append(TAG_Int(name="MinecraftDataVersion",
                             value=int(data_version)))
    root.tags.append(TAG_Int(name="Version", value=2))
    root.tags.append(TAG_Int(name="DataVersion", value=int(data_version)))
    root.tags.append(TAG_Short(name="Width", value=sx))
    root.tags.append(TAG_Short(name="Height", value=sy))
    root.tags.append(TAG_Short(name="Length", value=sz))
    off = TAG_Int_Array(name="Offset")
    off.value = [int(position[0]), int(position[1]), int(position[2])]
    root.tags.append(off)

    meta = _metadata_tags(metadata, name, author, description)
    for axis, key in enumerate(("WEOffsetX", "WEOffsetY", "WEOffsetZ")):
        if key not in meta:
            meta.tags.append(TAG_Int(name=key, value=int(position[axis])))
    meta.tags.append(TAG_Long(name="TimeCreated",
                              value=int(time.time() * 1000)))
    meta.tags.append(TAG_Long(name="TimeModified",
                              value=int(time.time() * 1000)))
    root.tags.append(meta)

    root.tags.append(TAG_Int(name="PaletteMax", value=len(palette)))
    pal = TAG_Compound(name="Palette")
    for i, entry in enumerate(palette):
        pal.tags.append(TAG_Int(name=state_str(entry), value=i))
    root.tags.append(pal)

    bd = TAG_Byte_Array(name="BlockData")
    bd.value = bytearray(_varint_encode(flat))
    root.tags.append(bd)

    if tile_entities is None:
        tile_entities = _make_list("BlockEntities", 10)
    elif isinstance(tile_entities, (list, tuple)):
        tile_entities = _tile_entity_list(tile_entities)
    else:
        tile_entities.name = "BlockEntities"
    root.tags.append(tile_entities)
    if entities is None:
        entities = _make_list("Entities", 10)
    else:
        entities.name = "Entities"
    root.tags.append(entities)

    root.write_file(path)


def _make_list(name: str, tag_id: int) -> TAG_List:
    lst = TAG_List(name=name)
    lst.tagID = tag_id
    return lst


def _tile_entity_list(items) -> TAG_List:
    """方块实体列表 → TAG_List（接受原始 TAG_Compound / ``{pos, tag}``）。"""
    lst = _make_list("BlockEntities", 10)
    for it in (items or []):
        if it is None:
            continue
        if isinstance(it, TAG_Compound):
            lst.tags.append(it)
        elif isinstance(it, dict) and isinstance(it.get("tag"), TAG_Compound):
            lst.tags.append(it["tag"])
    return lst


# ------------------------------------------------------------------ CLI
def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Sponge .schem 读写")
    ap.add_argument("file", help=".schem 文件")
    ap.add_argument("--json", action="store_true", help="打印摘要 JSON")
    a = ap.parse_args()
    d = read_schem(a.file)
    info = {"size": list(d["size"]), "position": list(d["position"]),
            "blocks": int((d["voxels"] != 0).sum()),
            "palette": len(d["palette"]),
            "data_version": d["data_version"], "version": d["version"]}
    if a.json:
        import json
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(f"{a.file}: " + " ".join(f"{k}={v}" for k, v in info.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
