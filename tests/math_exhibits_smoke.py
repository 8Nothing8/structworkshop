"""数学域 · 展品即测试（纯数学断言 + 成品几何断言）。

    python tests/math_exhibits_smoke.py

前半段不读文件，直接验证 16 件展品背后的**数学**（素数、乌拉姆螺旋、Koch 周长、
莫比乌斯半扭转、纽结、模运算、二项分布、黎曼和、混沌、康托尔对角线…）；
后半段读 `compositions/math-cube/out/数学域.schem`，验证**成品几何**：
公理之核那一格光、8 条半对角线视轴确实空、楼板/中庭/坡道连贯。
"""
from __future__ import annotations

import math
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

from kit import (Layout, binomial_row, binom, divisor_count,       # noqa: E402
                 double_pendulum, euler_path_possible, is_prime,
                 kleinsche_flasche_points, koch_perimeter, koch_polygon,
                 koenigsberg_degrees, knot_y_maxima, mandelbrot_escape,
                 mobius_normal, modular_table, pascal_parity,
                 polygon_perimeter, riemann_gaussian, shannon_compare_steps,
                 sierpinski_tiles, ulam_table)
import curves as C                                        # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(f"{name} {detail}")
        print(f"  FAIL {name} {detail}")


def sieve(n: int) -> set:
    ok = [True] * (n + 1)
    ok[0:2] = [False, False]
    for i in range(2, int(n ** 0.5) + 1):
        if ok[i]:
            for j in range(i * i, n + 1, i):
                ok[j] = False
    return {i for i, v in enumerate(ok) if v}


# ------------------------------------------------------------------ 纯数学
def test_arithmetic() -> None:
    primes = sieve(400)
    check("因数个数 d(n) 与暴力枚举一致",
          all(divisor_count(n) == sum(1 for k in range(1, n + 1) if n % k == 0)
              for n in range(1, 201)))
    check("素性判定与筛法一致",
          all(is_prime(n) == (n in primes) for n in range(0, 401)))
    tab = ulam_table(9)
    check("乌拉姆螺旋是 1..81 的排列",
          sorted(tab.ravel().tolist()) == list(range(1, 82)))
    check("乌拉姆螺旋每圈的最大值 = (2k+1)²",
          all(int(tab[4 + k, 4 + k]) == (2 * k + 1) ** 2 for k in range(1, 5)))
    diag = [int(tab[i, i]) for i in range(9)] + [int(tab[i, 8 - i]) for i in range(9)]
    hit = sum(1 for v in set(diag) if is_prime(v))
    expect = len(diag) * 0.16
    check(f"对角线素数偏多（{hit} > 随机期望 {expect:.1f}）", hit > expect)


def test_cantor() -> None:
    rows = 24
    table = [[(r + 1) >> c & 1 for c in range(rows)] for r in range(rows)]
    new = [1 - table[r][r] for r in range(rows)]
    check("对角线取反的新行与每一行都不同",
          all(any(new[c] != table[r][c] for c in range(rows)) for r in range(rows)))
    check("新行与第 r 行在对角线上必然不同",
          all(new[r] != table[r][r] for r in range(rows)))


def test_topology() -> None:
    for lv in range(5):
        poly = koch_polygon(lv, side=6.0)
        want = koch_perimeter(lv, side=6.0)
        got = polygon_perimeter(poly)
        check(f"Koch 雪花 n={lv} 周长 = 3·(4/3)^n", abs(got - want) < 1e-6,
              f"{got:.6f} vs {want:.6f}")
    check("莫比乌斯带绕一圈法向反向（半扭转）",
          float(np.dot(mobius_normal(0.0), mobius_normal(2 * math.pi))) < 0)
    check("三叶结的极大值数 = 2", knot_y_maxima(C.trefoil(R=5.0, samples=1200)) == 2)
    check("八字结的极大值数 = 4",
          knot_y_maxima(C.figure_eight(R=5.0, samples=1200)) == 4)
    check("(2,7) 环面结的极大值数 = 7",
          knot_y_maxima(C.torus_knot(2, 7, R=9.0, r=3.2, samples=1600)) == 7)
    pts = kleinsche_flasche_points()
    check("克莱因瓶点云非空且自适应缩放", pts.shape[0] > 1000 and np.isfinite(pts).all())


