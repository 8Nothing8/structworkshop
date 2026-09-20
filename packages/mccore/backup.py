"""备份策略：工作台「设置」页里配，落盘 ``.cache/mcstudio/settings.json``。

三种模式
--------
``off``    不备份：保存直接覆盖（**已有备份保留，不动**）
``count``  保留最近 N 次（默认 20）：按**文件**分别计数，超出的最旧份数删掉
``daily``  按天留存（默认 7 天）：同一文件一天只留一份（当天再存就覆盖当天那份），
           超过天数的旧备份删掉

目录布局（与历史一致，不做迁移）::

    .cache/backups/<kind>/<YYYYmmdd-HHMMSS>/<原文件名>

``kind`` 目前有：

- ``structures``  结构文件保存 / 另存为（mcstudio 编辑器）
- ``tools``       mctools ``apply --in-place``（原地改）

**不受本策略管理**的备份（永远照旧执行，因为它们本身就是"把旧东西挪走"而不是
"复制一份保护"）：删除模块 → ``.cache/backups/modules/``；资产包 ``--force`` 覆盖
→ ``.cache/backups/<包 id>-<时间戳>/``。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import date
from pathlib import Path

from .paths import env_first, repo_root

#: 受策略管的备份类别（顺序即 UI 展示顺序）
KINDS = ("structures", "tools")
#: 只统计、不受策略管的类别（"移动到备份" 式）
EXTRA_KINDS = ("modules", "packs")
MODES = ("off", "count", "daily")
DEFAULTS = {"mode": "count", "count": 20, "days": 7}
MAX_COUNT = 500
MAX_DAYS = 365

# ------------------------------------------------------------------ 打开闸门
#: 设置页可改的「打开结构」上限（键顺序即 UI 顺序）。
#: 环境变量仍是运维用的 escape hatch，且**优先级高于设置文件**（见 limits_effective）。
LIMIT_KEYS = ("max_mb", "max_cells", "max_blocks", "warn_cells")
LIMIT_DEFAULTS = {
    "max_mb": 24.0,             # 文件大小上限（MB，读取前就拦）
    "max_cells": 20_000_000,    # 画布体积 x*y*z 上限（防超大空壳）
    "max_blocks": 2_000_000,    # 实心方块数上限（真正决定渲染/编辑成本）
    "warn_cells": 8_000_000,    # 超过就提示「操作可能变慢」
}
LIMIT_RANGE = {
    "max_mb": (0.1, 4096.0),
    "max_cells": (1, 2_000_000_000),
    "max_blocks": (1, 2_000_000_000),
    "warn_cells": (0, 2_000_000_000),
}
LIMIT_ENV = {
    "max_mb": "STRUCTWORKSHOP_MAX_STRUCTURE_MB",
    "max_cells": "STRUCTWORKSHOP_MAX_STRUCTURE_CELLS",
    "max_blocks": "STRUCTWORKSHOP_MAX_STRUCTURE_BLOCKS",
    "warn_cells": "STRUCTWORKSHOP_WARN_STRUCTURE_CELLS",
}
#: 改名前的旧名（``MCFORGE_*``）：仍然认，新名优先。
LIMIT_ENV_LEGACY = {
    "max_mb": "MCFORGE_MAX_STRUCTURE_MB",
    "max_cells": "MCFORGE_MAX_STRUCTURE_CELLS",
    "max_blocks": "MCFORGE_MAX_STRUCTURE_BLOCKS",
    "warn_cells": "MCFORGE_WARN_STRUCTURE_CELLS",
}
LIMIT_LABEL = {
    "max_mb": "文件大小上限",
    "max_cells": "结构格数上限",
    "max_blocks": "方块数量上限",
    "warn_cells": "大结构告警阈值",
}
LIMIT_UNIT = {"max_mb": "MB", "max_cells": "格", "max_blocks": "块", "warn_cells": "格"}


# ---------------------------------------------------------------- settings
def settings_path() -> Path:
    return repo_root() / ".cache" / "mcstudio" / "settings.json"


def backups_root() -> Path:
    return repo_root() / ".cache" / "backups"


def _norm(policy: dict) -> dict:
    """把磁盘上读到的（可能被手改坏的）值收敛到合法范围。"""
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (policy or {}).items() if k in DEFAULTS})
    mode = str(out.get("mode") or "").lower()
    out["mode"] = mode if mode in MODES else DEFAULTS["mode"]
    for key, hi in (("count", MAX_COUNT), ("days", MAX_DAYS)):
        try:
            val = int(out[key])
        except (TypeError, ValueError):
            val = DEFAULTS[key]
        out[key] = max(1, min(val, hi))
    return out


def load_settings() -> dict:
    """读设置；文件缺失/损坏都退化成默认值。"""
    raw = {}
    p = settings_path()
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {"version": 1,
            "backup": _norm(raw.get("backup") or {}),
            "limits": _norm_limits(raw.get("limits") or {})}


def backup_policy() -> dict:
    """当前备份策略 ``{mode, count, days}``。"""
    return load_settings()["backup"]


def _norm_limits(raw: dict) -> dict:
    """把磁盘上读到的上限收敛到合法范围（手改坏也不会让闸门失效）。"""
    out = dict(LIMIT_DEFAULTS)
    raw = raw if isinstance(raw, dict) else {}
    for key in LIMIT_KEYS:
        if raw.get(key) is None:
            continue
        lo, hi = LIMIT_RANGE[key]
        try:
            val = float(raw[key]) if key == "max_mb" else int(raw[key])
        except (TypeError, ValueError):
            continue
        out[key] = max(lo, min(val, hi))
    if out["warn_cells"] > out["max_cells"]:
        out["warn_cells"] = out["max_cells"]
    return out


def limits_policy() -> dict:
    """设置页里保存的上限（不含环境变量覆盖）。"""
    return dict(load_settings()["limits"])


def limits_effective() -> dict:
    """真正生效的上限 + 每个键来自哪里。

    优先级：环境变量（运维 escape hatch，改了不用重启）> 设置文件 > 默认值。
    返回 ``{"values": {...}, "env": {key: 值来源文本}, "source": {key: env|settings|default}}``。
    """
    values = dict(limits_policy())
    env: dict[str, str] = {}
    source = {k: "settings" for k in LIMIT_KEYS}
    for key in LIMIT_KEYS:
        raw = env_first(LIMIT_ENV[key], LIMIT_ENV_LEGACY[key])
        if raw is None or str(raw).strip() == "":
            continue
        _hi = LIMIT_RANGE[key][1]
        try:
            val = float(raw) if key == "max_mb" else int(raw)
        except (TypeError, ValueError):
            continue
        # 环境变量是运维 escape hatch：允许 0（= 什么都不让开），只夹上界
        values[key] = max(0.0 if key == "max_mb" else 0, min(val, _hi))
        env[key] = str(raw).strip()
        source[key] = "env"
    if values["warn_cells"] > values["max_cells"]:
        values["warn_cells"] = values["max_cells"]
    for key in LIMIT_KEYS:
        if key not in env and values[key] == LIMIT_DEFAULTS[key]:
            source[key] = "default"
    return {"values": values, "env": env, "source": source}


def save_settings(patch: dict, limits: dict | None = None) -> dict:
    """合并写入设置（显式值超范围就报错；原子落盘）。

    ``patch`` 是备份策略；``limits`` 是打开闸门（可选，键见 :data:`LIMIT_KEYS`）。
    返回新的 backup 策略（历史行为，调用方多数只要它）。
    """
    patch = patch or {}
    merged = dict(backup_policy())
    merged_limits = _apply_limits_patch(limits)
    if patch.get("mode") is not None:
        mode = str(patch["mode"]).lower()
        if mode not in MODES:
            raise ValueError(f"备份模式只能是 {' / '.join(MODES)} 之一")
        merged["mode"] = mode
    for key, hi in (("count", MAX_COUNT), ("days", MAX_DAYS)):
        if patch.get(key) is None:
            continue
        try:
            val = int(patch[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须是整数") from None
        if not 1 <= val <= hi:
            raise ValueError(f"{key} 范围 1..{hi}")
        merged[key] = val
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"version": 1, "backup": merged,
                               "limits": merged_limits},
                              ensure_ascii=False, indent=1),
                   encoding="utf-8")  # lf-ok: .cache 里的设置，不进 git
    os.replace(tmp, p)
    return merged


def _apply_limits_patch(patch: dict | None) -> dict:
    """校验并合并一组上限设置（显式值超范围报错）。"""
    merged = dict(limits_policy())
    if not patch:
        return merged
    if not isinstance(patch, dict):
        raise ValueError("limits 必须是对象，例如 {max_mb:48,max_blocks:4000000}")
    for key in LIMIT_KEYS:
        if patch.get(key) is None:
            continue
        lo, hi = LIMIT_RANGE[key]
        try:
            val = float(patch[key]) if key == "max_mb" else int(patch[key])
        except (TypeError, ValueError):
            raise ValueError(f"{LIMIT_LABEL[key]}（{key}）必须是数字") from None
        if not lo <= val <= hi:
            unit = LIMIT_UNIT[key]
            raise ValueError(f"{LIMIT_LABEL[key]}（{key}）范围 {lo:g}..{hi:g} {unit}")
        merged[key] = val
    if merged["warn_cells"] > merged["max_cells"]:
        merged["warn_cells"] = merged["max_cells"]
    return merged


def reset_limits() -> dict:
    """把上限恢复成默认值（设置页「恢复默认」用）。"""
    return _apply_limits_patch(dict(LIMIT_DEFAULTS))


# ---------------------------------------------------------------- 备份 / 清理
def _versions(kind: str) -> dict[str, list[Path]]:
    """``{文件名: [备份路径, ...]}``，每个列表按"新 → 旧"排好。"""
    root = backups_root() / kind
    out: dict[str, list[Path]] = {}
    if not root.is_dir():
        return out
    for d in root.iterdir():
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file():
                out.setdefault(f.name, []).append(f)
    for name, items in out.items():
        items.sort(key=lambda p: (p.parent.name, p.name), reverse=True)
    return out


def _drop_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    for d in root.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass
    try:
        if not any(root.iterdir()):
            root.rmdir()
    except OSError:
        pass


def _day_index(stamp: str) -> int:
    """``"20260917"``（或 ``"20260917-005719"``）→ 日期序号（用于按天比较）。"""
    s = str(stamp)[:8]
    return date(int(s[:4]), int(s[4:6]), int(s[6:8])).toordinal()


def prune(kind: str | None = None, *, policy: dict | None = None,
          force: bool = False, today: str | None = None) -> dict:
    """按策略清理旧备份。

    ``off`` 模式下什么都不动（除非 ``force=True``，用于"我要清空该模式的遗留"）。
    返回 ``{mode, removed, bytes, kept}``。
    """
    pol = _norm(policy or backup_policy())
    if pol["mode"] == "off" and not force:
        return {"mode": "off", "removed": 0, "bytes": 0, "kept": None,
                "skipped": "off：不备份也不清理已有备份"}
    today_idx = _day_index(today or time.strftime("%Y%m%d"))
    removed = freed = kept = 0
    for k in ((kind,) if kind else KINDS):
        for _name, vers in _versions(k).items():
            drop: list[Path] = []
            if pol["mode"] == "count":
                kept += min(len(vers), pol["count"])
                drop = vers[pol["count"]:]
            else:                                   # daily
                seen: set[int] = set()
                for p in vers:                      # 新 → 旧
                    try:
                        day = _day_index(p.parent.name)
                    except ValueError:
                        drop.append(p)
                        continue
                    if day in seen or (today_idx - day) >= pol["days"]:
                        drop.append(p)
                        continue
                    seen.add(day)
                    kept += 1
            for p in drop:
                try:
                    freed += p.stat().st_size
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        _drop_empty_dirs(backups_root() / k)
    return {"mode": pol["mode"], "removed": removed, "bytes": freed,
            "kept": kept}


def backup_file(src, kind: str = "structures", *, when: float | None = None):
    """按策略给 ``src`` 留一份旧版本；返回备份路径（``off`` 或源文件不存在 → ``None``）。

    - ``count``：新建 ``<kind>/<时间戳>/<文件名>``，然后把该文件超出的旧份数删掉；
    - ``daily``：当天已有一份就**覆盖当天那份**，否则新建；再按天数清理。
    """
    src = Path(src)
    pol = backup_policy()
    if pol["mode"] == "off" or not src.is_file():
        return None
    ts = time.time() if when is None else float(when)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
    day = stamp[:8]
    if pol["mode"] == "daily":
        for p in _versions(kind).get(src.name, []):
            if p.parent.name.startswith(day):
                shutil.copy2(src, p)                # 当天只留一份：覆盖它
                prune(kind=kind, policy=pol, today=day)
                return p
    d = backups_root() / kind / stamp
    d.mkdir(parents=True, exist_ok=True)
    dst = d / src.name
    shutil.copy2(src, dst)
    prune(kind=kind, policy=pol, today=day)
    return dst


def clear(kinds=None) -> dict:
    """删掉指定类别的全部备份（默认只清受策略管的 KINDS）。

    这里删的是**留底副本**（旧版本），删了不影响当前文件；``modules`` / ``packs``
    里的备份是「删除/覆盖时挪过去的原件」，所以默认不动它们。
    """
    removed = freed = 0
    for k in (kinds or KINDS):
        root = backups_root() / k
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if f.is_file():
                try:
                    freed += f.stat().st_size
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        _drop_empty_dirs(root)
    return {"removed": removed, "bytes": freed}


def stats() -> dict:
    """备份现状（设置页展示）：每个类别多少份、占多少、最新一份是什么时候。"""
    out: dict[str, dict] = {}
    total = {"files": 0, "bytes": 0}
    for k in KINDS + EXTRA_KINDS:
        root = backups_root() / k
        files = nbytes = 0
        names: set[str] = set()
        newest = None
        if root.is_dir():
            for d in sorted(p for p in root.iterdir() if p.is_dir()):
                for f in d.rglob("*"):
                    if f.is_file():
                        files += 1
                        names.add(f.name)
                        try:
                            nbytes += f.stat().st_size
                        except OSError:
                            pass
                newest = d.name
        out[k] = {"files": files, "bytes": nbytes, "names": len(names),
                  "newest": newest, "managed": k in KINDS}
        total["files"] += files
        total["bytes"] += nbytes
    root = backups_root()
    try:
        rel = str(root.relative_to(repo_root())).replace("\\", "/")
    except ValueError:
        rel = str(root)
    return {"root": rel, "abs_root": str(root), "kinds": out, "total": total}
