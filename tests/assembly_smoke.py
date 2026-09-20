"""mcstudio 模块装配冒烟：多模块导入 / 拼接 / 移动旋转 / 端口吸附 / 保存还原。

Usage:  python tests/assembly_smoke.py
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

TMP = Path(tempfile.mkdtemp(prefix="structworkshop_assembly_"))
os.environ["STRUCTWORKSHOP_ROOT"] = str(TMP)
(TMP / "pyproject.toml").write_text("", encoding="utf-8")
(TMP / "packages").mkdir()
(TMP / "packs" / "demo" / "modules" / "corridor").mkdir(parents=True)
(TMP / "packs" / "demo" / "modules" / "rooms").mkdir(parents=True)
(TMP / "packs" / "demo" / "pack.json").write_text(
    json.dumps({"id": "demo", "name": "demo", "version": "1.0.0"}),
    encoding="utf-8")
(TMP / "builds").mkdir()

import numpy as np  # noqa: E402

from mccore import library as LB  # noqa: E402
from mccore import structure_io as S  # noqa: E402
from mccore.assemble import rot_axis_dims  # noqa: E402
from mccore.module_lib import VALID_FACES as MODULE_FACES  # noqa: E402
from mcstudio.server import App, make_handler  # noqa: E402

AIR = {"Name": "minecraft:air"}
STONE = {"Name": "minecraft:stone"}
PORT = {"host": "127.0.0.1", "port": 0}


def voxel_at(sid, x, y, z):
    """读合成后的某一格（含模块）方块的 state 字符串。"""
    _st, raw = call("GET", f"/api/structure/{sid}/voxels")
    _st, state = call("GET", f"/api/structure/{sid}")
    sx, sy, sz = state["size"]
    arr = np.frombuffer(raw, dtype=np.uint16).reshape(sy, sz, sx)
    return state["palette"][int(arr[y, z, x])]


def make_module(mid, size, ports, fill=True):
    sx, sy, sz = size
    v = np.zeros((sy, sz, sx), dtype=np.uint16)
    if fill:
        v[1:, 1:-1, 1:-1] = 1
    p = TMP / "packs" / "demo" / "modules" / "corridor" / f"{mid}.schem"
    S.write_structure(str(p), v, [AIR, STONE], (0, 0, 0), size, name=mid)
    LB.save_spec(p, {"id": mid, "pack": "demo", "category": "corridor",
                     "version": 1, "description": mid, "tags": ["corridor"],
                     "grid": {"size": list(size), "position": [0, 0, 0]},
                     "axis": "x", "flip": True, "ports": ports})
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
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
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
    make_module("seg_a", (6, 3, 5),
                [{"id": "w", "type": "passage", "face": "west",
                  "origin": [1, 2], "size": [2, 2]},
                 {"id": "e", "type": "passage", "face": "east",
                  "origin": [1, 2], "size": [2, 2]}])
    make_module("seg_b", (6, 3, 5),
                [{"id": "w", "type": "passage", "face": "west",
                  "origin": [1, 2], "size": [2, 2]},
                 {"id": "e", "type": "passage", "face": "east",
                  "origin": [1, 2], "size": [2, 2]}])
    make_module("room_c", (4, 4, 4), [])
    LB.load_index(refresh=True)

    # 空场景
    empty = TMP / "builds" / "empty.schem"
    S.write_structure(str(empty), np.zeros((1, 1, 1), np.uint16), [AIR],
                      (0, 0, 0), (1, 1, 1), name="empty")

    app = App()
    httpd = ThreadingHTTPServer((PORT["host"], 0), make_handler(app))
    PORT["port"] = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        # 1) 打开空场景
        st, s = call("POST", "/api/structure/open", {"path": "builds/empty.schem"})
        assert st == 200 and s["placements"] == 0, s
        sid = s["sid"]
        assert (TMP / empty).is_file()
        print("PASS 1/14  打开空场景")

        # 2) 同时导入多个模块（自动 +X 顺序拼接）
        st, r = call("POST", f"/api/structure/{sid}/modules",
                     {"ids": ["seg_a", "seg_b"], "auto": True})
        assert st == 200, r
        pl = r["placements"]
        assert len(pl) == 2, pl
        assert abs(pl[0]["pos"][0] - 0) == 0 and abs(pl[1]["pos"][0] - 6) == 0, pl
        assert r["size"] == [12, 3, 5], r
        assert r["resized"] is True, r
        print("PASS 2/14  多模块导入 + 顺序拼接")

        # 3) 端口吸附（导入时不吸附，移动时开启）
        pid_b = pl[1]["pid"]
        st, r = call("POST", f"/api/structure/{sid}/modules/update",
                     {"pid": pid_b, "pos": [12, 5, 5], "snapPort": True,
                      "snapRadius": 99})
        assert st == 200, r
        moved = next(x for x in r["placements"] if x["pid"] == pid_b)
        assert moved["pos"] == [6, 0, 0], moved
        print("PASS 3/14  端口吸附（move+snapPort）")

        # 4) 移动 + 旋转
        st, r = call("POST", f"/api/structure/{sid}/modules/update",
                     {"pid": pid_b, "pos": [7, 0, 0], "rot": 1})
        moved = next(x for x in r["placements"] if x["pid"] == pid_b)
        assert moved["pos"] == [7, 0, 0] and moved["rot"] == 1, moved
        assert moved["dims"] == [5, 3, 6], moved
        # 三轴旋转：绕 X / 绕 Z 各 90°（顺序 X→Y→Z）
        st, r = call("POST", f"/api/structure/{sid}/modules/update",
                     {"pid": pid_b, "rotx": 1, "rotz": 1})
        moved = next(x for x in r["placements"] if x["pid"] == pid_b)
        expect = rot_axis_dims(rot_axis_dims(rot_axis_dims([6, 3, 5], 0, 1), 1, 1), 2, 1)
        assert moved["dims"] == expect, (moved, expect)
        assert moved["rotx"] == 1 and moved["rotz"] == 1 and moved["rot"] == 1, moved
        # 复位到 yaw=1，供后续步骤断言
        st, r = call("POST", f"/api/structure/{sid}/modules/update",
                     {"pid": pid_b, "rotx": 0, "rotz": 0})
        moved = next(x for x in r["placements"] if x["pid"] == pid_b)
        assert moved["dims"] == [5, 3, 6], moved
        print("PASS 4/14  移动 + 旋转（含三轴）")

        # 5) 撤销/重做（装配动作可撤销，含三轴旋转步）
        st, r = call("POST", f"/api/structure/{sid}/undo", {})
        assert st == 200 and r["placements"], r
        back = next(x for x in r["placements"] if x["pid"] == pid_b)
        assert back["rotx"] == 1 and back["rotz"] == 1, back       # 撤销“复位”
        st, r = call("POST", f"/api/structure/{sid}/redo", {})
        fwd = next(x for x in r["placements"] if x["pid"] == pid_b)
        assert fwd["rotx"] == 0 and fwd["rotz"] == 0, fwd          # 重做“复位”
        for _ in range(3):                                          # 回到第一个移动之前
            st, r = call("POST", f"/api/structure/{sid}/undo", {})
        back = next(x for x in r["placements"] if x["pid"] == pid_b)
        assert back["pos"] == [6, 0, 0] and back["rot"] == 0, back
        for _ in range(3):                                          # 再前进回当前状态
            st, r = call("POST", f"/api/structure/{sid}/redo", {})
        fwd = next(x for x in r["placements"] if x["pid"] == pid_b)
        assert fwd["pos"] == [7, 0, 0] and fwd["rot"] == 1, fwd
        assert fwd["rotx"] == 0 and fwd["rotz"] == 0, fwd
        print("PASS 5/14  装配撤销/重做（含三轴）")

        # 6) 保存：sidecar + .schem Metadata 双写
        out = "builds/assembled.schem"
        st, r = call("POST", f"/api/structure/{sid}/save-as",
                     {"path": out, "format": ".schem"})
        assert st == 200 and r["placements"] == 2, r
        d = S.read_structure(str(TMP / out))
        assert "StructworkshopModules" in d["metadata"], d["metadata"].keys()
        side = TMP / "builds" / "assembled.layout.json"
        assert side.is_file(), side
        side_data = json.loads(side.read_text(encoding="utf-8"))
        assert len(side_data["instances"]) == 2, side_data
        print("PASS 6/14  保存 .schem + layout.json + Metadata")

        # 7) 重开：还原装配清单（base 剥离后仍能继续移动）
        st, s2 = call("POST", "/api/structure/open", {"path": out})
        assert st == 200, s2
        assert s2["placements"] == 2 and s2["restore"]["restored"] == 2, s2
        assert s2["restore"]["missing"] == [], s2["restore"]
        # 旋转在存取后保持不变
        st, r = call("GET", f"/api/structure/{s2['sid']}/modules")
        b2 = next(x for x in r["placements"] if x["id"] == "seg_b")
        assert b2["rot"] == 1 and b2["rotx"] == 0 and b2["rotz"] == 0, b2
        assert b2["dims"] == [5, 3, 6], b2
        sid2 = s2["sid"]
        st, r = call("GET", f"/api/structure/{sid2}/modules")
        assert len(r["placements"]) == 2, r
        pid = r["placements"][0]["pid"]
        st, r = call("POST", f"/api/structure/{sid2}/modules/update",
                     {"pid": pid, "delta": [1, 0, 0]})
        assert st == 200 and r["placements"][0]["pos"][0] == 1, r
        print("PASS 7/14  重开还原 + 继续移动")

        # 8) 画布框（保存时按它裁）：放大 → 缩小不裁数据/可撤销 → 框外还能放 → 按内容 → 保存才裁
        st, r = call("POST", f"/api/structure/{sid2}/resize",
                     {"size": [32, 12, 16]})
        assert st == 200 and r["resized"] is True and r["size"] == [32, 12, 16], r
        assert r["frame"] == [32, 12, 16] and r["outside"] == 0, r
        st, raw = call("GET", f"/api/structure/{sid2}/voxels")
        assert len(raw) == 32 * 12 * 16 * 2, len(raw)
        st, before = call("GET", f"/api/structure/{sid2}")
        assert before["frame"] == [32, 12, 16], before

        # 缩小：只改画布框，数据与撤销栈都不动
        st, r = call("POST", f"/api/structure/{sid2}/resize",
                     {"size": [8, 8, 8]})
        assert st == 200 and r["frame"] == [8, 8, 8], r
        assert r["size"] == [32, 12, 16] and r["resized"] is False, r
        assert r["outside"] > 0 and r["clipped_modules"], r
        assert r["can_undo"] is True, r
        st, after = call("GET", f"/api/structure/{sid2}")
        assert after["blocks"] == before["blocks"], (before["blocks"], after["blocks"])
        assert after["size"] == [32, 12, 16], after
        st, raw = call("GET", f"/api/structure/{sid2}/voxels")
        assert len(raw) == 32 * 12 * 16 * 2, len(raw)      # 数据范围没缩

        # 框外还能继续放（画布框不变）
        st, p = call("POST", f"/api/structure/{sid2}/ops",
                     {"ops": [{"type": "set", "x": 20, "y": 1, "z": 1,
                                "state": "minecraft:stone"}]})
        assert st == 200 and p["frame"] == [8, 8, 8], p
        st, after2 = call("GET", f"/api/structure/{sid2}")
        assert after2["blocks"] == before["blocks"] + 1, after2
        assert after2["outside"] >= 1 and after2["frame"] == [8, 8, 8], after2

        # 画布框这一步可撤销/重做（不丢数据）
        st, _ = call("POST", f"/api/structure/{sid2}/undo", {})        # 撤绘制
        st, u = call("POST", f"/api/structure/{sid2}/undo", {})        # 撤画布框
        assert st == 200 and u["frame"] == [32, 12, 16] and u["bbox"] is None, u
        assert u["size"] == [32, 12, 16], u                                # 数据范围没变
        st, rd = call("POST", f"/api/structure/{sid2}/redo", {})
        assert st == 200 and rd["frame"] == [8, 8, 8], rd

        # 按内容收缩（框跟着内容，数据仍在）
        st, r = call("POST", f"/api/structure/{sid2}/resize", {"fit": True})
        assert st == 200 and r["outside"] == 0, r
        assert r["size"] == [32, 12, 16] and 1 <= r["frame"][0] <= 32, r

        # 保存：按画布框裁，框外不写进文件（会话里还留着）
        st, r = call("POST", f"/api/structure/{sid2}/save-as",
                     {"path": "builds/framed.schem", "format": ".schem"})
        assert st == 200 and r["size"] == r["frame"], r
        d = S.read_structure(str(TMP / "builds" / "framed.schem"))
        assert [int(v) for v in d["size"]] == r["frame"], (d["size"], r["frame"])
        st, still = call("GET", f"/api/structure/{sid2}")
        assert still["size"] == [32, 12, 16], still                    # 会话没被裁
        # 端口约定：origin/size 的轴向必须与 mccore.assemble 一致
        # （编辑器里「点两个角 / 扫开口」算出来的就是这一套）
        from mccore.assemble import port_anchor3d, transform_port  # noqa: PLC0415
        w, h, d = 5, 5, 5
        cases = [
            ({"face": "east", "origin": [1, 2], "size": [2, 3]}, (w - 1, 1, 2)),
            ({"face": "west", "origin": [1, 2], "size": [2, 3]}, (0, 1, 2)),
            ({"face": "north", "origin": [1, 2], "size": [2, 3]}, (2, 1, 0)),
            ({"face": "south", "origin": [1, 2], "size": [2, 3]}, (2, 1, d - 1)),
            ({"face": "up", "origin": [1, 2], "size": [2, 3]}, (1, h - 1, 2)),
            ({"face": "down", "origin": [1, 2], "size": [2, 3]}, (1, 0, 2)),
        ]
        got = [(port_anchor3d({"id": "p", "type": "passage", **p}, w, h, d), want)
               for p, want in cases]
        assert all(a == b for a, b in got), got
        # 端口 = **接口**：不看里面实心/空心，只看「面贴合 + 面内重叠 + 类型兼容」
        from mccore.assemble import types_compatible  # noqa: PLC0415
        from mccore.module_lib import validate_spec  # noqa: PLC0415
        assert types_compatible("interface", "passage")
        assert types_compatible("interface", "anchor") and types_compatible("anchor", "interface")
        assert types_compatible("interface", "interface")
        assert types_compatible("redstone_in", "redstone_out")
        assert not types_compatible("redstone_in", "fluid_out")
        assert not types_compatible("anchor", "shaft")
        print("PASS 12/14 类型兼容：interface 与任何类型可接，in/out 成对，跨类不接")
        # **实心**柱子分段对接（接口区里全是实心方块） → 一样按几何吸附
        col = np.ones((4, 4, 4), dtype=np.uint16)
        col_ports = [{"id": "top", "type": "interface", "face": "up",
                      "origin": [0, 0], "size": [4, 4], "tags": []},
                     {"id": "bot", "type": "interface", "face": "down",
                      "origin": [0, 0], "size": [4, 4], "tags": []}]
        assert validate_spec({"grid": {"size": [4, 4, 4]}, "ports": col_ports},
                             (4, 4, 4)) == []
        cdir = TMP / "packs" / "demo" / "modules" / "columns"
        cdir.mkdir(parents=True, exist_ok=True)
        for tag in ("col_a", "col_b"):
            S.write_structure(str(cdir / f"{tag}.schem"), col,
                              [{"Name": "minecraft:air"}, {"Name": "minecraft:stone"}],
                              (0, 0, 0), (4, 4, 4), name=tag)
            LB.save_spec(cdir / f"{tag}.schem",
                         {"id": tag, "pack": "demo", "category": "columns",
                          "grid": {"size": [4, 4, 4], "position": [0, 0, 0]},
                          "ports": col_ports, "tags": [], "axis": "x", "flip": False})
        LB.module_scan(quiet=True)
        st, cs = call("POST", "/api/structure/new", {"size": [12, 16, 12], "name": "cols"})
        sid2 = cs["sid"]
        st, r = call("POST", f"/api/structure/{sid2}/modules", {"ids": ["col_a"], "auto": True})
        assert st == 200 and len(r["placements"]) == 1, r
        st, r = call("POST", f"/api/structure/{sid2}/modules", {"ids": ["col_b"], "auto": True})
        pid2 = [p for p in r["placements"] if p["id"] == "col_b"][0]["pid"]
        st, r = call("POST", f"/api/structure/{sid2}/modules/update",
                     {"pid": pid2, "pos": [0, 12, 0], "snapPort": True, "snapRadius": 99})
        moved = next(x for x in r["placements"] if x["pid"] == pid2)
        assert moved["pos"] == [0, 4, 0], moved
        print("PASS 13/14 实心柱子分段对接：接口区全是实心，照样按尺寸吸到柱顶")
        # 面不贴合就不吸：把同一根柱的接口改到 north/south（水平对接），
        # 与 col_a 的 up/down 没法面对面 → 请求的位置必须保持不动
        S.write_structure(str(cdir / "col_c.schem"), col,
                          [{"Name": "minecraft:air"}, {"Name": "minecraft:stone"}],
                          (0, 0, 0), (4, 4, 4), name="col_c")
        LB.save_spec(cdir / "col_c.schem",
                     {"id": "col_c", "pack": "demo", "category": "columns",
                      "grid": {"size": [4, 4, 4], "position": [0, 0, 0]},
                      "ports": [{"id": "n", "type": "interface", "face": "north",
                                 "origin": [0, 0], "size": [4, 4], "tags": []},
                                {"id": "s", "type": "interface", "face": "south",
                                 "origin": [0, 0], "size": [4, 4], "tags": []}],
                      "tags": [], "axis": "x", "flip": False})
        LB.module_scan(quiet=True)
        st, r = call("POST", f"/api/structure/{sid2}/modules", {"ids": ["col_c"], "auto": True})
        pid3 = [p for p in r["placements"] if p["id"] == "col_c"][0]["pid"]
        st, r = call("POST", f"/api/structure/{sid2}/modules/update",
                     {"pid": pid3, "pos": [8, 8, 8], "snapPort": True, "snapRadius": 99})
        moved3 = next(x for x in r["placements"] if x["pid"] == pid3)
        assert moved3["pos"] == [8, 8, 8], moved3
        print("PASS 14/14 面不贴合（up/down vs north/south）→ 不吸附，位置不动")
        print("PASS 8/14  画布框（放大/缩小不裁数据/框外可放/可撤销/按内容/保存才裁）")

        # 9) 打开闸门：超大结构直接拒绝
        import os as _os
        big = TMP / "builds" / "huge.schem"
        S.write_structure(str(big), np.zeros((1, 1, 1), np.uint16), [AIR],
                          (0, 0, 0), (1, 1, 1), name="huge")
        _os.environ["STRUCTWORKSHOP_MAX_STRUCTURE_CELLS"] = "0"
        st, r = call("POST", "/api/structure/open", {"path": "builds/huge.schem"})
        assert st == 400 and "结构过大" in r["error"], r
        _os.environ["STRUCTWORKSHOP_MAX_STRUCTURE_CELLS"] = "20000000"
        st, r = call("POST", "/api/structure/open", {"path": "builds/huge.schem"})
        assert st == 200, r
        print("PASS 9/14  打开闸门（超大结构拒绝 + 恢复后正常）")

        # 15) 模块 × 编辑工具**不互斥**：压在模块实心处的笔刷/工具就地改在模块实例上
        #     （overrides）——不用先「固化装配」；改动跟着模块（旋转也跟）。
        st, s = call("POST", "/api/structure/new",
                     {"size": [16, 16, 16], "name": "punch"})
        sid3 = s["sid"]
        st, r = call("POST", f"/api/structure/{sid3}/modules",
                     {"ids": ["col_c"], "positions": [[2, 2, 2]]})
        assert st == 200, r
        st, st0 = call("GET", f"/api/structure/{sid3}")
        blocks0 = st0["blocks"]
        # col_c = 4×4×4，实心在局部 y 1..3 / z 1..2 / x 1..2 → 世界 (3,3,3) 在模块里
        assert voxel_at(sid3, 3, 3, 3) == "minecraft:stone"
        st, op = call("POST", f"/api/structure/{sid3}/ops",
                      {"ops": [{"type": "set", "x": 3, "y": 3, "z": 3,
                                 "state": "minecraft:gold_block"}]})
        assert st == 200 and op["punched"] == 1, op
        assert op["placements"][0]["edits"] == 1, op["placements"]
        st, st1 = call("GET", f"/api/structure/{sid3}")
        assert st1["blocks"] == blocks0, (blocks0, st1["blocks"])   # 换了一块，不是多一块
        assert voxel_at(sid3, 3, 3, 3) == "minecraft:gold_block"     # 真的看得见
        # 撤销 → 模块自己那块回来；重做 → 又是金块
        call("POST", f"/api/structure/{sid3}/undo", {})
        assert voxel_at(sid3, 3, 3, 3) == "minecraft:stone"
        call("POST", f"/api/structure/{sid3}/redo", {})
        assert voxel_at(sid3, 3, 3, 3) == "minecraft:gold_block"

        # 擦除也穿透；再把模块绕 Y 转 90° → 擦掉的那格**跟着模块走**
        st, er = call("POST", f"/api/structure/{sid3}/ops",
                      {"ops": [{"type": "erase", "box": [3, 3, 3, 3, 3, 3]}]})
        assert st == 200 and er["punched"] == 1, er
        assert voxel_at(sid3, 3, 3, 3) == "minecraft:air"
        st, st2 = call("GET", f"/api/structure/{sid3}")
        n_before = st2["blocks"]
        pid = er["placements"][0]["pid"]
        st, mv = call("POST", f"/api/structure/{sid3}/modules/update",
                      {"pid": pid, "rot": 1})
        assert st == 200, mv
        st, st3 = call("GET", f"/api/structure/{sid3}")
        assert st3["blocks"] == n_before, (n_before, st3["blocks"])
        assert voxel_at(sid3, 3, 3, 3) == "minecraft:stone", \
            "擦掉的那格应该跟着模块转走，原位回到模块自己的石块"

        # 工具（不止笔刷）同样穿透：fill 一块铁块压在模块上（方块走 top-level "block"）
        st, tool = call("POST", f"/api/structure/{sid3}/tool",
                        {"tool": "fill", "sel": [2, 2, 2, 5, 5, 5],
                         "block": "minecraft:iron_block"})
        assert st == 200 and tool["punched"] > 0, tool
        assert voxel_at(sid3, 4, 4, 4) == "minecraft:iron_block", tool
        # 存盘 → 重开：overrides 跟着 layout.json 回来（合成结果不变）
        st, sv = call("POST", f"/api/structure/{sid3}/save-as",
                      {"path": "builds/punch.schem"})
        assert st == 200, sv
        st, re = call("POST", "/api/structure/open", {"path": "builds/punch.schem"})
        assert st == 200 and re["placements"] == 1, re
        assert voxel_at(re["sid"], 4, 4, 4) == "minecraft:iron_block"
        print("PASS 15/15 模块 × 编辑工具不互斥（笔刷/工具就地改模块 + 撤销 + 跟着旋转 + 存盘）")

        # 16) 生成器/AI 那侧也允许模块**堆叠**：Assembler(overlap=True) 不报冲突，
        #     后放的盖过先放的（与工作台里的装配语义一致）；默认保持严格的冲突校验
        from mccore.assemble import Assembler  # noqa: PLC0415
        blk = {"spec": {"grid": {"size": [2, 2, 2]}, "id": "blk", "ports": []},
               "voxels": np.ones((2, 2, 2), dtype=np.uint16),
               "palette": [{"Name": "minecraft:air"},
                           {"Name": "minecraft:stone"}]}
        strict = Assembler((16, 16, 16))
        strict.place("a", blk, (0, 0, 0), 0)
        try:
            strict.place("b", blk, (1, 1, 1), 0)
            raise AssertionError("默认应该报冲突")
        except ValueError as e:
            assert "冲突" in str(e), e
        loose = Assembler((16, 16, 16), overlap=True)
        loose.place("a", blk, (0, 0, 0), 0)
        loose.place("b", blk, (1, 1, 1), 0)     # 堆叠：不报错
        assert len(loose.instances) == 2, loose.instances
        assert "--overlap" in __import__("subprocess").run(
            [sys.executable, "-m", "mccore.assemble", "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(ROOT)).stdout
        print("PASS 16/16 模块可以堆叠（Assembler(overlap=True) + CLI --overlap）")

        # 17) 「打开即实例」：没有 id 的 detach = **整幅投影**（伪包 @self）。
        #     打开任意 .schem 后它就是可整体拖动/旋转的一个实例；保存 → 重开
        #     仍然还原成一个实例（否则「导入后当普通方块」的拖动力就丢了）。
        #     两个开关：markDirty=false（打开不该标未保存）、record=false（不进撤销栈）。
        st, s = call("POST", "/api/structure/new",
                     {"size": [12, 8, 12], "name": "自包装"})
        sid4 = s["sid"]
        st, op = call("POST", f"/api/structure/{sid4}/ops",
                      {"ops": [{"type": "set", "x": 1, "y": 1, "z": 1,
                                 "state": "minecraft:stone"},
                                {"type": "set", "x": 6, "y": 3, "z": 6,
                                 "state": "minecraft:gold_block"}]})
        assert st == 200, op
        st, before = call("GET", f"/api/structure/{sid4}")
        steps0 = before["undo_steps"]
        st, dt = call("POST", f"/api/structure/{sid4}/modules/detach",
                      {"markDirty": False, "record": False})
        assert st == 200, dt
        assert len(dt["placements"]) == 1, dt
        inst = dt["placements"][0]
        assert inst["pack"] == "@self", inst
        assert inst["blocks"] == 2, inst
        assert dt["dirty"] is False, dt           # 打开动作不算「改动」
        st, mid_st = call("GET", f"/api/structure/{sid4}")
        assert mid_st["undo_steps"] == steps0, \
            (steps0, mid_st["undo_steps"])        # record=false → 撤销栈不多一步
        assert voxel_at(sid4, 6, 3, 6) == "minecraft:gold_block"
        # 整幅实例能整体搬：+3 格 X
        st, mv = call("POST", f"/api/structure/{sid4}/modules/update",
                      {"pid": inst["pid"], "delta": [3, 0, 0]})
        assert st == 200, mv
        assert voxel_at(sid4, 9, 3, 6) == "minecraft:gold_block", "整幅实例应该搬过去了"
        assert voxel_at(sid4, 6, 3, 6) == "minecraft:air", "原位应该空了"
        # 保存 → 重开：体素逐格一致，而且**还是**一个可拖的实例
        st, sv = call("POST", f"/api/structure/{sid4}/save-as",
                      {"path": "builds/self_wrap.schem"})
        assert st == 200, sv
        assert (TMP / "builds" / "self_wrap.layout.json").is_file(), "应该写了边车清单"
        lay = json.loads((TMP / "builds" / "self_wrap.layout.json")
                         .read_text(encoding="utf-8"))
        assert lay["instances"][0]["pack"] == "@self", lay
        assert lay["instances"][0]["pos"] == [0, 0, 0], \
            "@self 实例的 pos 一律写 0（内容已含位移，记两次会推两遍）"
        st, re2 = call("POST", "/api/structure/open",
                       {"path": "builds/self_wrap.schem"})
        assert st == 200 and re2["placements"] == 1, re2
        assert re2["restore"]["restored"] == 1, re2
        assert re2["dirty"] is False, re2
        assert voxel_at(re2["sid"], 9, 3, 6) == "minecraft:gold_block", \
            "重开后内容不能变（也不能被推第二遍）"
        assert voxel_at(re2["sid"], 4, 1, 1) == "minecraft:stone", \
            "重开后内容停在移动后的位置（原位的石块已经跟过去了）"
        assert voxel_at(re2["sid"], 1, 1, 1) == "minecraft:air"
        # 它仍然能整体搬（重开的实例是真的实例，不是一盘散沙）
        st, pl2 = call("GET", f"/api/structure/{re2['sid']}/modules")
        pid4 = pl2["placements"][0]["pid"]
        st, mv2 = call("POST", f"/api/structure/{re2['sid']}/modules/update",
                       {"pid": pid4, "delta": [0, 0, 2]})
        assert st == 200, mv2
        assert voxel_at(re2["sid"], 9, 3, 8) == "minecraft:gold_block"
        print("PASS 17/17 打开即实例（@self 整幅包装 + markDirty/record + 保存重开仍可拖）")

        # 18) 连接状态重算要看**合成结果**（不只看基地层）。
        #     打开投影后内容在实例里、基地层是空的 —— 旧写法只算基地层，
        #     于是「往墙旁边放一格 → 墙不连」（方向值一直是 none）。
        wall = TMP / "builds" / "wall_probe.schem"
        wall_state = {"Name": "minecraft:cobblestone_wall",
                      "Properties": {"east": "none", "north": "none",
                                     "south": "none", "west": "none",
                                     "up": "true", "waterlogged": "false"}}
        wv = np.zeros((8, 8, 8), dtype=np.uint16)
        wv[1, 4, 4] = 2
        S.write_structure(str(wall), wv, [AIR, STONE, wall_state],
                          (0, 0, 0), (8, 8, 8), name="wall_probe")
        st, ws = call("POST", "/api/structure/open", {"path": "builds/wall_probe.schem"})
        wsid = ws["sid"]
        st, wdt = call("POST", f"/api/structure/{wsid}/modules/detach",
                       {"markDirty": False, "record": False})
        assert st == 200 and wdt["placements"][0]["pack"] == "@self", wdt
        assert "east=none" in voxel_at(wsid, 4, 1, 4)
        st, wop = call("POST", f"/api/structure/{wsid}/ops",
                       {"ops": [{"type": "set", "x": 5, "y": 1, "z": 4,
                                  "state": "minecraft:stone"}],
                        "update": True})
        assert st == 200, wop
        assert wop["updated"] >= 1, wop          # 至少那一格墙要重算
        after = voxel_at(wsid, 4, 1, 4)
        assert "east=none" not in after, \
            f"墙应该连上旁边的石头（内容在实例里也要重算）：{after}"
        # 而且这个更新是**记在实例上**的（跟着实例走）
        st, wst = call("GET", f"/api/structure/{wsid}/modules")
        assert wst["placements"][0]["edits"] >= 1, wst
        # 基地层模式（无实例）同样要工作：新建画布 → 墙 + 石头
        st, w2 = call("POST", "/api/structure/new",
                      {"size": [8, 8, 8], "name": "wall_base"})
        st, _ = call("POST", f"/api/structure/{w2['sid']}/ops",
                     {"ops": [{"type": "set", "x": 4, "y": 1, "z": 4,
                                "state": "minecraft:cobblestone_wall"},
                               {"type": "set", "x": 5, "y": 1, "z": 4,
                                "state": "minecraft:stone"}],
                      "update": True})
        base_wall = voxel_at(w2["sid"], 4, 1, 4)
        assert "east=none" not in base_wall and "east=" in base_wall, base_wall
        print("PASS 18/18 连接状态重算看合成结果（实例里的墙也会连 + 基地层照旧）")

        print("\nALL PASS: mcstudio assembly API")
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()
        time.sleep(0.1)
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
