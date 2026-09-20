"""mcstudio HTTP API 冒烟：模块/标签/导入/结构打开/编辑/撤销/另存为。

在临时仓库（STRUCTWORKSHOP_ROOT）里起一个本地 server 线程，用 urllib 打接口。
渲染类接口（mcrender 子进程）不在冒烟范围内。

Usage:  python tests/studio_smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
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

TMP = Path(tempfile.mkdtemp(prefix="structworkshop_studio_"))
os.environ["STRUCTWORKSHOP_ROOT"] = str(TMP)
(TMP / "pyproject.toml").write_text("", encoding="utf-8")
(TMP / "packages").mkdir()
(TMP / "packs" / "demo" / "modules" / "rooms").mkdir(parents=True)
(TMP / "packs" / "demo" / "pack.json").write_text(
    json.dumps({"id": "demo", "name": "demo", "version": "1.0.0"}),
    encoding="utf-8")

import numpy as np  # noqa: E402

from mccore import library as LB  # noqa: E402
from mccore import structure_io as S  # noqa: E402
from mcstudio.server import App, make_handler  # noqa: E402

AIR = {"Name": "minecraft:air"}
STONE = {"Name": "minecraft:stone"}
PORT = {"host": "127.0.0.1", "port": 0}


# ------------------------------------------------------------------ helpers
def make_module(mid="box_a", size=(4, 3, 5), tags=("room",)):
    sx, sy, sz = size
    v = np.zeros((sy, sz, sx), dtype=np.uint16)
    v[1:, :, :] = 1
    p = TMP / "packs" / "demo" / "modules" / "rooms" / f"{mid}.schem"
    S.write_structure(str(p), v, [AIR, STONE], (0, 0, 0), size, name=mid)
    LB.save_spec(p, {"id": mid, "pack": "demo", "category": "rooms",
                     "version": 1, "description": mid, "tags": list(tags),
                     "grid": {"size": list(size), "position": [0, 0, 0]},
                     "axis": "x", "flip": True, "ports": []})
    return p


def call(method, path, body=None, raw=False):
    url = f"http://127.0.0.1:{PORT['port']}{path}"
    data = None
    headers = {}
    if body is not None:
        if raw:
            data = body
        else:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = r.read()
            ctype = r.headers.get("Content-Type", "")
            if "json" in ctype:
                return r.status, json.loads(payload.decode("utf-8"))
            return r.status, payload
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, payload


def main() -> int:
    make_module("box_a", tags=["room", "style:modern"])
    make_module("box_b", tags=["room", "style:office"])
    LB.load_index(refresh=True)

    app = App()
    httpd = ThreadingHTTPServer((PORT["host"], 0), make_handler(app))
    PORT["port"] = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    ok = []
    try:
        # 1) 静态页 + 状态
        st, html = call("GET", "/")
        assert st == 200 and "结构工坊".encode() in html and b"Structworkshop" in html, (st, html[:80])
        st, js = call("GET", "/static/app.js")
        assert st == 200 and b"mcstudio" in js
        st, state = call("GET", "/api/state")
        assert state["stats"]["modules"] == 2, state["stats"]
        assert len(state["tags"]) == 3, state["tags"]
        print("PASS 1/8  静态页 + /api/state")

        # 2) 模块列表 + 标签过滤（facet 同组任一/跨组全选、and、or）+ 模式计数
        st, lst = call("GET", "/api/modules?tag=style:office")
        assert lst["total"] == 1 and lst["modules"][0]["id"] == "box_b", lst
        st, lst = call("GET", "/api/modules?tag=room&tag=style:modern")
        assert lst["total"] == 1 and lst["modules"][0]["id"] == "box_a", lst
        st, lst = call("GET", "/api/modules?tag=room&tag=style:modern&tagMode=or")
        assert lst["total"] == 2, lst
        # 同命名空间的两个标签 -> 取并集（旧行为 AND 会得到 0，是用户报的 bug）
        st, lst = call("GET", "/api/modules?tag=style:modern&tag=style:office&tagMode=facet")
        assert lst["total"] == 2, lst
        st, cnt = call("GET", "/api/modules/counts?tag=style:modern&tag=style:office")
        assert cnt == {"facet": 2, "and": 0, "or": 2}, cnt
        # 跨命名空间 -> 取交集
        st, lst = call("GET", "/api/modules?tag=room&tag=style:modern&tagMode=facet")
        assert lst["total"] == 1 and lst["modules"][0]["id"] == "box_a", lst
        st, cnt = call("GET", "/api/modules/counts?tag=room&tag=style:modern")
        assert cnt == {"facet": 1, "and": 1, "or": 2}, cnt
        print("PASS 2/8  /api/modules 标签过滤 (facet/and/or) + /api/modules/counts")

        # 3) 标签重命名 + 元数据 + 批量
        st, r = call("POST", "/api/tags/rename", {"old": "style:office", "new": "style:workplace"})
        assert r["affected"] == 1, r
        st, r = call("PATCH", "/api/modules/box_a",
                     {"description": "新描述", "tags": ["room", "glass"]})
        assert "meta" in r["changes"] and "tags" in r["changes"], r
        st, r = call("POST", "/api/modules/batch",
                     {"ids": ["box_a", "box_b"], "addTags": ["batch"]})
        assert r["tags"]["updated"] == ["box_a", "box_b"], r
        st, state = call("GET", "/api/state")
        names = {t["name"]: t["count"] for t in state["tags"]}
        assert names.get("batch") == 2 and names.get("glass") == 1, names
        print("PASS 3/8  标签重命名 / spec 编辑 / 批量打标")

        # 4) 上传导入
        src = TMP / "incoming.schem"
        S.write_structure(str(src), np.ones((2, 2, 2), np.uint16), [AIR, STONE],
                          (0, 0, 0), (2, 2, 2), name="incoming")
        st, rep = call("POST", "/api/import/upload?filename=incoming.schem"
                       "&pack=demo&category=imported&tags=a,b&trim=1",
                       src.read_bytes(), raw=True)
        assert st == 200 and len(rep["imported"]) == 1, rep
        new_id = rep["imported"][0]["id"]
        st, state = call("GET", "/api/state")
        assert state["stats"]["modules"] == 3, state["stats"]
        print(f"PASS 4/8  上传导入 -> {new_id}")

        # 5) 打开结构 + 体素下载
        path = "packs/demo/modules/rooms/box_a.schem"
        st, s = call("POST", "/api/structure/open", {"path": path})
        assert st == 200 and s["size"] == [4, 3, 5], s
        sid = s["sid"]
        st, raw = call("GET", f"/api/structure/{sid}/voxels")
        assert len(raw) == 4 * 3 * 5 * 2, len(raw)
        vox = np.frombuffer(raw, dtype="<u2")
        assert int(vox[0]) == 0 and int(vox[20]) == 1, vox[:6]
        print("PASS 5/8  结构打开 + /voxels")

        # 6) 编辑 op + 撤销/重做
        st, r = call("POST", f"/api/structure/{sid}/ops",
                     {"ops": [{"type": "set", "x": 0, "y": 0, "z": 0,
                               "state": "minecraft:stone"}]})
        assert r["bbox"] == [0, 0, 0, 0, 0, 0], r
        st, raw = call("GET", f"/api/structure/{sid}/voxels")
        assert int(np.frombuffer(raw, dtype="<u2")[0]) == 1, "op 未生效"
        st, r = call("POST", f"/api/structure/{sid}/undo", {})
        st, raw = call("GET", f"/api/structure/{sid}/voxels")
        assert int(np.frombuffer(raw, dtype="<u2")[0]) == 0, "撤销失败"
        st, r = call("POST", f"/api/structure/{sid}/redo", {})
        st, raw = call("GET", f"/api/structure/{sid}/voxels")
        assert int(np.frombuffer(raw, dtype="<u2")[0]) == 1, "重做失败"
        print("PASS 6/8  编辑 op + undo/redo")

        # 6b) 操作日志：标签/光标/jump 回退与前进
        st, h = call("GET", f"/api/structure/{sid}/history")
        assert st == 200 and h["total"] == 1 and h["cursor"] == 1, h
        assert h["undo"][0]["label"] == "绘制 1 格", h["undo"]
        assert isinstance(h["undo"][0]["at"], float), h["undo"]
        st, r = call("POST", f"/api/structure/{sid}/history/jump", {"index": 0})
        assert st == 200 and r["cursor"] == 0 and r["can_redo"], r
        st, raw = call("GET", f"/api/structure/{sid}/voxels")
        assert int(np.frombuffer(raw, dtype="<u2")[0]) == 0, "jump 回退失败"
        st, r = call("POST", f"/api/structure/{sid}/history/jump", {"index": 1})
        assert st == 200 and r["cursor"] == 1 and not r["can_redo"], r
        st, raw = call("GET", f"/api/structure/{sid}/voxels")
        assert int(np.frombuffer(raw, dtype="<u2")[0]) == 1, "jump 前进失败"
        st, r = call("POST", f"/api/structure/{sid}/history/jump", {"index": 9})
        assert st == 200 and r["cursor"] == 1, r          # 越界夹紧
        st, r = call("POST", f"/api/structure/{sid}/history/jump", {})
        assert st >= 400, (st, r)                         # 缺 index
        print("PASS 6b/8  操作日志 history/jump")

        # 7) 另存为（.schem / .litematic）逐格比对
        out = "packs/demo/modules/rooms/box_a_edit.schem"
        st, r = call("POST", f"/api/structure/{sid}/save-as",
                     {"path": out, "format": ".schem"})
        assert st == 200, r
        d = S.read_structure(str(TMP / out))
        assert tuple(d["size"]) == (4, 3, 5) and int((d["voxels"] != 0).sum()) == 41
        out_lt = "packs/demo/modules/rooms/box_a_edit.litematic"
        st, r = call("POST", f"/api/structure/{sid}/save-as",
                     {"path": out_lt, "format": ".litematic"})
        d2 = S.read_structure(str(TMP / out_lt))
        assert tuple(d2["size"]) == tuple(d["size"])
        print("PASS 7/8  save-as .schem/.litematic")

        # 8) 存为模块 + 删除备份
        st, r = call("POST", f"/api/structure/{sid}/save-as-module",
                     {"pack": "demo", "id": "saved_mod", "category": "custom",
                      "tags": ["from-editor"], "description": "编辑器另存"})
        assert st == 200 and (TMP / r["path"]).is_file(), r
        assert "saved_mod" in LB.load_index(refresh=True)["modules"]
        st, r = call("DELETE", "/api/modules/saved_mod")
        assert Path(r["backup"]).is_file() or (TMP / r["backup"]).is_file(), r
        assert "saved_mod" not in LB.load_index(refresh=True)["modules"]
        print("PASS 8/8  save-as-module + 删除备份")

        # 8b) 模块行的路径：`path` 是**包内相对**（预览图 URL / pack.json 用），
        #     但「在编辑器中打开 / 3D 查看」必须用**仓库相对**的 `file`（packs/…）。
        #     旧 bug：抽屉把 entry.path 直接发给 /api/structure/open
        #     → “不允许访问: modern-arch/modules/…”。
        mid = next(iter(LB.load_index(refresh=True)["modules"]))
        st, one = call("GET", f"/api/modules/{mid}")
        assert st == 200, one
        ent = one["entry"]
        assert ent["path"].startswith("demo/"), ent["path"]
        assert ent["file"] == "packs/" + ent["path"], ent
        st, op = call("POST", "/api/structure/open", {"path": ent["file"]})
        assert st == 200 and op["name"] == mid, op
        # 旧式包内相对路径仍然被拦住（安全边界不能因为“方便”而放宽）
        st, bad = call("POST", "/api/structure/open", {"path": ent["path"]})
        assert st == 403 and "不允许访问" in bad.get("error", ""), bad
        print("PASS 8b/8  模块 file（仓库相对）能开，包内相对 path 仍被拒")

        # 8c) 改画布尺寸后保存 → sidecar spec 的 grid 要跟着改（否则模块库报
        #     “grid.size [1, 5, 2] != 实际 [2, 5, 2]”），越界端口要报出来
        mid = next(k for k, v in LB.load_index(refresh=True)["modules"].items()
                   if v["pack"] == "demo")
        st, one = call("GET", f"/api/modules/{mid}")
        spec_file = TMP / one["path"].replace(".schem", ".module.json")   # path 现在是仓库相对
        # 先写一个**当前尺寸下合法**的端口（east 面 2×2），等下把画布改小 → 它才越界
        st, _pt = call("PATCH", f"/api/modules/{mid}",
                       {"ports": [{"id": "p", "type": "passage", "face": "east",
                                   "origin": [0, 0], "size": [2, 2], "tags": []},
                                  # 圆形接口：圆心 + 直径（引擎按外接矩形匹配）
                                  {"id": "round", "type": "vent", "face": "east",
                                   "shape": "circle", "origin": [0, 0],
                                   "size": [2, 2], "tags": []}]})
        assert st == 200, _pt
        st, one2 = call("GET", f"/api/modules/{mid}")
        got = {p["id"]: p for p in (one2["spec"]["ports"] or [])}
        assert got["round"]["shape"] == "circle", got
        assert got["p"].get("shape") in (None, "rect"), got
        # 非法形状 / 直径不相等都要被服务端拦住（不静默存进去）
        st, bad_shape = call("PATCH", f"/api/modules/{mid}",
                             {"ports": [{"id": "x", "type": "vent", "face": "east",
                                         "shape": "blob", "origin": [0, 0],
                                         "size": [2, 2], "tags": []}]})
        assert st == 400 and "shape" in bad_shape.get("error", ""), bad_shape
        st, bad_dia = call("PATCH", f"/api/modules/{mid}",
                           {"ports": [{"id": "x", "type": "vent", "face": "east",
                                       "shape": "circle", "origin": [0, 0],
                                       "size": [2, 3], "tags": []}]})
        assert st == 400 and "直径" in bad_dia.get("error", ""), bad_dia
        st, op = call("POST", "/api/structure/open", {"path": one["entry"]["file"]})
        sid = op["sid"]
        st, rz = call("POST", f"/api/structure/{sid}/resize", {"size": [1, 1, 1]})
        assert st == 200, rz
        st, sv = call("POST", f"/api/structure/{sid}/save", {})
        assert st == 200 and sv["size"] == [1, 1, 1], sv
        assert sv.get("spec_grid") == [1, 1, 1] and sv.get("spec_grid_from") is not None, sv
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        assert spec["grid"]["size"] == [1, 1, 1], spec["grid"]
        # 端口在新尺寸下越界 → 保存结果里要提醒（不静默）
        assert any("超出" in w for w in (sv.get("spec_warnings") or [])), sv
        print("PASS 8c/8  改画布尺寸→保存会同步 spec 的 grid，越界端口给警告（含圆形接口）")

        # 8d) 「在编辑器里打开模块」= 画布 = 模块尺寸 + 模块是可拖动实例：
        #     基地层整体转成装配实例（POST …/modules/detach），保存按画布相对坐标走。
        mid = next(k for k, v in LB.load_index(refresh=True)["modules"].items()
                   if v["pack"] == "demo")
        st, one = call("GET", f"/api/modules/{mid}")
        msize = list(one["entry"]["size"])
        st, op = call("POST", "/api/structure/open", {"path": one["entry"]["file"]})
        sid = op["sid"]
        st, dt = call("POST", f"/api/structure/{sid}/modules/detach", {"id": mid})
        assert st == 200 and len(dt["placements"]) == 1, dt
        pl = dt["placements"][0]
        assert pl["id"] == mid and pl["pos"] == [0, 0, 0], pl
        assert pl["dims"] == msize and dt["size"] == msize, (pl, dt["size"], msize)
        # 拖动实例 → 合成体素跟着走（画布会自动扩容）
        st, mv = call("POST", f"/api/structure/{sid}/modules/update",
                      {"pid": pl["pid"], "delta": [2, 0, 0]})
        assert st == 200 and mv["placements"][0]["pos"] == [2, 0, 0], mv
        # 撤销两次：先回原位，再回到「还没转实例」的状态
        st, _ = call("POST", f"/api/structure/{sid}/undo", {})
        st, un = call("POST", f"/api/structure/{sid}/undo", {})
        assert st == 200 and un["placements"] == [], un
        print("PASS 8d/8  打开模块 → 基地层转装配实例（可拖 + 可撤销）")

        # 8e) 接口吸附不能因为「索引里的 ports 没有 origin」而 500：
        #     detach 时要拿 spec 里的 ports（旧 bug：客户端把 entry.ports 塞进来，
        #     吸附 transform_port 直接 KeyError 'origin'，拖动提交失败、位置弹回）。
        st, ot = call("POST", "/api/structure/open", {"path": one["entry"]["file"]})
        sid3 = ot["sid"]
        call("PATCH", f"/api/modules/{mid}",
             {"ports": [{"id": "p", "type": "door", "face": "east",
                          "origin": [0, 0], "size": [1, 1], "tags": []}]})
        st, dt3 = call("POST", f"/api/structure/{sid3}/modules/detach",
                       # 故意给一份缺 origin 的 ports（旧客户端就是这么发的）
                       {"id": mid, "ports": [{"id": "bad", "type": "door",
                                                "face": "east", "size": [1, 1]}]})
        assert st == 200 and len(dt3["placements"]) == 1, dt3
        st, up = call("POST", f"/api/structure/{sid3}/modules/update",
                      {"pid": dt3["placements"][0]["pid"], "delta": [1, 0, 0],
                       "snapPort": True})
        assert st == 200 and up["placements"][0]["pos"] == [1, 0, 0], (st, up)
        # 还原 spec（别把测试端口留在仓库里）
        call("PATCH", f"/api/modules/{mid}", {"ports": []})
        print("PASS 8e/8  detach 用 spec 的 ports（缺 origin 的坏 ports 不再炸吸附）")

        # 8f) 存盘再开：装配清单从边车还原 → 客户端不能再 detach 一遍（否则体素叠两份）
        rt = "rt_mod"
        make_module(rt, size=(4, 3, 5), tags=("room",))
        LB.load_index(refresh=True)
        st, one2 = call("GET", f"/api/modules/{rt}")
        n0 = int(one2["entry"]["blocks"])
        assert n0 > 0, one2["entry"]
        st, op = call("POST", "/api/structure/open", {"path": one2["entry"]["file"]})
        sid4 = op["sid"]
        st, dt4 = call("POST", f"/api/structure/{sid4}/modules/detach", {"id": rt})
        assert dt4["placements"][0]["pos"] == [0, 0, 0], dt4
        out_rel = "builds/smoke_detach_roundtrip.schem"
        st, sv = call("POST", f"/api/structure/{sid4}/save-as", {"path": out_rel})
        assert st == 200, sv
        back = S.read_structure(str(TMP / out_rel))
        got = int((back["voxels"] != 0).sum())
        assert got == n0, (got, n0)
        st, re = call("POST", "/api/structure/open", {"path": out_rel})
        assert re["placements"] == 1 and re["restore"]["restored"] == 1, re
        # 重开后合成数据 = 模块体素数（没有「还原实例 + 再 detach」叠成两份）
        assert int(re["blocks"]) == n0, (re.get("blocks"), n0)
        print("PASS 8f/8  存盘再开：装配清单还原一次（体素不会翻倍）")

        print("\nALL PASS: mcstudio HTTP API")
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()
        time.sleep(0.1)
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
