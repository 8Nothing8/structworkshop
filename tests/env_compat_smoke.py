"""改名兼容冒烟：新名（STRUCTWORKSHOP_*）为主，旧名（MCFORGE_*）继续兜底。

项目从 mcforge 改名 structworkshop（避免与 Minecraft Forge 模组加载器混淆），但
**旧环境变量、旧落盘键一律继续认** —— 否则改名前的脚本、快捷方式、已存下的 `.schem`
就全废了。这个冒烟把这几条兼容契约钉死（防止以后有人"顺手清理旧名"）：

  1. 仓库根：`STRUCTWORKSHOP_ROOT` 生效；`MCFORGE_ROOT` 兜底；两个都设时新名优先
  2. 打开上限（4 个）：`STRUCTWORKSHOP_MAX_STRUCTURE_*` / `STRUCTWORKSHOP_WARN_STRUCTURE_CELLS`
     生效，同名 `MCFORGE_*` 兜底
  3. 项目目录：`STRUCTWORKSHOP_PROJECT_DIR` 生效；`MCFORGE_PROJECT_DIR` 兜底
  4. 装配清单落盘键：写 `StructworkshopModules`；读时 `McForgeModules`（改名前的产物）也认

Usage:  python tests/env_compat_smoke.py
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

from mccore import backup as BK                      # noqa: E402
from mccore import projects as PJ                    # noqa: E402
from mccore.paths import repo_root                   # noqa: E402
from mcstudio.session import LAYOUT_KEY, read_layout_meta  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="structworkshop_env_"))
OK: list[str] = []

#: 被测的 4 个打开上限：键 -> (新环境变量, 旧环境变量, 测试值)
LIMITS = {
    "max_mb": ("STRUCTWORKSHOP_MAX_STRUCTURE_MB", "MCFORGE_MAX_STRUCTURE_MB", "12.5"),
    "max_cells": ("STRUCTWORKSHOP_MAX_STRUCTURE_CELLS", "MCFORGE_MAX_STRUCTURE_CELLS", "1234"),
    "max_blocks": ("STRUCTWORKSHOP_MAX_STRUCTURE_BLOCKS", "MCFORGE_MAX_STRUCTURE_BLOCKS", "4321"),
    "warn_cells": ("STRUCTWORKSHOP_WARN_STRUCTURE_CELLS", "MCFORGE_WARN_STRUCTURE_CELLS", "999"),
}


def check(name: str, cond: bool, detail: object = "") -> None:
    assert cond, f"{name} 失败：{detail}"
    OK.append(name)
    print(f"  PASS  {name}")


def root_case(env: dict[str, str], expect: Path) -> Path:
    """设好环境变量后取仓库根（repo_root 有 lru_cache，必须先清）。"""
    for name in ("STRUCTWORKSHOP_ROOT", "MCFORGE_ROOT"):
        os.environ.pop(name, None)
    os.environ.update(env)
    repo_root.cache_clear()
    return repo_root()


def main() -> int:
    other = TMP / "other"
    other.mkdir()

    # 1) 仓库根
    check("STRUCTWORKSHOP_ROOT 生效", root_case({"STRUCTWORKSHOP_ROOT": str(TMP)}, TMP) == TMP.resolve())
    check("旧名 MCFORGE_ROOT 兜底", root_case({"MCFORGE_ROOT": str(TMP)}, TMP) == TMP.resolve())
    check("两个都设时新名优先",
          root_case({"STRUCTWORKSHOP_ROOT": str(other), "MCFORGE_ROOT": str(TMP)}, other) == other.resolve())

    # 2) 打开上限（新名生效 + 旧名兜底 + 新名优先）
    for key, (env_new, env_old, val) in LIMITS.items():
        for env_name in (env_new, env_old):
            os.environ[env_name] = val
            eff = BK.limits_effective()
            check(f"{env_name} 生效",
                  eff["source"][key] == "env" and float(eff["values"][key]) == float(val),
                  eff["values"].get(key))
            os.environ.pop(env_name, None)
    os.environ["STRUCTWORKSHOP_MAX_STRUCTURE_CELLS"] = "111"
    os.environ["MCFORGE_MAX_STRUCTURE_CELLS"] = "222"
    check("上限：新名优先于旧名", BK.limits_effective()["values"]["max_cells"] == 111,
          BK.limits_effective()["values"]["max_cells"])
    os.environ.pop("STRUCTWORKSHOP_MAX_STRUCTURE_CELLS", None)
    os.environ.pop("MCFORGE_MAX_STRUCTURE_CELLS", None)

    # 3) 项目目录
    for env_name in ("STRUCTWORKSHOP_PROJECT_DIR", "MCFORGE_PROJECT_DIR"):
        os.environ.pop(env_name, None)
    os.environ["STRUCTWORKSHOP_PROJECT_DIR"] = str(TMP / "p1")
    check("STRUCTWORKSHOP_PROJECT_DIR 生效", PJ.current() == (TMP / "p1").resolve())
    os.environ.pop("STRUCTWORKSHOP_PROJECT_DIR", None)
    os.environ["MCFORGE_PROJECT_DIR"] = str(TMP / "p2")
    check("旧名 MCFORGE_PROJECT_DIR 兜底", PJ.current() == (TMP / "p2").resolve())
    os.environ.pop("MCFORGE_PROJECT_DIR", None)
    check("都没有 -> None", PJ.current() is None)

    # 4) 装配清单落盘键
    prov = [{"id": "m1", "pos": [0, 0, 0], "rot": 0}]
    blob = json.dumps(prov, ensure_ascii=False)
    check("写入用新键 StructworkshopModules", LAYOUT_KEY == "StructworkshopModules", LAYOUT_KEY)
    check("读新键", read_layout_meta({"StructworkshopModules": blob}) == prov)
    check("读旧键 McForgeModules（改名前的 .schem）", read_layout_meta({"McForgeModules": blob}) == prov)
    check("新键优先于旧键",
          read_layout_meta({"StructworkshopModules": json.dumps([{"id": "new"}]),
                            "McForgeModules": blob}) == [{"id": "new"}])
    check("坏 JSON 不炸", read_layout_meta({"McForgeModules": "{oops"}) is None)
    check("空 Metadata -> None", read_layout_meta({}) is None and read_layout_meta(None) is None)

    # 5) 前端 localStorage 键（只做静态核对：新键在、旧键作兜底常量保留）
    web = ROOT / "packages/mcstudio/web"
    app_js = (web / "app.js").read_text(encoding="utf-8")
    ed_js = (web / "editor.js").read_text(encoding="utf-8")
    check("app.js 背景键已改名 + 旧键兜底",
          "'structworkshop.background'" in app_js and "'mcforge.background'" in app_js)
    check("editor.js 最近方块键已改名 + 旧键兜底",
          "'structworkshop.recentBlocks'" in ed_js and "'mcforge.recentBlocks'" in ed_js)
    check("前端不再写旧键",
          "localStorage.setItem('mcforge." not in app_js + ed_js)

    print(f"\nALL PASS  ({len(OK)} 项兼容契约)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
