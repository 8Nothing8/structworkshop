"""规范语料库 CLI：release / manifest / chunk 管理 + SQLite FTS5 条款检索。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mckb import fetch as fetch_mod
from mckb import index as index_mod
from mckb import maintain
from mckb import paths
from mckb.extract import ingest
from mckb.paths import export_dir, kb_dir, release_dir, topics_path

# ------------------------------------------------------------------ helpers
def _loc(r: dict) -> str:
    tag = r.get("section") or ""
    if r.get("clause"):
        tag = f"{tag} {r['clause']}".strip()
    return (f"{r['release']} · {tag or 'front'} · p.{r.get('page_from')}"
            f" · L{r.get('line_from')}-{r.get('line_to')}"
            f" · kb/releases/{r['release']}/full.md")


def _print_rows(rows: list[dict]) -> None:
    for i, r in enumerate(rows, 1):
        print(f"[{i}] {_loc(r)}")


# ------------------------------------------------------------------ commands
def cmd_fetch(a) -> int:
    if a.id:
        out = Path(a.out) if a.out else None
        fetch_mod.fetch_source(a.id, out)
    elif a.openstd:
        fetch_mod.fetch_openstd(a.openstd, Path(a.out or
                             fetch_mod.inbox_dir() / f"{a.openstd}.pdf"))
    elif a.url:
        out = Path(a.out or fetch_mod.inbox_dir() / "download.pdf")
        if "mohurd.gov.cn" in a.url:
            fetch_mod.fetch_mohurd(a.url, out)
        else:
            fetch_mod.download(a.url, out)
    else:
        raise SystemExit("给 --id / --url / --openstd 之一")
    print("[mckb] 下一步: python -m mckb ingest <pdf> --release <id> "
          "--code '<标准号>' --title '<名称>'")
    return 0


def cmd_ingest(a) -> int:
    ingest(Path(a.pdf), a.release, a.code, a.title, publisher=a.publisher,
           source_url=a.source_url, effective=a.effective, status=a.status,
           dpi=a.dpi, engine=a.engine, text=Path(a.text) if a.text else None,
           text_source=a.text_source, force=a.force)
    maintain.scan()
    index_mod.build()
    return 0


def cmd_splice(a) -> int:
    from mckb.extract import splice
    pages = [int(x) for x in str(a.pages).replace(",", " ").split()]
    splice(a.release, pages, Path(a.src))
    index_mod.build()
    maintain.scan()
    return 0


def cmd_audit(a) -> int:
    recs = maintain.audit(a.release[0] if a.release else None,
                          ratio=a.ratio, min_chars=a.min_chars, verbose=not a.json)
    if a.json:
        print(json.dumps(recs, ensure_ascii=False, indent=1))
    return 0


def cmd_rehash(a) -> int:
    from mckb.extract import rehash
    for rel in (a.release or paths.iter_releases()):
        rehash(rel, verbose=not a.quiet)
    index_mod.build()
    maintain.scan()
    return 0


def cmd_denoise(a) -> int:
    from mckb.extract import denoise
    for rel in (a.release or paths.iter_releases()):
        denoise(rel)
    index_mod.build()
    maintain.scan()
    return 0


def cmd_scan(a) -> int:
    maintain.scan()
    return 0


def cmd_catalog(a) -> int:
    p = paths.catalog_path()
    if not p.is_file():
        maintain.scan(verbose=False)
    print(p.read_text(encoding="utf-8"))
    return 0


def cmd_index(a) -> int:
    index_mod.build(releases=a.release or None)
    return 0


def cmd_rechunk(a) -> int:
    from mckb.extract import rechunk
    for rel in a.release:
        rechunk(rel)
    index_mod.build()
    maintain.scan()
    return 0


def cmd_search(a) -> int:
    rows = index_mod.search(a.query, release=a.release, limit=a.limit)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print("（无命中；试试更短的词或 `mckb catalog`）")
        return 1
    _print_rows(rows)
    if a.snippet:
        for r in rows:
            print(f"\n--- {r['release']} {r.get('section','')} ---\n"
                  f"{r['text'][:a.snippet]}")
    return 0


def cmd_read(a) -> int:
    rows = index_mod.read(a.release, section=a.section, clause=a.clause,
                          page=a.page)
    if not rows:
        print(f"（{a.release}: 没有匹配的位置）")
        return 1
    for r in rows:
        head = f"# {a.release} · {r.get('section','')} " \
               f"{r.get('clause','')} · p.{r.get('page_from')}-{r.get('page_to')}" \
               f" · L{r.get('line_from')}-{r.get('line_to')}"
        print(head)
        text = r.get("text", "")
        if a.max_chars and len(text) > a.max_chars:
            print(text[:a.max_chars] + "\n…（截断，用 --max-chars 0 看全文）")
        else:
            print(text)
        print()
    return 0


def cmd_readlist(a) -> int:
    topics = json.loads(topics_path().read_text(encoding="utf-8")) \
        if topics_path().is_file() else {"profiles": {}}
    prof = topics.get("profiles", {}).get(a.profile)
    if prof is None and a.profile:
        raise SystemExit(f"topics.json 里没有 profile {a.profile}")
    groups: dict[str, list[dict]] = {}
    if prof:
        for group, terms in prof.get("topics", {}).items():
            seen: set[tuple] = set()
            hits: list[dict] = []
            for term in terms:
                for r in index_mod.search(term, limit=a.per_term):
                    key = (r["release"], r["seq"])
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(r)
            groups[group] = hits
    if a.query:
        groups.setdefault("自定义", []).extend(
            index_mod.search(a.query, limit=a.per_term))
    if a.json:
        print(json.dumps(groups, ensure_ascii=False, indent=1))
        return 0
    print(f"# reading list · profile={a.profile or '-'}\n")
    print("> 完整文档在 `kb/releases/<release>/full.md`；"
          "用 read 工具打开下面每一条的全文行号。\n")
    for group, hits in groups.items():
        print(f"## {group}")
        if not hits:
            print("- （无命中）")
        for r in hits:
            print(f"- {_loc(r)}")
        print()
    return 0


def cmd_verify(a) -> int:
    recs = maintain.verify(a.release)
    return 0 if all(r["ok"] for r in recs) else 1


def cmd_lint(a) -> int:
    return 1 if maintain.lint() else 0


def cmd_export(a) -> int:
    maintain.export(a.format, Path(a.out) if a.out else None)
    return 0


def cmd_stats(a) -> int:
    s = maintain.stats()
    s["kb_dir"] = str(kb_dir())
    print(json.dumps(s, ensure_ascii=False, indent=1))
    return 0


# ------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mckb", description="building-code knowledge base: "
        "releases / chunks / FTS5 retrieval over complete documents")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="下载完整文档到 kb/inbox/")
    p.add_argument("--id", help="kb/sources.json 中的 id")
    p.add_argument("--url", help="公告页/直链")
    p.add_argument("--openstd", help="国家标准全文公开系统标准号")
    p.add_argument("--out", help="输出文件")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("ingest", help="PDF -> full.md + chunks + manifest")
    p.add_argument("pdf")
    p.add_argument("--release", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--publisher", default="")
    p.add_argument("--source-url", default="")
    p.add_argument("--effective", default="")
    p.add_argument("--status", default="current")
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--engine", default="auto", choices=["auto", "text", "ocr"])
    p.add_argument("--text", help="外部 OCR 文本（带 <!-- page N --> 锚点）代替本地 rapidocr")
    p.add_argument("--text-source", default="external",
                   help="外部文本来源标记（写入 manifest.extract，如 mineru/pi-ocr/manual）")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("audit", help="逐页文本密度体检：哪些页值得重扫/复核")
    p.add_argument("release", nargs="*")
    p.add_argument("--ratio", type=float, default=0.25, help="低于中位数×ratio 视为待复核")
    p.add_argument("--min-chars", type=int, default=40)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("splice", help="用外部 OCR 文本替换 full.md 中指定页（单页修复）")
    p.add_argument("release")
    p.add_argument("--pages", required=True, help="如 37,38,179")
    p.add_argument("--from", dest="src", required=True,
                   help="外部 OCR 文本（带 <!-- page N --> 锚点，可只含修复页）")
    p.set_defaults(func=cmd_splice)

    p = sub.add_parser("rehash", help="行尾统一为 LF 并重算 sha256（clone 后 verify 依然成立）")
    p.add_argument("release", nargs="*")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_rehash)

    p = sub.add_parser("denoise", help="清理 full.md 里的水印噪声行并重切 chunk")
    p.add_argument("release", nargs="*")
    p.set_defaults(func=cmd_denoise)

    p = sub.add_parser("scan", help="重建 registry.json + catalog.md")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("catalog", help="打印语料全览 (Tier-0)")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("index", help="重建 FTS5 索引")
    p.add_argument("--release", action="append")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("rechunk", help="从 full.md 重切 chunk（不改 PDF/OCR）")
    p.add_argument("release", nargs="+")
    p.set_defaults(func=cmd_rechunk)

    p = sub.add_parser("search", help="条款级检索（返回位置）")
    p.add_argument("query")
    p.add_argument("--release")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.add_argument("--snippet", type=int, default=0,
                   help="额外打印前 N 字符（仅导航用）")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("read", help="打印完整章节/条款/页范围")
    p.add_argument("release")
    p.add_argument("--section")
    p.add_argument("--clause")
    p.add_argument("--page", type=int)
    p.add_argument("--max-chars", type=int, default=0)
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("readlist", help="按项目画像列出应读的文件与章节")
    p.add_argument("--profile", default="office")
    p.add_argument("--query", help="额外检索词")
    p.add_argument("--per-term", type=int, default=2)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_readlist)

    p = sub.add_parser("verify", help="完整性校验")
    p.add_argument("release", nargs="?")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("lint", help="registry/release 一致性检查")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("export", help="导出给外部知识库")
    p.add_argument("--format", default="jsonl",
                   choices=["jsonl", "llamaindex", "langchain", "chroma", "txtai"])
    p.add_argument("--out")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("stats", help="索引统计")
    p.set_defaults(func=cmd_stats)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