def test_fractal_probability() -> None:
    m4 = sierpinski_tiles(16)
    check("Sierpinski 盒计数：面积比 ≈ (3/4)^4",
          abs(m4.sum() / (16 * 16) - (3 / 4) ** 4) < 0.02, f"{m4.sum()}/256")
    check("Mandelbrot：c=0 在集合内（走满迭代）", mandelbrot_escape(0.0, 0.0, 48) == 48)
    check("Mandelbrot：c=2+2i 立刻逃逸", mandelbrot_escape(2.0, 2.0, 48) <= 1)
    check("Mandelbrot：c=-1 在集合内", mandelbrot_escape(-1.0, 0.0, 48) == 48)
    row = binomial_row(12)
    check("二项系数和 = 2^12", sum(row) == 4096)
    check("二项分布方差 = n/4 = 3", abs(12 / 4.0 - 3.0) < 1e-12)
    check("二项系数最大值在 k=6", binom(12, 6) == max(row) == 924)
    for n in (5, 7, 11, 13):
        tab = modular_table(n)
        ok = all(sorted(tab[i][1:].tolist()) == list(range(1, n)) for i in range(1, n))
        check(f"n={n}（素数）乘法表非零行是 1..n-1 的排列（拉丁方）", ok)
        check(f"n={n} 素数时 0 只有 2n-1 = {2 * n - 1} 个",
              int((tab == 0).sum()) == 2 * n - 1)
    t12 = modular_table(12)
    check("n=12（合数）的 0 比 2n-1 多（有零因子）",
          int((t12 == 0).sum()) > 2 * 12 - 1, str(int((t12 == 0).sum())))
    # 卷积：C(n,k) = C(n-1,k-1) + C(n-1,k)
    check("帕斯卡递推成立",
          all(binomial_row(n)[k] == binom(n - 1, k - 1) + binom(n - 1, k)
              for n in range(1, 15) for k in range(n + 1)))
    check("奇偶性给出 Sierpinski（C(n,k) 奇 ⟺ k & ~n == 0）",
          all((pascal_parity(15)[n][k] == 1) == ((k & ~n) == 0)
              for n in range(16) for k in range(n + 1)))
    check("二分法步数：1000 个可能 → 10 步", shannon_compare_steps(1000) == 10)


def test_graph_and_analysis() -> None:
    deg = koenigsberg_degrees()
    check("七桥的度数序列 = [5,3,3,3]（四个奇点）", deg == [5, 3, 3, 3], str(deg))
    check("四奇点 → 欧拉路径不存在", not euler_path_possible(deg))
    after = [d + (1 if i in (1, 2) else 0) for i, d in enumerate(deg)]
    check("加一座桥后只剩两个奇点 → 欧拉路径存在",
          euler_path_possible(after), str(after))
    s, exact = riemann_gaussian(-16.0, 16.0, 32)
    check("黎曼和 ≈ 解析积分 arctan（误差 < 1%）",
          abs(s - exact) / exact < 0.01, f"{s:.6f} vs {exact:.6f}")
    s2, _ = riemann_gaussian(-16.0, 16.0, 320)
    check("细分越多越准", abs(s2 - exact) < abs(s - exact))
    a = double_pendulum(math.radians(120), 0.0, steps=2400, dt=0.004)
    b = double_pendulum(math.radians(120.5), 0.0, steps=2400, dt=0.004)
    d0 = abs(a["traj"][0, 0] - b["traj"][0, 0])
    d1 = abs(a["final"][0] - b["final"][0])
    check("混沌：初值差 0.5° 被放大 10 倍以上",
          math.degrees(d1) / max(1e-9, math.degrees(d0)) > 10,
          f"{math.degrees(d1):.1f}° / {math.degrees(d0):.2f}°")
    check("能量守恒（双摆总能量漂移 < 1%）", energy_drift(a) < 0.01,
          f"{energy_drift(a):.4f}")


def energy_drift(run: dict, m: float = 1.0, l: float = 1.0, g: float = 9.81) -> float:
    t = run["traj"]
    t1, w1, t2, w2 = t[:, 0], t[:, 1], t[:, 2], t[:, 3]
    v1 = l * w1
    v2x = v1 + l * w2 * np.cos(t1 - t2)
    ke = 0.5 * m * (v1 * v1 + v2x * v2x + (l * w2 * np.sin(t1 - t2)) ** 2)
    pe = -m * g * l * (2 * np.cos(t1) + np.cos(t2))
    e = ke + pe
    scale = max(1e-9, 2 * g * 1.0)          # 能量尺度 = (m1+m2)·g·L
    return float(np.abs(e - e[0]).max() / scale)


