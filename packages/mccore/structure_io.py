"""Structure format dispatcher — structworkshop 全流程统一入口。

Reads/writes both formats transparently:

* ``.schem``      — Sponge Schematic v2 (存储格式; see :mod:`mccore.schem_io`)
* ``.litematic``  — Litematica (兼容格式)

Storage policy: :data:`PRIMARY_SUFFIX` (``.schem``) is what writers produce;
readers accept either, so old Litematica files keep working. Use::

    from mccore import structure_io as S
    d = S.read_structure("build.schem")          # 或 "build.litematic"
    S.write_structure("out/x.schem", d["voxels"], d["palette"], d["position"],
                      d["size"], name="x")

``python -m mccore.convert`` 批量迁移既有文件。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from mccore import litematic_io as _litematic
from mccore import schem_io as _schem

PRIMARY_SUFFIX = ".schem"
SUPPORTED_SUFFIXES = (".schem", ".litematic")


def suffix_of(path) -> str:
    return Path(path).suffix.lower()


def is_structure(path) -> bool:
    return suffix_of(path) in SUPPORTED_SUFFIXES


def supported(dirpath) -> list[Path]:
    """All structure files under *dirpath* (recursive), both formats."""
    base = Path(dirpath)
    if base.is_file():
        return [base] if is_structure(base) else []
    out: list[Path] = []
    for suffix in SUPPORTED_SUFFIXES:
        out.extend(base.rglob("*" + suffix))
    return sorted(set(out))


def sniff_format(path) -> str:
    """Best-effort format sniffing for unknown extensions (NBT magic)."""
    from nbt.nbt import NBTFile  # noqa: PLC0415

    root = NBTFile(str(path), "rb")
    if "Regions" in root:
        return ".litematic"
    if "Schematic" in root or ("Width" in root and "Blocks" in root):
        return ".schem"
    if "Width" in root and ("Palette" in root or "BlockData" in root):
        return ".schem"
    raise ValueError(f"无法识别结构格式: {path}")


def read_structure(path, fmt: str | None = None) -> dict:
    """Read a structure file, dispatching on suffix (or *fmt*)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"没有文件: {path}")
    suffix = fmt or suffix_of(path)
    if suffix == "":
        suffix = sniff_format(path)
    if suffix in (".schem", ".schematic"):
        return _schem.read_schem(str(path))
    if suffix == ".litematic":
        return _litematic.read_litematic(str(path))
    raise ValueError(f"不支持的格式 {suffix!r}: {path}(支持 .schem / .litematic)")


def write_structure(path, voxels: np.ndarray, palette: list[dict],
                    position, size, fmt: str | None = None, **kw) -> Path:
    """Write a structure, dispatching on suffix (default :data:`PRIMARY_SUFFIX`)."""
    path = Path(path)
    if not path.suffix:
        path = path.with_suffix(PRIMARY_SUFFIX)
    suffix = fmt or suffix_of(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".schem":
        _schem.write_schem(str(path), voxels, palette, position, size, **kw)
    elif suffix == ".litematic":
        _litematic.write_litematic(str(path), voxels, palette, position, size,
                                   **kw)
    else:
        raise ValueError(f"不支持的格式 {suffix!r}(支持 .schem / .litematic)")
    return path


def primary_path(path) -> Path:
    """``foo.litematic`` -> ``foo.schem`` (storage canonical name)."""
    return Path(path).with_suffix(PRIMARY_SUFFIX)


def latest_path(path) -> Path:
    """Prefer the ``.schem`` sibling when both formats exist."""
    p = Path(path)
    if p.suffix.lower() == PRIMARY_SUFFIX:
        return p
    cand = p.with_suffix(PRIMARY_SUFFIX)
    return cand if cand.is_file() else p


def convert(src, dst=None, *, to: str = PRIMARY_SUFFIX, overwrite: bool = True,
            remove_source: bool = False, verify: bool = True) -> tuple[Path, dict]:
    """Convert one structure file. Returns (dst_path, stats)."""
    src = Path(src)
    target_suffix = to if to.startswith(".") else "." + to
    if target_suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"--to 只支持 {SUPPORTED_SUFFIXES}")
    if dst is None:
        dst = src.with_suffix(target_suffix)
    dst = Path(dst)
    if dst.is_dir():
        dst = dst / src.with_suffix(target_suffix).name
    if dst.exists() and not overwrite and dst != src:
        raise FileExistsError(f"已存在: {dst}(--force 覆盖)")
    if dst.resolve() == src.resolve():
        return dst, {"skipped": "已是目标格式"}

    d = read_structure(src)
    write_structure(dst, d["voxels"], d["palette"], d["position"], d["size"],
                    metadata=d.get("metadata"), name=Path(src).stem,
                    author="structworkshop.convert",
                    data_version=d.get("data_version",
                                       _schem.DEFAULT_DATA_VERSION))
    stats = {"blocks": int((d["voxels"] != 0).sum()), "size": list(d["size"])}
    if verify:
        back = read_structure(dst)
        registry: dict[tuple, int] = {}
        src_ids = _state_ids(d, registry)
        dst_ids = _state_ids(back, registry)
        ok = (tuple(int(v) for v in back["size"]) == tuple(int(v) for v in d["size"])
              and src_ids.shape == dst_ids.shape
              and bool(np.array_equal(src_ids, dst_ids))
              and tuple(int(v) for v in back["position"])
              == tuple(int(v) for v in d["position"]))
        if not ok:
            dst.unlink(missing_ok=True)
            raise RuntimeError(f"往返校验失败, 已删除产物: {dst}")
        stats["verified"] = True
    if remove_source and src.suffix.lower() != target_suffix:
        src.unlink()
    return dst, stats


def _state_key(entry: dict) -> tuple:
    return (entry.get("Name"),
            tuple(sorted((entry.get("Properties") or {}).items())))


def _state_ids(d: dict, registry: dict) -> np.ndarray:
    """Per-cell canonical state ids (cross-palette comparable)."""
    lut = np.array([registry.setdefault(_state_key(e), len(registry))
                    for e in d["palette"]], dtype=np.int64)
    return lut[d["voxels"].reshape(-1)]


def main() -> int:
    """Parse nothing here — the CLI lives in ``mccore.convert``."""
    raise SystemExit("用 python -m mccore.convert")


if __name__ == "__main__":
    raise SystemExit(main())
