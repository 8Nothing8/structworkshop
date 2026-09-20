"""Canonical locations for the code knowledge base (``<repo>/kb/``)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from mccore.paths import repo_root


@lru_cache(maxsize=1)
def kb_dir() -> Path:
    return repo_root() / "kb"


def releases_dir() -> Path:
    return kb_dir() / "releases"


def inbox_dir() -> Path:
    d = kb_dir() / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def export_dir() -> Path:
    return kb_dir() / "export"


def registry_path() -> Path:
    return kb_dir() / "registry.json"


def catalog_path() -> Path:
    return kb_dir() / "catalog.md"


def index_path() -> Path:
    return kb_dir() / "index.sqlite"


def sources_path() -> Path:
    return kb_dir() / "sources.json"


def topics_path() -> Path:
    return kb_dir() / "topics.json"


def release_dir(release: str) -> Path:
    return releases_dir() / release


def iter_releases() -> list[str]:
    """Release ids, sorted, that contain a manifest."""
    d = releases_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if (p / "manifest.json").is_file())
