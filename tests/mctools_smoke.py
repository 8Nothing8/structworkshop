"""mctools 引擎冒烟：噪声 / 掩码 / 形状 / 全部工具。

对每个工具在固定夹具上跑一遍，断言：不崩、能改到东西、改动不越界、
同 seed 结果确定（hash 一致）、产物仍然合法（无越界索引）。

Usage:  python tests/mctools_smoke.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

import numpy as np  # noqa: E402

from mccore.schem_io import state_str  # noqa: E402
from mccore import structure_io as SIO  # noqa: E402
from mctools import engine as E  # noqa: E402
from mctools import masks as M  # noqa: E402
from mctools import noise as NZ  # noqa: E402
from mctools import registry as R  # noqa: E402
from mctools import shapes as SH  # noqa: E402

PAL = [{"Name": "minecraft:air"}, {"Name": "minecraft:stone"},
       {"Name": "minecraft:andesite"}, {"Name": "minecraft:oak_log"},
       {"Name": "minecraft:water"}]
STONE = "minecraft:stone"
OK = []
FAIL = []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))


def fixture(size=(24, 24, 24), *, seed=5):
    """一块地 + 一段墙 + 一个箱子：够大多数工具折腾。"""
    sx, sy, sz = size
    v = np.zeros((sy, sz, sx), dtype=np.uint16)
    n = min(sy - 1, sx - 1, sz - 1)
    v[0:min(2, sy), :, :] = 1                          # 地面
    v[min(2, sy - 1):min(9, sy), min(4, sz - 1),
      min(4, sx - 1):max(5, sx - 5)] = 1               # 一堵墙
    v[min(2, sy - 1):min(7, sy), min(10, sz - 2):max(11, sz - 6),
      min(10, sx - 2):max(11, sx - 6)] = 2             # 一个箱子
    v[min(2, sy - 1):min(5, sy), min(4, sz - 1):min(6, sz),
      min(4, sx - 1):min(6, sx)] = 3
    if n >= 16:
        v[5, 12, 11:15] = 0          # 箱子中间的一道缝（测焊接）
        v[10, 2, 2] = 3              # 悬空方块（测重力）
    if n >= 5:
        v[1, n - 2, n - 2] = 4       # 一点水
    return v


def run_tool(tool_id, *, size=(24, 24, 24), sel=None, centers=None,
             params=None, block=STONE, brush=(4.0, "sphere"), seed=1):
    v = fixture(size)
    pal = [dict(p) for p in PAL]
    before = v.copy()
    r = E.apply(v, pal, tool_id, params or {}, sel_box=sel, centers=centers,
                brush_radius=brush[0], brush_shape=brush[1], seed=seed,
                block=block)
    h = hashlib.sha256(v.tobytes()).hexdigest()[:12]
    return v, pal, before, r, h


def main() -> int:
    print("== 噪声 ==")
    g = np.mgrid[0:16, 0:16, 0:16]
    x, y, z = (a.astype(float) for a in g)
    for kind in NZ.KINDS:
        a = NZ.sample(kind, x, y, z, scale=9.0, seed=3)
        b = NZ.sample(kind, x, y, z, scale=9.0, seed=3)
        check(f"noise/{kind} 0..1 且确定", a.min() >= 0 and a.max() <= 1
              and np.array_equal(a, b), f"mean={a.mean():.3f}")
    check("noise 分块无关", np.allclose(
        NZ.sample("fbm", x, y, z, scale=7, seed=1)[:8],
        NZ.sample("fbm", x[:8].copy(), y[:8].copy(), z[:8].copy(),
                  scale=7, seed=1)))

    print("\n== 掩码 ==")
    ctx = M.MaskContext(fixture((12, 12, 12)), origin=(0, 0, 0),
                        names=[state_str(p) for p in PAL])
    cases = {"solid": 1, "air": 1, "y<2": 1, "stone": 1, "above(air)": 1,
             "surface": 1, "!(y<2)": 1, "(y-1)%2==0": 1, "x>z": 1,
             "solid & y<2": 1, "below(stone)": 1, "oak*": 1,
             "adjacent(air)": 1, "sky": 1, "neighbor(air)": 1,
             "random(0.4)": 1, "solid;y<3": 1}
    for expr in cases:
        try:
            m = M.compile_mask(expr, ctx)
            check(f"mask {expr!r}", m.shape == ctx.vol.shape)
        except Exception as e:                                    # noqa: BLE001
            check(f"mask {expr!r}", False, f"{type(e).__name__}: {e}")
    for bad in ("y<", "foo(1)", "solid)", "#tag"):
        try:
            M.compile_mask(bad, ctx)
            check(f"mask 报错 {bad!r}", False)
        except M.MaskError:
            check(f"mask 报错 {bad!r}", True)

    print("\n== 形状 ==")
    Y, Z, X = SH.grid((21, 21, 21))
    bad = []
    for kind in SH.SHAPE_KINDS:
        kw = {"center": (10, 10, 10), "radius": 6.0, "thickness": 2.0,
              "exponent": 3.0, "segments": 5}
        if kind in ("cuboid", "cube", "ellipsoid"):
            kw["size"] = (10, 8, 6)
        if kind == "plane":
            kw["size"] = (12, 9)
        m = SH.shape_mask(kind, X, Y, Z, **kw)
        if not m.any():
            bad.append(kind)
        b = SH.bbox_of(kind, center=kw["center"], radius=6.0, thickness=2.0,
                       size=kw.get("size"), axis="y")
        if not (len(b) == 6 and b[0] <= b[3] and b[1] <= b[4] and b[2] <= b[5]):
            bad.append(kind + ":bbox")
    check(f"shapes 全覆盖（{len(SH.SHAPE_KINDS)} 种）", not bad, str(bad))
    for k in SH.BRUSH_SHAPES:
        m = SH.shape_mask(k, X, Y, Z, center=(10.0, 10.0, 10.0), radius=3.0)
        check(f"brush {k}", bool(m.any()))

    print("\n== 工具 ==")
    specs = {
        "shape": dict(params={"kind": "sphere", "radius": 5, "mode": "add"},
                      centers=[[6, 4, 6]], sel=[2, 2, 2, 20, 20, 20]),
        "path": dict(params={"curve": "catenary", "radius": 1.5,
                             "points": [[3, 12, 3], [20, 12, 20]]},
                     sel=[0, 0, 0, 23, 23, 23]),
        "noise_painter": dict(params={"blocks": "minecraft:stone*2,minecraft:andesite",
                                      "scale": 5.0}, sel=[0, 0, 0, 23, 23, 20]),
        "noise_painter(brush)": dict(tool="noise_painter",
                                     params={"blocks": "minecraft:stone,minecraft:andesite",
                                             "scale": 4.0},
                                     centers=[[10, 2, 10], [12, 2, 12]]),
        "gradient_painter": dict(params={"blocks": "minecraft:stone,minecraft:andesite",
                                         "axis": "y"},
                                 sel=[0, 0, 0, 23, 23, 23]),
        "painter": dict(params={"chance": 1.0}, sel=[0, 0, 0, 23, 8, 23]),
        "floodfill": dict(params={}, centers=[[12, 3, 12]]),
        "smooth": dict(params={"mode": "stable", "strength": 2},
                       sel=[0, 0, 0, 23, 12, 23]),
        "rock": dict(params={}, sel=[0, 0, 0, 23, 12, 23]),
        "shatter": dict(params={"axis": "y", "width": 2, "scale": 2.0},
                        sel=[0, 0, 0, 23, 12, 23]),
        "melt": dict(params={"strength": 3}, sel=[0, 0, 0, 23, 12, 23]),
        "roughen": dict(params={}, sel=[0, 0, 0, 23, 12, 23]),
        "distort": dict(params={"distance_x": 2, "distance_z": 2, "scale": 8},
                        sel=[0, 0, 0, 23, 12, 23]),
        "weld": dict(params={"strength": 2, "threshold": 4},
                     sel=[0, 0, 0, 23, 12, 23]),
        "blend": dict(params={"spread": 2, "warp": 0.6},
                      sel=[0, 0, 0, 23, 12, 23]),
        "sculpt": dict(params={"mode": "add", "strength": 1},
                       centers=[[12, 3, 12]], brush=(3.0, "sphere")),
        "fill": dict(params={}, sel=[4, 14, 4, 8, 16, 8]),
        "replace": dict(params={"from": "minecraft:andesite"},
                        sel=[0, 0, 0, 23, 23, 23]),
        "hollow": dict(params={"thickness": 1}, sel=[9, 1, 9, 17, 8, 17]),
        "grow": dict(params={"amount": 1}, sel=[9, 1, 9, 17, 8, 17]),
        "gravity": dict(params={}, sel=[0, 0, 0, 23, 23, 23]),
        "drain": dict(params={}, sel=[0, 0, 0, 23, 12, 23]),
        "autoshade": dict(params={"shade_blocks":
                                  "minecraft:stone,minecraft:andesite"},
                          sel=[0, 0, 0, 23, 12, 23]),
        "elevation": dict(params={"mode": "raise", "amount": 2},
                          centers=[[8, 2, 8]], brush=(4.0, "sphere")),
        "flatten": dict(params={"level": 3, "mode": "both"},
                        sel=[0, 0, 0, 23, 12, 23]),
        "slope": dict(params={"axis": "x", "mode": "both"},
                      sel=[0, 0, 0, 23, 12, 23]),
        "extrude": dict(params={"amount": 3}, sel=[0, 0, 0, 23, 12, 23]),
    }
    for label, kw in specs.items():
        tool = kw.pop("tool", label)
        try:
            v, pal, before, r, h = run_tool(tool, **kw)
            changed = int((v != before).sum())
            pal_ok = all(0 <= int(i) < len(pal) for i in np.unique(v))
            check(f"tool {label}", changed > 0 and pal_ok and
                  r["changed"] >= changed - 1,
                  f"changed={changed} stats={r['stats']}")
        except Exception as e:                                   # noqa: BLE001
            check(f"tool {label}", False, f"{type(e).__name__}: {e}")

    print("\n== 楼梯方向 / shape（路径切线 + 相邻台阶）==")
    # 旧实现把 facing 写死 north、shape 写死 straight；这里两条都断言住
    from mctools import tools as T
    from mctools.engine import ToolContext

    def stair_run(points, **kw):
        sx, sy, sz = 24, 16, 24
        v = np.zeros((sy, sz, sx), dtype=np.uint16)
        pal = [dict(p) for p in PAL]
        r = E.apply(v, pal, "path", dict(curve="line", radius=1.6,
                                         points=points, **kw),
                    block=STONE, sel_box=[0, 0, 0, sx - 1, sy - 1, sz - 1],
                    seed=1)
        facing = {}
        for (y, z, x) in np.argwhere(v != 0):
            st = state_str(pal[int(v[y, z, x])])
            if "_stairs" in st:
                facing[(x, y, z)] = st.split("facing=")[1].split(",")[0]
        return r, facing

    _, facing = stair_run([[2, 6, 12], [21, 6, 12]],
                          use_stairs_and_slabs=True)
    check("path 直线 +X：台阶朝向随切线（全 east）",
          bool(facing) and set(facing.values()) == {"east"},
          f"{len(facing)} 格 {sorted(set(facing.values()))}")
    _, facing2 = stair_run([[2, 6, 2], [18, 6, 2], [18, 6, 20]],
                           use_stairs_and_slabs=True)
    leg1 = {f for p, f in facing2.items() if p[0] < 14 and p[2] < 4}
    leg2 = {f for p, f in facing2.items() if p[2] > 8 and p[0] > 16}
    check("path 拐弯：两段朝向分别随切线（east → south）",
          leg1 == {"east"} and leg2 == {"south"},
          f"leg1={sorted(leg1)} leg2={sorted(leg2)}")
    _, facing3 = stair_run([[2, 6, 12], [21, 6, 12]])
    check("path 未开 use_stairs_and_slabs 时不出现台阶", not facing3)

    def shape_case(stone_x, existing):
        """手搭一格石头 + 已知朝向的邻格台阶 → 看 shape 是否由邻居推出"""
        pal = [{"Name": "minecraft:air"}, {"Name": "minecraft:stone"}]
        ix = {}
        for f in sorted(set(existing.values())):
            pal.append({"Name": "minecraft:oak_stairs", "Properties": {
                "facing": f, "half": "bottom", "shape": "straight",
                "waterlogged": "false"}})
            ix[f] = len(pal) - 1
        v = np.zeros((3, 3, 6), dtype=np.uint16)
        v[0, 0, stone_x] = 1
        for x, f in existing.items():
            v[0, 0, x] = ix[f]
        m = np.zeros_like(v, dtype=bool)
        m[0, 0, stone_x] = True
        ctx = ToolContext(v, pal, origin=(0, 0, 0), sel=m, params={},
                          block=STONE, seed=1)
        T._stairize(ctx, m, line=[[0, 0, 0], [5, 0, 0]])   # 切线 +X → east
        st = state_str(pal[int(v[0, 0, stone_x])])
        return st.split("shape=")[1].split(",")[0]

    shapes = [shape_case(0, {1: "north"}), shape_case(0, {1: "south"}),
              shape_case(1, {0: "north"}), shape_case(1, {0: "south"}),
              shape_case(2, {})]
    check("shape 由相邻台阶推出（outer/inner/straight）",
          shapes == ["outer_left", "outer_right", "inner_left",
                     "inner_right", "straight"], str(shapes))

    print("\n== 确定性 / 越界安全 ==")
    a = run_tool("noise_painter", params={"blocks": "minecraft:stone,minecraft:andesite",
                                          "scale": 4.0},
                 sel=[0, 0, 0, 23, 23, 23], seed=42)[4]
    b = run_tool("noise_painter", params={"blocks": "minecraft:stone,minecraft:andesite",
                                          "scale": 4.0},
                 sel=[0, 0, 0, 23, 23, 23], seed=42)[4]
    check("同 seed 输出确定（hash 一致）", a == b, a)
    v = fixture((12, 12, 12))
    pal = [dict(p) for p in PAL]
    r = E.apply(v, pal, "fill", {}, sel_box=[-50, -50, -50, 200, 200, 200],
                block=STONE)
    check("越界选区被夹住", v.shape == (12, 12, 12) and
          r["box"] == [0, 0, 0, 11, 11, 11], str(r["box"]))
    try:
        E.apply(fixture((10, 10, 10)), [dict(p) for p in PAL], "smooth", {},
                block=STONE)
        check("无选区/无笔刷时报错", False)
    except E.ToolError:
        check("无选区/无笔刷时报错", True)

    print("\n== CLI ==")
    import contextlib
    import io
    import json as JSON
    import shutil as SHU      # 别用 SH：和 mctools.shapes 的别名冲突
    import tempfile
    from mctools import __main__ as CLI

    def cli(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = CLI.main(argv)
        return rc, buf.getvalue()

    v = fixture((16, 16, 16))
    tmp = Path(tempfile.mkdtemp(prefix="mctools_cli_"))
    try:
        src = tmp / "in.schem"
        SIO.write_structure(str(src), v, PAL, (0, 0, 0), (16, 16, 16),
                            name="cli")
        out = tmp / "out.schem"
        rc, txt = cli(["run", "noise_painter", "--in", str(src),
                       "--out", str(out), "--sel", "0,0,0,15,10,15",
                       "--param", "scale=5",
                       "--param", "blocks=minecraft:stone,minecraft:andesite",
                       "--stats"])
        check("CLI run 写盘", rc == 0 and out.is_file(),
              txt.strip().splitlines()[0] if txt else "")
        check("CLI 产物可读且尺寸一致",
              tuple(SIO.read_structure(str(out))["size"]) == (16, 16, 16))

        # apply：多步流水线 + JSON 报告
        out2 = tmp / "out2.schem"
        steps = [
            {"tool": "noise_painter", "sel": [0, 0, 0, 15, 10, 15],
             "params": {"scale": 5,
                        "blocks": "minecraft:stone,minecraft:andesite"}},
            {"tool": "smooth", "sel": [0, 0, 0, 15, 10, 15],
             "params": {"mode": "stable", "strength": 1}},
        ]
        rc, txt = cli(["apply", "--in", str(src), "--out", str(out2),
                       "--json", "--step", JSON.dumps(steps[0]),
                       "--step", JSON.dumps(steps[1])])
        rep = JSON.loads(txt)
        check("CLI apply 多步 + --json 报告",
              rc == 0 and len(rep["steps"]) == 2 and rep["changed"] > 0 and
              rep["steps"][0]["tool"] == "noise_painter" and out2.is_file(),
              f"steps={len(rep['steps'])} changed={rep['changed']}")
        check("CLI apply 报告含包围盒与中文统计",
              isinstance(rep["steps"][0]["bbox"], list) and
              bool(rep["steps"][0]["stats"]))

        # --all / "all": true 应该作用于整张画布
        rc, txt = cli(["run", "noise_painter", "--in", str(src), "--out",
                       str(tmp / "o4.schem"), "--all", "--param", "scale=6",
                       "--json"])
        rep4 = JSON.loads(txt)
        check("CLI --all 作用于整张画布",
              rc == 0 and rep4["changed"] > 0 and
              rep4["steps"][0]["box"] == [0, 0, 0, 15, 15, 15],
              f"changed={rep4['changed']} box={rep4['steps'][0]['box']}")
        rc, txt = cli(["apply", "--in", str(src), "--out", str(tmp / "o5.schem"),
                       "--json", "--step", JSON.dumps(
                           {"tool": "noise_painter", "all": True,
                            "params": {"scale": 6}})])
        rep5 = JSON.loads(txt)
        check("CLI step \"all\":true 作用于整张画布",
              rc == 0 and rep5["changed"] > 0, f"changed={rep5['changed']}")
        rc, txt = cli(["apply", "--in", str(src), "--out", str(tmp / "o6.schem"),
                       "--step", JSON.dumps({"tool": "shatter", "params": {}})])
        check("CLI 缺作用范围时明确报错", rc == 2 and "作用范围" in txt,
              txt.strip()[:70])

        # steps 文件 / select / info
        rf = tmp / "recipe.json"
        rf.write_text(JSON.dumps(steps, ensure_ascii=False), encoding="utf-8")
        rc, _ = cli(["apply", "--in", str(src), "--out", str(tmp / "o3.schem"),
                     "--steps", str(rf)])
        check("CLI apply --steps 文件", rc == 0 and (tmp / "o3.schem").is_file())
        rc, txt = cli(["select", "--in", str(src), "--mask", "minecraft:stone",
                       "--json"])
        sel = JSON.loads(txt)
        check("CLI select 掩码探测",
              rc == 0 and sel["count"] > 0 and len(sel["bbox"]) == 6,
              f"{sel['count']} 格 {sel['bbox']}")
        rc, txt = cli(["select", "--in", str(src), "--at", "1,1,1", "--json"])
        check("CLI select 魔棒", rc == 0 and JSON.loads(txt)["count"] > 0)
        rc, txt = cli(["select", "--in", str(src), "--mask", "y<", "--json"])
        check("CLI select 坏掩码报错", rc == 2, txt.strip()[:60])
        rc, txt = cli(["info", "shatter", "--json"])
        check("CLI info --json", rc == 0 and JSON.loads(txt)["id"] == "shatter")

        # QA 闸门：故意留一个悬空方块 → 退出码 1
        # 悬浮组件检查要 scipy（可选依赖）：没装就跳这一条，不要在干净环境里假红。
        try:
            import scipy  # noqa: F401
            has_scipy = True
        except ImportError:
            has_scipy = False
        if not has_scipy:
            print('[skip] CLI --qa 悬浮组件闸门要 scipy（`pip install -e ".[runtime]"`）')
        else:
            vf = np.zeros((12, 12, 12), dtype=np.uint16)
            vf[0] = 1
            vf[8, 5, 5] = 1
            fsrc = tmp / "float.schem"
            SIO.write_structure(str(fsrc), vf, PAL[:2], (0, 0, 0), (12, 12, 12),
                                name="float")
            rc, txt = cli(["run", "painter", "--in", str(fsrc), "--out",
                           str(tmp / "float2.schem"), "--at", "5,8,5",
                           "--brush", "sphere:2", "--block", "minecraft:stone",
                           "--qa"])
            check("CLI --qa 有 ERROR 时退出码 1", rc == 1 and "QA" in txt,
                  txt.strip().splitlines()[-1] if txt else "")

        # --in-place：真实覆盖 + 自动备份
        ip = tmp / "inplace.schem"
        SHU.copy2(src, ip)
        rc, txt = cli(["run", "smooth", "--in", str(ip), "--in-place",
                       "--sel", "0,0,0,15,10,15", "--param", "mode=stable",
                       "--json"])
        rep2 = JSON.loads(txt)
        bak = Path(rep2["backup"]) if rep2.get("backup") else None
        check("CLI --in-place 覆盖并备份",
              rc == 0 and bak is not None and bak.is_file() and ip.is_file(),
              str(rep2.get("backup")))
        if bak is not None and bak.parent.parent.name == "tools":
            SHU.rmtree(bak.parent, ignore_errors=True)

        # 参数错误
        rc, _ = cli(["run", "smooth", "--in", str(src), "--dry-run"])
        check("CLI 缺 --at/--sel 时明确报错", rc == 2)
        rc, _ = cli(["apply", "--in", str(src), "--out", str(out),
                     "--step", "不是 json"])
        check("CLI 坏 step JSON 报错", rc == 2)
        rc, _ = cli(["run", "no-such-tool", "--in", str(src), "--out", str(out)])
        check("CLI 未知工具报错", rc == 2)
        rc, txt = cli(["list"])
        check("CLI list", rc == 0 and "噪声绘制" in txt)
    finally:
        SHU.rmtree(tmp, ignore_errors=True)

    print("\n== 文档一致性（skill ↔ 工具表）==")
    import re
    skill = ROOT / "skills" / "minecraft-voxel-tools" / "SKILL.md"
    if not skill.is_file():
        check("代理用 skill 存在", False, str(skill))
    else:
        doc = skill.read_text(encoding="utf-8")
        ids_doc = set(re.findall(r'"tool"\s*:\s*"([a-z_]+)"', doc)) | \
            set(re.findall(r"mctools (?:run|info)\s+([a-z_]+)", doc))
        unknown = sorted(i for i in ids_doc if i not in R.TOOLS)
        check("SKILL.md 引用的工具 id 都存在", not unknown, str(unknown))
        missing = [t for t in R.TOOLS if t not in doc]
        check("每个工具都在 skill 里出现过", not missing, str(missing))
        subs = set(re.findall(r"mctools ([a-z]+)", doc))
        check("SKILL.md 只用了已实现的子命令",
              subs <= {"list", "info", "select", "run", "apply", "catalog"},
              str(sorted(subs)))

    print(f"\n{'ALL PASS' if not FAIL else 'FAILED'}  "
          f"({len(OK)} passed, {len(FAIL)} failed)")
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    print(f"工具数：{R.catalog()['count']}，分组：{len(R.catalog()['groups'])}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
