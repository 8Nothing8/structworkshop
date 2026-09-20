"""一键评审回环：多视图渲染 → 分块送视觉模型 → 写评审报告 <out>.md。

Pipeline: render multiple views -> AI vision critique -> review report.

Usage:
  python -m mcqa.review_loop build.schem [--views iso,front,top]
      [--scale 3] [--cut x=112] [--night] [--hero]
      [--prompt "..."] [--out review] [--no-ai]
      [--max-tokens 1600] [--model deepseek-v4-flash-vision-exp]

Renders <out>_<view>.png (plus <out>_night_*.png / <out>_hero_*.png /
<out>_cut*.png) and writes the critique to <out>.md.
Images are sent to the vision model in chunks of 8.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from mccore.paths import child_env, repo_root  # noqa: E402

ROOT = repo_root()

DEFAULT_VIEWS = "iso,front,back,left,right,top"
DEFAULT_PROMPT = (
    "你是资深 Minecraft 建筑评审。请逐张描述你实际看到的内容"
    "(不要臆测没看到的东西),然后给出:\n"
    "1. 每张图的所见描述(1-2 句/张)\n"
    "2. 问题清单,每条格式:[P0致命/P1重要/P2细节] 位置或方向 + 问题"
    " + 具体修复建议(用方块名/尺寸/坐标)\n"
    "3. 整体评分 /10 及理由\n"
    "只基于图片事实。结构问题(悬浮、缺失支撑、门不配对)必须结合渲染图判断。"
)


def run(cmd: list[str]) -> None:
    # child_env()：让子进程用**本仓库**的 mccore/mcrender（机器上可能有别的 checkout）
    subprocess.run(cmd, cwd=str(ROOT), check=True, env=child_env())


def render(lt: str, out: str, views: str, scale: float,
           extra: list[str] | None = None) -> list[str]:
    """Render and return the produced PNG paths."""
    base = Path(out)
    cmd = [sys.executable, "-m", "mcrender.cli", str(lt),
           "--views", views, "--scale", str(scale), "--out", out]
    cmd += extra or []
    run(cmd)
    return sorted(str(p) for p in base.parent.glob(base.name + "_*.png"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="渲染多视角 + AI 视觉批判,报告写到 <out>.md",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("input", help=".schem/.schem 文件")
    ap.add_argument("--out", default=None,
                    help="输出前缀(默认 <输入名>_review)")
    ap.add_argument("--views", default=DEFAULT_VIEWS,
                    help="逗号分隔视角(默认 6 正交视角)")
    ap.add_argument("--scale", type=float, default=3.0, help="像素/方块")
    ap.add_argument("--cut", action="append", default=[],
                    metavar="AXIS=COORD", help="剖面(可重复),用 right 视角渲染")
    ap.add_argument("--night", action="store_true", help="加夜景(dark+bloom)")
    ap.add_argument("--night-views", default="iso,front",
                    help="夜景视角(默认 iso,front)")
    ap.add_argument("--hero", action="store_true", help="加高清透视 hero 图")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="批判 prompt")
    ap.add_argument("--no-ai", action="store_true", help="只渲染,不调视觉模型")
    ap.add_argument("--max-tokens", type=int, default=1600)
    ap.add_argument("--model", default=None,
                    help="视觉模型名（默认取 STRUCTWORKSHOP_VISION_MODEL 或内置默认）")
    a = ap.parse_args()

    lt = Path(a.input).resolve()
    if not lt.exists():
        raise SystemExit("no such file: %s" % lt)
    base = Path(a.out or (lt.stem + "_review"))
    base = base.with_suffix("") if base.suffix else base

    pngs: list[str] = []
    print("== 渲染: %s" % lt.name)
    pngs += render(str(lt), str(base), a.views, a.scale)
    if a.cut:
        for c in a.cut:
            axis, coord = c.split("=", 1)
            print("-- 剖面 %s=%s" % (axis, coord))
            pngs += render(str(lt), f"{base}_cut{axis}{coord}", "right",
                           a.scale, ["--cut", c])
    if a.night:
        print("-- 夜景")
        pngs += render(str(lt), f"{base}_night", a.night_views, a.scale,
                       ["--background", "dark", "--bloom", "1.0"])
    if a.hero:
        print("-- 透视")
        pngs += render(str(lt), f"{base}_hero", "hero", a.scale,
                       ["--proj", "persp"])

    print("== 渲染完成: %d 张图" % len(pngs))
    for p in pngs:
        print("   " + p)

    if a.no_ai or not pngs:
        print("== --no-ai:跳过视觉批判")
        return 0

    print("== 视觉批判(model=%s)" % (a.model or "<默认>"))
    from mcqa.vision_review import VisionConfigError, review  # noqa: E402

    parts: list[str] = []
    usage_total: dict = {}
    for i in range(0, len(pngs), 8):
        chunk = pngs[i:i + 8]
        print("   图 %d-%d / %d" % (i + 1, i + len(chunk), len(pngs)))
        try:
            txt, usage = review(chunk, a.prompt, model=a.model,
                                max_tokens=a.max_tokens)
        except VisionConfigError as e:
            print("\n== 没有配视觉模型凭据（--no-ai 可只出图）：\n%s" % e)
            return 2
        parts.append(txt)

        def add_usage(tot: dict, u: dict) -> None:
            for k, v in u.items():
                if isinstance(v, dict):
                    add_usage(tot, v)
                elif isinstance(v, (int, float)):
                    tot[k] = tot.get(k, 0) + int(v)

        add_usage(usage_total, usage)

    report = base.with_suffix(".md")
    lines = [
        "# 视觉评审报告",
        "",
        "- 文件: %s" % lt,
        "- 时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 模型: %s,usage: %s" % (a.model, usage_total),
        "",
        "## 渲染图",
        "",
    ]
    lines += ["| 视角 | 文件 |", "|---|---|"]
    lines += ["| %s | %s |" % (Path(p).stem, Path(p).name) for p in pngs]
    lines += ["", "## 批判 Prompt", "", "```", a.prompt, "```", "",
              "## 评审意见", ""]
    lines += parts
    report.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("== 报告: %s" % report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
