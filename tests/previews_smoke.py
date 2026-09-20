"""预览渲染队列冒烟：/api/previews 覆盖统计 + 渲染队列（排队/进度/取消）。

真正的渲染是 mcrender 子进程（慢），不在冒烟范围内：这里把
``mccore.library.render_previews`` 换成假实现，验证的是队列语义
（同通道串行、FIFO、进度回写、取消排队、scope 映射 force）与 HTTP 接口。

Usage:  python tests/previews_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

TMP = Path(tempfile.mkdtemp(prefix="structworkshop_previews_"))
os.environ["STRUCTWORKSHOP_ROOT"] = str(TMP)
(TMP / "pyproject.toml").write_text("", encoding="utf-8")
(TMP / "packages").mkdir()
(TMP / "packs" / "demo" / "modules" / "rooms").mkdir(parents=True)
(TMP / "packs" / "demo" / "previews").mkdir()
(TMP / "packs" / "demo" / "pack.json").write_text(
    json.dumps({"id": "demo", "name": "demo", "version": "1.0.0"}),
    encoding="utf-8")
# 第二个包（字母序在 demo 之后）：回归用——批量渲染必须写回**各自**包的 previews/，
# 曾经因为用了循环残留的 pdir，全部写进最后一个包。
(TMP / "packs" / "zeta" / "modules" / "rooms").mkdir(parents=True)
(TMP / "packs" / "zeta" / "previews").mkdir()
(TMP / "packs" / "zeta" / "pack.json").write_text(
    json.dumps({"id": "zeta", "name": "zeta", "version": "1.0.0"}),
    encoding="utf-8")

import numpy as np  # noqa: E402

from mccore import library as LB  # noqa: E402
from mccore import structure_io as S  # noqa: E402

AIR = {"Name": "minecraft:air"}
STONE = {"Name": "minecraft:stone"}
PORT = {"host": "127.0.0.1", "port": 0}


def make_module(mid="box_a", size=(4, 3, 5), pack="demo"):
    sx, sy, sz = size
    v = np.zeros((sy, sz, sx), dtype=np.uint16)
    v[1:, :, :] = 1
    p = TMP / "packs" / pack / "modules" / "rooms" / f"{mid}.schem"
    S.write_structure(str(p), v, [AIR, STONE], (0, 0, 0), size, name=mid)
    LB.save_spec(p, {"id": mid, "pack": pack, "category": "rooms",
                     "version": 1, "description": mid, "tags": ["room"],
                     "grid": {"size": list(size), "position": [0, 0, 0]},
                     "axis": "x", "flip": True, "ports": []})
    return p


def call(method, path, body=None):
    url = f"http://127.0.0.1:{PORT['port']}{path}"
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {}


def wait_state(app, jid, state, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = app.jobs.get(jid)
        if job and job["state"] == state:
            return job
        time.sleep(0.05)
    raise AssertionError(f"{jid} 未在 {timeout}s 内变成 {state}: "
                         f"{app.jobs.get(jid)}")


def main() -> int:
    make_module("box_a")
    make_module("box_b")
    make_module("box_c")
    make_module("zbox_a", pack="zeta")
    make_module("zbox_b", pack="zeta")
    LB.load_index(refresh=True)

    app = None
    try:
        from mcstudio.server import App, make_handler  # noqa: PLC0415
        app = App()
        httpd = ThreadingHTTPServer((PORT["host"], 0), make_handler(app))
        PORT["port"] = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        ok = []

        # 1) 覆盖统计：3 个模块都没有预览图
        st, d = call("GET", "/api/previews")
        assert st == 200, d
        demo = next(p for p in d["packs"] if p["id"] == "demo")
        assert st == 200 and d["missing"] == 5 and demo["missing"] == 3, d
        assert sorted(demo["missing_ids"]) == ["box_a", "box_b", "box_c"], demo
        assert demo["previews"] == 0 and demo["total"] == 3, demo
        assert d["jobs"] == [], d["jobs"]
        ok.append("GET /api/previews 覆盖统计")

        # 2) 假渲染器：模拟真实的“跳过已有图 / force 全渲染”，并记录并发度
        real = LB.render_previews
        stat = {"running": 0, "max": 0, "calls": []}

        def fake(pack=None, *, force=False, only=None, online=False,
                 progress=None, **_kw):
            stat["running"] += 1
            stat["max"] = max(stat["max"], stat["running"])
            stat["calls"].append({"pack": pack, "force": force,
                                  "only": list(only or [])})
            items = [f"m{i}" for i in range(3)]
            try:
                if progress:
                    progress(0, len(items), "", "start")
                out = {"rendered": [], "skipped": [], "failed": []}
                for i, mid in enumerate(items, 1):
                    time.sleep(0.12)          # 足够慢，能观察到排队
                    if not force and i == 1:
                        out["skipped"].append(mid)
                        state = "skipped"
                    else:
                        out["rendered"].append(mid)
                        state = "rendered"
                    if progress:
                        progress(i, len(items), mid, state)
                return out
            finally:
                stat["running"] -= 1

        LB.render_previews = fake
        try:
            # 3) 排队：连点两次 → 一个 running 一个 queued
            st, r1 = call("POST", "/api/previews/render",
                          {"pack": "demo", "scope": "missing"})
            assert st == 200 and r1["job"], r1
            st, r2 = call("POST", "/api/previews/render",
                          {"pack": "", "scope": "all"})
            assert st == 200 and r2["job"], r2
            j1, j2 = r1["job"], r2["job"]
            q = app.jobs.get(j2)
            assert q["state"] in ("queued", "running"), q
            if q["state"] == "queued":
                assert q["queued_ahead"] == 1, q
            st, jl = call("GET", "/api/jobs")
            prev = [j for j in jl["jobs"] if j["channel"] == "preview"]
            assert len(prev) == 2, prev
            ok.append("排队：POST /api/previews/render ×2")

            # 4) 运行中的任务不能取消（409），排队中的可以
            st, r = call("POST", f"/api/jobs/{j1}/cancel")
            assert st == 409, (st, r)
            if app.jobs.get(j2)["state"] == "queued":
                st, r = call("POST", f"/api/jobs/{j2}/cancel")
                assert st == 200 and r["state"] == "canceled", (st, r)
                ok.append("取消排队中的任务（运行中拒绝 409）")
            else:
                ok.append("取消排队中的任务（本机太快，跳过）")

            # 5) 第一个任务跑完：进度回写 + force 映射正确（missing → False）
            job = wait_state(app, j1, "done")
            assert job["progress"]["done"] == 3, job["progress"]
            assert job["result"]["rendered"] == ["m1", "m2"], job["result"]
            assert job["result"]["skipped"] == 1, job["result"]
            assert job["log"], job["log"]
            assert stat["calls"][0] == {"pack": "demo", "force": False,
                                        "only": []}, stat["calls"]
            ok.append("进度回写 + scope=missing → force=False")

            # 6) 单模块重渲染：走同一通道，force + only
            st, r3 = call("POST", "/api/modules/box_a/preview")
            assert st == 200 and r3["job"], r3
            job = wait_state(app, r3["job"], "done")
            assert stat["calls"][-1]["force"] is True, stat["calls"]
            assert stat["calls"][-1]["only"] == ["box_a"], stat["calls"]
            ok.append("单模块重渲染（force + only）")

            # 7) 同通道串行：任意时刻只有一个渲染在跑
            assert stat["max"] == 1, stat
            ok.append(f"同通道串行（最大并发 {stat['max']}）")

            # 8) 坏 scope / 坏资产包 / 坏背景报错
            st, r = call("POST", "/api/previews/render", {"scope": "nope"})
            assert st >= 400 and "scope" in r.get("error", ""), (st, r)
            st, r = call("POST", "/api/previews/render", {"pack": "ghost"})
            assert st == 404, (st, r)
            st, r = call("POST", "/api/previews/render", {"background": "pink"})
            assert st == 400 and "background" in r.get("error", ""), (st, r)
            st, r = call("POST", "/api/jobs/job999/cancel")
            assert st == 404, (st, r)
            ok.append("参数校验（scope / pack / background / jid）")
        finally:
            LB.render_previews = real

        # 9) 真 render_previews 的产物目录（回归：曾把 --out 写成最后一个包的 previews/）
        import subprocess as sp  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415
        real_run = sp.run

        def fake_run(cmd, **_kw):
            out = Path(cmd[cmd.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 6), (10, 20, 30)).save(str(out) + "_iso.png")

            class R:                      # 模拟 mcrender 成功返回
                returncode, stdout, stderr = 0, "", ""
            return R()

        seen = []
        bg_args = []

        def fake_run_bg(cmd, **_kw):
            bg_args.append(cmd[cmd.index("--background") + 1])
            return fake_run(cmd)

        sp.run = fake_run_bg
        try:
            rep = LB.render_previews(
                None, force=True, background="white",
                progress=lambda d, t, m, s: seen.append((d, t, m, s)))
        finally:
            sp.run = real_run
        assert rep["failed"] == [], rep["failed"]
        assert bg_args and set(bg_args) == {"white"}, bg_args
        ok.append("预览渲染传递 --background")
        assert len(rep["rendered"]) == 5, rep["rendered"]   # demo 3 + zeta 2
        for pack, ids in (("demo", ["box_a", "box_b", "box_c"]),
                          ("zeta", ["zbox_a", "zbox_b"])):
            prev = TMP / "packs" / pack / "previews"
            names = sorted(p.name for p in prev.glob("*.png"))
            want = sorted(f"{mid}{sfx}" for mid in ids
                          for sfx in (".png", "_iso.png"))
            assert names == want, (pack, names, want)
        assert seen[0][3] == "start" and seen[0][1] == 5, seen[0]
        assert [s[0] for s in seen[1:]] == [1, 2, 3, 4, 5], seen
        assert seen[-1][3] == "rendered", seen[-1]
        ok.append("批量渲染写回各自资产包（多包 + 缩略图）")

        # 10) 渲染任务结束后刷新 pack.json（files sha256 + previews 映射）
        from mcstudio import api as API  # noqa: PLC0415

        class P:                       # 假 Progress（只收日志）
            def __init__(self):
                self.lines = []

            def log(self, s):
                self.lines.append(str(s))

            def set(self, *_a):
                pass

        sp.run = fake_run
        try:
            prog = P()
            rep2 = API._render_previews_job(prog, None, force=True)
        finally:
            sp.run = real_run
        assert rep2["failed"] == [], rep2["failed"]
        assert any("刷新 pack.json 失败" in ln for ln in prog.lines) is False, prog.lines
        for pack, mid in (("demo", "box_a"), ("zeta", "zbox_a")):
            m = json.loads((TMP / "packs" / pack / "pack.json")
                           .read_text(encoding="utf-8"))
            assert m.get("previews", {}).get(mid) == f"previews/{mid}.png", m.get("previews")
            assert f"previews/{mid}.png" in m.get("files", {}), list(m.get("files", {}))[:4]
        ok.append("渲染后重刷 pack.json（previews + sha256）")

        # 预览图 URL 带版本号：重渲染后 URL 必须变，否则浏览器（页内图片缓存）
        # 会继续显示旧封面 —— 现象「重渲染了，但模块库封面没更新」。
        st, mods = call("GET", "/api/modules?pack=demo")
        row = [m for m in mods["modules"] if m["id"] == "box_a"][0]
        url1 = row["preview_urls"]["thumb"]
        assert "?v=" in url1, url1
        thumb = TMP / "packs" / "demo" / "previews" / "box_a.png"
        thumb.write_bytes(thumb.read_bytes() + b"\x00")     # 模拟重渲染（内容/时间变了）
        os.utime(thumb, (time.time() + 5, time.time() + 5))
        st, mods2 = call("GET", "/api/modules?pack=demo")
        row2 = [m for m in mods2["modules"] if m["id"] == "box_a"][0]
        url2 = row2["preview_urls"]["thumb"]
        assert url2 != url1 and url2.startswith("/files/packs/demo/previews/box_a.png?")
        ok.append("预览图 URL 带版本号（重渲染后换 URL，封面不再被缓存钉住）")

        print(f"ALL PASS ({len(ok)} 项): 预览渲染队列")
        for line in ok:
            print(f"  PASS {line}")
        return 0
    finally:
        import shutil  # noqa: PLC0415
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
