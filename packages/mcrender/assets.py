"""Minecraft 资源抓取 + 本地缓存（渲染要的方块状态 / 模型 / 贴图都从这里来）。

Pulls, straight from the internet, the three things a faithful block renderer
needs:

* the **block-state mapping table** -- every block and every legal property
  combination with its default state (``blocks_summary()``);
* the **block states** -- ``blockstates/<block>.json`` (variants / multipart);
* the **block models** -- ``models/block/<name>.json`` (element boxes, faces,
  texture variables, rotations, inheritance chains);
* the **block textures** -- ``textures/block/<name>.png``.

Source
------
`mcmeta <https://github.com/misode/mcmeta>`_ mirrors the vanilla resource pack
(models + blockstates + textures, extracted from the official client jar) and
the data-generator reports.  Every Minecraft version is a git tag
``<version>-assets-tiny``; the mapping table lives on the ``summary`` branch.

Downloads are cached under ``.cache/mcassets/`` so a render is fully offline
after the first run.  Two CDN mirrors are tried automatically if GitHub raw is
unreachable.

Usage
-----
    from mcrender.assets import Assets

    a = Assets(version="26.2")          # explicit
    a = Assets.for_data_version(4903)   # auto: 4903 -> 26.2
    a = Assets.auto(data_version=4903, cache_dir=".cache/mcassets")

    a.blockstate("oak_stairs")          # dict | None
    a.model("block/stairs")             # dict | None  (parents NOT merged)
    a.texture("block/oak_planks")       # PNG bytes | None
    a.blocks_summary()                  # {"oak_stairs": [props, defaults], ...}
    a.prefetch(["oak_stairs", "oak_slab", "iron_bars"])

CLI::

    python -m mcrender.assets --version 26.2 --prefetch oak_stairs oak_slab
    python -m mcrender.assets --version 26.2 --info oak_stairs
    python -m mcrender.assets --data-version 4903
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mccore.paths import cache_dir  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_CACHE = cache_dir()

# mcmeta refs: every version is tagged "<version>-<branch>".
ASSET_BRANCH = "assets-tiny"

# Tried in order.  ``{ref}`` is the tag, ``{path}`` the resource path.
MIRRORS = (
    "https://cdn.jsdelivr.net/gh/misode/mcmeta@{ref}/assets/minecraft/{path}",
    "https://raw.githubusercontent.com/misode/mcmeta/{ref}/assets/minecraft/{path}",
    "https://raw.githack.com/misode/mcmeta/{ref}/assets/minecraft/{path}",
)
MIRRORS_ORDER = list(MIRRORS)

SUMMARY_BLOCKS = "https://raw.githubusercontent.com/misode/mcmeta/summary/blocks/data.json"
SUMMARY_VERSIONS = "https://raw.githubusercontent.com/misode/mcmeta/summary/versions/data.json"

UA = {"User-Agent": "structworkshop-renderer/1.0 (+https://github.com/misode/mcmeta)"}

# Fallback when the data version cannot be mapped.
FALLBACK_VERSION = "26.2"


def _norm_ref(ref: str) -> str:
    """``minecraft:block/stairs`` -> ``block/stairs``."""
    if not ref:
        return ref
    if ref.startswith("minecraft:"):
        ref = ref[len("minecraft:"):]
    return ref


NOT_FOUND = b"\x00NOT_FOUND"  # sentinel: server returned 404


def _http_get(url: str, timeout: int = 10, retries: int = 1):
    """Return bytes, NOT_FOUND for a 404, or None for a network failure."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                return data if data else b" "
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return NOT_FOUND
        except Exception:  # noqa: BLE001 - network flakiness
            pass
        time.sleep(0.15 * (attempt + 1))
    return None


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp%d" % os.getpid())
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Assets:
    """Version-pinned, disk-cached view of the vanilla block assets."""

    def __init__(
        self,
        version: str | None = None,
        cache_dir: str | Path | None = None,
        offline: bool = False,
        workers: int = 16,
        verbose: bool = True,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.version = version or FALLBACK_VERSION
        self.ref = "%s-%s" % (self.version, ASSET_BRANCH)
        self.offline = offline
        self.workers = max(1, int(workers))
        self.verbose = verbose
        self.root = self.cache_dir / self.version
        self.root.mkdir(parents=True, exist_ok=True)
        self._missing_path = self.root / "_missing.json"
        self._missing: set[str] = set()
        if self._missing_path.exists():
            try:
                self._missing = set(json.loads(self._missing_path.read_text("utf-8")))
            except Exception:  # noqa: BLE001
                self._missing = set()
        self._lock = threading.Lock()
        self._summary_blocks: dict | None = None
        self._downloads = 0
        self._cache_hits = 0
        self._dead_mirrors: set[str] = set()

    # ------------------------------------------------------------------ paths
    def _path(self, rel: str) -> Path:
        return self.root / rel

    def _note_missing(self, rel: str) -> None:
        with self._lock:
            if rel in self._missing:
                return
            self._missing.add(rel)
            try:
                _atomic_write(self._missing_path, json.dumps(sorted(self._missing), indent=0).encode())
            except Exception:  # noqa: BLE001
                pass

    # -------------------------------------------------------------- download
    def fetch(self, rel: str) -> bytes | None:
        """Fetch a resource path (e.g. ``blockstates/oak_stairs.json``)."""
        rel = rel.replace("\\", "/").lstrip("/")
        p = self._path(rel)
        if p.exists():
            self._cache_hits += 1
            return p.read_bytes()
        if rel in self._missing or self.offline:
            return None
        data = None
        order = [m for m in MIRRORS_ORDER if m not in self._dead_mirrors] or list(MIRRORS_ORDER)
        for tmpl in order:
            url = tmpl.format(ref=self.ref, path=rel)
            data = _http_get(url)
            if data is NOT_FOUND:
                data = None
                break  # real 404: the file does not exist on any mirror
            if data is not None:
                if tmpl != MIRRORS_ORDER[0]:
                    with self._lock:
                        if tmpl in MIRRORS_ORDER:
                            MIRRORS_ORDER.remove(tmpl)
                            MIRRORS_ORDER.insert(0, tmpl)
                break
            self._dead_mirrors.add(tmpl)  # network failure: skip for this session
        if data is None:
            if self.verbose:
                sys.stderr.write("  ! missing: %s\n" % rel)
            self._note_missing(rel)
            return None
        with self._lock:
            self._downloads += 1
        _atomic_write(p, data)
        return data

    def fetch_json(self, rel: str) -> dict | None:
        raw = self.fetch(rel)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("  ! bad json %s: %s\n" % (rel, e))
            return None

    def fetch_many(self, rels: list[str]) -> dict[str, bytes | None]:
        """Parallel fetch; returns ``{rel: bytes|None}``."""
        out: dict[str, bytes | None] = {}
        todo = []
        for rel in rels:
            p = self._path(rel)
            if p.exists():
                out[rel] = p.read_bytes()
            elif rel in self._missing or self.offline:
                out[rel] = None
            else:
                todo.append(rel)
        if todo:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                for rel, data in zip(todo, ex.map(self.fetch, todo)):
                    out[rel] = data
        return out

    # ------------------------------------------------------------- resources
    def blockstate(self, block: str) -> dict | None:
        block = _norm_ref(block)
        return self.fetch_json("blockstates/%s.json" % block)

    def model(self, ref: str) -> dict | None:
        ref = _norm_ref(ref)
        return self.fetch_json("models/%s.json" % ref)

    def texture(self, ref: str) -> bytes | None:
        ref = _norm_ref(ref)
        return self.fetch("textures/%s.png" % ref)

    def texture_exists(self, ref: str) -> bool:
        return self.texture(ref) is not None

    # --------------------------------------------------------- mapping table
    def blocks_summary(self) -> dict:
        """``{block: [ {prop: [values...]}, {prop: default} ]}`` for this version.

        Prefers the version-pinned ``<version>-summary`` tag so the table lists
        exactly the blocks that exist in that version (the plain ``summary``
        branch tracks the newest snapshot and can contain blocks that do not
        exist yet in this version).
        """
        if self._summary_blocks is not None:
            return self._summary_blocks
        p = self.root / "_blocks_summary.json"
        src = self.root / "_blocks_summary_source.txt"
        if p.exists():
            try:
                have = src.read_text("utf-8").strip() if src.exists() else ""
                if have in (self.ref, "branch"):
                    self._summary_blocks = json.loads(p.read_text("utf-8"))
                    return self._summary_blocks
            except Exception:  # noqa: BLE001
                pass
        if self.offline:
            self._summary_blocks = {}
            return {}
        tag = "%s-summary" % self.version
        urls = (
            "https://raw.githubusercontent.com/misode/mcmeta/%s/blocks/data.json" % tag,
            "https://cdn.jsdelivr.net/gh/misode/mcmeta@%s/blocks/data.json" % tag,
            SUMMARY_BLOCKS,
        )
        data = {}
        used = ""
        for i, url in enumerate(urls):
            raw = _http_get(url, timeout=60)
            if not raw or raw is NOT_FOUND:
                continue
            try:
                data = json.loads(raw.decode("utf-8"))
                used = self.ref if i < 2 else "branch"
                _atomic_write(p, raw)
                _atomic_write(src, used.encode())
                break
            except Exception as e:  # noqa: BLE001
                sys.stderr.write("  ! bad blocks summary: %s\n" % e)
        self._summary_blocks = data
        return data

    # -------------------------------------------------------------- versions
    def versions_manifest(self) -> list:
        p = self.cache_dir / "_versions.json"
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                pass
        if self.offline:
            return []
        raw = _http_get(SUMMARY_VERSIONS, timeout=60)
        if not raw or raw is NOT_FOUND:
            return []
        try:
            data = json.loads(raw.decode("utf-8"))
            _atomic_write(p, raw)
            return data
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("  ! bad versions manifest: %s\n" % e)
            return []

    def version_for_data_version(self, dv: int) -> str | None:
        """Map a litematic ``MinecraftDataVersion`` to a mcmeta version id."""
        manifest = self.versions_manifest()
        exact = [v for v in manifest if v.get("data_version") == dv]
        if exact:
            # prefer releases over snapshots
            for v in exact:
                if v.get("type") == "release":
                    return v["id"]
            return exact[0]["id"]
        # nearest older version (manifest is newest-first)
        older = [v for v in manifest if isinstance(v.get("data_version"), int) and v["data_version"] <= dv]
        if older:
            return older[0]["id"]
        if manifest:
            return manifest[0]["id"]
        return None

    # ---------------------------------------------------------------- prefetch
    def prefetch(self, blocks: list[str], textures: bool = True) -> dict:
        """Warm the cache for a list of blocks.

        Downloads blockstates, then (iteratively) every model reachable through
        ``parent`` chains, then every texture referenced by those models.
        Returns a small report.
        """
        blocks = sorted({_norm_ref(b) for b in blocks if b})
        report = {"blockstates": 0, "models": 0, "textures": 0, "missing": []}

        # 1. blockstates
        bs = self.fetch_many(["blockstates/%s.json" % b for b in blocks])
        report["blockstates"] = sum(1 for v in bs.values() if v is not None)
        report["missing"] += [b for b in blocks if bs.get("blockstates/%s.json" % b) is None]

        # 2. models, breadth-first over parents
        seen_models: set[str] = set()
        frontier: set[str] = set()
        for raw in bs.values():
            if not raw:
                continue
            try:
                d = json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for ref in _collect_model_refs(d):
                frontier.add(ref)
        while frontier:
            todo = sorted(r for r in frontier if r not in seen_models)
            seen_models.update(todo)
            frontier = set()
            if not todo:
                break
            got = self.fetch_many(["models/%s.json" % r for r in todo])
            for ref, raw in zip(todo, got.values()):
                if raw is None:
                    continue
                report["models"] += 1
                try:
                    d = json.loads(raw.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                parent = d.get("parent")
                if parent and not _norm_ref(parent).startswith("builtin/"):
                    frontier.add(_norm_ref(parent))

        # 2b. 方块实体几何用的 entity/* 贴图（不在任何模型里，见表）
        try:
            from mcrender.entity_models import textures as _ent_textures  # noqa: PLC0415
            tex_refs = set(_ent_textures())
        except Exception:  # noqa: BLE001
            tex_refs = set()

        # 3. textures referenced by those models（+ 2b 里的方块实体 entity/* 贴图）
        if textures:
            for ref in seen_models:
                d = self.model(ref)
                if not d:
                    continue
                for val in (d.get("textures") or {}).values():
                    if isinstance(val, str) and not val.startswith("#"):
                        tex_refs.add(_norm_ref(val))
                    elif isinstance(val, dict) and val.get("sprite"):
                        tex_refs.add(_norm_ref(val["sprite"]))
        if tex_refs:
            got = self.fetch_many(["textures/%s.png" % t for t in sorted(tex_refs)])
            report["textures"] = sum(1 for v in got.values() if v is not None)
        report["missing"] = sorted(set(report["missing"]))
        return report

    def stats(self) -> str:
        return "assets %s: %d downloaded, %d cache hits, %d known-missing" % (
            self.version, self._downloads, self._cache_hits, len(self._missing),
        )


def _collect_model_refs(bs: dict) -> list[str]:
    """Every model ref inside a blockstate json (variants + multipart)."""
    out: list[str] = []
    if "variants" in bs:
        for v in bs["variants"].values():
            items = v if isinstance(v, list) else [v]
            for it in items:
                if isinstance(it, dict) and it.get("model"):
                    out.append(_norm_ref(it["model"]))
    if "multipart" in bs:
        for part in bs["multipart"]:
            apply = part.get("apply")
            items = apply if isinstance(apply, list) else [apply]
            for it in items:
                if isinstance(it, dict) and it.get("model"):
                    out.append(_norm_ref(it["model"]))
    return out


#: 公开别名（跨包用这两个，别 import 下划线名）：
#: `mcmaterials` 的稳健抓取要按同一套规则解析 blockstate → 模型 → 贴图。
norm_ref = _norm_ref
collect_model_refs = _collect_model_refs


def auto_assets(
    data_version: int | None = None,
    version: str | None = None,
    cache_dir: str | Path | None = None,
    offline: bool = False,
    verbose: bool = True,
) -> Assets:
    """Build an :class:`Assets` for a version or a litematic data version."""
    if version:
        return Assets(version=version, cache_dir=cache_dir, offline=offline, verbose=verbose)
    probe = Assets(version=FALLBACK_VERSION, cache_dir=cache_dir, offline=offline, verbose=verbose)
    if data_version:
        mapped = probe.version_for_data_version(int(data_version))
        if mapped:
            if verbose and mapped != FALLBACK_VERSION:
                print("  data version %d -> Minecraft %s" % (data_version, mapped))
            return Assets(version=mapped, cache_dir=cache_dir, offline=offline, verbose=verbose)
    return probe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Minecraft asset scraper / cache")
    ap.add_argument("--version", help="Minecraft version id, e.g. 26.2 / 1.21.4")
    ap.add_argument("--data-version", type=int, help="litematic MinecraftDataVersion, e.g. 4903")
    ap.add_argument("--cache", default=None, help="cache directory (default .cache/mcassets)")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--prefetch", nargs="*", default=None, help="block names to pre-download")
    ap.add_argument("--info", nargs="*", default=None, help="print blockstate/model info")
    a = ap.parse_args(argv)

    assets = auto_assets(a.data_version, a.version, a.cache, a.offline)
    print(assets.stats())

    if a.prefetch:
        rep = assets.prefetch(a.prefetch)
        print("prefetch:", {k: v for k, v in rep.items() if k != "missing"})
        if rep["missing"]:
            print("  no blockstate:", ", ".join(rep["missing"][:20]))
    if a.info:
        for b in a.info:
            bs = assets.blockstate(b)
            print("-" * 70)
            print(b, "blockstate:", "MISSING" if bs is None else "ok")
            if bs:
                if "variants" in bs:
                    print("  variants:", len(bs["variants"]))
                if "multipart" in bs:
                    print("  multipart:", len(bs["multipart"]))
                print("  models:", sorted(set(_collect_model_refs(bs)))[:12])
            summ = assets.blocks_summary().get(_norm_ref(b))
            if summ:
                print("  properties:", json.dumps(summ[0])[:400])
                print("  default   :", json.dumps(summ[1])[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
