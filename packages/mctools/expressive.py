"""表达工具：``field``（公式 → 方块）与 ``glyph``（文字 → 方块）。

这两个工具是**数学域展品**的通用笔。``field`` 把一条公式变成体素（占位 /
等值壳 / 分级色），``glyph`` 把一行文字（含中文）刻成墙面铭牌。

设计上它们与其余 27 个工具完全同构（``fn(ctx) -> str``，就地改 ``ctx.vol``），
所以 CLI / Web 面板 / 脚本三处自动共用。
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from mctools.spec import Param, ToolSpec

__all__ = ["t_field", "t_glyph", "bbox_field", "bbox_glyph", "SPECS_EXTRA",
           "FONT_CANDIDATES", "find_font", "render_text_mask"]


# ------------------------------------------------------------------ field
def _split3(text: str, default=(0, 0, 0)) -> tuple[int, int, int]:
    s = str(text or "").replace(" ", "").strip("[]()")
    if not s:
        return default
    parts = [p for p in s.split(",") if p != ""]
    while len(parts) < 3:
        parts.append("0")
    return tuple(int(round(float(v))) for v in parts[:3])   # type: ignore[return-value]


def t_field(ctx) -> str:
    """公式 f(x,y,z) → 体素。三种模式：solid / shell / bands。"""
    from mccore import fields as F

    expr = str(ctx.param("expr", "x*x+y*y+z*z-100"))
    mode = str(ctx.param("mode", "shell"))
    thick = ctx.p_int("shell", 2)
    sy, sz, sx = ctx.vol.shape
    Y, Z, X = np.mgrid[0:sy, 0:sz, 0:sx]
    fn = F.compile_expr(expr)
    f = fn(X + ctx.origin[0], Y + ctx.origin[1], Z + ctx.origin[2])
    blocks = ctx.blocks_arg("blocks", "minecraft:white_concrete")
    rng_txt = (f"值域 [{f.min():.2f}, {f.max():.2f}]")
    if mode == "bands":
        idx, _pal = F.stratify(f, blocks, dither=ctx.p_bool("dither", True),
                               seed=ctx.p_int("seed", 7))
        extra = ctx.mask_arg("mask")
        if extra is not None:
            idx = np.where(extra, idx, 0)
        n = 0
        for k, name in enumerate(blocks):
            sub = idx == (k + 1)
            if sub.any():
                n += ctx.fill(sub, name)
        return f"f = {expr} → bands[{len(blocks)}] {rng_txt} 写入 {n:,} 格"
    m = F.mask_of(f, str(ctx.param("op", "<=")), ctx.p_float("threshold", 0.0))
    if mode == "shell" and int(thick) > 0:
        m = F.shell_of(m, thick)
    extra = ctx.mask_arg("mask")
    if extra is not None:
        m = m & extra
    n = ctx.add(m, blocks[0])
    tail = f"（壳厚 {thick}）" if mode == "shell" else ""
    return f"f = {expr} → {mode} {rng_txt} 写入 {n:,} 格{tail}"


def bbox_field(params, size, sel_box):
    """field 自带几何：整张画布（region=own）。"""
    return (0, 0, 0, size[0] - 1, size[1] - 1, size[2] - 1)


# ------------------------------------------------------------------ glyph
#: 中文铭碑用的字体：Windows / macOS / Linux 都列上，可用
#: ``STRUCTWORKSHOP_FONT`` 指定一个绝对路径（最稳）。
FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",        # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",      # 黑体
    "C:/Windows/Fonts/simsun.ttc",      # 宋体
    "C:/Windows/Fonts/Deng.ttf",        # 等线
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)


def find_font(prefer: str = "") -> str | None:
    """找一个能画中文的字体；找不到返回 None（退回 PIL 内置点阵）。"""
    if prefer and Path(prefer).is_file():
        return prefer
    env = os.environ.get("STRUCTWORKSHOP_FONT")
    if env and Path(env).is_file():
        return env
    for p in FONT_CANDIDATES:
        if Path(p).is_file():
            return p
    return None


def render_text_mask(text: str, px: int = 16, font_path: str = "",
                     bold: bool = False) -> tuple[np.ndarray, str]:
    """把一段文字渲染成 0/1 掩码（``(行, 列)``，行 = 从上到下）。"""
    from PIL import Image, ImageDraw, ImageFont
    fp = find_font(font_path)
    try:
        font = ImageFont.truetype(fp, int(px)) if fp else ImageFont.load_default()
    except Exception:                                     # noqa: BLE001
        font = ImageFont.load_default()
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    box = probe.textbbox((0, 0), text, font=font)
    w, h = max(1, box[2] - box[0]), max(1, box[3] - box[1])
    img = Image.new("L", (w + 4, h + 4), 0)
    ImageDraw.Draw(img).text((2 - box[0], 2 - box[1]), text, fill=255, font=font,
                             stroke_width=1 if bold else 0)
    return np.asarray(img) > 96, (fp or "PIL-default")


def t_glyph(ctx) -> str:
    """文字 → 方块铭牌（巨碑）。中文靠系统字体，缺字时退回点阵。"""
    text = str(ctx.param("text", "数学"))
    px = ctx.p_int("px", 16)
    scale = max(1, ctx.p_int("scale", 1))
    depth = max(1, ctx.p_int("depth", 1))
    ink = ctx.blocks_arg("blocks", "minecraft:polished_blackstone")[0]
    back = str(ctx.param("back", "") or "")
    axis = str(ctx.param("axis", "x"))
    mask, _fp = render_text_mask(text, px, str(ctx.param("font", "")),
                                 ctx.p_bool("bold"))
    rows, cols = mask.shape
    at = ctx.param("at") or ctx.param("_center")
    if at:
        wx, wy, wz = _split3(str(at))
        ox = wx - ctx.origin[0]
        oy = wy - ctx.origin[1]
        oz = wz - ctx.origin[2]
    else:
        ox, oy, oz = 0, 0, 0
    n = 0
    for r in range(rows):
        for c in range(cols):
            if not mask[r, c] and not back:
                continue
            for s in range(depth):
                for rr in range(scale):
                    for cc in range(scale):
                        yy = oy + (rows - 1 - r) * scale + rr
                        if axis == "z":
                            xx, zz = ox + s, oz + c * scale + cc
                        elif axis == "y":
                            xx, zz = ox + c * scale + cc, oz + r * scale + rr
                            yy = oy + s
                        else:
                            xx, zz = ox + c * scale + cc, oz + s
                        ctx.put(xx, yy, zz, ink if mask[r, c] else back)
                        n += 1
    return (f"glyph {text!r} → {rows}×{cols} 点阵 ×{scale} 深度 {depth}  "
            f"写入 {n:,} 格")


def bbox_glyph(params, size, sel_box):
    text = str(params.get("text") or "数学")
    px = int(params.get("px") or 16)
    scale = max(1, int(params.get("scale") or 1))
    depth = max(1, int(params.get("depth") or 1))
    mask, _ = render_text_mask(text, px, str(params.get("font") or ""))
    rows, cols = mask.shape
    axis = str(params.get("axis") or "x")
    at = params.get("at") or params.get("_center") or "0,0,0"
    ox, oy, oz = _split3(str(at))
    if axis == "z":
        return (ox, oy, oz, ox + depth - 1, oy + rows * scale - 1,
                oz + cols * scale - 1)
    if axis == "y":
        return (ox, oy, oz, ox + cols * scale - 1, oy + depth - 1,
                oz + rows * scale - 1)
    return (ox, oy, oz, ox + cols * scale - 1, oy + rows * scale - 1,
            oz + depth - 1)


# ------------------------------------------------------------------ 规格
SPECS_EXTRA = [
    ToolSpec(
        id="field", label="公式体", group="shape", fn=t_field,
        hint="用 f(x,y,z) 公式生成体素：solid 占位 / shell 等值壳 / bands 分级色",
        region="own", bbox_fn=bbox_field, needs_block=False, pad=0,
        params=(
            Param("expr", "公式 f(x,y,z)", "text", "x*x+y*y+z*z-100",
                  hint="可用 sin/cos/sqrt/exp/log/abs/min/max/clamp/noise/fbm 等"),
            Param("mode", "模式", "enum", "shell",
                  options=("solid", "shell", "bands"),
                  label_of={"solid": "占位（f≤t 实心）", "shell": "等值壳（推荐）",
                            "bands": "分级色（颜色 = 场值）"}),
            Param("op", "比较", "enum", "<=", options=("<=", "<", ">=", ">"),
                  label_of={"<=": "f ≤ t", "<": "f < t", ">=": "f ≥ t",
                            ">": "f > t"}),
            Param("threshold", "阈值 t", "float", 0.0, -1000, 1000, 0.1),
            Param("shell", "壳厚", "int", 2, 0, 24, 1),
            Param("blocks", "方块（多档 = 色带）", "blocks",
                  "minecraft:white_concrete"),
            Param("dither", "抖动", "bool", True),
            Param("seed", "随机种子", "int", 7, 0, 99999, 1),
        )),
    ToolSpec(
        id="glyph", label="铭碑（文字）", group="paint", fn=t_glyph,
        hint="把文字（含中文）刻成方块铭牌：巨碑 / 门牌 / 公式墙",
        region="own", bbox_fn=bbox_glyph, needs_block=False, pad=0,
        params=(
            Param("text", "文字", "text", "数学"),
            Param("at", "世界位置 x,y,z", "text", "0,0,0"),
            Param("axis", "面板方向", "enum", "x", options=("x", "z", "y"),
                  label_of={"x": "朝 X（YZ 面）", "z": "朝 Z（XY 面）",
                            "y": "平铺地面"}),
            Param("px", "字号（像素）", "int", 16, 8, 128, 1),
            Param("scale", "放大倍数", "int", 1, 1, 8, 1),
            Param("depth", "厚度", "int", 1, 1, 8, 1),
            Param("blocks", "字色", "blocks", "minecraft:polished_blackstone"),
            Param("back", "底色（留空 = 空）", "text", ""),
            Param("font", "字体文件", "text", ""),
            Param("bold", "加粗", "bool", False),
        )),
]
