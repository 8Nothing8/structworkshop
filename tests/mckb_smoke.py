"""mckb smoke test: chunking + CJK unigram FTS5 retrieval + OCR 质量管理.

    python tests/mckb_smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from mckb import index as idx  # noqa: E402
from mckb.extract import (build_full_md, chunk_md, is_watermark,  # noqa: E402
                          read_text_pages)

SAMPLE_PAGES = [
    "1 总则\n1.0.1 为保障人身和财产安全，制定本规范。\n1.0.2 本规范适用于新建建筑。",
    "2 术语\n2.0.1 建筑高度：自室外地面至屋面面层的高度。\n3.1 防火分区\n3.1.1 防火分区面积应符合规定。",
]


def test_chunking() -> None:
    md = build_full_md("GB TEST-2026", "测试规范", SAMPLE_PAGES)
    chunks = chunk_md(md, "test")
    labels = [(c["section"], c["clause"]) for c in chunks]
    assert ("1 总则", "") in labels, labels
    assert ("1 总则", "1.0.1") in labels, labels
    assert ("3.1 防火分区", "3.1.1") in labels, labels
    for c in chunks:
        assert c["line_from"] <= c["line_to"]
    print(f"[ok] chunking: {len(chunks)} chunks")


def test_search() -> None:
    con = sqlite3.connect(":memory:")
    con.executescript(idx.SCHEMA)
    rows = [("r1", "3.1 防火分区", "3.1.1", "防火分区面积应符合规定。"),
            ("r1", "1 总则", "1.0.1", "为保障人身和财产安全，制定本规范。")]
    for i, (rel, sec, clause, text) in enumerate(rows):
        con.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (rel, i, sec, clause, 1, 1, i + 1, i + 1, text,
                     idx.unigram(f"GB TEST-2026 测试规范 {sec} {clause} {text}")))
    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    for query, want in (("防火分区", "3.1.1"), ("人身 财产安全", "1.0.1"),
                        ("建筑高度", None)):
        r = con.execute(
            "SELECT clause FROM chunks_fts JOIN chunks c ON c.rowid=chunks_fts.rowid "
            "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts)",
            (idx.query_to_match(query),)).fetchall()
        got = r[0][0] if r else None
        assert got == want, (query, got, want, r)
    print("[ok] FTS5 CJK retrieval")


def test_watermark() -> None:
    junk = ["浏览专用", "信息公开", "息公开", "住房城乡", "住房城乡建设部信息",
            "住房城乡建设部信息公开", "3信息公开", "设部信息公开", "住房城乡建设部"]
    keep = ["住房城乡建设部关于发布国家标准《建筑防火通用规范》的公告",
            "本规范由住房城乡建设部负责管理和对强制性条文的解释",
            "5.5.31 建筑高度大于54m的住宅建筑，每户应有一间房间",
            "公开招标的工程应", "住房和城乡建设部"]
    for s in junk:
        assert is_watermark(s), s
    for s in keep:
        assert not is_watermark(s), s
    print(f"[ok] 水印判定: {len(junk)} 噪声命中 / {len(keep)} 正文保留")


def test_text_pages() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="mckb_text_"))
    try:
        md = tmp / "full.md"
        md.write_text("# GB TEST-2026 测试规范\n\n<!-- page 1 -->\n甲条正文\n"
                      "\n<!-- page 2 -->\n浏览专用\n乙条正文\n", encoding="utf-8")
        pages = read_text_pages(md)
        assert len(pages) == 2, pages
        assert pages[0] == "甲条正文", pages[0]
        assert pages[1] == "乙条正文", pages[1]        # 水印行被 normalize 丢掉
        bad = tmp / "no_anchor.md"
        bad.write_text("没有页码锚点的正文\n", encoding="utf-8")
        try:
            read_text_pages(bad)
        except SystemExit as e:
            assert "page" in str(e)
        else:
            raise AssertionError("缺锚点应报错")
        print("[ok] 外部文本解析 + 缺锚点报错")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_text_ingest() -> None:
    """--text 外部 OCR 导入：页码校验 / manifest.extract / audit 待复核页。"""
    try:
        import fitz  # noqa: F401  PyMuPDF（kb extra）：用来造 PDF 夹具
    except ImportError:
        print('[skip] --text 导入链路要 PyMuPDF（kb extra）：`pip install -e ".[kb]"`')
        return
    tmp = Path(tempfile.mkdtemp(prefix="mckb_ingest_"))
    root = tmp / "repo"
    (root / "packages").mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    os.environ["STRUCTWORKSHOP_ROOT"] = str(root)
    from mccore import paths as core_paths
    core_paths.repo_root.cache_clear()
    from mckb import paths as kb_paths
    kb_paths.kb_dir.cache_clear()
    try:
        from mccore import paths as cp
        from mckb.extract import ingest

        pdf = tmp / "src.pdf"
        doc = fitz.open()
        for _ in range(2):
            doc.new_page()
        doc.save(str(pdf))
        doc.close()

        text = tmp / "ocr.md"
        text.write_text("# GB TEST-2026 测试规范\n\n<!-- page 1 -->\n1 总则\n"
                        "1.0.1 为保障人身安全，制定本规范。\n\n"
                        "<!-- page 2 -->\n表格页只剩碎片\n", encoding="utf-8")
        man = ingest(pdf, "gbtest-2026", "GB TEST-2026", "测试规范",
                     text=text, text_source="mineru", verbose=False)
        assert man["artifacts"]["full.md"]["extract"] == "mineru", man
        assert man["artifacts"]["full.md"]["pages"] == 2
        rel = cp.repo_root() / "kb" / "releases" / "gbtest-2026"
        assert (rel / "full.md").is_file() and (rel / "chunks.jsonl").is_file()
        assert (rel / "source.pdf").is_file()
        man2 = json.loads((rel / "manifest.json").read_text(encoding="utf-8"))
        assert man2["artifacts"]["source.pdf"]["bytes"] == pdf.stat().st_size

        from mckb import maintain
        rec = maintain.audit("gbtest-2026", verbose=False)[0]
        assert rec["pages"] == 2 and rec["watermark_lines"] == 0, rec
        kinds = {p["page"]: p["kind"] for p in rec["thin"]}
        assert kinds.get(2) is not None, rec        # 碎片页必须出现在待复核里

        # 单页修复：splice 只换指定页，页码/页数不变
        from mckb.extract import rehash, splice
        fix = tmp / "fix.md"
        fix.write_text("<!-- page 2 -->\n表格已用外部 OCR 恢复：丙类厂房 12m\n",
                       encoding="utf-8")
        splice("gbtest-2026", [2], fix, verbose=False)
        body = (rel / "full.md").read_text(encoding="utf-8")
        assert "表格已用外部 OCR 恢复" in body, body
        assert "1.0.1 为保障人身安全" in body, body        # 其它页未被碰
        man3 = json.loads((rel / "manifest.json").read_text(encoding="utf-8"))
        from mckb.extract import sha256_file
        assert man3["artifacts"]["full.md"]["sha256"] == sha256_file(rel / "full.md")
        # 行尾统一 LF：否则 clone 后 sha256 会不一致（Windows 默认写 CRLF）
        assert b"\r\n" not in (rel / "full.md").read_bytes()
        assert b"\r\n" not in (rel / "chunks.jsonl").read_bytes()
        rehash("gbtest-2026", verbose=False)
        assert b"\r\n" not in (rel / "full.md").read_bytes()

        # 页码对不上 -> 报错（引用会错位）
        bad = tmp / "short.md"
        bad.write_text("<!-- page 1 -->\n只有一页\n", encoding="utf-8")
        try:
            ingest(pdf, "gbtest-2026", "GB TEST-2026", "测试规范",
                   text=bad, force=True, verbose=False)
        except SystemExit as e:
            assert "页" in str(e)
        else:
            raise AssertionError("页数不一致应报错")
        print("[ok] --text 导入 + 页数校验 + splice 单页修复 + LF 行尾 + audit 标记碎片页")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("STRUCTWORKSHOP_ROOT", None)
        from mccore import paths as cp2
        cp2.repo_root.cache_clear()
        kb_paths.kb_dir.cache_clear()
        import importlib
        importlib.reload(kb_paths)


if __name__ == "__main__":
    test_chunking()
    test_search()
    test_watermark()
    test_text_pages()
    test_text_ingest()
    print("mckb smoke: OK")
