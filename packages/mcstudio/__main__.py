"""本地可视化工作台的入口：python -m mcstudio serve [--port N] [--open]。"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mcstudio",
                                 description="结构工坊（Structworkshop）本地可视化工作台")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("serve", help="启动本地网页工作台")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8617)
    p.add_argument("--open", action="store_true", help="启动后打开浏览器")
    a = ap.parse_args(argv)

    from mcstudio.server import App, serve  # noqa: PLC0415
    app = App()
    if a.cmd == "serve":
        return serve(app, host=a.host, port=a.port, open_browser=a.open)
    if a.cmd is None:
        return serve(app, port=8617, open_browser=False)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
