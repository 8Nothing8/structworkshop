"""方块更新模拟（连接状态）冒烟：mckit.update + 渲染/预览接线 + 编辑器 UI 接线。

覆盖：
1. ``mckit.update``：墙/栅栏/铁栏杆/玻璃板/楼梯的邻居规则、幂等、调色板去重、
   外扩 margin、越界当空气、保留 waterlogged/half/facing 等非连接属性。
2. ``mcrender.cli --update-states``：渲染时补连接且**不改文件**。
3. ``mccore.library.render_previews``：预览图渲染命令带上 ``--update-states``。
4. 编辑器接线（静态）：开关只在会改方块的笔刷工具下显示、ops 带 update 标记。

Usage:  python tests/blockstate_smoke.py
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

TMP = Path(tempfile.mkdtemp(prefix="structworkshop_blockstate_"))
os.environ["STRUCTWORKSHOP_ROOT"] = str(TMP)
(TMP / "pyproject.toml").write_text("", encoding="utf-8")
(TMP / "packages").mkdir()
(TMP / "packs" / "demo" / "modules").mkdir(parents=True)
(TMP / "packs" / "demo" / "pack.json").write_text(
    json.dumps({"id": "demo", "name": "demo", "version": "1.0.0"}),
    encoding="utf-8")

import numpy as np  # noqa: E402

from mccore import library as LB  # noqa: E402
from mccore import structure_io as S  # noqa: E402
from mccore.schem_io import state_str  # noqa: E402
from mckit import connect as C  # noqa: E402
from mckit import update as UPD  # noqa: E402

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))


AIR = {"Name": "minecraft:air"}
STONE = {"Name": "minecraft:stone"}
WALL = {"Name": "minecraft:polished_deepslate_wall"}
FLOWER_POT = {"Name": "minecraft:flower_pot"}
TORCH = {"Name": "minecraft:torch"}
FENCE = {"Name": "minecraft:oak_fence"}
BARS = {"Name": "minecraft:iron_bars"}
PANE = {"Name": "minecraft:glass_pane"}
GATE = {"Name": "minecraft:oak_fence_gate"}


def stair(facing="east", half="bottom", shape="straight", water="false"):
    return {"Name": "minecraft:oak_stairs", "Properties": {
        "facing": facing, "half": half, "shape": shape, "waterlogged": water}}


def vol(*cells, size=(4, 4, 8), pal=None):
    """按 ``(name, x, y, z)`` 建个体素；调色板自动去重。"""
    p = [dict(AIR)] if pal is None else [dict(e) for e in pal]
    idx = {}

    def i_of(entry):
        key = state_str(entry)
        if key not in idx:
            idx[key] = len(p)
            p.append(dict(entry))
        return idx[key]

    v = np.zeros((size[1], size[2], size[0]), dtype=np.uint16)
    for name, x, y, z in cells:
        v[y, z, x] = i_of(name)
    return v, p


def state_at(v, p, x, y, z):
    i = int(v[y, z, x])
    return state_str(p[i]) if i else "minecraft:air"


def props_at(v, p, x, y, z):
    i = int(v[y, z, x])
    return dict(p[i].get("Properties") or {}) if i else {}


def main() -> int:
    print("== 家族分类 ==")
    fams = {
        "polished_deepslate_wall": "wall",
        "minecraft:mud_brick_wall": "wall",
        "oak_fence": "fence",
        "minecraft:iron_bars": "pane",
        "waxed_copper_bars": "pane",
        "glass_pane": "pane",
        "oak_stairs": "stairs",
        "oak_fence_gate": None,          # 栅栏门不模拟 in_wall
        "stone": None, "wall_torch": None,
    }
    bad = [k for k, want in fams.items() if UPD.family(k) != want]
    check("family 分类", not bad, str([(k, UPD.family(k)) for k in bad]))
    check("命名空间无关", UPD.family("minecraft:oak_wall") == UPD.family("oak_wall"))
    check("树叶不算固体（栅栏不连）", not C.is_full_solid("oak_leaves"))

    print("\n== 墙：四向 + up ==")
    v, p = vol((WALL, 0, 0, 0), (WALL, 1, 0, 0), (WALL, 2, 0, 0))
    rep = UPD.update_volume(v, p, margin=0)
    check("一排墙：内部互连 tall", props_at(v, p, 1, 0, 0) == {
        "north": "none", "south": "none", "west": "tall", "east": "tall",
        "up": "false"}, str(props_at(v, p, 1, 0, 0)))
    check("一排墙：端头只连一侧", props_at(v, p, 0, 0, 0).get("east") == "tall"
          and props_at(v, p, 0, 0, 0).get("west") == "none")
    check("连成一段的墙不带柱子（up=false）",
          all(props_at(v, p, x, 0, 0)["up"] == "false" for x in range(3)))
    v, p = vol((WALL, 3, 0, 3))
    UPD.update_volume(v, p, margin=0)
    check("孤立的墙有柱子（up=true）", props_at(v, p, 3, 0, 3)["up"] == "true")
    v, p = vol((WALL, 0, 0, 0), (STONE, 0, 1, 0))
    UPD.update_volume(v, p, margin=0)
    check("上方有东西→立柱子", props_at(v, p, 0, 0, 0)["up"] == "true")
    # 上方是**不完整方块但 shape 盖住中心**（花盆/压力板/火把/栅栏/半砖）——原版也立柱。
    # 旧实现只看“整方块/墙” → 算成 up=false：没柱、臂的内侧面本来就不存在
    #（template_wall_side_tall 只有 down/up/north/west/east）→ 看着像“高臂没渲染”。
    v, p = vol((WALL, 0, 0, 0), (FLOWER_POT, 0, 1, 0))
    UPD.update_volume(v, p, margin=0)
    check("上方是花盆（盖住中心 2×2）→ 也立柱子",
          props_at(v, p, 0, 0, 0)["up"] == "true", str(props_at(v, p, 0, 0, 0)))
    v, p = vol((WALL, 0, 0, 0), (TORCH, 0, 1, 0))
    UPD.update_volume(v, p, margin=0)
    check("上方是火把 → 立柱子", props_at(v, p, 0, 0, 0)["up"] == "true")
    # 对边都是 tall（一段直墙）→ 顶面已经被抬起来，即使上面压着方块也不立柱（原版 unless）
    v, p = vol((WALL, 0, 0, 0), (WALL, 1, 0, 0), (WALL, 2, 0, 0), (STONE, 1, 1, 0))
    UPD.update_volume(v, p, margin=0)
    check("直墙中段即使上方压着石头也不立柱（对边都 tall）",
          props_at(v, p, 1, 0, 0)["up"] == "false", str(props_at(v, p, 1, 0, 0)))
    v, p = vol((WALL, 0, 0, 0), (WALL, 0, 1, 0))
    UPD.update_volume(v, p, margin=0)
    check("叠墙（下面那根跟着上面的柱）",
          props_at(v, p, 0, 0, 0)["up"] == "true" and props_at(v, p, 0, 1, 0)["up"] == "true",
          str(props_at(v, p, 0, 0, 0)))
    v, p = vol((WALL, 0, 0, 0), (FENCE, 1, 0, 0), (BARS, 0, 0, 1))
    UPD.update_volume(v, p, margin=0)
    check("邻居是栅栏/栏杆 → low", props_at(v, p, 0, 0, 0)["east"] == "low"
          and props_at(v, p, 0, 0, 0)["south"] == "low",
          str(props_at(v, p, 0, 0, 0)))

    print("\n== 栅栏 / 铁栏杆 / 玻璃板 ==")
    v, p = vol((FENCE, 0, 0, 0), (FENCE, 1, 0, 0), (GATE, 0, 0, 1),
               (PANE, 2, 0, 0))
    UPD.update_volume(v, p, margin=0)
    f0 = props_at(v, p, 0, 0, 0)
    check("栅栏连同类/栅栏门，不连玻璃板",
          f0["east"] == "true" and f0["south"] == "true" and f0["west"] == "false",
          str(f0))
    check("栅栏不连玻璃板（西侧空、东侧栅栏）",
          props_at(v, p, 2, 0, 0)["west"] == "false", str(props_at(v, p, 2, 0, 0)))
    v, p = vol((BARS, 0, 0, 0), (PANE, 1, 0, 0), (STONE, 0, 0, 1))
    UPD.update_volume(v, p, margin=0)
    check("铁栏杆连玻璃板与固体",
          props_at(v, p, 0, 0, 0)["east"] == "true"
          and props_at(v, p, 0, 0, 0)["south"] == "true",
          str(props_at(v, p, 0, 0, 0)))
    check("玻璃板连铁栏杆", props_at(v, p, 1, 0, 0)["west"] == "true")

    print("\n== 楼梯：只改 shape，不动 facing ==")
    v, p = vol((stair("east"), 0, 0, 0), (stair("north"), 1, 0, 0))
    UPD.update_volume(v, p, margin=0)
    check("东邻朝北→outer_left", props_at(v, p, 0, 0, 0)["shape"] == "outer_left",
          str(props_at(v, p, 0, 0, 0)))
    check("facing 不被改", props_at(v, p, 0, 0, 0)["facing"] == "east")
    v, p = vol((stair("east"), 1, 0, 0), (stair("north"), 0, 0, 0))
    UPD.update_volume(v, p, margin=0)
    check("西邻朝北→inner_left", props_at(v, p, 1, 0, 0)["shape"] == "inner_left",
          str(props_at(v, p, 1, 0, 0)))
    v, p = vol((stair("east", half="top", water="true"), 0, 0, 0))
    UPD.update_volume(v, p, margin=0)
    check("楼梯 half/waterlogged 保留",
          props_at(v, p, 0, 0, 0)["half"] == "top"
          and props_at(v, p, 0, 0, 0)["waterlogged"] == "true",
          str(props_at(v, p, 0, 0, 0)))

    print("\n== 幂等 / 去重 / 范围 / 越界 ==")
    v, p = vol((WALL, 0, 0, 0), (WALL, 1, 0, 0), (BARS, 3, 0, 3))
    r1 = UPD.update_volume(v, p, margin=0)
    n_pal = len(p)
    r2 = UPD.update_volume(v, p, margin=0)
    check("第一次有改动", r1["changed"] == 3, str(r1["changed"]))
    check("幂等：第二次 0 改动、0 新状态",
          r2["changed"] == 0 and r2["palette_added"] == 0)
    check("调色板去重（没有重复状态）",
          len(set(state_str(e) for e in p)) == n_pal == len(p), f"palette={len(p)}")
    check("报告含 families/cells", r1["families"].get("wall") == 2
          and len(r1["cells"]) == 3, str(r1["families"]))
    # margin：只重算 (0,0,0) 附近，邻居 (1,0,0) 也要跟着变
    v, p = vol((WALL, 0, 0, 0), (WALL, 1, 0, 0))
    rep = UPD.update_volume(v, p, (0, 0, 0, 0, 0, 0), margin=1)
    check("margin 让被改格的邻居一起重算",
          props_at(v, p, 1, 0, 0).get("west") == "tall"
          and props_at(v, p, 0, 0, 0).get("east") == "tall",
          str(rep["changed"]))
    check("margin=0 时不碰盒外格子",
          UPD.update_volume(*vol((WALL, 0, 0, 0), (WALL, 1, 0, 0)),
                            (0, 0, 0, 0, 0, 0), margin=0)["changed"] == 1)
    v, p = vol((WALL, 0, 0, 0), size=(4, 4, 4))
    UPD.update_volume(v, p, margin=0)
    check("出画布按空气（不报错）", props_at(v, p, 0, 0, 0)["west"] == "none")
    check("空调色板安全", UPD.update_volume(np.zeros((1, 1, 1), dtype=np.uint16),
                                          [], margin=0)["changed"] == 0)

    print("\n== updated_copy 不改输入 ==")
    v0, p0 = vol((WALL, 0, 0, 0), (WALL, 1, 0, 0))
    v_before = v0.copy()
    pal_len = len(p0)
    v2, p2, rep = UPD.updated_copy(v0, p0)
    check("原体素/调色板不变",
          np.array_equal(v0, v_before) and len(p0) == pal_len
          and rep["changed"] > 0 and len(p2) > pal_len)

    print("\n== mcrender.cli --update-states ==")
    from mcrender import cli as RCLI
    src = TMP / "packs" / "demo" / "modules" / "walls.schem"
    v, p = vol((WALL, 0, 0, 0), (WALL, 1, 0, 0), (BARS, 0, 0, 3),
               (WALL, 3, 0, 3), size=(8, 4, 4))
    S.write_structure(str(src), v, p, (0, 0, 0), (8, 4, 4), name="walls")
    import contextlib
    import io

    def run_cli(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = RCLI.main(argv)
        return rc, buf.getvalue()

    rc, txt = run_cli([str(src), "--list", "--offline", "--quiet"])
    check("无开关时不补连接", "east=tall" not in txt, txt.strip()[-60:])
    rc, txt = run_cli([str(src), "--update-states", "--list", "--offline",
                       "--quiet"])
    check("--update-states 补出连接状态",
          rc == 0 and "east=tall" in txt, txt.strip()[-70:])
    d_after = S.read_structure(str(src))
    check("只影响渲染，不改文件",
          all(not (e.get("Properties") or {}) for e in d_after["palette"]
              if "wall" in e["Name"]), str(len(d_after["palette"])))

    print("\n== 预览图渲染带上 --update-states ==")
    from mccore import library as LB2
    LB2.load_index(refresh=True)
    caps = {}

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        caps["cmd"] = cmd
        out = Path(cmd[cmd.index("--out") + 1])
        from PIL import Image
        for suffix in ("_iso.png", ".png"):
            p2 = out.with_name(out.name + suffix)
            p2.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (8, 8), (0, 0, 0, 255)).save(p2)
        return _R()

    real_run = LB2.subprocess.run
    LB2.subprocess.run = fake_run
    try:
        rep = LB2.render_previews("demo", force=True, scale_cap=16)
        cmd = caps.get("cmd") or []
        check("预览命令带 --update-states", "--update-states" in cmd, " ".join(cmd[-6:]))
        check("预览渲染成功回落", rep["rendered"] == ["walls"] and not rep["failed"],
              str(rep))
    finally:
        LB2.subprocess.run = real_run
    LB2.subprocess.run = fake_run
    try:
        caps.clear()
        LB2.render_previews("demo", force=True, update_states=False)
        check("可显式关掉（update_states=False）",
              "--update-states" not in (caps.get("cmd") or []))
    finally:
        LB2.subprocess.run = real_run

    print("\n== 编辑器接线（静态检查）==")
    js = (ROOT / "packages" / "mcstudio" / "web" / "editor.js").read_text(
        encoding="utf-8")
    html = (ROOT / "packages" / "mcstudio" / "web" / "index.html").read_text(
        encoding="utf-8")
    sess = (ROOT / "packages" / "mcstudio" / "session.py").read_text(
        encoding="utf-8")
    check("放置工具下有「方块更新」开关",
          'id="place-update-on"' in html and 'id="btn-recompute"' in html)
    check("开关状态进入 ops 请求", "update: E.update" in js)
    check("只在会改方块的笔刷工具下显示",
          "WRITE_TOOLS" in js and "updatePlaceRow()" in js)
    check("重算连接按钮走 update 操作", "{ type: 'update', box" in js)
    check("本地镜像随服务端区域同步（2D 视图不落后）",
          js.count("patchLocal(r.bbox, r.region)") >= 3 and "b64ToU16" in js)
    check("会话支持 update 参数与 update 操作",
          "update: bool = False" in sess and '"type") == "update"' in sess)

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAIL else 'FAILED'}  "
          f"({len(OK)} passed, {len(FAIL)} failed)")
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
