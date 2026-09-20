"""mckb — building-code knowledge base.

A tiny, dependency-light retrieval stack for full standard documents:

  release  — one standard, stored as an immutable artifact set
             (``source.pdf`` + ``full.md`` + ``chunks.jsonl`` + ``manifest.json``)
  chunk    — clause/section level unit with page + line anchors
  index    — SQLite FTS5 (BM25) over chunks, CJK-friendly unigram tokenization

Commands (``python -m mckb ...``)::

    fetch     download a full-text PDF (mohurd announcement / openstd / url)
    ingest    pdf -> full.md -> chunks -> manifest (OCR fallback: rapidocr)
    scan      rebuild registry.json + catalog.md
    index     rebuild index.sqlite (FTS5)
    search    clause-level retrieval over complete documents
    read      print a complete section / clause / page range
    readlist  given a project profile, list the documents+sections to read
    verify    integrity: sha256, page coverage, chunk coverage
    lint      registry/release consistency checks
    export    dump the corpus for external KBs (jsonl / llamaindex / chroma / txtai)

The corpus is the source of truth: ``search`` answers with *locations*, and
``read`` returns the full, unedited text of the located section.
"""

__version__ = "0.1.0"
