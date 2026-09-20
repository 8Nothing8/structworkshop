"""SQLite FTS5 clause index over the corpus (BM25 ranking, CJK-friendly).

Chinese has no whitespace tokenization: we index a *unigram* rendering of every
CJK character (``走道净宽`` -> ``走 道 净 宽``) and translate a user query into
phrase queries over those unigrams (``"走 道"``).  This gives exact substring
semantics with BM25 ranking and zero extra dependencies.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from mckb.paths import index_path, iter_releases, release_dir

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

SCHEMA = """
DROP TABLE IF EXISTS chunks;
CREATE TABLE chunks(
  release TEXT, seq INTEGER, section TEXT, clause TEXT,
  page_from INTEGER, page_to INTEGER,
  line_from INTEGER, line_to INTEGER, text TEXT, search TEXT);
DROP TABLE IF EXISTS chunks_fts;
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  search, content='chunks', content_rowid='rowid', tokenize='unicode61');
DROP TABLE IF EXISTS meta;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""


def unigram(text: str) -> str:
    """Space-separate CJK characters so FTS5 can index them individually."""
    out = []
    for ch in text:
        if CJK_RE.match(ch):
            out.append(f" {ch} ")
        else:
            out.append(ch)
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


def query_to_match(query: str) -> str:
    """Turn a free-text query into an FTS5 MATCH expression (AND of phrases)."""
    parts = []
    for term in re.split(r"\s+", query.strip()):
        term = term.replace('"', "").strip()
        if not term:
            continue
        if CJK_RE.search(term):
            term = " ".join(term)          # 走道 -> 走 道 (phrase of unigrams)
        parts.append(f'"{term}"')
    if not parts:
        raise SystemExit("空查询")
    return " AND ".join(parts)


def loose_match(query: str) -> str:
    """Fallback: AND individual chars, so 窗地比 also matches 窗地面积比."""
    parts = []
    for term in re.split(r"\s+", query.strip()):
        if not term:
            continue
        cjk = "".join(ch for ch in term if CJK_RE.match(ch))
        other = "".join(ch for ch in term if not CJK_RE.match(ch)).strip()
        parts += [f'"{ch}"' for ch in cjk]
        if other:
            parts.append(f'"{other}"')
    return " AND ".join(parts)


def build(releases: list[str] | None = None, db: Path | None = None,
          verbose: bool = True) -> dict:
    db = db or index_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    names = releases or iter_releases()
    rows = []
    for rel in names:
        cp = release_dir(rel) / "chunks.jsonl"
        if not cp.is_file():
            print(f"[mckb] 跳过 {rel}: 缺 chunks.jsonl")
            continue
        man = json.loads((release_dir(rel) / "manifest.json").read_text(encoding="utf-8"))
        head = f"{man.get('code', '')} {man.get('title', '')}"
        for line in cp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            search = unigram("\n".join([head, c.get("section", ""),
                                        c.get("clause", ""), c.get("text", "")]))
            rows.append((c["release"], c["seq"], c.get("section", ""),
                         c.get("clause", ""), c.get("page_from", 0),
                         c.get("page_to", 0), c.get("line_from", 0),
                         c.get("line_to", 0), c.get("text", ""), search))
    con = sqlite3.connect(db)
    try:
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        con.execute("INSERT INTO meta VALUES('built_at', ?)",
                    (time.strftime("%Y-%m-%d %H:%M:%S"),))
        con.execute("INSERT INTO meta VALUES('releases', ?)",
                    (json.dumps(names, ensure_ascii=False),))
        con.execute("INSERT INTO meta VALUES('chunks', ?)", (str(len(rows)),))
        con.commit()
    finally:
        con.close()
    if verbose:
        print(f"[mckb] index: {len(rows)} chunks / {len(names)} releases -> {db}")
    return {"chunks": len(rows), "releases": names, "db": str(db)}


def open_db(db: Path | None = None) -> sqlite3.Connection:
    db = db or index_path()
    if not db.is_file():
        raise SystemExit(f"{db} 不存在，先跑 `python -m mckb index`")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def search(query: str, release: str | None = None, limit: int = 10,
           db: Path | None = None) -> list[dict]:
    con = open_db(db)
    try:
        def run(match: str) -> list[dict]:
            sql = ("SELECT c.*, bm25(chunks_fts) AS rank FROM chunks_fts "
                   "JOIN chunks c ON c.rowid = chunks_fts.rowid "
                   "WHERE chunks_fts MATCH ?")
            args: list = [match]
            if release:
                sql += " AND c.release = ?"
                args.append(release)
            sql += " ORDER BY rank LIMIT ?"
            args.append(limit)
            return [dict(r) for r in con.execute(sql, args)]

        try:
            rows = run(query_to_match(query))
        except sqlite3.OperationalError as e:
            raise SystemExit(f"FTS5 查询失败: {e}（query={query!r}）")
        if not rows and query.strip():
            try:
                rows = run(loose_match(query))
            except sqlite3.OperationalError:
                pass
        return rows
    finally:
        con.close()


def read(release: str, section: str | None = None, clause: str | None = None,
         page: int | None = None, db: Path | None = None) -> list[dict]:
    con = open_db(db)
    try:
        if section or clause or page is not None:
            sql = "SELECT * FROM chunks WHERE release = ?"
            args: list = [release]
            if section:
                sql += " AND (section = ? OR section LIKE ?)"
                args += [section, f"%{section}%"]
            if clause:
                sql += " AND clause = ?"
                args.append(clause)
            if page is not None:
                sql += " AND page_from <= ? AND page_to >= ?"
                args += [page, page]
            sql += " ORDER BY seq"
            return [dict(r) for r in con.execute(sql, args)]
        return [{"release": release, "text": _read_full_md(release)}]
    finally:
        con.close()


def _read_full_md(release: str) -> str:
    return (release_dir(release) / "full.md").read_text(encoding="utf-8")


def stats(db: Path | None = None) -> dict:
    con = open_db(db)
    try:
        meta = {r["key"]: r["value"] for r in con.execute("SELECT * FROM meta")}
        n = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        rels = con.execute(
            "SELECT release, COUNT(*) c FROM chunks GROUP BY release").fetchall()
        return {"meta": meta, "chunks": n,
                "releases": {r["release"]: r["c"] for r in rels}}
    finally:
        con.close()
