"""方块实体（BlockEntities）保留冒烟。

修复的旧行为：读结构时不读 ``BlockEntities``、写盘时传 ``None`` —— 打开再保存
一次，箱子内容 / 告示牌文字 / 刷怪笼数据就被静默丢掉了（真数据丢失）。

覆盖：
1. ``write_schem`` 能写方块实体；``read_structure`` 能读回来（保留原始 NBT）。
2. ``StructureSession`` 打开 → 保存：方块实体原样写回（含 Items 这类渲染用不到的字段）。
3. 擦掉那一格 → 保存时不会留下"悬空"方块实体。
4. 画布框裁掉的区域里的方块实体不写进文件。
5. ``GET /api/structure/<sid>/blockentities``：给渲染用的子集（旗帜图案 / 告示牌颜色）。
6. ``.litematic`` 往返也保留 TileEntities。

Usage:  python tests/blockentity_smoke.py
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
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

TMP = Path(tempfile.mkdtemp(prefix="structworkshop_be_"))
os.environ["STRUCTWORKSHOP_ROOT"] = str(TMP)
(TMP / "pyproject.toml").write_text("", encoding="utf-8")
(TMP / "packages").mkdir()
(TMP / "packs" / "demo" / "modules").mkdir(parents=True)
(TMP / "packs" / "demo" / "pack.json").write_text(
    json.dumps({"id": "demo", "name": "demo", "version": "1.0.0"}), encoding="utf-8")
(TMP / "builds").mkdir()

import numpy as np  # noqa: E402
from nbt.nbt import (TAG_Byte, TAG_Compound, TAG_Int, TAG_List,  # noqa: E402
                     TAG_String)

from mccore import structure_io as S  # noqa: E402
from mcstudio.server import App, make_handler  # noqa: E402
from mcstudio.session import StructureSession  # noqa: E402

AIR = {"Name": "minecraft:air"}
CHEST = {"Name": "minecraft:chest",
         "Properties": {"facing": "north", "type": "single", "waterlogged": "false"}}
BANNER = {"Name": "minecraft:white_banner", "Properties": {"rotation": "0"}}
STONE = {"Name": "minecraft:stone"}
OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))


def port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(method, path, body=None, port_=None):
    url = f"http://127.0.0.1:{port_}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:  # noqa: F821
        return e.code, json.loads(e.read().decode() or "{}")


def chest_be(x: int, y: int, z: int, *, items: int = 3,
             name: str = "minecraft:chest") -> TAG_Compound:
    be = TAG_Compound()
    be.tags.append(TAG_String(name="id", value=name))
    pos = TAG_List(name="Pos")
    pos.tagID = 3
    for v in (x, y, z):
        pos.tags.append(TAG_Int(value=v))
    be.tags.append(pos)
    if items:
        lst = TAG_List(name="Items")
        lst.tagID = 10
        for i in range(items):
            it = TAG_Compound()
            it.tags.append(TAG_String(name="id", value="minecraft:diamond"))
            it.tags.append(TAG_Byte(name="Count", value=3 + i))
            it.tags.append(TAG_Byte(name="Slot", value=i))
            lst.tags.append(it)
        be.tags.append(lst)
    return be


def banner_be(x: int, y: int, z: int) -> TAG_Compound:
    be = TAG_Compound()
    be.tags.append(TAG_String(name="id", value="minecraft:banner"))
    pos = TAG_List(name="Pos")
    pos.tagID = 3
    for v in (x, y, z):
        pos.tags.append(TAG_Int(value=v))
    be.tags.append(pos)
    be.tags.append(TAG_String(name="Color", value="white"))
    pats = TAG_List(name="patterns")
    pats.tagID = 10
    pat = TAG_Compound()
    pat.tags.append(TAG_String(name="color", value="red"))
    pat.tags.append(TAG_String(name="pattern", value="stripe_top"))
    pats.tags.append(pat)
    be.tags.append(pats)
    return be


def world() -> np.ndarray:
    """6×4×4：两张箱子 + 一面旗（y=0 一层石头地板）。"""
    v = np.zeros((4, 4, 6), dtype=np.uint16)      # (y, z, x)
    v[0, :, :] = 1                                 # stone
    v[1, 1, 1] = 2                                 # chest
    v[1, 1, 3] = 2
    v[2, 2, 2] = 3                                 # white_banner
    return v


def main() -> int:
    pal = [AIR, STONE, CHEST, BANNER]
    src = TMP / "builds" / "be.schem"
    S.write_structure(str(src), world(), pal, (0, 0, 0), (6, 4, 4), name="be",
                      tile_entities=[chest_be(1, 1, 1, items=3),
                                     chest_be(3, 1, 1, items=0),
                                     banner_be(2, 2, 2)])

    print("\n== 1. 读写往返（保留原始 NBT） ==")
    d = S.read_structure(str(src))
    bes = d.get("block_entities") or []
    check("read_structure 带回 block_entities", len(bes) == 3, f"n={len(bes)}")
    chest = [e for e in bes if e["pos"] == (1, 1, 1)]
    check("坐标与 id 正确",
          bool(chest) and chest[0]["tag"]["id"].value == "minecraft:chest"
          and tuple(chest[0]["pos"]) == (1, 1, 1),
          str(chest[:1])[:90])
    items = chest[0]["tag"]["Items"] if chest else None
    check("箱子里的 Items 原样保留（3 组）",
          items is not None and len(items.tags) == 3, str(items)[:80])
    check("旗帜图案 patterns 保留",
          any(e["pos"] == (2, 2, 2)
              and "patterns" in e["tag"]
              and len(e["tag"]["patterns"].tags) == 1 for e in bes))

    print("\n== 2. 会话保存：方块实体写回去 ==")
    dst = TMP / "builds" / "be_saved.schem"
    s = StructureSession("t1", src, d)
    s.save(dst)
    d2 = S.read_structure(str(dst))
    bes2 = {tuple(e["pos"]): e for e in (d2.get("block_entities") or [])}
    check("保存后仍有 3 个方块实体", len(bes2) == 3, f"n={len(bes2)}")
    it2 = bes2.get((1, 1, 1), {}).get("tag", {}).get("Items")
    check("箱子内容逐字段写回（Items=3）",
          it2 is not None and len(it2.tags) == 3, str(it2)[:80])
    check("payload() 报方块实体数量", s.payload().get("block_entities") == 3,
          str(s.payload().get("block_entities")))

    print("\n== 3. 擦掉那格 → 不留悬空方块实体 ==")
    s2 = StructureSession("t2", src, S.read_structure(str(src)))
    s2.apply_ops([{"type": "erase", "from": [1, 1, 1], "to": [1, 1, 1]}])
    dst2 = TMP / "builds" / "be_erased.schem"
    s2.save(dst2)
    bes3 = S.read_structure(str(dst2)).get("block_entities") or []
    check("被擦掉的箱子不再有方块实体",
          all(tuple(e["pos"]) != (1, 1, 1) for e in bes3) and len(bes3) == 2,
          f"left={[tuple(e['pos']) for e in bes3]}")

    print("\n== 4. 画布框裁掉的区域不写方块实体 ==")
    s3 = StructureSession("t3", src, S.read_structure(str(src)))
    s3.frame = [3, 4, 4]                      # 只留 x<3 → x=3 的箱子被裁掉
    dst3 = TMP / "builds" / "be_framed.schem"
    s3.save(dst3)
    bes4 = S.read_structure(str(dst3)).get("block_entities") or []
    check("框外方块实体不写进文件", all(tuple(e["pos"])[0] < 3 for e in bes4)
          and len(bes4) == 2, f"left={[tuple(e['pos']) for e in bes4]}")

    print("\n== 5. /api/structure/<sid>/blockentities（渲染用子集） ==")
    app = App()
    p = port()
    httpd = ThreadingHTTPServer(("127.0.0.1", p), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        st, r = call("POST", "/api/structure/open", {"path": "builds/be.schem"}, p)
        sid = r["sid"]
        st, r = call("GET", f"/api/structure/{sid}/blockentities", None, p)
        rows = r.get("block_entities") or []
        check("接口返回 3 条 + total", st == 200 and r.get("total") == 3
              and len(rows) == 3, str(r)[:90])
        banner = [x for x in rows if x[2] == 2]
        check("旗帜图案在 JSON 里（patterns.red=stripe_top）",
              bool(banner) and banner[0][4].get("patterns", [{}])[0].get("pattern")
              == "stripe_top", str(banner)[:140])
        check("渲染子集不带物品清单（响应不含 Items 字段）",
              all("Items" not in (x[4] or {}) for x in rows))
        check("payload 里带方块实体计数", r.get("total") == 3)
    finally:
        httpd.shutdown()
        httpd.server_close()

    print("\n== 6. .litematic 往返 ==")
    lit = TMP / "builds" / "be.litematic"
    S.write_structure(str(lit), world(), pal, (0, 0, 0), (6, 4, 4),
                      name="be", tile_entities=[chest_be(1, 1, 1)])
    bes5 = S.read_structure(str(lit)).get("block_entities") or []
    check("litematic 方块实体往返", len(bes5) == 1
          and tuple(bes5[0]["pos"]) == (1, 1, 1)
          and bes5[0]["tag"]["id"].value == "minecraft:chest",
          str(bes5)[:90])

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAIL else 'FAILED'}  "
          f"({len(OK)} passed, {len(FAIL)} failed)")
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
