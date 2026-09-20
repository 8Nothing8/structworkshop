"""Litematica .litematic read/write.

Bit packing matches LitematicaBitArray: a continuous LSB-first bit stream that
may span across 64-bit long boundaries.

Local index layout (Litematica):
    index = (y * |sizeZ| + z) * |sizeX| + x
so a region array reshapes to (sizeY, sizeZ, sizeX).
"""
from __future__ import annotations

import time

import numpy as np
from nbt.nbt import (
    _TAG_End,
    NBTFile,
    TAG_Byte,
    TAG_Compound,
    TAG_Int,
    TAG_List,
    TAG_Long,
    TAG_Long_Array,
    TAG_String,
)

AIR = {"Name": "minecraft:air"}


def nbits_for(npal: int) -> int:
    return max(2, (npal - 1).bit_length())


def unpack_indices(longs: np.ndarray, nbits: int, count: int) -> np.ndarray:
    """Unpack a Litematica continuous bit stream into uint16 palette indices."""
    out = np.empty(count, dtype=np.uint16)
    mask = np.uint64((1 << nbits) - 1)
    chunk = 1 << 23
    for s in range(0, count, chunk):
        e = min(count, s + chunk)
        idx = np.arange(s, e, dtype=np.uint64)
        bp = idx * np.uint64(nbits)
        a0 = (bp >> np.uint64(6)).astype(np.int64)
        off = (bp & np.uint64(63)).astype(np.int64)
        lo = (longs[a0] >> off.astype(np.uint64)) & mask
        need = off > 64 - nbits
        if need.any():
            hi = longs[a0[need] + 1] << (np.uint64(64) - off[need].astype(np.uint64))
            lo[need] = (lo[need] | hi) & mask
        out[s:e] = lo.astype(np.uint16)
    return out


def pack_indices(indices: np.ndarray, nbits: int) -> np.ndarray:
    """Pack uint16 palette indices into a Litematica continuous bit stream."""
    n = len(indices)
    nlongs = (n * nbits + 63) // 64
    arr = np.zeros(nlongs, dtype=np.uint64)
    vals_all = np.asarray(indices, dtype=np.uint64)
    chunk = 1 << 22
    for s in range(0, n, chunk):
        e = min(n, s + chunk)
        idx = np.arange(s, e, dtype=np.uint64)
        bp = idx * np.uint64(nbits)
        a0 = (bp >> np.uint64(6)).astype(np.int64)
        off = (bp & np.uint64(63)).astype(np.int64)
        v = vals_all[s:e]
        np.bitwise_or.at(arr, a0, v << off.astype(np.uint64))
        need = off > 64 - nbits
        if need.any():
            np.bitwise_or.at(
                arr,
                a0[need] + 1,
                v[need] >> (np.uint64(64) - off[need].astype(np.uint64)),
            )
    return arr


def _read_palette(tag_list: TAG_List) -> list[dict]:
    palette = []
    for t in tag_list.tags:
        entry: dict = {"Name": t["Name"].value}
        if "Properties" in t:
            entry["Properties"] = {k: v.value for k, v in t["Properties"].items()}
        palette.append(entry)
    return palette


def _palette_key(entry: dict):
    props = entry.get("Properties") or {}
    return entry["Name"], tuple(sorted(props.items()))


def read_litematic(path: str) -> dict:
    """Read a .litematic and return the voxel array plus palette/metadata."""
    root = NBTFile(path, "rb")
    region_name = root["Regions"].keys()[0]
    region = root["Regions"][region_name]

    size = tuple(
        abs(region["Size"][k].value) for k in ("x", "y", "z")
    )
    position = tuple(region["Position"][k].value for k in ("x", "y", "z"))
    sx, sy, sz = size
    palette = _read_palette(region["BlockStatePalette"])
    nbits = nbits_for(len(palette))

    longs = np.asarray(region["BlockStates"].value, dtype=np.int64).view(np.uint64)
    flat = unpack_indices(longs, nbits, sx * sy * sz)
    voxels = flat.reshape(sy, sz, sx)

    meta = {k: v.value for k, v in root["Metadata"].items() if k != "EnclosingSize"}
    # 方块实体（TileEntities 在 region 里；坐标是 region 局部坐标）
    from .schem_io import _tag_ints  # noqa: PLC0415
    block_entities = []
    te_tag = region["TileEntities"] if "TileEntities" in region else None
    for be in (getattr(te_tag, "tags", None) or []):
        pos = None
        if "Pos" in be:
            vals = _tag_ints(be["Pos"])
            if len(vals) >= 3:
                pos = tuple(vals[:3])
        if pos is None:
            continue
        block_entities.append({"pos": pos, "tag": be})
    return {
        "voxels": voxels,
        "palette": palette,
        "size": size,
        "position": position,
        "metadata": meta,
        "region_name": region_name,
        "data_version": root["MinecraftDataVersion"].value,
        "version": root["Version"].value,
        "sub_version": root["SubVersion"].value,
        "block_entities": block_entities,
        "root": root,
    }


