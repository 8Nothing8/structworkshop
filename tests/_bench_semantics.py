"""网格基准的语义生成器：给一个结构，输出与 `/api/palette-info` 同款的 JSON。

基准（``tests/editor_mesh_bench.js``）要在 Node 里跑我们自己的 mesher，它需要的
`layer / tint / liquid / special / has_elements / ao_occluder / opaque / self_culling`
判定在 Python 侧（`mcstudio.blocks`）—— 这里把真实管线跑一遍落成 JSON，
保证「基准里的语义」和「浏览器里拿到的语义」是同一份。

用法：python tests/_bench_semantics.py <结构.schem> <输出.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from mccore import structure_io as S  # noqa: E402
from mcstudio.blocks import BlockCatalog  # noqa: E402


def main() -> int:
    src, out = sys.argv[1], sys.argv[2]
    d = S.read_structure(src)
    cat = BlockCatalog()
    try:
        version = cat.version_of(int(d.get("data_version") or 0))
    except Exception:  # noqa: BLE001
        version = "26.2"
    states = [{"Name": p["Name"], "Properties": p.get("Properties") or {}}
              for p in d["palette"]]
    info = cat.resolve(states, version)
    Path(out).write_text(json.dumps({"version": version, "info": info,
                                     "textures": cat.extra_textures(version)},
                                    ensure_ascii=False), encoding="utf-8")
    print(f"{src}: {len(info)} states -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
