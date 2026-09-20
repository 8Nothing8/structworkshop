"""渲染背景冒烟：white / black / dark / transparent（透明要真带 alpha）。

跑真的 mcrender.cli（小夹具，几秒），逐模式检查背景像素与几何像素：

* dark  → 角落 (26,26,26)
* black → 角落 (0,0,0)
* white → 角落 (255,255,255)
* transparent → RGBA，角落 alpha=0，模型处 alpha>0 且颜色不是全黑（回归：
  曾因预乘缩放时 alpha 没有归一化，被乘成 0）

Usage:  python tests/render_bg_smoke.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

SRC = ROOT / "tests" / "fixtures" / "small_house.schem"
TMP = Path(tempfile.mkdtemp(prefix="structworkshop_bg_"))


def render(mode: str) -> Image.Image:
    out = TMP / f"bg_{mode}"
    r = subprocess.run(
        [sys.executable, "-m", "mcrender.cli", str(SRC), "--views", "iso",
         "--scale", "3", "--background", mode, "--quiet", "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"{mode} 渲染失败：{r.stdout[-400:]}{r.stderr[-400:]}"
    p = Path(str(out) + "_iso.png")
    assert p.is_file(), f"{mode} 没有产出 {p.name}"
    return Image.open(p)


def main() -> int:
    ok = []

    dark = render("dark")
    assert dark.mode == "RGB", dark.mode
    a = np.asarray(dark.convert("RGB"))
    assert tuple(a[1, 1]) == (26, 26, 26), a[1, 1]
    ok.append("dark 深灰底 (26,26,26)")

    black = render("black")
    a = np.asarray(black.convert("RGB"))
    assert tuple(a[1, 1]) == (0, 0, 0), a[1, 1]
    ok.append("black 纯黑底 (0,0,0)")

    white = render("white")
    a = np.asarray(white.convert("RGB"))
    assert tuple(a[1, 1]) == (255, 255, 255), a[1, 1]
    ok.append("white 纯白底 (255,255,255)")

    tr = render("transparent")
    assert tr.mode == "RGBA", tr.mode
    t = np.asarray(tr)
    alpha = t[:, :, 3]
    assert tuple(t[1, 1]) == (0, 0, 0, 0), t[1, 1]
    assert int((alpha == 0).sum()) > 0, "没有透明像素"
    cov = alpha > 200
    assert int(cov.sum()) > 50, f"模型覆盖太少：{int(cov.sum())}"
    rgb = t[:, :, :3][cov].astype(np.float64)
    assert rgb.mean() > 20, f"模型像素被压黑：mean={rgb.mean():.1f}"   # 回归点
    assert not np.allclose(rgb.max(axis=0), 0), "模型像素全黑"
    ok.append("transparent 真透明（角落 alpha=0，模型 alpha=255 且不发黑）")

    # 同一模型：透明模式与黑底模式的几何像素应当接近（说明 alpha 没改坏颜色）
    bk = np.asarray(black.convert("RGB")).astype(np.int16)
    diff = np.abs(bk[cov] - np.clip(rgb, 0, 255).astype(np.int16)).mean()
    assert diff < 12, f"透明图与黑底图差异过大：{diff:.1f}"
    ok.append(f"透明图颜色与黑底图一致（平均差 {diff:.1f}/255）")

    print(f"ALL PASS ({len(ok)} 项): 渲染背景")
    for line in ok:
        print(f"  PASS {line}")
    for f in TMP.glob("*"):
        f.unlink()
    TMP.rmdir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
