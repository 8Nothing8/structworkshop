"""Registry / catalog / verify / lint / export for the corpus."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from mckb import index as index_mod
from mckb.extract import sha256_file, write_text_lf
from mckb.paths import (catalog_path, export_dir, index_path, iter_releases,
                        kb_dir, registry_path, release_dir)


# ------------------------------------------------------------------ scan
def _rel(p: Path, root: Path) -> str:
    """仓库相对 posix 路径（不在仓库内就原样返回）—— 写进 registry 的路径不许带本机目录。"""
    try:
        return Path(p).relative_to(root).as_posix()
    except (ValueError, OSError):
        return str(p)


def _major_sections(rel: str, limit: int = 12) -> list[str]:
    """Chapter summary derived from clause ids (OCR chapter titles are lost)."""
    cp = release_dir(rel) / "chunks.jsonl"
    if not cp.is_file():
        return []
    chapters: dict[str, list] = {}
    for line in cp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        cl = c.get("clause") or ""
        if not cl:
            continue
        ch = cl.split(".")[0]
        rec = chapters.setdefault(ch, [0, c.get("page_from"), c.get("page_to")])
        rec[0] += 1
        rec[2] = c.get("page_to")
    out = [f"第{ch}章({n}条, p{p0}-{p1})"
           for ch, (n, p0, p1) in sorted(
               chapters.items(), key=lambda kv: (not kv[0].isdigit(), kv[0]))]
    return out[:limit]


def scan(verbose: bool = True) -> dict:
    releases = []
    for rel in iter_releases():
        man = json.loads((release_dir(rel) / "manifest.json").read_text(encoding="utf-8"))
        arts = man.get("artifacts", {})
        md = arts.get("full.md", {})
        releases.append({
            "release": rel,
            "code": man.get("code", rel),
            "title": man.get("title", ""),
            "status": man.get("status", ""),
            "effective": man.get("effective", ""),
            "publisher": man.get("publisher", ""),
            "pages": md.get("pages", 0),
            "chars": md.get("chars", 0),
            "chunks": md.get("chunks", 0),
            "extract": md.get("extract", ""),
            "complete": man.get("complete", False),
            "source_url": man.get("source_url", ""),
            "path": f"kb/releases/{rel}/full.md",
            "sections": _major_sections(rel),
        })
    root = kb_dir().parent
    reg = {
        "schema": "mckb/registry@1",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        # **仓库相对 posix**：以前写的是 `str(kb_dir())` —— 绝对路径 = 把本机
        # 目录名钉进仓库（换台机器/改个目录名就废），也是本机标识。
        "kb_dir": _rel(kb_dir(), root),
        "releases": releases,
        "index": (_rel(index_path(), root) if index_path().exists() else ""),
    }
    # 内容没变就**沿用旧时间戳**：否则每跑一次 bootstrap 都会把 kb/registry.json
    # 弄脏（只有 generated_at 变了），提交历史里全是这种无谓 diff。
    old = registry_path()
    if old.is_file():
        try:
            prev = json.loads(old.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prev = None
        if isinstance(prev, dict) and prev.get("generated_at") and \
                {k: v for k, v in prev.items() if k != "generated_at"} == \
                {k: v for k, v in reg.items() if k != "generated_at"}:
            reg["generated_at"] = prev["generated_at"]
    write_text_lf(registry_path(),
                  json.dumps(reg, ensure_ascii=False, indent=1))

    lines = ["# mckb corpus · Tier-0 catalog", ""]
    lines.append(f"- releases: {len(releases)}")
    lines.append(f"- documents: complete full texts "
                 f"(`kb/releases/<release>/full.md`), no excerpts")
    lines.append("")
    for r in releases:
        secs = " / ".join(r["sections"])
        lines.append(
            f"- **{r['release']}** · {r['code']} {r['title']} · "
            f"{r['pages']}p · {r['chunks']} chunks · extract={r['extract']} · "
            f"complete={str(r['complete']).lower()} · `{r['path']}`")
        if secs:
            lines.append(f"    sections: {secs}")
    write_text_lf(catalog_path(), "\n".join(lines) + "\n")
    if verbose:
        print(f"[mckb] registry: {len(releases)} releases -> {registry_path()}")
        print(f"[mckb] catalog  -> {catalog_path()}")
    return reg


# ------------------------------------------------------------------ verify
def verify(release: str | None = None, verbose: bool = True) -> list[dict]:
    out = []
    names = [release] if release else iter_releases()
    for rel in names:
        d = release_dir(rel)
        man_p = d / "manifest.json"
        if not man_p.is_file():
            out.append({"release": rel, "ok": False, "errors": ["缺 manifest.json"]})
            continue
        man = json.loads(man_p.read_text(encoding="utf-8"))
        errs = []
        for name, meta in man.get("artifacts", {}).items():
            p = d / name
            if not p.is_file():
                errs.append(f"缺 {name}")
                continue
            if sha256_file(p) != meta.get("sha256"):
                errs.append(f"{name} sha256 不匹配")
        full = (d / "full.md")
        pages = man.get("artifacts", {}).get("full.md", {}).get("pages", 0)
        markers = len(re.findall(r"<!-- page \d+ -->", full.read_text(encoding="utf-8"))) if full.is_file() else 0
        if markers != pages:
            errs.append(f"页标记 {markers} != manifest pages {pages}")
        chunks = 0
        cp = d / "chunks.jsonl"
        if cp.is_file():
            chunks = sum(1 for line in cp.read_text(encoding="utf-8").splitlines() if line.strip())
        if chunks != man.get("artifacts", {}).get("full.md", {}).get("chunks", chunks):
            errs.append("chunks 数与 manifest 不一致")
        rec = {"release": rel, "ok": not errs, "errors": errs,
               "pages": pages, "chunks": chunks,
               "extract": man.get("artifacts", {}).get("full.md", {}).get("extract", ""),
               "complete": man.get("complete", False)}
        out.append(rec)
        if verbose:
            flag = "ok " if rec["ok"] else "FAIL"
            print(f"[{flag}] {rel}: {pages}p {chunks}chunks "
                  f"extract={rec['extract']} complete={rec['complete']}"
                  + (f" errors={errs}" if errs else ""))
    return out


# ------------------------------------------------------------------ audit
def page_texts(rel: str) -> list[tuple[int, str]]:
    """(page number, page text) from ``full.md`` page anchors."""
    p = release_dir(rel) / "full.md"
    if not p.is_file():
        return []
    out: list[tuple[int, str]] = []
    num: int | None = None
    buf: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^<!-- page (\d+) -->$", line.strip())
        if m:
            if num is not None:
                out.append((num, "\n".join(buf).strip()))
            num, buf = int(m.group(1)), []
        elif num is not None:
            buf.append(line)
    if num is not None:
        out.append((num, "\n".join(buf).strip()))
    return out


def audit(release: str | None = None, ratio: float = 0.25, min_chars: int = 40,
          verbose: bool = True) -> list[dict]:
    """Per-page text density: which pages are worth re-reading / re-OCRing.

    Not a verdict — a thin page may be a cover, a figure or a value table whose
    rows rapidocr lost.  ``kind`` guesses which: 表 (numbers collapsed into
    fragments) / 图 (caption only) / 空 (nothing at all).
    """
    from mckb.extract import is_watermark

    names = [release] if release else iter_releases()
    recs: list[dict] = []
    for rel in names:
        pages = page_texts(rel)
        if not pages:
            continue
        lens = sorted(len(t) for _, t in pages)
        med = lens[len(lens) // 2]
        thin, wm = [], 0
        for n, t in pages:
            wm += sum(1 for ln in t.splitlines() if is_watermark(ln))
            if len(t) >= max(min_chars, med * ratio):
                continue
            lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
            short = sum(1 for ln in lines if len(ln) <= 6)
            digits = sum(ch.isdigit() for ch in t)
            if not lines:
                kind = "空"
            elif short >= max(3, len(lines) * 0.5) and digits >= 4:
                kind = "表"
            elif re.search(r"图\s*\d|示意|如图", t):
                kind = "图"
            else:
                kind = "少"
            thin.append({"page": n, "chars": len(t), "kind": kind})
        rec = {"release": rel, "pages": len(pages),
               "chars": sum(len(t) for _, t in pages), "median": med,
               "thin": thin, "watermark_lines": wm}
        recs.append(rec)
        if verbose:
            tag = " ".join(f"p{p['page']}({p['kind']})" for p in thin) or "—"
            print(f"{rel:<24} {rec['pages']:>3}p  中位 {med:>5.0f} 字/页  "
                  f"待复核 {len(thin):>2} 页  水印残留 {wm:>2}  {tag}")
    return recs


# -------------------------------------------------------------------- lint
def lint(verbose: bool = True) -> list[str]:
    errs: list[str] = []
    if not kb_dir().is_dir():
        return ["kb/ 不存在"]
    for rel in iter_releases():
        d = release_dir(rel)
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        for key in ("release", "code", "title", "artifacts", "complete"):
            if key not in man:
                errs.append(f"{rel}: manifest 缺字段 {key}")
        for name in ("source.pdf", "full.md", "chunks.jsonl", "SHA256SUMS"):
            if not (d / name).is_file():
                errs.append(f"{rel}: 缺 {name}")
        if (d / "manifest.json").is_file():
            if man.get("release") != rel:
                errs.append(f"{rel}: manifest.release={man.get('release')}")
    # orphan dirs without manifest
    rd = release_dir("")
    if rd.is_dir():
        for p in rd.iterdir():
            if p.is_dir() and not (p / "manifest.json").is_file():
                errs.append(f"{p.name}: 目录存在但没有 manifest.json（未完成 ingest？）")
    if not index_path().is_file():
        errs.append("index.sqlite 不存在（跑 `python -m mckb index`）")
    if not registry_path().is_file():
        errs.append("registry.json 不存在（跑 `python -m mckb scan`）")
    if verbose:
        if errs:
            for e in errs:
                print(f"[FAIL] {e}")
        else:
            print(f"[ok] {len(iter_releases())} releases, no issues")
    return errs



# ------------------------------------------------------------------ export
def export(fmt: str = "jsonl", out: Path | None = None, verbose: bool = True) -> Path:
    out = out or (export_dir() / fmt)
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for rel in iter_releases():
        man = json.loads((release_dir(rel) / "manifest.json").read_text(encoding="utf-8"))
        cp = release_dir(rel) / "chunks.jsonl"
        if not cp.is_file():
            continue
        for line in cp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            records.append({
                "id": f"{rel}#{c['seq']}",
                "release": rel,
                "source": man.get("code", ""),
                "title": man.get("title", ""),
                "section": c.get("section", ""),
                "clause": c.get("clause", ""),
                "page_from": c.get("page_from"), "page_to": c.get("page_to"),
                "line_from": c.get("line_from"), "line_to": c.get("line_to"),
                "path": f"kb/releases/{rel}/full.md",
                "text": c.get("text", ""),
            })
    if fmt in ("jsonl",):
        p = out / "chunks.jsonl"
        write_text_lf(p, "\n".join(json.dumps(r, ensure_ascii=False)
                                    for r in records) + "\n")
    elif fmt in ("llamaindex", "langchain"):
        p = out / "documents.jsonl"
        write_text_lf(p, "\n".join(json.dumps({
            "text": r["text"],
            "metadata": {k: v for k, v in r.items() if k != "text"},
        }, ensure_ascii=False) for r in records) + "\n")
        write_text_lf(out / "README.md",
                      f"# {fmt} export\n\n"
                      f"`documents.jsonl` 每行 `{{text, metadata}}`，可直接喂给 "
                      f"{'LlamaIndex (SimpleDirectoryReader / JSONReader)' if fmt == 'llamaindex' else 'LangChain (Document / BM25Retriever)'}。\n")
    elif fmt == "chroma":
        try:
            import chromadb
        except ImportError:
            raise SystemExit("pip install chromadb 后再导出 chroma（或先导 jsonl）")
        client = chromadb.PersistentClient(path=str(out))
        col = client.get_or_create_collection("mckb")
        col.upsert(ids=[r["id"] for r in records],
                   documents=[r["text"] for r in records],
                   metadatas=[{k: v for k, v in r.items()
                               if k in ("release", "source", "title", "section",
                                        "clause", "page_from", "page_to", "path")}
                              for r in records])
        p = out
    elif fmt == "txtai":
        try:
            from txtai import Embeddings
        except ImportError:
            raise SystemExit("pip install txtai 后再导出 txtai（或先导 jsonl）")
        emb = Embeddings(content=True)
        emb.index([(r["id"], r["text"], {k: v for k, v in r.items() if k != "text"})
                   for r in records])
        emb.save(str(out))
        p = out
    else:
        raise SystemExit(f"未知格式 {fmt}（jsonl|llamaindex|langchain|chroma|txtai）")
    if verbose:
        print(f"[mckb] export {fmt}: {len(records)} chunks -> {p}")
    return p


def stats() -> dict:
    return index_mod.stats()
