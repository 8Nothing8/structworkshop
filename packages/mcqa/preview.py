"""ASCII 预览：没有图形环境时用文字看形体（剪影 / 俯视 / 剖面）。

给 AI 和人都用得上：渲染器要 GPU 与贴图缓存，而「这栋楼到底长什么样、
第 40 层是不是空的、外壳有没有把中庭盖住」这类问题，一张字符图就够了。

用法::

    # 单文件：按调色板出字符图
    python -m mcqa.preview build.schem --all
    python -m mcqa.preview build.schem --views --ys 4 --us 8
    python -m mcqa.preview build.schem --top
    python -m mcqa.preview build.schem --slices --slice-y 10,40,74

    # 叠加对比：<base> 是原建筑，<structure> 是改完的 —— 新增部分用调色板字符，
    # 原有部分画成 "&"（原来那个「外壳补全」脚本的用法，这里通用化了）
    python -m mcqa.preview new.schem --base old.schem --views

参数 ``--ys`` / ``--us`` 是纵向 / 横向的**合并粒度**（每字符代表几格）：
大建筑用大一点，否则一屏放不下。

作为库用（回归脚本 / 生成器收尾自检）::

    from mcqa.preview import load, render_top, render_slice
    v, ch = load("build.schem")
    render_top(v, ch)
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from mccore import structure_io as S  # noqa: E402

#: 方块名（去掉 ``minecraft:``）-> 字符。按「材质家族」分组，认不出的画 ``?``。
GLASS = {
    "glass": ".", "tinted_glass": "-",
    "light_blue_stained_glass": ".", "white_stained_glass": ":",
    "gray_stained_glass": ";", "black_stained_glass": "*",
}
WHITE = {
    "white_concrete": "#", "smooth_quartz": "#", "quartz_block": "#",
    "light_gray_concrete": "%", "calcite": "%", "white_terracotta": "%",
    "stone_bricks": "%", "smooth_stone": "%", "polished_tuff": "+",
    "chiseled_tuff_bricks": "+",
}
DARK = {
    "polished_blackstone": "@", "black_concrete": "@", "polished_deepslate": "@",
    "deepslate_tiles": "@", "polished_basalt": "@", "obsidian": "@",
}
GREEN = {
    "moss_block": "*", "moss_carpet": "*", "azalea_leaves": "*",
    "flowering_azalea_leaves": "*", "spruce_leaves": "*", "oak_leaves": "*",
    "hanging_roots": "*",
}
LIGHT = {
    "sea_lantern": "o", "glowstone": "o", "ochre_froglight": "o",
    "pearlescent_froglight": "o", "shroomlight": "o", "end_rod": "o",
}
METAL = {
    "iron_block": "=", "iron_bars": "=", "iron_chain": "=", "lantern": "=",
    "waxed_lightning_rod": "=", "waxed_oxidized_copper": "=",
    "waxed_oxidized_cut_copper": "=", "oxidized_copper": "=",
    "waxed_copper_grate": "=", "waxed_copper_bulb": "=", "copper_block": "=",
}
PAVE = {
    "polished_diorite": ",", "polished_andesite": ",", "polished_granite": ",",
    "gray_concrete": ",", "smooth_stone_slab": ",",
}   # 注：深色橡木不放这里 —— 它是木材，让 char_for 的木头前缀把它归到 w，别自相矛盾
WATER = {"water": "~", "blue_ice": "~"}
WOOD = {"oak_planks": "w", "spruce_planks": "w", "birch_planks": "w",
        "oak_log": "w", "spruce_log": "w"}
REDSTONE = {"redstone_block": "!", "redstone_lamp": "!", "redstone_torch": "!",
            "crying_obsidian": "!"}
#: 石质 / 地面：最常见的两种，别让它们落到 "?"（否则剪影看不出是石头还是没画）
STONE = {"stone": "s", "cobblestone": "s", "andesite": "s", "diorite": "s",
         "granite": "s", "deepslate": "s", "tuff": "s", "stone_bricks": "s",
         "mossy_stone_bricks": "s", "smooth_stone": "s"}
GROUND = {"dirt": "g", "grass_block": "g", "podzol": "g", "mud": "g",
          "sand": "g", "red_sand": "g", "gravel": "g", "snow_block": "g",
          "sandstone": "g"}

#: 认不出的方块。
UNKNOWN_CHAR = "?"
#: 叠加模式下「原建筑」的字符（``--base`` 给的那份）。
OLD_CHAR = "&"

CHARS: dict[str, str] = {}
for _d in (STONE, GROUND, GLASS, WHITE, DARK, GREEN, LIGHT, METAL, PAVE,
           WATER, WOOD, REDSTONE):
    CHARS.update(_d)

#: 查表失败时先剥掉这些后缀再查（``cobblestone_wall`` -> ``cobblestone``）。
#: 半砖/楼梯/栅栏/墙/门窗/告示牌…… 几百个方块靠这一条就都有字符了。
STRIP_SUFFIX = ("_wall", "_fence_gate", "_fence", "_slab", "_stairs", "_pane",
                "_trapdoor", "_door", "_gate", "_button", "_pressure_plate",
                "_carpet", "_hanging_sign", "_wall_sign", "_sign", "_bars",
                "_chain", "_bed", "_banner", "_shulker_box", "_head", "_skull",
                "_bulb", "_grate", "_rod", "_lamp")

#: 木头材质前缀（``oak_stairs`` / ``spruce_trapdoor`` -> ``w``）。
WOOD_SPECIES = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak",
                "mangrove", "cherry", "bamboo", "crimson", "warped", "pale_oak")

#: 颜色前缀（``red_concrete`` -> 剥成 ``concrete`` 再查）。
COLOR_PREFIX = ("light_gray", "light_blue", "white", "orange", "magenta",
                "yellow", "lime", "pink", "gray", "cyan", "purple", "blue",
                "brown", "green", "red", "black")

#: 材质家族兜底（颜色/后缀剥完之后剩下的词）。
FAMILY = {
    "concrete": "%", "concrete_powder": "%", "terracotta": ",", "wool": "*",
    "planks": "w", "log": "w", "wood": "w", "hyphae": "w", "stem": "w",
    "glass": ".", "glass_pane": ".", "brick": "%", "bricks": "%",
    "stone": "s", "cobblestone": "s", "deepslate": "s", "tuff": "s",
    "sandstone": "g", "dirt": "g", "sand": "g", "gravel": "g",
    "leaves": "*", "carpet": "*",
}

#: 图例（命令行会打出来，跟上面的分组保持同步）
LEGEND = (("石质", "s"), ("土/地", "g"), ("玻璃", "."), ("白/浅灰", "%"),
          ("深色", "@"), ("绿植", "*"), ("发光", "o"), ("金属", "="),
          ("铺装", ","), ("水", "~"), ("木材", "w"), ("红石", "!"),
          ("认不出", UNKNOWN_CHAR))


def char_for(name: str) -> str:
    """方块名 -> 字符（``minecraft:`` 前缀可带可不带）。

    查表**从精确到宽松**，目标是「剪影看得懂」，不是「材质分得清」：

    1. 精确名（``CHARS``）→ 材质家族（``FAMILY``）
    2. 剥掉 ``_wall`` / ``_stairs`` / ``_slab`` … 再查
    3. 木头材质前缀（``oak_stairs``）→ ``w``
    4. 剥掉颜色前缀（``red_concrete`` → ``concrete``）再查
    5. 认不出才画 ``?``
    """
    base = name.replace("minecraft:", "")
    if base.endswith("air"):                     # air / cave_air / void_air
        return " "
    if base in CHARS:
        return CHARS[base]
    if base in FAMILY:
        return FAMILY[base]
    for suf in STRIP_SUFFIX:
        if base.endswith(suf):
            base = base[: -len(suf)]
            if base in CHARS:
                return CHARS[base]
            if base in FAMILY:
                return FAMILY[base]
            break
    if any(base == w or base.startswith(w + "_") for w in WOOD_SPECIES):
        return "w"
    for pre in COLOR_PREFIX:
        if base.startswith(pre + "_"):
            rest = base[len(pre) + 1:]
            if rest in CHARS:
                return CHARS[rest]
            if rest in FAMILY:
                return FAMILY[rest]
            break
    for key, ch in FAMILY.items():
        if base.startswith(key + "_") or base.endswith("_" + key):
            return ch
    return UNKNOWN_CHAR


def load(path: str) -> tuple[np.ndarray, list[str]]:
    """读一个结构 -> ``(体素, 每个调色板项对应的字符)``。"""
    d = S.read_structure(path)
    return d["voxels"], [char_for(p["Name"]) for p in d["palette"]]


def outer_index(m: np.ndarray, axis: str, side: str):
    """每条射线上**最外层**那格在该轴上的下标；返回 ``(idx, has)``。

    ``has`` 标记这条射线上到底有没有东西。叠加对比时要用**同一组下标**去
    取两份结构的值，否则两边取到的不是同一格（就变成了广播形状不匹配）。
    """
    n = m.shape[2] if axis == "x" else m.shape[1]
    if axis == "x":
        has = m.any(axis=2)
        if side == "min":
            idx = np.where(has, m.argmax(axis=2), 0)
        else:
            idx = np.where(has, n - 1 - m[:, :, ::-1].argmax(axis=2), 0)
    else:
        has = m.any(axis=1)
        if side == "min":
            idx = np.where(has, m.argmax(axis=1), 0)
        else:
            idx = np.where(has, n - 1 - m[:, ::-1, :].argmax(axis=1), 0)
    return idx, has


def _take(v: np.ndarray, idx: np.ndarray, axis: str) -> np.ndarray:
    """按 ``outer_index`` 给的下标取格（结果降一维）。"""
    if axis == "x":
        return np.take_along_axis(v, idx[:, :, None], axis=2)[:, :, 0]
    return np.take_along_axis(v, idx[:, None, :], axis=1)[:, 0, :]


def outer_view(v: np.ndarray, m: np.ndarray, axis: str, side: str):
    """沿 ``axis`` 看过去，取每条射线上**最外层**那格。

    ``axis in {'x','z'}``（侧视），``side in {'min','max'}``（从哪边看）。
    返回 ``(vals, has)``：``vals`` 是该格在 ``v`` 里的调色板下标，
    ``has`` 标记这条射线上到底有没有东西（没有就画空格）。

    以前这里写死了 ``511``（只对某个 512³ 的建筑成立）；现在按 ``v.shape`` 走。
    """
    idx, has = outer_index(m, axis, side)
    return _take(v, idx, axis), has


def _cell_char(sub: np.ndarray, chars: list[str], mask: np.ndarray | None = None,
               old_char: str | None = None) -> str:
    """把一个 ``ys×us`` 小块的调色板下标压成一个字符（取出现最多的那个）。

    ``mask`` 给定时只统计掩码内的格子（叠加模式用：只看「新增」的那些）。
    """
    if mask is not None:
        sub = sub[mask]
    else:
        sub = sub[sub != 0]
    if sub.size == 0:
        return old_char if old_char is not None else " "
    return chars[int(np.bincount(sub.reshape(-1)).argmax())]


def _fit(a: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    """把 ``a`` 裁剪/补零到 ``shape``（叠加对比用）。

    两份结构的尺寸常常不一样（比如外壳比原建筑高、宽）。语义：
    公共角落逐格对齐，多出来的部分按「另一边是空的」算 —— 于是
    ``--base`` 之外的地方全算「新增」，正是叠图想看的东西。
    """
    if a.shape == shape:
        return a
    out = np.zeros(shape, dtype=a.dtype)
    common = tuple(slice(0, min(x, y)) for x, y in zip(a.shape, shape))
    out[common] = a[common]
    return out


def render_side(title: str, v: np.ndarray, chars: list[str], axis: str, side: str,
                ys: int = 4, us: int = 8,
                base: np.ndarray | None = None, old_char: str = OLD_CHAR) -> None:
    """一张侧视图（剪影：每条射线只画最外层那格）。

    注意水平轴：沿 ``x`` 看过去，图上的横轴是 **Z**；沿 ``z`` 看过去横轴是 **X**。
    （旧脚本把这里写死成 512 —— 只有正方体建筑才对。）
    """
    m = (v != 0) if base is None else ((v != 0) | (base != 0))
    idx, has = outer_index(m, axis, side)
    vals = _take(v, idx, axis)
    bvals = _take(base, idx, axis) if base is not None else None
    height = v.shape[0]
    width = v.shape[1] if axis == "x" else v.shape[2]
    print("=== %s （行 Y step%d / 列 step%d）===" % (title, ys, us))
    for y in range(height - 1, -1, -ys):
        ychunk = np.arange(max(y - ys + 1, 0), y + 1)
        row = []
        for u in range(0, width, us):
            uchunk = np.arange(u, min(u + us, width))
            subh = has[np.ix_(ychunk, uchunk)]
            if not subh.any():
                row.append(" ")
                continue
            sub = vals[np.ix_(ychunk, uchunk)]
            if bvals is None:
                row.append(_cell_char(sub, chars, mask=subh))
            else:
                # 叠加：最外层那格在原建筑里是空的 → 算「新增」，画调色板字符；
                # 否则画 & （原建筑本来就有的）
                bsub = bvals[np.ix_(ychunk, uchunk)]
                newmask = subh & (bsub == 0)
                row.append(_cell_char(sub, chars, mask=newmask, old_char=old_char))
        print("%4d %s" % (y, "".join(row)))
    print()


def render_top(v: np.ndarray, chars: list[str], ys: int = 8, us: int = 8) -> None:
    """俯视图：每列画最高的那格。"""
    m = v != 0
    has = m.any(axis=0)
    idx = np.where(has, m.shape[0] - 1 - m[::-1, :, :].argmax(axis=0), 0)
    vals = np.take_along_axis(v, idx[None, :, :], axis=0)[0]
    print("=== TOP view（每列最高格；行 Z step%d / 列 X step%d）===" % (ys, us))
    for z in range(0, v.shape[1], ys):
        row = []
        for x in range(0, v.shape[2], us):
            sub = vals[z:z + ys, x:x + us]
            sh = has[z:z + ys, x:x + us]
            row.append(_cell_char(sub, chars, mask=sh))
        print("%4d %s" % (z, "".join(row)))
    print()


def render_slice(v: np.ndarray, chars: list[str], y: int, us: int = 8) -> None:
    """一层平面图（``y`` 是层号，越界就什么都不画）。"""
    print("=== SLICE y=%d（行 Z step%d / 列 X step%d）===" % (y, us, us))
    if not (0 <= y < v.shape[0]):
        print("  （这一层不存在：y 要在 0..%d 之间）" % (v.shape[0] - 1))
        print()
        return
    for z in range(0, v.shape[1], us):
        row = []
        for x in range(0, v.shape[2], us):
            sub = v[y, z:z + us, x:x + us]
            row.append(_cell_char(sub, chars))
        print("%4d %s" % (z, "".join(row)))
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="ASCII 预览：剪影 / 俯视 / 剖面（无图形环境时看形体）",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("structure", help="要看的结构（.schem / .litematic）")
    ap.add_argument("--base", default=None, metavar="FILE",
                    help="叠加对比：这份当「原建筑」画成 '%s'，只有新增部分用调色板字符" % OLD_CHAR)
    ap.add_argument("--views", action="store_true", help="四张侧视图（东南西北）")
    ap.add_argument("--top", action="store_true", help="俯视图")
    ap.add_argument("--slices", action="store_true", help="平面切片")
    ap.add_argument("--all", action="store_true", help="上面全要")
    ap.add_argument("--slice-y", default=None, metavar="Y[,Y...]",
                    help="切片层号（默认按高度均分取 6 层）")
    ap.add_argument("--ys", type=int, default=4, help="纵向合并粒度（每字符几格，默认 4）")
    ap.add_argument("--us", type=int, default=8, help="横向合并粒度（每字符几格，默认 8）")
    a = ap.parse_args(argv)

    if not (a.views or a.top or a.slices or a.all):
        a.views = a.top = True          # 不给参数时给个有用的默认

    v, chars = load(a.structure)
    base = load(a.base)[0] if a.base else None
    if base is not None:
        if base.shape != v.shape:
            print("注意：--base 尺寸 %s ≠ 主结构 %s，按公共角落对齐（多出来的算新增）"
                  % (tuple(base.shape[::-1]), tuple(v.shape[::-1])), file=sys.stderr)
        base = _fit(base, v.shape)

    x, y, z = v.shape[2], v.shape[0], v.shape[1]
    print("%s  X×Y×Z = %d×%d×%d" % (a.structure, x, y, z))
    if base is None:
        print("图例：%s" % "  ".join("%s=%s" % (c, n) for n, c in LEGEND))
    else:
        print("图例：%s = 原建筑（--base），其余字符 = 新增" % OLD_CHAR)
    print()
    if a.views or a.all:
        render_side("NORTH（看 +Z）", v, chars, "z", "min", a.ys, a.us, base)
        render_side("SOUTH（看 -Z）", v, chars, "z", "max", a.ys, a.us, base)
        render_side("WEST（看 +X）", v, chars, "x", "min", a.ys, a.us, base)
        render_side("EAST（看 -X）", v, chars, "x", "max", a.ys, a.us, base)
    if a.top or a.all:
        render_top(v, chars, a.ys, a.us)
    if a.slices or a.all:
        if a.slice_y:
            levels = [int(x) for x in a.slice_y.replace(",", " ").split()]
        else:
            h = v.shape[0]
            levels = sorted({int(h * f) for f in (0.05, 0.2, 0.4, 0.6, 0.8, 0.95)})
        for y in levels:
            render_slice(v, chars, y, a.us)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
