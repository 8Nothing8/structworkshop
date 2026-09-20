"""看某个世界坐标附近有什么（无图形环境的放大镜）。

用法: python tools/whois.py x.schem 173 12 83 [--r 6]
坐标是区域索引（与 qa_check 报告一致）。
"""
from __future__ import annotations
import argparse, sys
import numpy as np
from mccore import structure_io as S

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input"); ap.add_argument("x", type=int)
    ap.add_argument("y", type=int); ap.add_argument("z", type=int)
    ap.add_argument("--r", type=int, default=6)
    a = ap.parse_args()
    d = S.read_structure(a.input)
    v = d["voxels"]; pos = d["position"]; names = [e["Name"].removeprefix("minecraft:")
                                                  for e in d["palette"]]
    ox, oy, oz = pos
    ox = oy = oz = 0        # qa_check 报的是区域索引，这里直接用索引坐标
    print(f"file pos={pos} size={tuple(int(t) for t in d['size'])}  "
          f"目标世界坐标 ({a.x},{a.y},{a.z})，世界坐标 = pos + 索引")
    for y in range(a.y + a.r, a.y - a.r - 1, -1):
        rows = []
        for z in range(a.z - a.r, a.z + a.r + 1):
            row = ""
            for x in range(a.x - a.r, a.x + a.r + 1):
                ix, iy, iz = x - ox, y - oy, z - oz
                if 0 <= iy < v.shape[0] and 0 <= iz < v.shape[1] and 0 <= ix < v.shape[2]:
                    i = int(v[iy, iz, ix])
                    row += (names[i][:2].center(2) if i else "..")
                else:
                    row += "  "
            rows.append(row)
        print(f"y={y:4d}  " + "|".join(rows[:1]))
        for r in rows[1:]:
            print("       " + r)
    return 0

if __name__ == "__main__":
    sys.exit(main())
