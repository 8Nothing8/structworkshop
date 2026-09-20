# -*- coding: utf-8 -*-
"""Robust asset fetching (mirrors + retries), independent of session caches.

mcrender.assets.Assets is fine for one-shot renders, but it remembers every
failure in ``_missing.json`` and then refuses to retry for the whole session --
a transient network hiccup poisons the cache.  The catalog and the chart tools
use this module instead: check the local cache first, otherwise download with
retries across mirrors.
"""
from __future__ import annotations

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache" / "mcassets"
VERSION = "26.2"
REF_TAG = "%s-assets-tiny" % VERSION
BASES = (
    "https://raw.githubusercontent.com/misode/mcmeta/%s/assets/minecraft/%s",
    "https://cdn.jsdelivr.net/gh/misode/mcmeta@%s/assets/minecraft/%s",
)
HEADERS = {"User-Agent": "mcmaterials/1.0 (+mcmeta)"}


def cache_path(rel: str) -> Path:
    return CACHE / VERSION / rel.replace("\\", "/").lstrip("/")


def robust_get(rel: str, tries: int = 3, timeout: int = 30) -> bytes | None:
    """Download one resource with retries across mirrors."""
    rel = rel.replace("\\", "/").lstrip("/")
    for attempt in range(tries):
        for tmpl in BASES:
            url = tmpl % (REF_TAG, rel)
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    if r.status == 200:
                        return r.read()
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.4 * (attempt + 1))
    return None


def cached(rel: str, fetch: bool = True) -> bytes | None:
    """Local cache first, then (optionally) a robust download."""
    p = cache_path(rel)
    if p.exists() and p.stat().st_size > 0:
        return p.read_bytes()
    if not fetch:
        return None
    data = robust_get(rel)
    if data:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(p)
    return data


def cached_json(rel: str, fetch: bool = True):
    raw = cached(rel, fetch)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def fetch_into_cache(rels: list[str], workers: int = 24, label: str = "") -> dict:
    """Fill the cache for a list of resource paths (parallel, retried)."""
    todo, have = [], 0
    for rel in rels:
        p = cache_path(rel)
        if p.exists() and p.stat().st_size > 0:
            have += 1
        else:
            todo.append(rel)
    ok = 0
    if todo:
        def job(rel):
            data = robust_get(rel)
            if data:
                p = cache_path(rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(p.suffix + ".part")
                tmp.write_bytes(data)
                tmp.replace(p)
                return 1
            return 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, n in enumerate(ex.map(job, todo)):
                ok += n
                if label and (i + 1) % 200 == 0:
                    print("  %s %d/%d  (+%d)" % (label, i + 1, len(todo), ok), flush=True)
    return {"cached": have, "downloaded": ok, "failed": len(todo) - ok}
