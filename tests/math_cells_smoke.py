"""数学域 · 蒙皮与骨架断言（不读成品文件，纯几何/拓扑）。

    python tests/math_cells_smoke.py

覆盖:
  * cells    —— 四维超立方体投影的 8 胞体分解（L∞ 范数，组合本地模块）
  * fields   —— 表达式 → 场 → 占位 / 等值壳 / 分级色（引擎件：`mctools field` 工具也用它）
  * curves   —— 参数曲线（环面结 / 黄金角螺旋 / 波形，组合本地模块）
  * redstone —— 红石构件（时钟"永不停机"的元数据，组合本地模块）
  * defloat  —— 悬浮物加固（造一个悬浮块，加固后不再悬浮，组合本地模块）
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages"))
sys.path.insert(0, str(REPO / "tests"))
import _support  # noqa: E402

# 组合是**可选内容**（代码仓库不附带）—— 缺了就跳过，别让 import 抛 ImportError
_support.need_compositions("math-cube", script=Path(__file__).name)
sys.path.insert(0, str(REPO / "compositions" / "math-cube"))

import numpy as np                                        # noqa: E402

import cells as CE                                        # noqa: E402
import curves as C                                        # noqa: E402
import defloat as DF                                      # noqa: E402
import redstone as RS                                     # noqa: E402
from mccore import fields as F                            # noqa: E402
from mckit.voxbrush import Grid                           # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {detail}" if not cond else ""))
    if not cond:
        FAILS.append(name)


def test_cells() -> None:
    CE.selftest()
    f = CE.tesseract_facts()
    check("超立方体投影：8 胞体 / 24 面 / 32 棱 / 16 顶点",
          (f["cells"], f["faces"], f["edges"], f["vertices"]) == (8, 24, 32, 16))
    check("胞体邻接对 = 24，每胞体 6 邻居", f["adjacent_pairs"] == 24
          and set(f["degree"].values()) == {6})
    check("胞体图直径 = 2（任意两域最多隔一个中间域）",
          f["max_cell_distance"] == 2)
    check("内核↔外壳 不相邻（数学与艺术没有直接的门）",
          "core-shell" in f["opposite_pairs"])
    ties = [e for e in CE.edge_list() if e["kind"] == "tie"]
    check("8 条斜撑都在体对角线上（|x|=|y|=|z|）",
          len(ties) == 8 and all(abs(v) == 1 for e in ties for v in e["v0"]))
    d = CE.decompose((129, 129, 129), core=16, shell=48)
    check("分解：中心 = core，角 = shell，六向各归其棱台",
          (d["cell"][64, 64, 64], d["cell"][0, 0, 0], d["cell"][64, 64, 104],
           d["cell"][104, 64, 64], d["cell"][64, 104, 64])
          == (0, 7, 1, 3, 5))
    full = CE.decompose((70, 40, 40), core=6, shell=14)["cell"]
    ok = all(np.array_equal(sub["cell"], full[y0:y1])
             for y0, y1, sub in CE.iter_decompose((70, 40, 40), core=6, shell=14,
                                                  chunk_y=16))
    check("分块流式分解 = 整块分解", ok)
    cnt = np.bincount(full.reshape(-1), minlength=8)
    check("8 个胞体划分完整（无空洞、无重复）", int(cnt.sum()) == full.size
          and bool((cnt > 0).all()))


def test_fields() -> None:
    f = F.sample("sin(x/4)*cos(z/4) - y/6", (24, 24, 24), (-12, -12, -12))
    check("隐式场采样形状 = (sy, sz, sx)", f.shape == (24, 24, 24))
    m = F.mask_of(f, "<=", 0.0)
    check("占位掩码非空且非满", 0 < m.sum() < m.size)
    sh = F.shell_of(m, 1)
    check("等值壳 ⊂ 占位，且严格更少", bool((sh & ~m).sum() == 0)
          and int(sh.sum()) < int(m.sum()))
    check("球面场 r=10 的占位体积 ≈ (4/3)πr³ 的体素近似",
          abs(int(F.mask_of(F.sample("x*x+y*y+z*z-100", (24, 24, 24),
                                     (-12, -12, -12))).sum()) - 4189) < 250)
    idx, pal = F.stratify(f, ["minecraft:white_concrete", "minecraft:gray_concrete"])
    check("分级色：档数 = 方块数，索引从 1 开始",
          int(idx.min()) == 1 and int(idx.max()) == 2 and len(pal) == 3)
    try:
        F.compile_expr("__import__('os').system('echo hi')")
        check("白名单拦住危险表达式", False, "居然通过了")
    except F.FieldError:
        check("白名单拦住危险表达式", True)
    try:
        F.compile_expr("sin(x) + ")
        check("语法错误被拦", False)
    except F.FieldError:
        check("语法错误被拦", True)
    check("chebyshev（L∞）与 cells 的抽象度同源",
          float(F.compile_expr("chebyshev(x, y, z)")(3.0, -5.0, 2.0)) == 5.0)


def test_curves_and_redstone() -> None:
    check("环面结为非退化的闭合参数曲线",
          C.arc_length(C.torus_knot(2, 3, R=8, r=3)) > 30)
    g = C.golden_angle_spiral(100, spacing=2.0)
    r = np.linalg.norm(g[:, [0, 2]], axis=1)
    check("黄金角螺旋半径单调不减（叶序）", bool((np.diff(r) > -1e-9).all()))
    w1 = C.waveform("square", n=65, terms=3)
    w2 = C.waveform("square", n=65, terms=15)
    ideal = np.sign(np.sin(w1[:, 0] / 8.0)) * 8.0 * (np.pi / 4)
    check("方波：谐波越多越接近理想方波",
          float(np.sqrt(((w2[:, 1] - ideal) ** 2).mean()))
          < float(np.sqrt(((w1[:, 1] - ideal) ** 2).mean())))
    grid = Grid(16, 12, 16)
    rw = RS.Writer(grid)
    meta = RS.clock_loop(rw, 4, 2, 4, delay=2)
    names = [e["Name"] for e in grid.pal]
    check("中继器环时钟：4 个中继器 + 拉杆 + 导线",
          names.count("minecraft:repeater") == 4
          and "minecraft:lever" in names and "minecraft:redstone_wire" in names)
    check("时钟元数据：永不停机、周期 = 4×delay",
          meta["halts"] is False and meta["period_ticks"] == 8)
    check("拉杆属性合法（face ∈ floor/wall/ceiling）",
          all(p["Properties"]["face"] in ("floor", "wall", "ceiling")
              for p in grid.pal if p["Name"] == "minecraft:lever"))


def test_defloat() -> None:
    grid = Grid(8, 10, 8)
    stone = grid.mat("stone")
    grid.v[6, 4, 4] = stone                     # 一块悬在空中的石头
    pal = grid.pal
    before = DF.find_floating(grid.v, pal)
    check("能识别悬浮块", len(before) == 1 and before[0]["bottom_y"] == 6)
    rep = DF.defloat(grid.v, pal, hanger="minecraft:copper_block")
    after = DF.find_floating(grid.v, pal)
    check("加固后不再有悬浮块", not after, str(after))
    check("加固 = 补了一根到地面的柱子", rep["added"] == 6, str(rep))
    grid2 = Grid(8, 10, 8)
    grid2.v[6, 4, 4] = grid2.mat("stone")
    avoid = np.zeros((10, 8, 8), dtype=bool)
    for y in range(0, 7):
        avoid[y, 4, 4] = True                   # 原位被禁
    rep2 = DF.defloat(grid2.v, grid2.pal, hanger="minecraft:copper_block",
                      avoid=avoid)
    check("避让区生效：柱子整体挪到旁边",
          rep2["shifted"] >= 1 and not DF.find_floating(grid2.v, grid2.pal),
          str(rep2))


def main() -> int:
    print("== cells（组合本地）==")
    test_cells()
    print("== fields（引擎件）==")
    test_fields()
    print("== curves / redstone（组合本地）==")
    test_curves_and_redstone()
    print("== defloat（组合本地）==")
    test_defloat()
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: " + ", ".join(FAILS))
        return 1
    print("math_cells_smoke OK（引擎骨架全部通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
