"""Fetch complete standards documents from public sources.

Sources
-------
mohurd  公告页附件（住建部工程建设标准全文 PDF，无需验证码）—
        ``/api-gateway/.../document/download?fileUrl=...``
openstd 国家标准全文公开系统（GB/GB-T；部分标准需要人机验证码，
        装了 ``ddddocr`` 可自动识别，否则请用 mohurd 或手动下载）
url     任意直链 PDF

Nothing is re-written here: the downloaded file is dropped into ``kb/inbox/``
verbatim and later ingested as an immutable release artifact.
"""
from __future__ import annotations

import html as html_mod
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mckb.paths import inbox_dir, sources_path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")

MOHURD_DL_RE = re.compile(
    r'href="(/api-gateway/[^"]*document/download\?[^"]+)"')


def _ascii_url(url: str) -> str:
    """urllib 只接受 ASCII URL；保留已有 %XX，只编码裸非 ASCII 字符。"""
    return "".join(ch if ord(ch) < 128 else urllib.parse.quote(ch, safe="")
                   for ch in url)


def _opener() -> urllib.request.OpenerDirector:
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))


def http_get(url: str, referer: str = "", opener=None, timeout: int = 120,
             binary: bool = False):
    req = urllib.request.Request(_ascii_url(url), headers={
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        **({"Referer": referer} if referer else {}),
    })
    op = opener or _opener()
    with op.open(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        disp = resp.headers.get("Content-Disposition", "")
        if binary:
            return data, ctype, disp, resp.geturl()
        return data.decode("utf-8", "ignore"), ctype, disp, resp.geturl()


def download(url: str, out: Path, referer: str = "", opener=None) -> Path:
    data, ctype, disp, final = http_get(url, referer=referer, opener=opener,
                                        binary=True)
    if not data:
        raise SystemExit(f"下载为空: {url}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"[mckb] {len(data) / 1e6:.1f} MB  {ctype}  -> {out}")
    return out


# --------------------------------------------------------------------- mohurd
def fetch_mohurd(page_url: str, out: Path) -> Path:
    op = _opener()
    page, _, _, _ = http_get(page_url, opener=op)
    links = [html_mod.unescape(m) for m in MOHURD_DL_RE.findall(page)]
    if not links:
        raise SystemExit(f"{page_url}: 没找到附件下载链接")
    best = None
    for rel in links:
        url = urllib.parse.urljoin(page_url, rel)
        try:
            head = urllib.request.Request(_ascii_url(url), method="HEAD", headers={
                "User-Agent": UA, "Referer": page_url})
            with op.open(head, timeout=60) as r:
                ctype = r.headers.get("Content-Type", "")
                size = int(r.headers.get("Content-Length") or 0)
        except urllib.error.HTTPError as e:
            ctype, size = e.headers.get("Content-Type", ""), 0
        print(f"[mckb]   attachment {size / 1e6:.1f} MB {ctype}")
        if "pdf" in ctype.lower() and (best is None or size > best[0]):
            best = (size, url)
    if best is None:
        best = (0, urllib.parse.urljoin(page_url, links[0]))
    return download(best[1], out, referer=page_url, opener=op)


# -------------------------------------------------------------------- openstd
def _openstd_search(code: str, op) -> list[dict]:
    q = urllib.parse.urlencode({
        "r": str(time.time()), "page": 1, "pageSize": 10,
        "p.p1": 0, "p.p2": code, "p.p5": 0, "p.p6": "", "p.p7": "",
        "p.p90": "", "p.p91": ""})
    page, _, _, _ = http_get(
        f"https://openstd.samr.gov.cn/bzgk/gb/std_list?{q}", opener=op)
    rows = re.findall(
        r"showInfo\('([0-9A-F]+)'\)[^>]*>([^<]+)<", page)
    return [{"hcno": h, "code": c.strip()} for h, c in rows]


def fetch_openstd(code: str, out: Path, prefer_download: bool = True) -> Path:
    op = _opener()
    rows = _openstd_search(code, op)
    if not rows:
        raise SystemExit(f"openstd 未收录 {code}（工程建设标准请用 mohurd/手动）")
    hit = next((r for r in rows if code.replace(" ", "").lower()
                in r["code"].replace(" ", "").lower()), rows[0])
    info_url = f"https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hit['hcno']}"
    page, _, _, _ = http_get(info_url, opener=op)
    have_dl, have_pv = "xz_btn" in page, "ck_btn" in page
    if not (have_dl or have_pv):
        raise SystemExit(f"{hit['code']}: 不允许预览/下载")
    try:                      # openstd gates both paths behind a captcha
        _solve_captcha(op)
    except SystemExit as e:
        print(f"[mckb] 验证码跳过: {e}")
    if have_dl and prefer_download:
        try:
            return download(
                f"http://c.gb688.cn/bzgk/gb/viewGb?hcno={hit['hcno']}",
                out, referer=info_url, opener=op)
        except SystemExit as e:
            print(f"[mckb] 直下失败（{e}），尝试预览拼页…")
    return _openstd_preview(hit["hcno"], out, info_url, op)


def _openstd_preview(hcno: str, out: Path, referer: str, op) -> Path:
    """Preview-only standard: images -> PDF, then OCR at ingest time."""
    page, _, _, _ = http_get(
        f"http://c.gb688.cn/bzgk/gb/showGb?type=online&hcno={hcno}",
        referer=referer, opener=op)
    img_ids = re.findall(r'fileName=([A-Za-z0-9_.-]+)', page)
    if not img_ids:
        raise SystemExit("开放预览失败（可能需要验证码）；建议改用 mohurd 公告附件")
    import fitz
    pdf = fitz.open()
    tmp = []
    for i, img_id in enumerate(img_ids, 1):
        data, ctype, _, _ = http_get(
            f"http://c.gb688.cn/bzgk/gb/viewGbImg?fileName={img_id}",
            referer=referer, opener=op, binary=True)
        tmp.append(data)
        rect = fitz.Rect(0, 0, 595, 842)
        p = pdf.new_page(width=595, height=842)
        p.insert_image(rect, stream=data)
        print(f"[mckb]   preview page {i}/{len(img_ids)}", flush=True)
    pdf.save(str(out))
    pdf.close()
    print(f"[mckb] preview PDF -> {out}")
    return out


def _solve_captcha(op) -> str:
    try:
        import ddddocr
    except ImportError:
        raise SystemExit("需要人机验证码：pip install ddddocr 或改用手动下载")
    ocr = ddddocr.DdddOcr(show_ad=False)
    data, _, _, _ = http_get(f"http://c.gb688.cn/bzgk/gc?_{int(time.time() * 1000)}",
                             opener=op, binary=True)
    code = ocr.classification(data)
    req = urllib.request.Request(
        "http://c.gb688.cn/bzgk/gb/verifyCode",
        data=urllib.parse.urlencode(
            {"verifyCode": code, "agreeIECTips": "true"}).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    with op.open(req, timeout=60) as r:
        ok = r.read().decode("utf-8", "ignore").strip() == "success"
    if not ok:
        raise SystemExit("验证码识别失败，重试或手动下载")
    return code


# --------------------------------------------------------------------- sources
def load_sources() -> dict:
    if not sources_path().is_file():
        return {"sources": []}
    return json.loads(sources_path().read_text(encoding="utf-8"))


def fetch_source(source_id: str, out: Path | None = None) -> Path:
    entry = next((s for s in load_sources().get("sources", [])
                  if s["id"] == source_id), None)
    if entry is None:
        raise SystemExit(f"sources.json 里没有 {source_id}")
    out = out or (inbox_dir() / f"{source_id}.pdf")
    url = entry["url"]
    if "mohurd.gov.cn" in url:
        return fetch_mohurd(url, out)
    if url.startswith("openstd://") or entry.get("source") == "openstd":
        return fetch_openstd(entry.get("code", source_id), out)
    return download(url, out)
