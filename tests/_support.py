"""渲染类冒烟的共用前置：Minecraft 资源缓存（`.cache/mcassets/`）没热就跳过。

为什么需要
----------
`mcrender` 的方块模型 / 贴图是从 mcmeta 镜像**按需下载**并缓存的
（见 `packages/mcrender/assets.py`）。渲染类冒烟为了可复现，走的是
``auto_assets(..., offline=True)`` —— 只读缓存、不联网。于是：

* 老机器上缓存早就热了 → 测试跑得动；
* **全新 clone / CI / 没网** → 缓存是空的 → 拿不到模型，测试会在断言里失败，
  报出来的却是「水没楼梯也画出水面」这种看不懂的现象，而不是「你没缓存」。

这里把这件事收成一处：``need_mc_assets()`` 先看离线缓存够不够；
不够就**联网补一次**（首次 clone 的常见情形）；再不够就
**明确跳过（退出码 2）** 并告诉人怎么预热。

用法::

    import _support
    _support.need_mc_assets(4903, blocks=("oak_stairs", "water"),
                            script="fluid_smoke.py")

调用方需要把 `tests/` 放进 `sys.path`：

    sys.path.insert(0, str(Path(__file__).resolve().parent))
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 跳过时统一的退出码（与 tests/_browser.js 的 skip 对齐）。
SKIP_CODE = 2


def _skip(msg: str, script: str | None) -> None:
    print(f"[skip] {msg}")
    if script:
        print(f"       （tests/{script}）")
    raise SystemExit(SKIP_CODE)


def need_compositions(*need: str, script: str | None = None) -> None:
    """确保 ``compositions/<name>/`` 在（组合是**可选内容**，不在代码仓库里）。

    缺了就跳过（退出码 2）并告诉人怎么弄进来 —— 不要让 `import kit` 抛
    ImportError 把整个套件弄红。

    用法（放在 ``sys.path.insert`` 到组合目录**之前**）::

        _support.need_compositions("math-cube", script="math_cells_smoke.py")
        sys.path.insert(0, str(REPO / "compositions" / "math-cube"))
    """
    missing = [n for n in need if not (ROOT / "compositions" / n /
                                       "structure.json").is_file()]
    if not missing:
        return
    _skip(
        "`compositions/` 里没有 " + ", ".join(missing)
        + "\n       组合是**可选内容**（代码仓库不附带）：把一套 compositions "
          "拷到仓库根目录即可。\n"
          "       引擎本身不依赖它 —— 零依赖的自检：\n"
          "         python -m mccore.bootstrap --check",
        script)


def need_mc_assets(data_version: int | None = None, *,
                   version: str | None = None,
                   blocks: tuple[str, ...] = ("stone",),
                   script: str | None = None):
    """确保 ``blocks`` 在资源缓存里可解析；返回可用的 :class:`Assets`。

    1. 离线能全解析 → 直接用（测试要的确定性路径）；
    2. 不能 → 联网 ``prefetch`` 一次，再试；
    3. 还不行 → 跳过（退出码 2），并打印预热命令。
    """
    if str(ROOT / "packages") not in sys.path:
        sys.path.insert(0, str(ROOT / "packages"))
    from mcrender.assets import auto_assets

    def ok(a) -> bool:
        return all(a.blockstate(b) is not None for b in blocks)

    warm = auto_assets(data_version, version, offline=True, verbose=False)
    if ok(warm):
        return warm

    # 缓存没热：联网补一次（全新 clone / CI 的常见情形）。
    online = auto_assets(data_version, version, offline=False, verbose=False)
    try:
        online.prefetch(list(blocks))
    except Exception as e:  # noqa: BLE001 —— 没网/镜像挂了都走“跳过”
        print(f"  ! prefetch 失败：{type(e).__name__}: {str(e)[:160]}")
    if ok(online):
        return online

    missing = [b for b in blocks if online.blockstate(b) is None]
    _skip(
        "Minecraft 资源缓存不可用，缺 " + ", ".join(missing[:6])
        + ("…" if len(missing) > 6 else "")
        + "\n       首次渲染需要联网从 mcmeta 镜像拉方块状态/模型/贴图；"
          "拉到之后就完全离线了。预热：\n"
          f"         python -m mcrender.assets --data-version "
          f"{data_version or 4903} --prefetch {' '.join(blocks[:8])}",
        script)
