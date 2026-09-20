# -*- coding: utf-8 -*-
"""mcmaterials CLI - 全量方块材质目录 / 图表 / 参考匹配 / 审计。

  python -m mcmaterials catalog [--all] [--limit N] [--refetch] [--blocks a,b]
  python -m mcmaterials colors  [--md PATH] [--json PATH]
  python -m mcmaterials chart   [--all] [--family X] [--class rough] [--per-page 64]
  python -m mcmaterials match   IMG [--pool facade|<family>|all] [--class rough] [--colors 6] [--topk 4]
  python -m mcmaterials profile IMG [--colors 6] [--crop L,T,R,B]
  python -m mcmaterials compare MINE.png REF.png [--crop-a L,T,R,B] [--crop-b L,T,R,B]
  python -m mcmaterials ramp    FAMILY [--top 40]
  python -m mcmaterials families
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mcmaterials.chart import facade_luminance_chart, save_all_charts, save_family_charts
from mcmaterials.families import FAMILIES, all_families, facade_blocks
from mcmaterials.textures import average_color, block_texture, luminance

ROOT = Path(__file__).resolve().parents[2]
SKILL_DATA = ROOT / "skills" / "minecraft-material-lab" / "data"
CHARTS = SKILL_DATA / "charts"


def load_catalog() -> dict:
    p = SKILL_DATA / "block_catalog.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def cmd_catalog(a) -> None:
    from mcmaterials.catalog import build, block_list
    blocks = [b.strip() for b in a.blocks.split(",")] if a.blocks else None
    if not blocks and not a.all and not a.limit:
        names = block_list()
        print(f"block list = {len(names)} (使用 --all 建立全量目录)")
    cat = build(blocks=blocks, limit=a.limit, refetch=a.refetch, recompute=a.recompute,
                progress_every=a.progress, save_every=200)
    ok = sum(1 for e in cat.values() if e.get("color"))
    print(f"blocks={len(cat)}  with-color={ok}  missing={len(cat) - ok}")


def cmd_colors(a) -> None:
    cat = load_catalog()
    if not cat or a.refresh:
        from mcmaterials.catalog import build
        cat = build(refetch=False)
    rows = {b: e for b, e in cat.items() if e.get("color")}
    jp = Path(a.json) if a.json else SKILL_DATA / "block_colors.json"
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True), encoding="utf-8", newline="\n")
    print("colors ->", jp, f"({len(rows)} blocks)")
    if a.md:
        lines = ["# 全量方块色表（均色 / 明度 / 质感 / 透明度）", "",
                 "| 方块 | class | is_full | 均色 | 明度 | rough | 纹路 | alpha(op/tr/em) |",
                 "|---|---|---|---|---|---|---|---|"]
        for b in sorted(rows):
            e = rows[b]; c = e["color"]; t = e.get("texture") or {}; al = e.get("alpha") or {}
            lines.append("| `%s` | %s | %s | #%02x%02x%02x | %.1f | %.2f | %s | %.2f/%.2f/%.2f |" % (
                b, e.get("class"), e.get("is_full"),
                int(c["avg"][0]), int(c["avg"][1]), int(c["avg"][2]),
                t.get("lum_mean", 0), t.get("roughness", 0), t.get("grain", "?"),
                al.get("opaque", 0), al.get("translucent", 0), al.get("empty", 0)))
        Path(a.md).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print("md ->", a.md)


def cmd_chart(a) -> None:
    from mcmaterials.chart import compose_paged
    cat = load_catalog()
    made = []
    report: dict = {}
    if a.classes:
        wanted = set(a.classes.split(","))
        names = [b for b, e in cat.items() if e.get("class") in wanted]
        tag = "class_" + a.classes.replace(",", "-")
        made += compose_paged(names, "texture class: " + a.classes, CHARTS, tag,
                              cols=a.cols, per_page=a.per_page)
    elif a.family:
        fi = SKILL_DATA / "family_index.json"
        index = json.loads(fi.read_text(encoding="utf-8")) if fi.exists() else {}
        fams = {}
        for fam in a.family:
            if fam == "facade":
                fams[fam] = facade_blocks(list(cat) or None)
            else:
                fams[fam] = index.get(fam) or [b for b in cat if fam in b]
        made += save_all_charts(CHARTS, fams, per_page=a.per_page, cols=a.cols)
    else:
        made += save_family_charts(CHARTS, FAMILIES, cols=a.cols, report=report)
        if cat:
            made += facade_luminance_chart(cat, CHARTS, per_page=max(a.per_page, 90))
        if a.all and cat:
            made += save_all_charts(CHARTS, all_families(list(cat)), per_page=a.per_page,
                                    cols=a.cols, report=report)
    for p in made:
        print("chart ->", p)
    if report:
        print("cells=%d  missing-textures=%d" % (report.get("cells", 0), report.get("missing", 0)))


def cmd_match(a) -> None:
    from mcmaterials.match import render_report, suggest
    box = tuple(float(x) for x in a.crop.split(",")) if a.crop else None
    classes = set(a.classes.split(",")) if a.classes else None
    res = suggest(a.image, n_colors=a.colors, topk=a.topk, pool=a.pool,
                  sample_box=box, classes=classes)
    for item in res:
        near = ", ".join(f"{n['block']}({n['d']})" for n in item["nearest"])
        print(f"{item['color']} {item['pct']:.0f}%  ->  {near}")
    out = Path(a.out) if a.out else CHARTS / f"match_{Path(a.image).stem}.png"
    render_report(a.image, res, out)
    print("report ->", out)


def cmd_profile(a) -> None:
    from mcmaterials.match import ref_profile, suggest
    box = tuple(float(x) for x in a.crop.split(",")) if a.crop else None
    prof = ref_profile(a.image, box, n_colors=a.colors)
    print("reference colours:")
    for c in prof["colors"]:
        print("  ", c["rgb"], f"{c['share']*100:.0f}%")
    st = prof["stats"]
    print(f"reference texture: edge={st['edge']} rough={st['roughness']} grain={st['grain']} "
          f"lum={st['lum_mean']} contrast={st['contrast']}")
    cls = None
    if st["roughness"] >= 0.22:
        cls = {"rough", "grained"}
    elif st["roughness"] <= 0.06:
        cls = {"smooth"}
    else:
        cls = {"matte", "grained"}
    print("suggested texture classes:", sorted(cls))
    res = suggest(a.image, n_colors=a.colors, topk=3, pool=a.pool, sample_box=box, classes=cls)
    for item in res:
        near = ", ".join(f"{n['block']}({n['d']})" for n in item["nearest"])
        print(f"  {item['color']} -> {near}")


def cmd_compare(a) -> None:
    from mcmaterials.compare import audit
    box_a = tuple(float(x) for x in a.crop_a.split(",")) if a.crop_a else None
    box_b = tuple(float(x) for x in a.crop_b.split(",")) if a.crop_b else None
    out = a.out or str(CHARTS / "compare.png")
    r = audit(a.mine, a.ref, out, box_a, box_b)
    for k in ("a", "b"):
        m = r[k]
        print(f"{k}: mean {m['hex']} L={m['lum_mean']} std={m['lum_std']} "
              f"warm/cool={m['warm_cool']} top={m['top'][:3]}")
    print(f"delta L(mine-ref) = {r['delta_lum']:+}   delta warm/cool = {r['delta_warm_cool']:+}")
    print("sheet ->", r["out"])


def cmd_ramp(a) -> None:
    cat = load_catalog()
    if True:
        names = None
        if cat:
            fi = SKILL_DATA / "family_index.json"
            if fi.exists():
                names = json.loads(fi.read_text(encoding="utf-8")).get(a.family)
        if not names:
            names = FAMILIES.get(a.family)
        if not names:
            raise SystemExit(f"没有家族 {a.family}（python -m mcmaterials families）")
        rows = []
        for b in names:
            e = cat.get(b) or {}
            c = (e.get("color") or {}).get("avg")
            if c:
                rows.append((sum(c) / 3.0, b, tuple(int(v) for v in c), e.get("class")))
        if not rows:
            for b in names:
                tex, _ = block_texture(b)
                avg = average_color(tex) if tex is not None else None
                if avg:
                    rows.append((luminance(avg), b, avg, None))
        rows.sort(key=lambda t: -t[0])
        for lum, b, avg, cls in rows[: a.top]:
            print(f"{lum:6.1f}  {b:30} {avg} {cls or ''}")


def cmd_check(_a) -> None:
    from collections import Counter
    cat = load_catalog()
    if not cat:
        raise SystemExit("没有目录：先 python -m mcmaterials catalog --all")
    n = len(cat)
    withc = sum(1 for e in cat.values() if e.get("color"))
    cls = Counter(e.get("class") for e in cat.values())
    full = sum(1 for e in cat.values() if e.get("is_full"))
    fac = sum(1 for e in cat.values() if e.get("facade_safe"))
    tr = sum(1 for e in cat.values() if e.get("transparent"))
    miss = [b for b, e in cat.items() if e.get("missing")]
    print(f"blocks={n}  with_color={withc}  missing={len(miss)}  full={full}  facade_safe={fac}  transparent={tr}")
    print("classes:", dict(cls.most_common()))
    fi = SKILL_DATA / "family_index.json"
    if fi.exists():
        fams = json.loads(fi.read_text(encoding="utf-8"))
        print("families:", {k: len(v) for k, v in sorted(fams.items(), key=lambda kv: -len(kv[1]))})
    woods = ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove",
             "cherry", "bamboo", "crimson", "warped", "pale_oak"]
    for kind in ("sign", "hanging_sign", "slab", "stairs"):
        have = [w for w in woods if f"{w}_{kind}" in cat]
        print(f"wood x {kind:12} {len(have)}/12  missing: {[w for w in woods if w not in have]}")
    for group in ("copper_chain", "cut_copper", "dead_", "sand", "glass", "wool",
                  "concrete", "concrete_powder", "terracotta", "shulker_box", "button", "chain"):
        hits = [b for b in cat if group in b]
        print(f"contains '{group}': {len(hits)}  e.g. {hits[:4]}")
    from mcmaterials.textures import block_texture
    nonblank = sum(1 for b in cat if block_texture(b, fetch=False)[0] is not None)
    print(f"chart textures resolvable: {nonblank}/{len(cat)}")
    if miss:
        print("missing textures (first 20):", miss[:20])


def cmd_families(_a) -> None:
    cat = load_catalog()
    if cat:
        fams = all_families(list(cat))
    else:
        fams = FAMILIES
    for k, v in fams.items():
        print(f"{k:16} {len(v):4}  {', '.join(v[:6])}{' ...' if len(v) > 6 else ''}")




def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("catalog", help="建立/刷新全量方块材质目录")
    p.add_argument("--all", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--blocks", help="逗号分隔的方块列表")
    p.add_argument("--refetch", action="store_true", help="清 _missing 标记并重取缺图")
    p.add_argument("--recompute", action="store_true", help="不抓图,仅用贴图缓存重算目录")
    p.add_argument("--progress", type=int, default=100)
    p.set_defaults(fn=cmd_catalog)

    p = sub.add_parser("colors", help="从目录导出色表 JSON/MD")
    p.add_argument("--json")
    p.add_argument("--md")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(fn=cmd_colors)

    p = sub.add_parser("chart", help="生成材质图表(家族/类别/外墙明度)")
    p.add_argument("--out")
    p.add_argument("--family", action="append")
    p.add_argument("--class", dest="classes")
    p.add_argument("--all", action="store_true", help="额外输出全部自动家族(分页)")
    p.add_argument("--per-page", type=int, default=64)
    p.add_argument("--cols", type=int, default=8)
    p.set_defaults(fn=cmd_chart)

    p = sub.add_parser("match", help="参考图片 -> 主色 -> 最接近方块")
    p.add_argument("image")
    p.add_argument("--colors", type=int, default=6)
    p.add_argument("--topk", type=int, default=4)
    p.add_argument("--pool", default="facade", help="facade | all | 家族名 | walls")
    p.add_argument("--class", dest="classes", help="只看这些质感类别,逗号分隔")
    p.add_argument("--crop")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_match)

    p = sub.add_parser("profile", help="参考图颜色+质感画像 -> 同类质感方块建议")
    p.add_argument("image")
    p.add_argument("--colors", type=int, default=6)
    p.add_argument("--crop")
    p.add_argument("--pool", default="facade")
    p.set_defaults(fn=cmd_profile)

    p = sub.add_parser("compare", help="成品 vs 参考：并排 + 主色/明度/暖冷审计")
    p.add_argument("mine")
    p.add_argument("ref")
    p.add_argument("--crop-a")
    p.add_argument("--crop-b")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("ramp", help="按明度列出家族方块(做渐变)")
    p.add_argument("family")
    p.add_argument("--top", type=int, default=40)
    p.set_defaults(fn=cmd_ramp)

    sub.add_parser("families", help="列出家族").set_defaults(fn=cmd_families)
    sub.add_parser("check", help="目录覆盖/家族/质感类别自检").set_defaults(fn=cmd_check)

    a = ap.parse_args()
    a.fn(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
