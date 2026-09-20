"""格式层冒烟测试：`.schem`(存储) 与 `.litematic`(兼容) 全流程互通。

  1. read_structure() 按扩展名分派，两格式读到同一份体素/调色板
  2. 往返转换逐格一致（含 position 与重复状态去重）
  3. 存储策略：资产包模块一律 `.schem`
  4. write_structure 默认后缀 = .schem

Usage:  python tests/formats_smoke.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

import numpy as np  # noqa: E402

from mccore import litematic_io as L  # noqa: E402
from mccore import structure_io as S  # noqa: E402
from mccore.module_lib import iter_modules  # noqa: E402


def ids(d: dict, registry: dict) -> np.ndarray:
    key = lambda e: (e["Name"], tuple(sorted((e.get("Properties") or {}).items())))  # noqa: E731
    lut = np.array([registry.setdefault(key(e), len(registry))
                    for e in d["palette"]], dtype=np.int64)
    return lut[d["voxels"].reshape(-1)]


def main() -> int:
    src = ROOT / "tests" / "fixtures" / "small_house.schem"
    assert src.is_file(), f"缺夹具 {src}"
    d = S.read_structure(src)
    tmp = Path(tempfile.mkdtemp(prefix="structworkshop_fmt_"))
    try:
        # 1) .schem -> .litematic -> .schem，逐格一致
        lt = tmp / "small_house.litematic"
        S.write_structure(lt, d["voxels"], d["palette"], d["position"],
                          d["size"], name="small_house")
        back_l = S.read_structure(lt)
        back_s = tmp / "roundtrip.schem"
        S.write_structure(back_s, back_l["voxels"], back_l["palette"],
                          back_l["position"], back_l["size"], name="small_house")
        reg: dict = {}
        assert tuple(back_l["size"]) == tuple(d["size"]), back_l["size"]
        assert tuple(back_l["position"]) == tuple(d["position"]), back_l["position"]
        assert np.array_equal(ids(S.read_structure(back_s), reg), ids(d, reg)), \
            "往返后体素不一致"
        assert L.read_litematic(str(lt))["size"] == tuple(d["size"])
        print("PASS 1/4  .schem <-> .litematic 往返逐格一致")

        # 2) 分派与默认后缀
        assert S.suffix_of(lt) == ".litematic" and S.suffix_of(back_s) == ".schem"
        assert S.primary_path(lt).suffix == ".schem"
        no_suffix = Path(tmp / "x")
        assert S.write_structure(no_suffix, d["voxels"], d["palette"],
                                 d["position"], d["size"]).suffix == ".schem"
        print("PASS 2/4  格式分派 / 默认 .schem")

        # 3) 重复状态去重：palette 状态唯一
        from mccore import schem_io as SC
        states = [SC.state_str(e) for e in S.read_structure(back_s)["palette"]]
        assert len(states) == len(set(states)), "写出的 palette 有重复状态"
        print("PASS 3/4  palette 去重")

        # 4) 存储策略：所有资产包模块都是 .schem
        #    注意：`packs/` 是**可选/本地内容**（仓库只带引擎 + 组合定义），
        #    纯代码 checkout 上一个模块也没有 —— 那就不该把「空集」当成失败，
        #    否则 CI 里永远红。有模块时这条仍然严格执行。
        mods = list(iter_modules())
        if not mods:
            print("SKIP 4/4  没有资产包（`packs/` 是本地内容）—— 跳过存储策略检查")
        else:
            bad = [str(p) for _pack, p in mods if p.suffix != ".schem"]
            assert not bad, f"存在非 .schem 模块: {bad[:3]}"
            print(f"PASS 4/4  {len(mods)} 个模块全部为 .schem 存储")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL PASS: 格式层（.schem 存储 / .litematic 兼容）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