def _make_list(name: str, tag_id: int) -> TAG_List:
    lst = TAG_List(name=name)
    lst.tagID = tag_id
    return lst


def _make_palette_list(palette: list[dict]) -> TAG_List:
    out = _make_list("BlockStatePalette", 10)
    for entry in palette:
        c = TAG_Compound()
        c.tags.append(TAG_String(name="Name", value=entry["Name"]))
        props = entry.get("Properties")
        if props:
            p = TAG_Compound(name="Properties")
            for k, v in props.items():
                p.tags.append(TAG_String(name=k, value=v))
            c.tags.append(p)
        out.tags.append(c)
    return out


def write_litematic(
    path: str,
    voxels: np.ndarray,
    palette: list[dict],
    position: tuple,
    size: tuple,
    metadata: dict | None = None,
    name: str = "Unnamed",
    author: str = "",
    description: str = "",
    data_version: int = 4903,
    tile_entities: TAG_List | None = None,
    entities: TAG_List | None = None,
) -> None:
    """Write a region array (uint16 palette indices) as a .litematic file."""
    sy, sz, sx = voxels.shape
    assert (sx, sy, sz) == tuple(abs(v) for v in size), (voxels.shape, size)
    npal = len(palette)
    nbits = nbits_for(npal)
    flat = np.ascontiguousarray(voxels.reshape(-1))
    packed = pack_indices(flat, nbits)

    root = NBTFile()
    root.name = ""
    root.tags.append(TAG_Int(name="MinecraftDataVersion", value=data_version))
    root.tags.append(TAG_Int(name="Version", value=7))
    root.tags.append(TAG_Int(name="SubVersion", value=1))

    md = TAG_Compound(name="Metadata")
    now = int(time.time() * 1000)
    md.tags.append(TAG_Long(name="TimeCreated", value=now))
    md.tags.append(TAG_Long(name="TimeModified", value=now))
    enc = TAG_Compound(name="EnclosingSize")
    for k, v in zip(("x", "y", "z"), (sx, sy, sz)):
        enc.tags.append(TAG_Int(name=k, value=v))
    md.tags.append(enc)
    md.tags.append(TAG_String(name="Description", value=description))
    md.tags.append(TAG_Int(name="RegionCount", value=1))
    md.tags.append(TAG_Int(name="TotalBlocks", value=int((voxels != 0).sum())))
    md.tags.append(TAG_String(name="Author", value=author))
    md.tags.append(TAG_Int(name="TotalVolume", value=sx * sy * sz))
    md.tags.append(TAG_String(name="Name", value=name))
    root.tags.append(md)

    regions = TAG_Compound(name="Regions")
    region = TAG_Compound(name="Unnamed")

    region.tags.append(_make_palette_list(palette))
    bs = TAG_Long_Array(name="BlockStates")
    bs.value = packed.view(np.int64).tolist()
    region.tags.append(bs)

    pos = TAG_Compound(name="Position")
    for k, v in zip(("x", "y", "z"), position):
        pos.tags.append(TAG_Int(name=k, value=int(v)))
    region.tags.append(pos)

    szc = TAG_Compound(name="Size")
    for k, v in zip(("x", "y", "z"), size):
        szc.tags.append(TAG_Int(name=k, value=int(v)))
    region.tags.append(szc)

    if tile_entities is None:
        tile_entities = _make_list("TileEntities", 10)
    elif isinstance(tile_entities, (list, tuple)):
        lst = _make_list("TileEntities", 10)
        for it in tile_entities:
            if it is None:
                continue
            if isinstance(it, TAG_Compound):
                lst.tags.append(it)
            elif isinstance(it, dict) and isinstance(it.get("tag"), TAG_Compound):
                lst.tags.append(it["tag"])
        tile_entities = lst
    else:
        tile_entities.name = "TileEntities"
    region.tags.append(tile_entities)

    if entities is None:
        entities = _make_list("Entities", 0)
    else:
        entities.name = "Entities"
    region.tags.append(entities)

    region.tags.append(_make_list("PendingBlockTicks", 0))
    region.tags.append(_make_list("PendingFluidTicks", 0))

    regions.tags.append(region)
    root.tags.append(regions)

    root.write_file(path)
