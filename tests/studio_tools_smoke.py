"""mcstudio × mctools：Axiom 式工具的 HTTP 端到端冒烟。

在临时仓库里起一个 server 线程，用 urllib 打接口：
工具目录 → 各类工具执行 → 结果区域/撤销 → 选区接口 → 静态资源。

Usage:  python tests/studio_tools_smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
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

TMP = Path(tempfile.mkdtemp(prefix="structworkshop_tools_"))
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
from mccore.schem_io import state_str  # noqa: E402
from mcstudio.server import App, make_handler  # noqa: E402

AIR = {"Name": "minecraft:air"}
STONE = {"Name": "minecraft:stone"}
PORT = {"host": "127.0.0.1", "port": 0}
OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))


def call(method, path, body=None):
    url = f"http://127.0.0.1:{PORT['port']}{path}"
    data, headers = None, {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            payload = r.read()
            if "json" in r.headers.get("Content-Type", ""):
                return r.status, json.loads(payload.decode("utf-8"))
            return r.status, payload
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload.decode("utf-8"))
        except Exception:                                   # noqa: BLE001
            return e.code, payload


def make_fixture():
    """一座小楼：地面 + 四面墙 + 屋顶 + 一点水（够所有工具折腾）。"""
    sx, sy, sz = 28, 24, 28
    v = np.zeros((sy, sz, sx), dtype=np.uint16)
    v[0:2, :, :] = 1
    v[2:10, 4:6, 4:24] = 1          # 四面 2 格厚的墙（可掏空）
    v[2:10, 22:24, 4:24] = 1
    v[2:10, 6:22, 4:6] = 1
    v[2:10, 6:22, 22:24] = 1
    v[10, 4:24, 4:24] = 2
    v[2:6, 12:16, 12:16] = 3        # 实心箱（掏空/平滑用）
    v[11, 26, 26] = 3               # 悬空块（平滑/重力用）
    v[1, 26, 26] = 4
    pal = [AIR, STONE, {"Name": "minecraft:andesite"},
           {"Name": "minecraft:oak_log"}, {"Name": "minecraft:water"}]
    p = TMP / "packs" / "demo" / "modules" / "rooms" / "house.schem"
    S.write_structure(str(p), v, pal, (0, 0, 0), (sx, sy, sz), name="house")
    # 一个 3×6×3 的小灯柱：盖章对象（与夹具不同，才能看出改动）
    w = np.zeros((6, 3, 3), dtype=np.uint16)
    w[0:5, 1, 1] = 1
    w[5, :, :] = 2
    S.write_structure(str(TMP / "packs" / "demo" / "modules" / "rooms" /
                          "lamp.schem"), w, pal, (0, 0, 0), (3, 6, 3),
                      name="lamp")
    # 连接状态夹具：两格墙 + 一格铁栏杆，四向/up 属性故意不写
    # （模拟 mcslice 切片 / 导模来的模块：状态是“过期”的）
    bpal = pal + [{"Name": "minecraft:polished_deepslate_wall"},
                  {"Name": "minecraft:iron_bars"}]
    b = np.zeros((4, 4, 8), dtype=np.uint16)
    b[0, 0, 0] = 5
    b[0, 0, 1] = 5
    b[0, 2, 5] = 6
    S.write_structure(str(TMP / "packs" / "demo" / "modules" / "rooms" /
                          "posts.schem"), b, bpal, (0, 0, 0), (8, 4, 4),
                      name="posts")
    # 方块面板数据（真实仓库里由 mcmaterials 生成到 skills/…/data/；这里给个小样本）
    dd = TMP / "skills" / "minecraft-material-lab" / "data"
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "block_catalog.json").write_text(json.dumps({
        "stone": {"color": {"avg": [125.5, 125.5, 125.5]}, "is_full": True,
                  "textures": ["block/stone"]},
        "oak_stairs": {"color": {"avg": [162.2, 130.8, 78.6]}, "is_full": False,
                      "textures": ["block/oak_planks"]},
        "glass_pane": {"color": {"avg": [174.0, 212.7, 218.4]}, "is_full": False,
                      "textures": ["block/glass_pane_top", "block/glass"]},
        "iron_bars": {"color": {"avg": [136.8, 139.2, 135.6]}, "is_full": False,
                     "textures": ["block/iron_bars"]},
        "oak_leaves": {"color": {"avg": [144.0, 144.0, 144.0]}, "is_full": False,
                      "textures": ["block/oak_leaves"]},
        "air": {"color": {"avg": [0, 0, 0]}, "is_full": False, "technical": True},
        "command_block": {"color": {"avg": [1, 2, 3]}, "is_full": True,
                          "technical": True},
        "gone_block": {"color": {}, "missing": True},
    }), encoding="utf-8")
    (dd / "family_index.json").write_text(json.dumps({
        "stone": ["stone"], "stairs": ["oak_stairs"],
        "glass": ["glass_pane"], "metal": ["iron_bars"],
    }), encoding="utf-8")
    return p


def main() -> int:
    make_fixture()
    LB.load_index(refresh=True)
    app = App()
    httpd = ThreadingHTTPServer((PORT["host"], 0), make_handler(app))
    PORT["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        print("== 工具目录 ==")
        st, cat = call("GET", "/api/tools")
        check("GET /api/tools", st == 200 and cat["count"] >= 20,
              f"{cat.get('count')} 个工具")
        ids = [t["id"] for g in cat["groups"] for t in g["tools"]]
        check("目录含 5 个分组", len(cat["groups"]) == 5, str([g["id"] for g in cat["groups"]]))
        check("shape/path/noise_painter 都在", all(
            k in ids for k in ("shape", "path", "noise_painter", "shatter",
                               "rock", "smooth", "stamp")))
        shape = [t for g in cat["groups"] for t in g["tools"] if t["id"] == "shape"][0]
        check("参数带类型与取值范围",
              all(("type" in p and "name" in p) for p in shape["params"]) and
              any(p.get("options") for p in shape["params"]))
        check("笔刷形状下发", "sphere" in cat["brushShapes"])

        print("\n== 打开结构 + 工具执行 ==")
        st, stt = call("POST", "/api/structure/open",
                       {"path": "packs/demo/modules/rooms/house.schem"})
        check("打开夹具", st == 200 and stt["size"] == [28, 24, 28], str(stt.get("size")))
        sid = stt["sid"]

        def fresh():
            st2, s2 = call("POST", "/api/structure/open",
                           {"path": "packs/demo/modules/rooms/house.schem"})
            assert st2 == 200, s2
            return s2["sid"]

        def tool(name, sid2=None, **body):
            b = {"tool": name, "block": "minecraft:stone", "seed": 3}
            b.update(body)
            return call("POST", f"/api/structure/{sid2 or sid}/tool", b)

        cases = [
            ("noise_painter", {"params": {"blocks": "minecraft:stone,minecraft:andesite",
                                          "scale": 6.0},
                               "sel": [0, 0, 0, 27, 12, 27]}),
            ("gradient_painter", {"params": {"blocks": "minecraft:stone,minecraft:andesite",
                                             "axis": "y"},
                                  "sel": [0, 0, 0, 27, 12, 27]}),
            ("smooth", {"params": {"mode": "stable", "strength": 2},
                        "sel": [0, 0, 0, 27, 12, 27]}),
            ("rock", {"params": {}, "sel": [0, 0, 0, 27, 12, 27]}),
            ("shatter", {"params": {"axis": "y", "width": 2},
                         "sel": [0, 0, 0, 27, 12, 27]}),
            ("distort", {"params": {"distance_x": 2, "scale": 9},
                         "sel": [0, 0, 0, 27, 12, 27]}),
            ("hollow", {"params": {"thickness": 1}, "sel": [3, 1, 3, 24, 10, 24]}),
            ("autoshade", {"params": {"shade_blocks": "minecraft:stone,minecraft:andesite"},
                           "sel": [0, 0, 0, 27, 12, 27]}),
            ("elevation", {"params": {"mode": "raise", "amount": 2},
                           "centers": [[8, 2, 8]],
                           "brush": {"shape": "sphere", "radius": 4}}),
            ("painter", {"params": {}, "centers": [[12, 3, 12], [14, 3, 14]],
                         "brush": {"shape": "sphere", "radius": 3}}),
        ]
        for name, body in cases:
            st, r = tool(name, sid2=fresh(), **body)
            n = r.get("changed") if isinstance(r, dict) else None
            check(f"工具 {name}", st == 200 and n and n > 0 and
                  r.get("palette") and r.get("stats"), f"changed={n} {r.get('stats') if isinstance(r, dict) else r}")

        st, r = tool("shape", sid2=fresh(),
                     params={"kind": "sphere", "radius": 4, "mode": "add"},
                     centers=[[14, 14, 14]])
        check("工具 shape（自带几何）", st == 200 and r["changed"] > 0, r.get("stats"))
        st, r = tool("path", sid2=fresh(),
                     params={"curve": "catenary", "radius": 2,
                             "points": [[3, 10, 3], [24, 10, 24]]})
        check("工具 path（悬链线）", st == 200 and r["changed"] > 0, r.get("stats"))
        st, r = tool("replace", sid2=fresh(),
                     params={"from": "minecraft:andesite"})
        check("工具 replace（全画布回退）", st == 200, r.get("stats"))
        st, r = tool("stamp", sid2=fresh(),
                     params={"blueprint": "lamp", "chance": 1.0,
                             "min_spacing": 7},
                     sel=[0, 0, 0, 27, 20, 27])
        check("工具 stamp（散布模块）", st == 200 and r["changed"] > 0 and
              "盖章" in r.get("stats", ""), r.get("stats"))
        st, r = tool("smooth", sid2=fresh(), params={"mode": "stable"},
                     sel=[0, 0, 0, 27, 12, 27], mask="minecraft:stone")
        check("掩码表达式生效", st == 200, r.get("stats"))
        st, r = tool("smooth", sid2=fresh(), sel=[0, 0, 0, 27, 12, 27], mask="y<")
        check("坏掩码报 400", st == 400 and "掩码" in str(r), str(r)[:80])
        st, r = tool("nope", sid2=fresh())
        check("未知工具报 400", st == 400, str(r)[:60])

        print("\n== 结果 / 撤销 / 选区 ==")
        fsid = fresh()
        st, r = tool("fill", sid2=fsid, params={}, sel=[2, 2, 2, 5, 5, 5])
        check("工具返回改动区域", st == 200 and r["bbox"] == [1, 1, 1, 6, 6, 6],
              str(r.get("bbox")))
        check("返回 region 供前端打补丁", bool(r.get("region")))
        check("工具进入撤销栈", r.get("can_undo") is True)
        st, u = call("POST", f"/api/structure/{fsid}/undo")
        check("撤销工具改动", st == 200 and u.get("bbox") == [1, 1, 1, 6, 6, 6],
              str(u.get("bbox")))
        st, red = call("POST", f"/api/structure/{fsid}/redo")
        check("重做工具改动", st == 200 and red.get("bbox") == [1, 1, 1, 6, 6, 6])

        st, sel = call("POST", f"/api/structure/{sid}/select", {"mask": "minecraft:stone"})
        check("按掩码选取（返回包围盒）",
              st == 200 and sel["count"] > 0 and len(sel["bbox"]) == 6,
              f"{sel.get('count')} 格 {sel.get('bbox')}")
        st, sel2 = call("POST", f"/api/structure/{sid}/select", {"at": [13, 3, 13]})
        check("魔棒（同类）", st == 200 and sel2["count"] > 0, str(sel2.get("count")))
        st, sel3 = call("POST", f"/api/structure/{sid}/select",
                        {"at": [13, 3, 13], "connected": True})
        check("魔棒（连通）", st == 200 and 0 < sel3["count"] <= sel2["count"],
              f"{sel3.get('count')} <= {sel2.get('count')}")
        st, sel4 = call("POST", f"/api/structure/{sid}/select", {"at": [1, 20, 1]})
        check("点空气取选 → 报错", st == 400, str(sel4)[:60])

        print("\n== 方块更新模拟（连接状态）==")
        st, bt = call("POST", "/api/structure/open",
                      {"path": "packs/demo/modules/rooms/posts.schem"})
        check("打开连接夹具", st == 200 and bt["size"] == [8, 4, 4], str(bt)[:60])
        bsid = bt["sid"]

        def saved(path):
            st2, r2 = call("POST", f"/api/structure/{bsid}/save-as",
                           {"path": path})
            assert st2 == 200, r2
            d2 = S.read_structure(str(TMP / path))

            def at(x, y, z):
                i = int(d2["voxels"][y, z, x])
                return state_str(d2["palette"][i]) if i else "minecraft:air"
            return at

        st, r = call("POST", f"/api/structure/{bsid}/ops",
                     {"ops": [{"type": "update"}]})
        check("重算整幅：有过期状态被改", st == 200 and r["updated"] >= 3,
              f"updated={r.get('updated')}")
        check("重算进撤销栈", r.get("can_undo") is True)
        at = saved("packs/demo/modules/rooms/posts_upd.schem")
        check("墙补上 up/四向", "north=" in at(0, 0, 0) and "up=" in at(0, 0, 0),
              at(0, 0, 0))
        check("两格墙互连（tall）", "east=tall" in at(0, 0, 0)
              and "west=tall" in at(1, 0, 0), at(1, 0, 0))
        check("铁栏杆补上四向", "north=" in at(5, 0, 2) and "east=" in at(5, 0, 2),
              at(5, 0, 2))
        check("一排墙不带柱子（up=false，一段墙）", "up=false" in at(0, 0, 0),
              at(0, 0, 0))

        st, r2 = call("POST", f"/api/structure/{bsid}/ops",
                      {"ops": [{"type": "update"}]})
        check("再算一次为 0 且不进历史", r2["updated"] == 0 and r2["bbox"] is None,
              f"updated={r2.get('updated')} bbox={r2.get('bbox')}")
        st, u = call("POST", f"/api/structure/{bsid}/undo")
        check("撤销重算操作", st == 200, str(u.get("bbox")))
        at = saved("packs/demo/modules/rooms/posts_undo.schem")
        check("撤销后回到无属性状态",
              at(0, 0, 0) == "minecraft:polished_deepslate_wall", at(0, 0, 0))

        st, r3 = call("POST", f"/api/structure/{bsid}/ops",
                      {"ops": [{"type": "set", "x": 2, "y": 0, "z": 0,
                                 "state": "minecraft:polished_deepslate_wall"}],
                       "update": True})
        check("放置带 update：邻居一起重算", st == 200 and r3["updated"] >= 2,
              f"updated={r3.get('updated')}")
        check("bbox 外扩 1 格容纳邻居", r3["bbox"] == [1, 0, 0, 3, 1, 1],
              str(r3["bbox"]))
        at = saved("packs/demo/modules/rooms/posts_place.schem")
        check("新墙与旧邻居互连", "east=tall" in at(1, 0, 0)
              and "west=tall" in at(2, 0, 0), at(1, 0, 0))

        st, r4 = call("POST", f"/api/structure/{bsid}/ops",
                      {"ops": [{"type": "set", "x": 6, "y": 0, "z": 3,
                                 "state": "minecraft:iron_bars"}],
                       "update": False})
        check("关 update：不动邻居", r4.get("updated") == 0, str(r4.get("updated")))
        at = saved("packs/demo/modules/rooms/posts_noupd.schem")
        check("关 update：新方块不带连接属性", at(6, 0, 3) == "minecraft:iron_bars",
              at(6, 0, 3))

        # 负方向绘制：画布原点固定在 (0,0,0)，报清楚的话（旧行为：含糊的「空选区」）
        for axis, pos in (("X", (-1, 0, 0)), ("Y", (0, -1, 0)), ("Z", (0, 0, -1))):
            st, rn = call("POST", f"/api/structure/{bsid}/ops",
                          {"ops": [{"type": "set", "x": pos[0], "y": pos[1],
                                     "z": pos[2], "state": "minecraft:stone"}]})
            check(f"负坐标绘制 {pos} 报 400 且说明不能向 −{axis} 扩",
                  st == 400 and "0,0,0" in str(rn) and f"−{axis}" in str(rn),
                  str(rn)[:70])

        st, r5 = tool("replace", sid2=fresh(),
                      params={"from": "minecraft:stone"},
                      sel=[0, 0, 0, 27, 2, 27],
                      block="minecraft:iron_bars", update=True)
        joined = " ".join(r5.get("palette") or [])
        check("工具应用带 update 也补连接",
              st == 200 and r5.get("updated", 0) > 0 and "iron_bars[east=" in joined,
              f"updated={r5.get('updated')}")
        check("工具提示里说明重算了连接",
              any("连接状态" in n for n in (r5.get("notes") or [])),
              str(r5.get("notes"))[:80])

        print("\n== 方块面板 / 朝向（新接口）==")
        st, pk = call("GET", "/api/blocks/picker")
        blocks = pk.get("blocks") or []
        check("GET /api/blocks/picker（不用先开结构）",
              st == 200 and pk.get("available") and len(blocks) == 5,
              f"{len(blocks)} 个方块 / {len(pk.get('families') or [])} 家族")
        # 行格式：``[名, 色, is_full, [分组下标], 调色贴图, tint, 中文别名]``
        #（第 7 项（中文搜索别名）后加；调色贴图现在可能是 ``entity/...``——旗帜/头颅用游戏里真那张）
        ok_shape = all(len(r) in (6, 7) and r[1].startswith("#") and len(r[1]) == 7
                       and r[2] in (0, 1) and isinstance(r[3], list)
                       and (r[4] is None or str(r[4]).startswith(("block/", "entity/")))
                       for r in blocks)
        check("面板数据：颜色 + 形状 + 家族 + 贴图/染色字段", ok_shape, str(blocks))
        by = {r[0]: r for r in blocks}
        check("贴图挑得对（同名/同前缀/最近色）",
              by["stone"][4] == "block/stone"
              and by["oak_stairs"][4] == "block/oak_planks"
              and str(by["glass_pane"][4]).startswith("block/glass_pane"),
              str({k: by[k][4] for k in ("stone", "oak_stairs", "glass_pane")}))
        check("群系染色块给出 tint（树叶非空、石头为空）",
              by["oak_leaves"][5] and str(by["oak_leaves"][5]).startswith("#")
              and by["stone"][5] is None,
              str({k: by[k][5] for k in ("oak_leaves", "stone")}))
        names = {r[0] for r in blocks}
        check("技术/缺贴图方块已排除、完整方块标记对",
              names == {"stone", "oak_stairs", "glass_pane", "iron_bars", "oak_leaves"}
              and by["stone"][2] == 1 and by["oak_stairs"][2] == 0, str(sorted(names)))
        check("分类芯片定义齐全（含拆开的半砖/楼梯）",
              len(pk.get("groups") or []) >= 12
              and all(len(g) == 3 for g in pk["groups"])
              and {"slab", "stair"} <= {g[0] for g in pk["groups"]}
              and {"半砖", "楼梯"} <= {g[1] for g in pk["groups"]},
              str([(g[0], g[1]) for g in (pk.get("groups") or [])])[:120])

        print("\n== 方块搜索：精确优先（点谁就是谁）==")
        stat = {}
        for q in ("stone", "glass", "sand", "stone_slab", "deepslate"):
            st, sr = call("GET", f"/api/blocks?version={pk.get('version') or '26.2'}"
                                 f"&q={q}&limit=60")
            names = [b["name"] for b in (sr.get("blocks") or [])]
            stat[q] = names[0] if names else None
        check("q=stone 的首个结果就是 stone（旧 bug：blackstone / 列表截断）",
              stat.get("stone") == "stone" and stat.get("glass") == "glass"
              and stat.get("sand") == "sand" and stat.get("deepslate") == "deepslate",
              str(stat))
        fam_used = {i for r in blocks for i in r[3]}
        check("家族索引能对上",
              bool(fam_used) and max(fam_used) < len(pk["families"]),
              f"用到的家族下标 {sorted(fam_used)}")

        print("\n== 新建空画布（不开文件也能干活）==")
        st, nw = call("POST", "/api/structure/new", {"size": [16, 16, 16],
                                                       "name": "新建测试"})
        check("POST /api/structure/new 默认 16³ 空画布",
              st == 200 and nw["size"] == [16, 16, 16] and nw["blocks"] == 0
              and nw["path"] is None and nw["name"] == "新建测试",
              f"{nw.get('size')} blocks={nw.get('blocks')} path={nw.get('path')}")
        st, r = call("POST", f"/api/structure/{nw['sid']}/ops",
                     {"ops": [{"type": "set", "x": 3, "y": 4, "z": 5,
                                "state": "minecraft:stone"}], "update": True})
        check("空画布上能直接画（放置/撤销链路可用）",
              st == 200 and r["bbox"] == [2, 3, 4, 4, 5, 6], str(r.get("bbox")))
        check("空画布调色板从 air 起步",
              r["palette"][0] == "minecraft:air" and "minecraft:stone" in r["palette"],
              str(r["palette"][:3]))
        st, r2 = call("POST", f"/api/structure/{nw['sid']}/save", {})
        check("空画布保存时报“没路径”而不是偷偷写盘", st == 400, str(r2)[:70])
        for bad in ([0, 8, 8], [9999, 8, 8], [8, 8]):
            st, e = call("POST", "/api/structure/new", {"size": bad})
            check(f"新建尺寸校验 {bad}", st == 400, str(e)[:60])
        call("POST", f"/api/structure/{nw['sid']}/close", {})

        st, js2 = call("GET", "/static/editor.js")
        check("编辑器：色块面板 + 朝向自动定向代码已下发",
              st == 200 and b"blk-grid" in js2 and b"autoProps" in js2
              and b"stateForPlace" in js2 and b"MASK_PRESETS" in js2)
        st, vjs2 = call("GET", "/static/viewer3d.js")
        check("viewer3d 提供 viewDir（跟视角定朝向）", b"viewDir" in vjs2)
        st, html2 = call("GET", "/")
        check("页面：方块面板 + 朝向 + 掩码下拉 + 新建按钮已就位",
              b'id="blk-grid"' in html2 and b'id="blk-fams"' in html2
              and b'id="face-ctl"' in html2 and b'id="ax-mask-drop"' in html2
              and b'id="btn-new-canvas"' in html2
              and b"ax-mask-list" not in html2)

        print("\n== 保存后仍然可读 ==")
        st, r = call("POST", f"/api/structure/{sid}/save-as",
                     {"path": "packs/demo/modules/rooms/house_tools.schem"})
        check("另存 .schem", st == 200 and (TMP / r["path"]).is_file(), str(r)[:80])
        d = S.read_structure(str(TMP / "packs/demo/modules/rooms/house_tools.schem"))
        v = d["voxels"]
        check("产物索引合法", int(v.max()) < len(d["palette"]),
              f"palette={len(d['palette'])} max={int(v.max())}")
        check("产物仍有方块", int((v != 0).sum()) > 0, str(int((v != 0).sum())))

        print("\n== 静态资源 ==")
        st, js = call("GET", "/static/editor.js")
        check("editor.js 可下发", st == 200 and b"axApply" in js)
        st, html = call("GET", "/")
        check("页面含工具面板", b'id="ax-tools"' in html and b"ax-brush-radius" in html)
        st, css = call("GET", "/static/styles.css")
        check("样式含 ax- 规则", b".ax-tool" in css and b".ax-params" in css)
        st, vjs = call("GET", "/static/viewer3d.js")
        check("viewer3d 有叠加层", b"setOverlay" in vjs and b"_drawOverlay" in vjs)
    finally:
        httpd.shutdown()
        httpd.server_close()
        time.sleep(0.1)
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\n{'ALL PASS' if not FAIL else 'FAILED'}  "
          f"({len(OK)} passed, {len(FAIL)} failed)")
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
