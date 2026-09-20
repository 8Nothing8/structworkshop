"""PDF -> full.md -> clause chunks -> release manifest.

The corpus must be the *complete* document: no excerpts, no re-writing.  The
PDF is stored verbatim as ``source.pdf``; ``full.md`` is a faithful text
rendering with page markers, and ``chunks.jsonl`` only slices that text into
clause/section-level units so retrieval can point at a location.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from mccore.paths import write_text_lf  # noqa: F401  (再导出：maintain 从这里取)
from mckb.paths import release_dir

PAGE_MARK = "<!-- page {n} -->"
CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"

CLAUSE_RE = re.compile(r"^(\d+(?:\.\d+){2,3})(?![\d.])")
SECTION_RE = re.compile(rf"^(\d+(?:\.\d+)?)\s+([^\d\s].{{0,60}})$")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+[章节]\s*\S*")
APPENDIX_RE = re.compile(r"^附录\s*[A-Z0-9]")
CLAUSE_END_RE = re.compile(r"^(.*?)\s+(\d+(?:\.\d+){2,3})$")
MAX_CHUNK_LINES = 30          # cap chunk size so a hit is a readable range


# --------------------------------------------------------------------- utils
def sha256_file(path: Path, buf: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(buf):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# `write_text_lf` 的规范实现在 `mccore.paths`（这里是它在语料库侧的最早副本，
# 2026-09 已收编为一份；语料 manifest 按字节算 sha256，行尾必须锁 LF）。


def despace_cjk(line: str) -> str:
    """OCR often splits Chinese words with spaces: 建 筑 防 火 -> 建筑防火."""
    return re.sub(rf"(?<=[{CJK}])[ \t]+(?=[{CJK}])", "", line)


# Scanned copies stamp a viewing watermark on every page; rapidocr returns the
# fragments as standalone lines (公开 / 浏览专用 / 住房城乡建设部信息公开 …).
WM_STRONG = ("浏览专用", "信息公开", "住房城乡", "息公开", "信急")
WM_TOKENS = ("浏览专用", "信息公开", "住房城乡建设部", "住房", "城乡",
             "建设", "信息", "公开", "浏览", "专用", "信", "息", "急",
             "设部", "设", "部", "3")
WM_URL = re.compile(r"^(?:https?://)?(?:www\.)?[\w.-]+\.(?:com|cn|net|org)(?:/[\w./-]*)?$")


def is_watermark(line: str) -> bool:
    """True for a line that is *only* watermark fragments (never a sentence)."""
    s = line.strip().replace(" ", "").replace("\u3000", "")
    if not s or not any(k in s for k in WM_STRONG):
        return False
    rest = s
    for tok in WM_TOKENS:
        rest = rest.replace(tok, "")
    return not rest.strip("．.,、。0123456789")


def normalize_line(line: str) -> str:
    if is_watermark(line):
        return ""
    return despace_cjk(line.replace("\u3000", " ")).rstrip()


# ------------------------------------------------------------------ extraction
def _text_layer(doc) -> list[str] | None:
    pages = [(p.get_text("text") or "") for p in doc]
    if not pages:
        return None
    chars = sum(len(p.strip()) for p in pages)
    if chars / max(1, len(pages)) >= 40:      # real text layer
        return pages
    return None


def _ocr_pages(doc, dpi: int, verbose: bool) -> list[str]:
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    out: list[str] = []
    for i, page in enumerate(doc, 1):
        t0 = time.time()
        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8)
        img = img.reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]
        res, _ = ocr(img)
        lines = [r[1] for r in (res or [])]
        out.append("\n".join(lines))
        if verbose:
            print(f"  ocr page {i}/{doc.page_count} "
                  f"({len(lines)} lines, {time.time() - t0:.1f}s)", flush=True)
    return out


PAGE_MARK_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$")


def parse_paged_md(path: Path) -> list[tuple[int, str]]:
    """[(page number, page text)] from an anchored markdown (sparse pages ok)."""
    text = Path(path).read_text(encoding="utf-8")
    out: list[tuple[int, str]] = []
    num: int | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = PAGE_MARK_RE.match(line)
        if m:
            if num is not None:
                out.append((num, "\n".join(buf).strip("\n")))
            num, buf = int(m.group(1)), []
            continue
        if num is not None:
            buf.append(line)
    if num is not None:
        out.append((num, "\n".join(buf).strip("\n")))
    if not out:
        raise SystemExit(
            f"{path}: 没有 <!-- page N --> 页码锚点，无法对齐页码。\n"
            f"  期望格式：每页前一行 `<!-- page 1 -->`（见 kb/releases/*/full.md）")
    return [(n, normalize_pages_text(t)) for n, t in out]


def read_text_pages(path: Path) -> list[str]:
    """Page texts of an externally produced markdown, must be 1..N contiguous.

    Used by ``ingest --text`` so a stronger engine than the built-in rapidocr
    (MinerU / a vision model / a hand-corrected file) can feed the corpus.
    """
    pages = parse_paged_md(path)
    nums = [n for n, _ in pages]
    if nums != list(range(1, len(pages) + 1)):
        raise SystemExit(
            f"{path}: 页码锚点必须从 1 连续到 {len(pages)}（现在是 {nums[:8]}…）；"
            f"只补部分页请用 `mckb splice`")
    return [t for _, t in pages]


def extract_pages(pdf: Path, dpi: int = 200, engine: str = "auto",
                  verbose: bool = True) -> tuple[list[str], str]:
    """Return (page texts, engine used).  engine: auto|text|ocr."""
    import fitz

    doc = fitz.open(str(pdf))
    try:
        pages = None if engine == "ocr" else _text_layer(doc)
        if pages is not None:
            return [normalize_pages_text(p) for p in pages], "pymupdf"
        if engine == "text":
            raise SystemExit(f"{pdf}: 没有文字层（用 --engine ocr）")
        return [normalize_pages_text(p) for p in _ocr_pages(doc, dpi, verbose)], "rapidocr"
    finally:
        doc.close()


def normalize_pages_text(text: str) -> str:
    return "\n".join(normalize_line(ln) for ln in text.splitlines()).strip("\n")


# --------------------------------------------------------------------- markdown
def build_full_md(code: str, title: str, pages: list[str]) -> str:
    lines = [f"# {code} {title}", ""]
    for n, text in enumerate(pages, 1):
        lines.append(PAGE_MARK.format(n=n))
        body = text.strip("\n")
        if body:
            lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def chunk_md(full_md: str, release: str) -> list[dict]:
    """Slice the full text into clause/section chunks (locations, not excerpts).

    Scanned standards put clause numbers in the left margin; OCR may render
    them as their own line, glued to the following text, or trailing the
    previous line.  We treat a clause number at line start *or* line end as a
    boundary, and cap chunks at ``MAX_CHUNK_LINES`` so a hit is always a small
    readable range.
    """
    chunks: list[dict] = []
    page = 1
    section = "front"
    clause = ""
    buf: list[str] = []
    start_line = 1
    buf_page_from = 1
    continuation = False

    def section_label() -> str:
        """Keep a real heading; otherwise derive the chapter from the clause id.

        Scanned OCR often loses chapter titles (e.g. "1 总则"), while the TOC
        may have injected a wrong heading ("8 消防设施") much earlier.
        """
        if clause:
            ch = clause.split(".")[0]
            head = section.split()[0] if section[:1].isdigit() else ""
            if head.split(".")[0] != ch:
                return f"第{ch}章"
        return section

    def flush(end_line: int, end_page: int) -> None:
        nonlocal buf, continuation
        text = "\n".join(buf).strip()
        if text:
            chunks.append({
                "release": release,
                "seq": len(chunks),
                "section": section_label(),
                "clause": clause,
                "page_from": buf_page_from,
                "page_to": end_page,
                "line_from": start_line,
                "line_to": end_line,
                "continuation": continuation,
                "text": text,
            })
        buf = []
        continuation = False

    def begin(ln: int, pg: int) -> None:
        nonlocal start_line, buf_page_from
        start_line, buf_page_from = ln, pg

    lines = full_md.splitlines()
    for ln, raw in enumerate(lines, 1):
        p = re.match(r"<!-- page (\d+) -->", raw.strip())
        if p:
            page = int(p.group(1))
            continue
        line = raw.strip()
        if not line:
            if buf:
                buf.append("")
            continue

        # chapter / section / appendix heading switches context
        if (SECTION_RE.match(line) or CHAPTER_RE.match(line)
                or APPENDIX_RE.match(line)):
            if any(x.strip() for x in buf):
                flush(ln - 1, page)
            section, clause = line, ""
            begin(ln, page)
            buf.append(line)
            continue

        # clause number at line start -> opens a new clause
        m = CLAUSE_RE.match(line)
        if m:
            if any(x.strip() for x in buf):
                flush(ln - 1, page)
            clause = m.group(1)
            begin(ln, page)
            buf.append(line)
            continue

        # clause number trailing a line -> opens the *next* clause
        e = CLAUSE_END_RE.match(line)
        if e:
            prefix, num = e.group(1).strip(), e.group(2)
            if prefix:
                buf.append(prefix)
            if any(x.strip() for x in buf):
                flush(ln, page)
            clause = num
            begin(ln, page)
            buf.append(num)
            continue

        if len(buf) >= MAX_CHUNK_LINES:
            flush(ln - 1, page)
            continuation = True
            begin(ln, page)
        buf.append(line)
    if any(x.strip() for x in buf):
        flush(len(lines), page)
    return chunks


# ----------------------------------------------------------------------- ingest
def ingest(pdf: Path, release: str, code: str, title: str, *,
           publisher: str = "", source_url: str = "", effective: str = "",
           status: str = "current", dpi: int = 200, engine: str = "auto",
           text: Path | None = None, text_source: str = "external",
           force: bool = False, verbose: bool = True) -> dict:
    out = release_dir(release)
    prev = {}
    if (out / "manifest.json").is_file():
        prev = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    if out.exists() and any(out.iterdir()) and not force:
        raise SystemExit(f"{out} 已存在（--force 覆盖）")
    out.mkdir(parents=True, exist_ok=True)
    src = out / "source.pdf"
    if not (src.exists() and sha256_file(src) == sha256_file(pdf)):
        shutil.copy2(pdf, src)

    t0 = time.time()
    if text is not None:
        pages, used = read_text_pages(text), (text_source or "external")
        n_pdf = _pdf_page_count(src)
        if n_pdf and len(pages) != n_pdf:
            raise SystemExit(
                f"{text}: {len(pages)} 页，但 {src.name} 有 {n_pdf} 页 —— 页码会对不上，"
                f"引用/检索都会错位。（补齐 <!-- page N --> 锚点后重试）")
        if verbose:
            print(f"[mckb] 用外部文本 {text}（{len(pages)} 页，extract={used}），跳过本地 OCR")
    else:
        pages, used = extract_pages(src, dpi=dpi, engine=engine, verbose=verbose)
    full_md = build_full_md(code, title, pages)
    write_text_lf(out / "full.md", full_md)
    chunks = chunk_md(full_md, release)
    write_text_lf(out / "chunks.jsonl",
                  "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n")

    manifest = {
        "release": release,
        "kind": "standard",
        "code": code,
        "title": title,
        "status": status,
        "effective": effective,
        "publisher": publisher,
        "source_url": source_url,
        "created_at": prev.get("created_at") or time.strftime("%Y-%m-%d"),
        "artifacts": {
            "source.pdf": {"bytes": src.stat().st_size, "sha256": sha256_file(src)},
            "full.md": {"bytes": (out / "full.md").stat().st_size,
                        "sha256": sha256_file(out / "full.md"),
                        "pages": len(pages), "chars": len(full_md),
                        "extract": used, "chunks": len(chunks)},
            "chunks.jsonl": {"bytes": (out / "chunks.jsonl").stat().st_size,
                             "sha256": sha256_file(out / "chunks.jsonl")},
        },
        "complete": bool(pages) and all(p.strip() for p in pages),
    }
    write_text_lf(out / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1))
    sums = "".join(f"{sha256_file(out / n)}  {n}\n"
                   for n in ("source.pdf", "full.md", "chunks.jsonl"))
    write_text_lf(out / "SHA256SUMS", sums)
    if verbose:
        print(f"[mckb] release {release}: {len(pages)} pages, "
              f"{len(chunks)} chunks, extract={used}, {time.time() - t0:.1f}s "
              f"-> {out}")
    return manifest


def _pdf_page_count(pdf: Path) -> int:
    try:
        import fitz
    except ImportError:
        return 0
    try:
        doc = fitz.open(str(pdf))
        try:
            return doc.page_count
        finally:
            doc.close()
    except Exception:
        return 0


def _refresh_release(out: Path, verbose: bool, note: str = "") -> dict:
    """Re-chunk full.md and refresh every derived hash (full.md bytes/sha/chars)."""
    p = out / "full.md"
    man = rechunk(out.name, verbose=False)
    man["artifacts"]["full.md"].update({
        "bytes": p.stat().st_size, "sha256": sha256_file(p),
        "chars": len(p.read_text(encoding="utf-8"))})
    write_text_lf(out / "manifest.json",
                  json.dumps(man, ensure_ascii=False, indent=1))
    write_text_lf(out / "SHA256SUMS",
                  "".join(f"{sha256_file(out / n)}  {n}\n"
                          for n in ("source.pdf", "full.md", "chunks.jsonl")))
    if verbose:
        print(f"[mckb] {note or 'refresh'} {out.name}: "
              f"{man['artifacts']['full.md']['chunks']} chunks")
    return man


def splice(release: str, pages: list[int], src: Path,
           verbose: bool = True) -> dict:
    """Replace selected pages' bodies in ``full.md`` with external OCR text.

    The single-page repair path: ``mckb audit`` -> pi_ocr the bad pages ->
    ``mckb splice <release> --pages 37,38 --from fix.md``.  Page numbering and
    page count are preserved (that is what clause citations rely on).
    """
    out = release_dir(release)
    full = out / "full.md"
    if not full.is_file():
        raise SystemExit(f"{full} 不存在")
    fix = dict(parse_paged_md(src))
    want = [int(n) for n in pages]
    missing = [n for n in want if n not in fix]
    if missing:
        raise SystemExit(f"{src}: 缺这些页的内容 {missing}（每页前加 `<!-- page N -->`）")
    blocks = _anchored_lines(full)
    nums = [n for n, _ in blocks]
    bad = [n for n in want if n not in nums]
    if bad:
        raise SystemExit(f"{release}: 没有这些页 {bad}（full.md 共 {len(nums)} 页）")
    idx = {n: i for i, n in enumerate(nums)}
    for n in want:
        blocks[idx[n]] = (n, fix[n])
    full.write_text(_render_blocks(blocks), encoding="utf-8", newline="\n")
    return _refresh_release(out, verbose, note="splice")


def _anchored_lines(path: Path) -> list[tuple[int, str]]:
    """Anchored page blocks, keeping text verbatim (no normalization)."""
    out: list[tuple[int, str]] = []
    num: int | None = None
    buf: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = PAGE_MARK_RE.match(line)
        if m:
            if num is not None:
                out.append((num, "\n".join(buf).strip("\n")))
            num, buf = int(m.group(1)), []
        elif num is not None:
            buf.append(line)
    if num is not None:
        out.append((num, "\n".join(buf).strip("\n")))
    return out


def _render_blocks(blocks: list[tuple[int, str]], code: str = "",
                   title: str = "") -> str:
    head = [f"# {code} {title}".strip(), ""] if code else []
    lines = head
    for n, text in blocks:
        lines.append(PAGE_MARK.format(n=n))
        if text.strip():
            lines.append(text.strip("\n"))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def rehash(release: str, verbose: bool = True) -> dict:
    """Normalize line endings to LF and recompute every hash for a release.

    Needed once for corpora ingested on Windows (CRLF on disk): the committed
    blob is LF, so ``mckb verify`` used to fail after a fresh clone.
    """
    out = release_dir(release)
    p = out / "full.md"
    text = p.read_text(encoding="utf-8")
    write_text_lf(p, text if text.endswith("\n") else text + "\n")
    man = _refresh_release(out, verbose=False)
    if verbose:
        print(f"[mckb] rehash {release}: LF 行尾 + 重算 sha256")
    return man


def denoise(release: str, verbose: bool = True) -> dict:
    """Drop watermark lines from an existing ``full.md`` and re-chunk it."""
    out = release_dir(release)
    p = out / "full.md"
    lines = p.read_text(encoding="utf-8").splitlines()
    kept, dropped = [], 0
    for line in lines:
        if PAGE_MARK_RE.match(line):
            kept.append(line)
            continue
        new = normalize_line(line)
        if not new and line.strip():
            dropped += 1
            continue
        kept.append(new)
    write_text_lf(p, "\n".join(kept).rstrip() + "\n")
    man = _refresh_release(out, verbose=False)
    if verbose:
        print(f"[mckb] denoise {release}: 去掉 {dropped} 行水印噪声")
    return man


def rechunk(release: str, verbose: bool = True) -> dict:
    """Rebuild ``chunks.jsonl`` from ``full.md`` (no PDF re-extraction)."""
    out = release_dir(release)
    full_md = (out / "full.md").read_text(encoding="utf-8")
    chunks = chunk_md(full_md, release)
    cp = out / "chunks.jsonl"
    write_text_lf(cp, "\n".join(json.dumps(c, ensure_ascii=False)
                                 for c in chunks) + "\n")
    man_p = out / "manifest.json"
    man = json.loads(man_p.read_text(encoding="utf-8"))
    man["artifacts"]["chunks.jsonl"] = {"bytes": cp.stat().st_size,
                                        "sha256": sha256_file(cp)}
    man["artifacts"]["full.md"]["chunks"] = len(chunks)
    write_text_lf(man_p, json.dumps(man, ensure_ascii=False, indent=1))
    write_text_lf(out / "SHA256SUMS",
                  "".join(f"{sha256_file(out / n)}  {n}\n"
                          for n in ("source.pdf", "full.md", "chunks.jsonl")))
    if verbose:
        print(f"[mckb] rechunk {release}: {len(chunks)} chunks")
    return man
