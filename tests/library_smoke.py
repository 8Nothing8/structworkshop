"""模块库写操作冒烟：标签增删改、元数据、裁剪、导入、删除备份。

在临时仓库（STRUCTWORKSHOP_ROOT）里跑，不触碰真实 packs/。

Usage:  python tests/library_smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

TMP = Path(tempfile.mkdtemp(prefix="structworkshop_lib_"))
os.environ["STRUCTWORKSHOP_ROOT"] = str(TMP)
(TMP / "pyproject.toml").write_text("", encoding="utf-8")
(TMP / "packages").mkdir()
(TMP / "packs" / "demo" / "modules" / "rooms").mkdir(parents=True)
(TMP / "packs" / "demo" / "pack.json").write_text(
    json.dumps({"id": "demo", "name": "demo", "version": "1.0.0"}), encoding="utf-8")

import numpy as np  # noqa: E402

from mccore import library as LB  # noqa: E402
from mccore import structure_io as S  # noqa: E402

AIR = {"Name": "minecraft:air"}
STONE = {"Name": "minecraft:stone"}


def make_module(mid: str, size=(5, 3, 4), pad=0, tags=None) -> Path:
    sx, sy, sz = size
    v = np.zeros((sy, sz, sx), dtype=np.uint16)
    v[:, :, :] = 1
    if pad:  # 外围留空气，用于 crop 测试（x/z 各留 pad）
        v[:, :pad, :] = 0
        v[:, -pad:, :] = 0
        v[:, :, :pad] = 0
        v[:, :, -pad:] = 0
    p = TMP / "packs" / "demo" / "modules" / "rooms" / f"{mid}.schem"
    S.write_structure(str(p), v, [AIR, STONE], (0, 0, 0), size, name=mid)
    spec = {"id": mid, "pack": "demo", "category": "rooms", "version": 1,
            "description": f"{mid} 描述", "tags": list(tags or []),
            "grid": {"size": list(size), "position": [0, 0, 0]},
            "axis": "x", "flip": True, "ports": []}
    LB.save_spec(p, spec)
    return p


def main() -> int:
    try:
        a = make_module("box_a", tags=["room", "modern"])
        make_module("box_b", tags=["room", "office"])
        LB.load_index(refresh=True)

        # 1) 标签计数 + 增删
        counts = LB.tag_counts()
        assert counts.get("room") == 2, counts
        r = LB.set_tags(["box_a"], add=["glass", "room"])  # room 已存在
        assert r["updated"] == ["box_a"], r
        counts = LB.tag_counts()
        assert counts["glass"] == 1 and counts["room"] == 2, counts
        r = LB.set_tags(["box_a"], remove=["glass"])
        assert "glass" not in LB.tag_counts(), LB.tag_counts()
        print("PASS 1/11  标签增删 + 计数")

        # 2) 重命名 / 合并
        r = LB.rename_tag("office", "workplace")
        assert r["affected"] == 1, r
        assert LB.tag_counts().get("workplace") == 1
        r = LB.merge_tags("workplace", "room")
        assert r["affected"] == 1, r
        assert LB.tag_counts().get("workplace") is None
        assert LB.tag_counts()["room"] == 2
        print("PASS 2/11  标签重命名 + 合并")

        # 3) 元数据
        r = LB.set_meta("box_a", description="新描述", category="rooms")
        assert "description" in r["changed"]
        spec = json.loads((TMP / "packs" / "demo" / "modules" / "rooms" /
                           "box_a.module.json").read_text(encoding="utf-8"))
        assert spec["description"] == "新描述"
        print("PASS 3/11  set_meta 写 sidecar")

        # 4) 裁剪空气边界
        make_module("padded", size=(6, 3, 5), pad=1)
        LB.load_index(refresh=True)
        r = LB.crop_module("padded")
        assert r["changed"] and r["size"] == [4, 3, 3], r
        d = S.read_structure(str(TMP / "packs" / "demo" / "modules" / "rooms" /
                                 "padded.schem"))
        assert tuple(d["size"]) == (4, 3, 3), d["size"]
        print("PASS 4/11  crop_module 裁空气 + 更新 grid")

        # 5) 导入（去重、id 冲突加后缀、统一标签）
        src = TMP / "incoming"
        src.mkdir()
        S.write_structure(str(src / "box_a.schem"), np.ones((2, 2, 2), np.uint16),
                          [AIR, STONE], (0, 0, 0), (2, 2, 2), name="box_a")
        rep = LB.import_files([src / "box_a.schem"], "demo", category="imported",
                              tags=["imported", "imported"], description="批量")
        assert len(rep["imported"]) == 1, rep
        new_id = rep["imported"][0]["id"]
        assert new_id == "box_a_2", rep
        ent = LB.load_index()["modules"][new_id]
        assert ent["tags"] == ["imported"], ent["tags"]
        print("PASS 5/11  import_files 去重 + 标签")

        # 6) 删除 -> 备份
        old = str(a)
        r = LB.delete_module("box_a")
        assert not Path(old).exists()
        assert Path(r["backup"]).is_file(), r
        assert "box_a" not in LB.load_index()["modules"]
        print("PASS 6/11  delete_module 备份到 .cache/backups")

        # 7) 复制 / 转移到别的资产包（结构 + spec + 预览图 + 两包 catalog/pack.json）
        (TMP / "packs" / "demo2" / "modules" / "rooms").mkdir(parents=True)
        (TMP / "packs" / "demo2" / "pack.json").write_text(
            json.dumps({"id": "demo2", "name": "demo2", "version": "1.0.0"}),
            encoding="utf-8")
        src_id = next(iter(LB.load_index()["modules"]))       # 剩下的那个模块
        # 造一张预览图，验证预览图跟着走
        prev = TMP / "packs" / "demo" / "previews"
        prev.mkdir(exist_ok=True)
        (prev / f"{src_id}.png").write_bytes(b"\x89PNG\r\n\x1a\n")   # 内容不重要
        (prev / f"{src_id}_iso.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        cp = LB.transfer_module(src_id, "demo2", mode="copy", new_id=src_id + "_copy")
        assert cp["id"] == src_id + "_copy" and cp["pack"] == "demo2", cp
        assert (TMP / cp["path"]).is_file(), cp
        assert cp["previews"] == [f"{src_id}_copy.png", f"{src_id}_copy_iso.png"], cp
        cp_spec = json.loads((TMP / cp["path"].replace(".schem", ".module.json"))
                             .read_text(encoding="utf-8"))
        assert cp_spec["pack"] == "demo2" and "复制自 demo/" in cp_spec["notes"], cp_spec
        assert LB.load_index()["modules"].get(src_id)["pack"] == "demo"   # 源不受影响
        print("PASS 7/11  transfer_module copy（spec/预览图/新包归位）")

        # 8) 目标包重名（id 全局唯一，不允许两个包同一个 id）→ 自动加 _2
        cp2 = LB.transfer_module(src_id, "demo2", mode="copy")
        assert cp2["id"] == f"{src_id}_2" and cp2["renamed_from"] == src_id, cp2
        print("PASS 8/11  transfer_module 重名自动 _2")

        # 9) 转移：**保留 id**，源模块备份后消失，两边 catalog/pack.json 都重建
        mv = LB.transfer_module(src_id, "demo2", mode="move")
        assert mv["id"] == src_id and mv["renamed_from"] is None and mv["deleted"], mv
        idx = LB.load_index()["modules"]
        assert idx[src_id]["pack"] == "demo2", idx[src_id]
        assert (TMP / mv["path"]).is_file()
        assert Path(mv["deleted"]["backup"]).is_file(), mv["deleted"]
        for pack in ("demo", "demo2"):
            assert (TMP / "packs" / pack / "catalog.md").is_file()
        man2 = json.loads((TMP / "packs" / "demo2" / "pack.json").read_text(encoding="utf-8"))
        man1 = json.loads((TMP / "packs" / "demo" / "pack.json").read_text(encoding="utf-8"))
        assert src_id in (man2.get("provides", {}).get("modules") or []), man2
        assert src_id not in (man1.get("provides", {}).get("modules") or []), man1
        print("PASS 9/11  transfer_module move（保留 id + 备份 + 两包重建）")

        # 10) grid 自愈：spec 里画布尺寸写错（例如改过画布又手改了 spec），
        #     扫索引 / 写 spec 时都要校正回来（否则模块库报 grid.size ≠ 实际）
        mid = next(iter(LB.load_index(refresh=True)["modules"]))
        mpath = LB.module_path(mid)
        spec = LB.load_spec(mpath)
        spec["grid"] = {"size": [1, 1, 1], "position": [0, 0, 0]}
        LB.save_spec(mpath, spec)
        assert LB.validate_spec(LB.load_spec(mpath), tuple(__import__(
            "mccore.structure_io", fromlist=["x"]).read_structure(str(mpath))["size"]))
        LB.module_scan(quiet=True)
        fixed = LB.load_spec(mpath)["grid"]["size"]
        real = list(__import__("mccore.structure_io", fromlist=["x"]).read_structure(str(mpath))["size"])
        assert fixed == real, (fixed, real)
        print("PASS 10/11  扫索引会把 spec 的 grid 校正到实际尺寸（自愈）")
        # 11) 任何一次写 spec（PATCH 描述/标签/端口）也顺手校正
        spec = LB.load_spec(mpath)
        spec["grid"] = {"size": [1, 1, 1], "position": [0, 0, 0]}
        LB.save_spec(mpath, spec)
        out = LB.set_meta(mid, notes="grid heal probe")
        assert "grid" in out["changed"], out
        assert LB.load_spec(mpath)["grid"]["size"] == real, LB.load_spec(mpath)["grid"]
        print("PASS 11/11  写 spec（改描述/标签/端口）也会顺手校正 grid")

        # 12) 接口形状：矩形（两点）/ 圆形（圆心 + 直径）——缺省仍是矩形，
        #     圆形校验前先化成外接矩形（与引擎同一套换算）
        from mccore.module_lib import port_bbox, port_from_bbox
        rect = {"id": "r1", "type": "passage", "face": "west",
                "origin": [1, 1], "size": [3, 3]}
        circ = {"id": "c1", "type": "door", "face": "west", "shape": "circle",
                "origin": [2, 2], "size": [3, 3]}
        SZ8 = (8, 8, 8)
        assert LB.validate_spec({"grid": {"size": list(SZ8)}, "ports": [rect, circ]},
                                SZ8) == []
        assert port_bbox(rect) == ([1, 1], [3, 3])
        assert port_bbox(circ) == ([1, 1], [3, 3]), port_bbox(circ)   # 圆心 (2,2)、d=3
        assert port_bbox(port_from_bbox(dict(circ), [1, 1], [3, 3])) == ([1, 1], [3, 3])
        bad_shape = dict(circ, shape="blob")
        assert LB.validate_spec({"grid": {"size": list(SZ8)}, "ports": [bad_shape]},
                                SZ8), "非法 shape 应该报错"
        thin = dict(circ, size=[3, 4])
        assert LB.validate_spec({"grid": {"size": list(SZ8)}, "ports": [thin]},
                                SZ8), "圆形直径不相等应该报错"
        # 圆形溢出面边界也要拦（圆心贴边 + 直径大）
        edge = dict(circ, origin=[0, 1], size=[5, 5])
        assert LB.validate_spec({"grid": {"size": list(SZ8)}, "ports": [edge]},
                                SZ8), "圆形越界应该报错"
        print("PASS 12/12  接口形状：矩形/圆形（圆心 + 直径）校验与换算")

        # 13) 标签容错：整段文字（HTTP/CLI 那种）按 ; , 换行拆开，不再逐字符拆
        assert LB.norm_tags("imported; facade, roof") == ["imported", "facade", "roof"]
        assert LB.norm_tags("a;b") == ["a", "b"]
        assert LB.norm_tags(["x", "x", " y "]) == ["x", "y"]
        print("PASS 13/13  标签：整段文字的 ; / , / 换行切分 + 去重", )

        print("\nALL PASS: mccore.library")
        return 0
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
