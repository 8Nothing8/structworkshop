"""Stdlib HTTP server for the mcstudio workbench (JSON API + static web app)."""
from __future__ import annotations

import json
import mimetypes
import re
import sys
import traceback
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from mccore.paths import repo_root
from mcstudio.blocks import BlockCatalog
from mcstudio.jobs import JobRunner
from mcstudio.session import SessionRegistry

WEB_DIR = Path(__file__).resolve().parent / "web"
ALLOWED_PREFIXES = ("packs", "builds", "compositions", "tests/fixtures",
                    ".cache/mcstudio")
JSON_CT = "application/json; charset=utf-8"


# ------------------------------------------------------------------ objects
class Request:
    def __init__(self, method: str, path: str, query: dict, headers,
                 body: bytes, params: dict | None = None):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.params = params or {}

    def q(self, key: str, default=None):
        vals = self.query.get(key)
        if not vals:
            return default
        return vals[0] if len(vals) == 1 else vals

    def qlist(self, key: str) -> list[str]:
        return list(self.query.get(key) or [])

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"非法 JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("JSON 必须是对象")
        return data


class Response:
    def __init__(self, status: int = 200, body: bytes = b"",
                 content_type: str = JSON_CT, headers: dict | None = None):
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = headers or {}

    @classmethod
    def json(cls, data, status: int = 200) -> "Response":
        return cls(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    @classmethod
    def text(cls, text: str, status: int = 200) -> "Response":
        return cls(status, text.encode("utf-8"),
                   "text/plain; charset=utf-8")

    @classmethod
    def bytes(cls, data: bytes, content_type: str,
              status: int = 200, headers: dict | None = None) -> "Response":
        return cls(status, data, content_type, headers)


def err(status: int, msg: str) -> Response:
    return Response.json({"error": msg}, status=status)


# 已经在压缩域里的类型：再 gzip 一次只是白烧 CPU
_NO_GZIP_CT = ("image/", "font/", "audio/", "video/", "application/zip",
               "application/gzip", "text/event-stream")


def maybe_gzip(resp: Response, accept_encoding: str | None) -> bytes:
    """按协商给响应体做 gzip（返回要写的字节）。

    结构体素是裸 uint16：太空探索者 37MB / 枪之恶魔 45MB，**逐格压缩率 85–138×**
    （空气连片），压完 0.3–1.0MB。浏览器带 ``Accept-Encoding: gzip`` 时会自己解压，
    所以前端 fetch(...).arrayBuffer() 一行都不用改；不带该头的客户端（比如
    tests/*.py 里的 urllib）拿到的仍是原始字节，行为不变。
    """
    body = resp.body
    if len(body) < 4096 or "gzip" not in (accept_encoding or ""):
        return body
    ct = (resp.content_type or "").lower()
    if ct.startswith(_NO_GZIP_CT) or resp.headers.get("Content-Encoding"):
        return body
    # level=1：45MB 压到 0.94MB 只要 ~0.13s（level=6 是 0.53MB / 0.39s，本地传输不值）
    # 注意 wbits=31：要的是**gzip 封装**（RFC1952，头 1f 8b），不是 zlib 封装（78 9c）——
    # Content-Encoding: gzip 配 zlib 流浏览器会解不开。
    co = zlib.compressobj(1, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    packed = co.compress(body) + co.flush()
    if len(packed) >= len(body):
        return body
    resp.body = packed
    resp.headers = dict(resp.headers)
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    return packed


# ------------------------------------------------------------------ app
class App:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else repo_root()
        self.sessions = SessionRegistry()
        self.jobs = JobRunner()
        self.blocks = BlockCatalog()
        self.routes: list[tuple[str, re.Pattern, callable]] = []
        self.uploads = self.root / ".cache" / "mcstudio" / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        from mcstudio import api  # noqa: PLC0415
        api.register(self)

    # -------------------------------------------------------- routing
    def route(self, method: str, pattern: str, fn) -> None:
        rx = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern)
        self.routes.append((method.upper(), re.compile("^" + rx + "/?$"), fn))

    def dispatch(self, method: str, target: str, headers, body: bytes) -> Response:
        parsed = urlparse(target)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if path == "/" or path.startswith("/static/"):
            return self._static(path)
        if path.startswith("/files/"):
            return self._files(path[len("/files/"):])
        for m, rx, fn in self.routes:
            if m != method.upper():
                continue
            match = rx.match(path)
            if match:
                req = Request(method, path, query, headers, body,
                              match.groupdict())
                try:
                    return fn(self, req)
                except KeyError as e:
                    return err(404, str(e))
                except FileNotFoundError as e:
                    return err(404, str(e))
                except PermissionError as e:
                    return err(403, str(e))
                except (ValueError, FileExistsError) as e:
                    return err(400, str(e))
        return err(404, f"没有路由 {method} {path}")

    # -------------------------------------------------------- static
    def _static(self, path: str) -> Response:
        rel = "index.html" if path == "/" else path[len("/static/"):]
        f = (WEB_DIR / rel).resolve()
        if not str(f).startswith(str(WEB_DIR.resolve())) or not f.is_file():
            return err(404, "not found")
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or f.suffix in (".js", ".css", ".json"):
            ctype += "; charset=utf-8"
        # 工作台代码会被持续迭代：禁掉浏览器缓存，避免用户跑到旧 JS
        return Response.bytes(
            f.read_bytes(), ctype,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                     "Pragma": "no-cache", "Expires": "0"})

    def _files(self, rel: str) -> Response:
        try:
            f = self.resolve_path(rel)
        except FileNotFoundError as e:
            return err(404, str(e))
        except PermissionError as e:
            return err(403, str(e))
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        return Response.bytes(f.read_bytes(), ctype)

    # -------------------------------------------------------- paths
    def resolve_path(self, rel: str, *, must_exist: bool = True,
                     prefixes=ALLOWED_PREFIXES) -> Path:
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel or rel.startswith("../") or "/../" in rel:
            raise PermissionError(f"非法路径: {rel}")
        f = (self.root / rel).resolve()
        root = self.root.resolve()
        if not str(f).startswith(str(root)):
            raise PermissionError(f"越界路径: {rel}")
        inside = any(str(f).startswith(str((root / p).resolve()))
                     for p in prefixes)
        if not inside:
            raise PermissionError(f"不允许访问: {rel}")
        if must_exist and not f.exists():
            raise FileNotFoundError(f"没有文件: {rel}")
        return f