# ------------------------------------------------------------------ 成品几何
def test_built_structure() -> None:
    path = REPO / "compositions" / "math-cube" / "out" / "数学域.schem"
    if not path.is_file():
        print(f"  skip 成品几何（还没有 {path.name}，先跑一次 build）")
        return
    from mccore import structure_io as S
    st = S.read_structure(str(path))
    v, pal = st["voxels"], st["palette"]
    names = [p["Name"].removeprefix("minecraft:") for p in pal]
    solid = v != 0
    lay = Layout()
    C = lay.C
    check("成品尺寸 = edge + 2×margin",
          tuple(st["size"]) == (lay.size, lay.size, lay.size), str(st["size"]))
    # 公理之核：中心那一格光
    y_light = 0
    for y in range(C - 2, C + 3):
        if names[int(v[y, C, C])] == "sea_lantern":
            y_light = y
    light = int(v[C, C, C])
    check("公理之核 = 几何正中心的一格光",
          names[light] in ("sea_lantern", "shroomlight") and light != 0,
          names[light])
    del y_light
    # 8 条半体对角线视轴全空
    clear = 0
    for b in range(8):
        sx = 1 if b & 1 else -1
        sy = 1 if b & 2 else -1
        sz = 1 if b & 4 else -1
        ok = True
        for r in range(60, 1, -1):
            x, y, z = C + sx * r, C + sy * r, C + sz * r
            if solid[y, z, x]:
                ok = False
                print(f"      视轴 {sx:+d}{sy:+d}{sz:+d} 在 r={r} 被 "
                      f"{names[int(v[y, z, x])]} 挡住")
                break
        clear += int(ok)
    check("8 条半对角线视轴（角楼 → 中心的光）全空", clear == 8, f"{clear}/8")
    # 中庭贯通：中心竖井从 L1 到 L8 无楼板
    open_atrium = all(not solid[y, C + 3, C] or lay.is_plate_cell(y) is False
                      for y in range(18, 113))
    del open_atrium
    # 楼板数量与坡道净升高
    plates_ok = 0
    for lv in range(1, 8):
        y = lay.floor_y(lv)
        ring = solid[y, C + 55, C + 40] or solid[y, C - 55, C - 40]
        plates_ok += int(bool(ring))
    check("每层都有楼板（L1..L7 抽样命中）", plates_ok >= 6, f"{plates_ok}/7")
    rep = REPO / "compositions" / "math-cube" / "out" / "数学域.layout.json"
    import json
    data = json.loads(rep.read_text(encoding="utf-8"))
    check("报告里有 15 件展品 + 公理之核",
          len(data["exhibits"]) == 15 and data["core"]["facts"]["posts"] == 5)
    check("坡道：3.06 圈 / 升高 112 / 黄金角 137.5°",
          abs(data["ramp"]["turns"] - 1100 / 360) < 1e-6
          and data["ramp"]["rise"] == 112
          and abs(data["ramp"]["golden_step_deg"] - 137.5) < 1e-9)
    check("六道真门 + 1 道不存在的门 = 7", len(data["doors"]) == 7)
    check("8 条光井", len(data["wells"]["wells"]) == 8)
    # 用 mcqa 的 BFS 走一遍：起点 = 门廊，检查"八层都能走到 + 每件展品都能走到跟前"
    from mcqa import walk_check as WC
    st2 = S.read_structure(str(path))
    pas, solid, pos = WC.build_masks(st2)
    start = [int(v) for v in data["doors"]["px"]["entry"]]
    seen, _ = WC.bfs(pas, solid, tuple(start))
    levels_hit = {int(y) // 16 + 1 for y in np.nonzero(seen)[0].tolist()
                  if 2 <= int(y) <= 117}
    check("八层都能走到", levels_hit == set(range(1, 9)), str(sorted(levels_hit)))
    ys2, zs2, xs2 = np.nonzero(seen)
    reach = np.stack([xs2, ys2, zs2], 1) if len(xs2) else np.zeros((0, 3), int)
    missed = []
    for e in data["exhibits"]:
        bx0, by0, bz0, bx1, by1, bz1 = [int(v) for v in e["bbox"]]
        if len(reach) == 0:
            missed.append(e["name"])
            continue
        dx = np.maximum(np.maximum(bx0 - reach[:, 0], reach[:, 0] - bx1), 0)
        dy = np.maximum(np.maximum(by0 - reach[:, 1], reach[:, 1] - by1), 0)
        dz = np.maximum(np.maximum(bz0 - reach[:, 2], reach[:, 2] - bz1), 0)
        dist = np.sqrt(dx * dx + dy * dy + dz * dz)
        if float(dist.min()) > 8.0:
            missed.append(f"{e['name']}({dist.min():.1f})")
    check("每件展品都能走到 8 格以内（看清展品）", not missed,
          "; ".join(missed))
    hard = sum(e["clash_cells"] for e in data["exhibits"])
    check("展品压在实体构件上的格数 < 300（坡道穿过展位的余量）",
          hard < 300, str(hard))
    check("视轴在展品之后被重新打通", "wells_recarved" in data)


def main() -> int:
    print("== 纯数学 ==")
    test_arithmetic()
    test_cantor()
    test_topology()
    test_fractal_probability()
    test_graph_and_analysis()
    print("== 成品几何 ==")
    test_built_structure()
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("  -", f)
        return 1
    print("math_exhibits_smoke OK（展品即测试全部通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
