"""ASCII 查看器：无图形环境下快速检查 .schem/.litematic 的形体与剖面。

用法:
  python tools/ascii_view.py x.schem --top 110            # 指定标高俯视
  python tools/ascii_view.py x.schem --proj top           # 全楼俯视投影
  python tools/ascii_view.py x.schem --proj front         # 正立面投影(看剪影)
  python tools/ascii_view.py x.schem --cut z=112          # 垂直剖面(南->北)
  python tools/ascii_view.py x.schem --cut x=112 --w 140
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from mccore import structure_io as S  # noqa: E402

CHARS = [
    ("sea_lantern", "o"), ("redstone_lamp", "o"), ("shroomlight", "o"),
    ("glass", ":"), ("stained_glass", ":"), ("tinted_glass", ":"),
    ("white_concrete", "#"), ("light_gray_concrete", "."), ("gray_concrete", "^"),
    ("black_concrete", "|"), ("polished_andesite", "="), ("smooth_stone", "_"),
    ("polished_blackstone", "@"), ("iron_block", "*"), ("iron_bars", "-"),
    ("quartz", "+"), ("calcite", "+"), ("copper", "c"), ("waxed_copper", "c"),
    ("waxed_exposed", "c"), ("waxed_weathered", "c"), ("waxed_oxidized", "c"),
    ("water", "~"), ("light_blue_stained_glass", "~"), ("leaves", "Y"),
    ("oak_log", "T"), ("slab", "'"), ("stairs", "/"), ("dirt", ","),
]


def char_of(name: str) -> str:
    n = name.removeprefix("minecraft:")
    for key, ch in CHARS:
        if key in n:
            return ch
    return "?"


def render(grid: np.ndarray, names: list[str], step: int,
           step_r: int | None = None) -> list[str]:
    out = []
    step_r = step_r or step
    h, w_ = grid.shape
    for r in range(0, h, step_r):
        line = []
        for c in range(0, w_, step):
            block = grid[r:r + step_r, c:c + step]
            nz = block[block > 0]
            line.append(char_of(names[int(nz[0])]) if nz.size else " ")
        out.append("".join(line).rstrip())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--top", type=int, default=None, help="俯视标高")
    ap.add_argument("--proj", choices=("top", "front", "left"), default=None)
    ap.add_argument("--cut", default=None, help="x=N 或 z=N 垂直剖面")
    ap.add_argument("--w", type=int, default=110, help="输出宽度(字符)")
    ap.add_argument("--hmax", type=int, default=70, help="输出最大行数")
    a = ap.parse_args()
    d = S.read_structure(a.input)
    v = d["voxels"]
    names = [e["Name"] for e in d["palette"]]
    sy, sz, sx = v.shape
    step = max(1, sx // a.w)
    if a.top is not None:
        g = v[a.top]
    elif a.proj == "top":
        g = v.max(axis=0)
    elif a.proj == "front":
        g = v.max(axis=1)          # (sy, sx)
    elif a.proj == "left":
        g = v.max(axis=2)          # (sy, sz)
    elif a.cut:
        axis, _, val = a.cut.partition("=")
        i = int(val)
        g = v[:, i, :] if axis == "z" else v[:, :, i]
    else:
        ap.error("需要 --top/--proj/--cut")
    step_r = max(step, -(-g.shape[0] // a.hmax))
    lines = render(g, names, step, step_r)
    if a.proj == "front" or a.proj == "left" or a.cut:
        lines = list(reversed(lines))          # 立面/剖面：y 向上
    print(f"# {a.input}  size={tuple(int(x) for x in d['size'])}  "
          f"mode={'top@'+str(a.top) if a.top is not None else (a.cut or a.proj)}")
    for ln in lines:
        print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
