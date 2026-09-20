"""模块库写操作：标签 / 元数据 / 导入 / 预览 / 删除。

CLI(`module_lib`、`pack`)与 mcstudio 网页工作台共用这一层，避免两套实现。
所有写盘都是原子的（临时文件 + os.replace），改完统一 ``scan()`` 重建
``packs/index.json``。删除只做“移动到 .cache/backups”，不直接抹文件。

标签仍是 spec 的 ``tags`` 字段（sidecar ``<stem>.module.json`` 优先，可 embed），
本模块不做中央标签库，重命名/合并即遍历全部 spec 改写。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from mccore import structure_io as S
from mccore.module_lib import (
    INDEX,
    iter_modules,
    load_spec,
    scan as module_scan,
    spec_path_for,
    sync_grid,
    validate_spec,
)
from mccore.paths import (child_env, pack_dirs, packs_dir, repo_root,
                          write_text_lf)

# ------------------------------------------------------------------ helpers


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_index(refresh: bool = False) -> dict:
    if refresh or not INDEX.exists():
        return module_scan(quiet=True)
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return module_scan(quiet=True)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    write_text_lf(tmp, text)
    os.replace(tmp, path)


def save_spec(struct_path: Path, spec: dict) -> None:
    _atomic_write_text(spec_path_for(struct_path),
                       json.dumps(spec, ensure_ascii=False, indent=1))


def norm_tags(tags) -> list[str]:
    """去重 + 去空白；**字符串**（HTTP 查询参数 / CLI 参数）按 `;` `,` 换行切分。

    CLI/HTTP 传进来的是一整段文字（如 ``--tags "imported; facade"``），
    旧写法会把字符串当可迭代对象逐字符拆开（`'a;b'` → `['a',';','b']`）。
    """
    if isinstance(tags, str):
        tags = [x for x in re.split(r"[;,\n]", tags)]
    out: list[str] = []
    seen: set[str] = set()
    for t in tags or []:
        t = str(t).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def find_entry(mid: str, index: dict | None = None) -> dict | None:
    idx = index or load_index()
    return idx.get("modules", {}).get(mid)


def module_path(mid: str) -> Path | None:
    e = find_entry(mid)
    if e:
        return packs_dir() / e["path"]
    for _pack, p in iter_modules():
        if p.stem == mid:
            return p
    return None


def iter_specs():
    """Yield (pack_id, structure path, spec) for every module."""
    for pack, path in iter_modules():
        try:
            spec = load_spec(path)
        except SystemExit:
            continue
        yield pack, path, spec


def backup_dir() -> Path:
    """删模块用的备份目录。

    注意：删模块 = **移到这里**（不是复制保护），所以不受工作台「设置」里的备份策略
    影响 —— 策略只作用于「旧版本留底」式的备份（结构保存 / mctools 原地改）。
    """
    d = repo_root() / ".cache" / "backups" / "modules" / time.strftime("%Y%m%d-%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ tags


def tag_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for _pack, _path, spec in iter_specs():
        for t in norm_tags(spec.get("tags")):
            counts[t] = counts.get(t, 0) + 1
    return counts


def tag_tree() -> list[dict]:
    """Tags with counts, namespace and un-namespaced flag, sorted."""
    rows = []
    for name, count in tag_counts().items():
        ns = name.split(":", 1)[0] if ":" in name else ""
        rows.append({"name": name, "count": count, "namespace": ns,
                     "leaf": name.split(":", 1)[1] if ns else name})
    rows.sort(key=lambda r: (r["namespace"] == "", r["namespace"], r["name"]))
    return rows


def set_tags(ids, *, add=(), remove=(), set_to=None) -> dict:
    """Add/remove/replace tags on the given module ids. Writes sidecars."""
    add, remove = norm_tags(add), norm_tags(remove)
    report = {"updated": [], "unchanged": [], "missing": [], "errors": []}
    for mid in ids:
        path = module_path(mid)
        if path is None or not path.is_file():
            report["missing"].append(mid)
            continue
        try:
            spec = load_spec(path)
            old = norm_tags(spec.get("tags"))
            if set_to is not None:
                new = norm_tags(set_to)
            else:
                new = [t for t in old if t not in remove]
                for t in add:
                    if t not in new:
                        new.append(t)
            if new == old:
                report["unchanged"].append(mid)
                continue
            spec["tags"] = new
            save_spec(path, spec)
            report["updated"].append(mid)
        except Exception as e:  # noqa: BLE001
            report["errors"].append({"id": mid, "error": str(e)})
    if report["updated"]:
        module_scan(quiet=True)
    return report


def rename_tag(old: str, new: str) -> dict:
    old, new = old.strip(), new.strip()
    if not old or not new:
        raise ValueError("old/new 不能为空")
    if old == new:
        return {"affected": 0, "from": old, "to": new}
    affected = []
    for _pack, path, spec in iter_specs():
        tags = norm_tags(spec.get("tags"))
        if old in tags:
            tags = [new if t == old else t for t in tags]
            # dedupe (old -> new may collide)
            spec["tags"] = norm_tags(tags)
            save_spec(path, spec)
            affected.append(spec.get("id", path.stem))
    if affected:
        module_scan(quiet=True)
    return {"affected": len(affected), "from": old, "to": new,
            "modules": affected}


def merge_tags(src: str, dst: str) -> dict:
    """Merge src into dst: add dst, drop src (rename 的语义强化版)."""
    src, dst = src.strip(), dst.strip()
    if not src or not dst:
        raise ValueError("src/dst 不能为空")
    if src == dst:
        return {"affected": 0, "from": src, "to": dst}
    affected = []
    for _pack, path, spec in iter_specs():
        tags = norm_tags(spec.get("tags"))
        if src in tags:
            tags = [t for t in tags if t != src]
            if dst not in tags:
                tags.append(dst)
            spec["tags"] = norm_tags(tags)
            save_spec(path, spec)
            affected.append(spec.get("id", path.stem))
    if affected:
        module_scan(quiet=True)
    return {"affected": len(affected), "from": src, "to": dst,
            "modules": affected}


def delete_tag(name: str) -> dict:
    name = name.strip()
    affected = []
    for _pack, path, spec in iter_specs():
        tags = norm_tags(spec.get("tags"))
        if name in tags:
            spec["tags"] = [t for t in tags if t != name]
            save_spec(path, spec)
            affected.append(spec.get("id", path.stem))
    if affected:
        module_scan(quiet=True)
    return {"affected": len(affected), "tag": name, "modules": affected}


# ------------------------------------------------------------------ metadata


def set_meta(id: str, *, description=None, category=None, taxonomy=None,
             notes=None, ports=None) -> dict:
    path = module_path(id)
    if path is None or not path.is_file():
        raise FileNotFoundError(f"没有模块 {id}")
    spec = load_spec(path)
    changed = []
    # grid 是派生数据（结构多大就拿多大）：任何一次写 spec 都顺手校正，
    # 免得改过画布尺寸后模块库报 “grid.size [1, 5, 2] != 实际 [2, 5, 2]”
    if sync_grid(spec, _size_of(path)):
        changed.append("grid")
    if description is not None and description != spec.get("description", ""):
        spec["description"] = str(description)
        changed.append("description")
    if category is not None and category != spec.get("category"):
        spec["category"] = str(category).strip() or spec.get("category")
        changed.append("category")
    if taxonomy is not None:
        spec["taxonomy"] = taxonomy
        changed.append("taxonomy")
    if notes is not None:
        spec["notes"] = str(notes)
        changed.append("notes")
    if ports is not None:
        errs = validate_spec({**spec, "ports": ports}, _size_of(path))
        if errs:
            raise ValueError("接口校验失败: " + "; ".join(errs))
        spec["ports"] = ports
        changed.append("ports")
    if changed:
        save_spec(path, spec)
        module_scan(quiet=True)
    return {"id": id, "changed": changed, "spec": spec}


def _size_of(path: Path) -> tuple[int, int, int]:
    d = S.read_structure(str(path))
    return tuple(int(v) for v in d["size"])


def set_category(ids, category: str) -> dict:
    report = {"updated": [], "errors": [], "missing": []}
    for mid in ids:
        path = module_path(mid)
        if path is None or not path.is_file():
            report["missing"].append(mid)
            continue
        try:
            spec = load_spec(path)
            if spec.get("category") == category:
                continue
            spec["category"] = category
            save_spec(path, spec)
            report["updated"].append(mid)
        except Exception as e:  # noqa: BLE001
            report["errors"].append({"id": mid, "error": str(e)})
    if report["updated"]:
        module_scan(quiet=True)
    return report


# ------------------------------------------------------------------ delete


def delete_module(mid: str, backup: bool = True) -> dict:
    path = module_path(mid)
    if path is None or not path.is_file():
        raise FileNotFoundError(f"没有模块 {mid}")
    spec_file = spec_path_for(path)
    if backup:
        bdir = backup_dir()
        rel = path.relative_to(repo_root())
        dst = bdir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dst))
        if spec_file.is_file():
            shutil.move(str(spec_file), str(dst.with_suffix(".module.json")))
        where = str(dst)
    else:
        path.unlink()
        spec_file.unlink(missing_ok=True)
        where = "(deleted)"
    module_scan(quiet=True)
    return {"id": mid, "backup": where}


def crop_module(mid: str) -> dict:
    """Trim empty outer layers, keep spec grid/ports in sync."""
    path = module_path(mid)
    if path is None or not path.is_file():
        raise FileNotFoundError(f"没有模块 {mid}")
    d = S.read_structure(str(path))
    vox, pal, size, pos = d["voxels"], d["palette"], d["size"], d["position"]
    xs, ys, zs, crop = _bounds(vox, size)
    if not crop:
        return {"id": mid, "changed": False, "size": list(size)}
    new = np.ascontiguousarray(vox[ys[0]:ys[1], zs[0]:zs[1], xs[0]:xs[1]])
    new_size = (int(new.shape[2]), int(new.shape[0]), int(new.shape[1]))
    spec = load_spec(path)
    spec["grid"] = {"size": list(new_size), "position": [0, 0, 0]}
    spec["ports"] = _shift_ports(spec.get("ports", []), (xs[0], ys[0], zs[0]),
                                 new_size)
    tmp = path.with_name(path.stem + ".crop-tmp" + S.PRIMARY_SUFFIX)
    S.write_structure(str(tmp), new, pal, (0, 0, 0), new_size,
                      metadata=d.get("metadata"), name=spec.get("id", mid),
                      data_version=d.get("data_version"))
    os.replace(tmp, path)
    save_spec(path, spec)
    module_scan(quiet=True)
    return {"id": mid, "changed": True, "size": list(new_size),
            "from": list(size), "offset": [int(xs[0]), int(ys[0]), int(zs[0])]}


def _bounds(vox: np.ndarray, size):
    mask = vox != 0
    if not mask.any():
        return (0, size[0]), (0, size[1]), (0, size[2]), False
    xs = np.flatnonzero(mask.any(axis=(0, 1)))
    ys = np.flatnonzero(mask.any(axis=(1, 2)))
    zs = np.flatnonzero(mask.any(axis=(0, 2)))
    x0, x1 = int(xs[0]), int(xs[-1]) + 1
    y0, y1 = int(ys[0]), int(ys[-1]) + 1
    z0, z1 = int(zs[0]), int(zs[-1]) + 1
    crop = (x0, y0, z0) != (0, 0, 0) or (x1, y1, z1) != tuple(size)
    return (x0, x1), (y0, y1), (z0, z1), crop


def _shift_ports(ports, off, new_size) -> list[dict]:
    """Shift port origins by the crop offset, drop ports now out of range."""
    dx, dy, dz = off
    sx, sy, sz = new_size
    out = []
    for p in ports:
        face = p.get("face")
        o = list(p.get("origin", [0, 0]))
        s = list(p.get("size", [1, 1]))
        if face in ("west", "east"):
            o = [o[0] - dy, o[1] - dz]
            lim = sz
        elif face in ("north", "south"):
            o = [o[0] - dy, o[1] - dx]
            lim = sx
        else:
            o = [o[0] - dx, o[1] - dz]
            lim = sz
        h_lim = sy
        if min(o) < 0 or o[0] + s[0] > h_lim or o[1] + s[1] > lim:
            continue
        q = dict(p)
        q["origin"] = [int(v) for v in o]
        out.append(q)
    return out


# ------------------------------------------------------------------ import


def import_files(files, pack: str, *, category=None, flat=False, tags=(),
                 description="", move=False, overwrite=False, embed=False,
                 trim=False, dry_run=False) -> dict:
    """Batch-import structure files as modules into ``packs/<pack>``.

    Returns a report dict; the CLI prints it, the web UI shows it.
    """
    pack_dir = packs_dir() / pack
    if not (pack_dir / "pack.json").is_file():
        raise FileNotFoundError(f"没有资产包 {pack}")
    files = [Path(f) for f in files]
    tags = norm_tags(tags)
    idx = load_index()
    used = set(idx.get("modules", {}))
    report = {"imported": [], "skipped": [], "failed": []}

    for f in files:
        try:
            d = S.read_structure(str(f))
        except Exception as e:  # noqa: BLE001
            report["failed"].append({"file": str(f), "error": str(e)})
            continue
        vox, pal = d["voxels"], d["palette"]
        size = [int(v) for v in d["size"]]
        mid = re.sub(r"[\\/:*?\"<>|\s]+", "_", f.stem).strip("_")
        if not mid:
            report["failed"].append({"file": str(f), "error": "文件名为空"})
            continue
        if mid in used:
            ent = idx.get("modules", {}).get(mid)
            old = repo_root() / "packs" / ent["path"] if ent else None
            if old and old.is_file() and sha256_file(old) == sha256_file(f):
                report["skipped"].append({"file": str(f), "reason": f"内容相同，已是 {mid}"})
                continue
            n = 2
            while f"{mid}_{n}" in used:
                n += 1
            mid = f"{mid}_{n}"
        used.add(mid)

        if category:
            cat = category
        elif flat or f.parent == files[0].parent:
            cat = "imported"
        else:
            cat = f.parent.name
        cat = re.sub(r"[\\/:*?\"<>|\s]+", "_", cat).strip("_") or "imported"

        if trim:
            xs, ys, zs, crop = _bounds(vox, size)
            if crop:
                vox = np.ascontiguousarray(vox[ys[0]:ys[1], zs[0]:zs[1],
                                                xs[0]:xs[1]])
                size = [int(vox.shape[2]), int(vox.shape[0]), int(vox.shape[1])]

        dst_dir = pack_dir / "modules" / cat
        dst = dst_dir / f"{mid}{S.PRIMARY_SUFFIX}"
        if dst.exists() and not overwrite:
            report["skipped"].append({"file": str(f),
                                      "reason": f"已存在 {dst.name}"})
            continue
        if dry_run:
            report["imported"].append({"file": str(f), "id": mid,
                                       "category": cat, "size": size,
                                       "dry_run": True})
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        spec = {
            "id": mid, "pack": pack, "category": cat, "version": 1,
            "description": description or "", "tags": tags,
            "grid": {"size": size, "position": [0, 0, 0]},
            "axis": "x", "flip": True,
            "ports": [],
            "notes": f"导入自 {f.name}；补 description/tags/ports 后重新 scan",
        }
        meta = dict(d["metadata"]) if embed else None
        if meta is not None:
            meta["ModuleSpec"] = json.dumps(spec, ensure_ascii=False)
        S.write_structure(str(dst), vox, pal, d["position"], size,
                          metadata=meta, name=mid,
                          data_version=d.get("data_version"))
        if move and f.resolve() != dst.resolve():
            f.unlink()
        save_spec(dst, spec)
        report["imported"].append({"file": str(f), "id": mid, "category": cat,
                                   "size": size})

    if not dry_run and report["imported"]:
        module_scan(quiet=True)
    return report


# ------------------------------------------------------------------ previews


def render_previews(pack: str | None = None, *, only=None, force=False,
                    online=False, scale_cap: int = 80, progress=None,
                    background: str = "dark", update_states: bool = True) -> dict:
    """Render iso previews (+thumbnails) for a pack; mirrors pack.cmd_previews.

    ``force=False`` 只渲染**缺图**的模块（缩略图与 iso 图都在才算有图）。
    ``progress`` 是可选回调 ``progress(done, total, mid, state)``，state ∈
    ``start`` / ``rendered`` / ``skipped`` / ``failed``，用于把进度报给渲染队列 UI。
    ``background`` ∈ ``dark`` / ``black`` / ``white`` / ``transparent``（透明图带 alpha）。
    ``update_states=True``：渲染前按邻居重算连接状态（墙/栅栏/铁栏杆/玻璃板接起来、
    楼梯 shape 正确）——模块大多是切片/倒模来的，存的连接状态常常过期。
    """
    if pack:
        packs = [packs_dir() / pack]
    else:
        # `packs/` 可能整个不存在（纯代码 checkout）→ 空列表，不要 iterdir 崩
        packs = [p for p in pack_dirs() if (p / "modules").is_dir()]
    report = {"rendered": [], "skipped": [], "failed": []}
    only = set(only or [])
    # 先收集任务清单（跨资产包一起计数），进度条才能给出准确的总数
    items = []
    for pd in packs:
        pdir = pd / "previews"
        pdir.mkdir(parents=True, exist_ok=True)
        for lt in sorted((pd / "modules").rglob("*.schem")):
            if only and lt.stem not in only:
                continue
            items.append((lt, pdir / f"{lt.stem}.png",
                          pdir / f"{lt.stem}_iso.png"))
    total = len(items)
    if progress:
        progress(0, total, "", "start")
    for done, (lt, thumb, full) in enumerate(items, 1):
        stem = lt.stem
        out_dir = thumb.parent          # 写回模块自己所在资产包的 previews/
        if not force and thumb.exists() and full.exists():
            report["skipped"].append(stem)
            if progress:
                progress(done, total, stem, "skipped")
            continue
        try:
            d = S.read_structure(str(lt))
            max_dim = max(d["size"])
            scale = min(scale_cap, max(8, -(-256 // max(1, max_dim))))
            cmd = [sys.executable, "-m", "mcrender.cli", str(lt),
                   "--views", "iso", "--scale", str(scale), "--ssaa", "2",
                   "--background", str(background), "--quiet",
                   "--out", str(out_dir / stem)]
            if update_states:
                cmd.append("--update-states")
            if not online:
                cmd.append("--offline")
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               env=child_env())
            # 子进程退出 0 但没产出文件也算失败（曾出现：--out 写错目录时静默“成功”）
            if r.returncode != 0 or not full.exists():
                err = (f"渲染没有产出 {full.name}（--out {out_dir / stem}）"
                       if r.returncode == 0 else
                       (r.stdout or "")[-400:] + (r.stderr or "")[-400:])
                report["failed"].append({"id": stem, "error": err})
                if progress:
                    progress(done, total, stem, "failed")
                continue
            try:
                from PIL import Image  # noqa: PLC0415
                im = Image.open(full)
                # 透明背景的渲染图带 alpha：缩略图也要保留，否则会被压成黑底
                if "A" not in im.getbands():
                    im = im.convert("RGB")
                im.thumbnail((512, 512))
                if max(im.size) < 256:
                    ratio = 256 / max(im.size)
                    im = im.resize((max(1, int(im.width * ratio)),
                                    max(1, int(im.height * ratio))),
                                   Image.LANCZOS)
                im.save(thumb, optimize=True)
            except Exception as e:  # noqa: BLE001
                report["failed"].append({"id": stem,
                                         "error": f"缩略图失败 {e}"})
                shutil.copyfile(full, thumb)
            report["rendered"].append(stem)
            if progress:
                progress(done, total, stem, "rendered")
        except Exception as e:  # noqa: BLE001
            report["failed"].append({"id": stem, "error": str(e)})
            if progress:
                progress(done, total, stem, "failed")
    if report["rendered"]:
        module_scan(quiet=True)
    return report


# ------------------------------------------------------------------ save as module


def save_as_module(voxels, palette, position, pack: str, mid: str, *,
                   category: str = "custom", tags=(), description="",
                   overwrite: bool = False, data_version=None,
                   ports=None) -> dict:
    """Write an in-memory structure (from the editor) as a new pack module."""
    pack_dir = packs_dir() / pack
    if not (pack_dir / "pack.json").is_file():
        raise FileNotFoundError(f"没有资产包 {pack}")
    mid = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(mid)).strip("_")
    if not mid:
        raise ValueError("模块 id 不能为空")
    cat = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(category)).strip("_") or "custom"
    dst_dir = pack_dir / "modules" / cat
    dst = dst_dir / f"{mid}{S.PRIMARY_SUFFIX}"
    if dst.exists() and not overwrite:
        raise FileExistsError(f"已存在模块 {dst.name}（overwrite 可覆盖）")
    dst_dir.mkdir(parents=True, exist_ok=True)
    size = (int(voxels.shape[2]), int(voxels.shape[0]), int(voxels.shape[1]))
    if data_version is None:
        from mccore import schem_io  # noqa: PLC0415
        data_version = schem_io.DEFAULT_DATA_VERSION
    S.write_structure(str(dst), voxels, palette, position, size,
                      name=mid, data_version=data_version)
    spec = {
        "id": mid, "pack": pack, "category": cat, "version": 1,
        "description": description or "", "tags": norm_tags(tags),
        "grid": {"size": list(size), "position": [0, 0, 0]},
        "axis": "x", "flip": True,
        "ports": ports or [],
        "notes": "mcstudio 编辑器另存为模块",
    }
    save_spec(dst, spec)
    module_scan(quiet=True)
    return {"id": mid, "pack": pack, "category": cat, "size": list(size),
            "path": str(dst.relative_to(repo_root())).replace("\\", "/")}


def transfer_module(mid: str, to_pack: str, *, mode: str = "copy",
                    category: str | None = None, new_id: str | None = None,
                    overwrite: bool = False) -> dict:
    """把一个模块**复制 / 移动**到另一个资产包。

    结构文件 + ``.module.json``（spec）+ 预览图（``previews/<id>*.png``）一起走，
    结束后重建两个包的 ``catalog.md`` 与 ``pack.json``（sha256 / previews）。

    - ``mode="copy"``（默认）：源模块保留；``mode="move"``：源模块**备份后删除**
      （``.cache/backups/modules/``，与「删除模块」同一套）；
    - 目标包已有同名 id（或别的包占用了这个 id）→ 自动加 ``_2``/``_3`` 后缀，
      返回值里的 ``renamed_from`` 写出原名；``overwrite=True`` 则不重命名；
    - ``category`` 不填就沿用原来的分类。
    """
    if mode not in ("copy", "move"):
        raise ValueError("mode 只能是 copy / move")
    entry = find_entry(mid)
    src = module_path(mid)
    if entry is None or src is None or not src.is_file():
        raise FileNotFoundError(f"没有模块 {mid}")
    from_pack = str(entry.get("pack") or "")
    pack_dir = packs_dir() / to_pack
    if not (pack_dir / "pack.json").is_file():
        raise FileNotFoundError(f"没有资产包 {to_pack}")
    if from_pack == to_pack:
        raise ValueError(f"{mid} 已经在 {to_pack} 里了")

    idx = load_index(refresh=True)
    used = set(idx.get("modules", {}))
    if mode == "move":
        used.discard(mid)          # 源马上就没了：id 要能原样带走（否则会被改成 _2）
    dest_id = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(new_id or mid)).strip("_")
    if not dest_id:
        raise ValueError("模块 id 不能为空")
    renamed_from = None
    if dest_id in used and not overwrite:
        renamed_from = dest_id
        n = 2
        while f"{dest_id}_{n}" in used:
            n += 1
        dest_id = f"{dest_id}_{n}"

    spec = dict(load_spec(src) or {})
    cat = re.sub(r"[\\/:*?\"<>|\s]+", "_",
                 str(category or spec.get("category") or entry.get("category")
                     or "custom")).strip("_") or "custom"
    dst_dir = pack_dir / "modules" / cat
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{dest_id}{S.PRIMARY_SUFFIX}"
    if dst.exists() and not overwrite:
        raise FileExistsError(f"已存在 {dst.name}（overwrite 可覆盖）")
    shutil.copy2(src, dst)

    src_pack_dir = packs_dir() / from_pack
    previews = []
    src_prev = src_pack_dir / "previews"
    if src_prev.is_dir():
        for p in sorted(src_prev.glob(f"{mid}*.png")):
            p2 = pack_dir / "previews" / p.name.replace(mid, dest_id, 1)
            p2.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, p2)
            previews.append(p2.name)

    stamp = time.strftime("%Y-%m-%d %H:%M")
    verb = "移动" if mode == "move" else "复制"
    spec.update({"id": dest_id, "pack": to_pack, "category": cat})
    note = f"{stamp} {verb}自 {from_pack}/{mid}"
    spec["notes"] = (str(spec.get("notes") or "").rstrip() + "\n" + note).strip()
    save_spec(dst, spec)

    deleted = None
    if mode == "move":
        deleted = delete_module(mid, backup=True)    # 源文件进 .cache/backups/modules/

    module_scan(quiet=True)                           # 重建 packs/index.json
    try:
        from mccore.pack import rescan_manifests      # noqa: PLC0415
        rescan_manifests(quiet=True)                  # catalog.md + pack.json（两个包一起）
    except Exception:                                 # noqa: BLE001
        pass
    out = {"id": dest_id, "pack": to_pack, "category": cat, "mode": mode,
           "from_pack": from_pack, "from_id": mid, "renamed_from": renamed_from,
           "previews": previews, "note": note,
           "path": str(dst.relative_to(repo_root())).replace("\\", "/")}
    if deleted is not None:
        out["deleted"] = deleted
    return out


# ------------------------------------------------------------------ stats


def stats() -> dict:
    idx = load_index()
    mods = idx.get("modules", {})
    packs = idx.get("packs", {})
    with_preview = sum(1 for m in mods.values() if m.get("preview"))
    return {
        "modules": len(mods),
        "packs": len(packs),
        "tags": len(tag_counts()),
        "previews": with_preview,
        "missing_previews": len(mods) - with_preview,
    }