# ------------------------------------------------------------------ serve
def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mcstudio/0.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # keep the console clean
            pass

        def _handle(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                resp = app.dispatch(method, self.path, self.headers, body)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                resp = err(500, f"{e}")
            try:
                # 大二进制（结构体素）按 Accept-Encoding 协商 gzip
                payload = maybe_gzip(resp, self.headers.get("Accept-Encoding"))
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                for k, v in resp.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                if method != "HEAD":
                    self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")

        def do_PATCH(self):  # noqa: N802
            self._handle("PATCH")

        def do_DELETE(self):  # noqa: N802
            self._handle("DELETE")

    return Handler


class McStudioServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 上 SO_REUSEADDR 会让两个进程同时绑定同一接口；关掉以便接口自增
    allow_reuse_address = False


def serve(app: App | None = None, host: str = "127.0.0.1", port: int = 8617,
          open_browser: bool = False) -> int:
    app = app or App()
    handler = make_handler(app)
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = McStudioServer((host, p), handler)
            break
        except OSError:
            continue
    if httpd is None:
        print(f"接口 {port}~{port + 19} 都占用了", file=sys.stderr)
        return 1
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"结构工坊 工作台: {url}")
    print(f"  仓库: {app.root}")
    print("  Ctrl+C 退出")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()
    return 0
