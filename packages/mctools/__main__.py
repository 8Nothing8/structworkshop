"""命令行入口：``python -m mctools <list|info|select|run|apply>``。

这是给 **脚本 / AI 代理**用的入口（Web 编辑器面板走同一套 ``mctools.engine``，
所以参数与效果完全一致）。核心是 ``apply``：一次读入投影 → 顺序跑多步工具 →
可选 QA / 渲染 → 写盘（或 ``--in-place`` 自动备份），全程可 ``--json`` 解析。

    # 看有什么工具 / 某个工具的参数表
    python -m mctools list
    python -m mctools info noise_painter

    # 选区探测（先问「有多少格 / 包围盒在哪」，再决定怎么改）
    python -m mctools select --in a.schem --mask "minecraft:stone" --json

    # 单步
    python -m mctools run rock --in a.schem --out rocky.schem --at 16,8,16 --brush sphere:8

    # 多步流水线（--step 可重复，值是 JSON；也可 --steps 一个 JSON 文件）
    python -m mctools apply --in a.schem --out b.schem \\
        --step '{"tool":"noise_painter","sel":[0,0,0,31,20,31],
                 "params":{"scale":8,"blocks":"minecraft:stone,minecraft:andesite"}}' \\
        --step '{"tool":"rock","sel":[0,0,0,31,20,31],"params":{"noisiness":0.4}}' \\
        --qa --render iso --json

    # 原地改（自动备份到 .cache/backups/tools/<时间戳>/）
    python -m mctools apply --in builds/x/x.schem --in-place \\
        --step '{"tool":"smooth","sel":[0,0,0,63,15,63],"params":{"strength":2}}'

约定：
* 工具参数名与 Web 面板一致（``python -m mctools info <工具>`` 查表）。
* 掩码表达式见 ``mctools.masks``：``solid``、``y<64``、``near(air)``、``oak*`` …
* ``--dry-run`` 只跑不写盘；``--json`` 输出机器可读报告；退出码 2 = 参数问题，
  1 = QA 有 ERROR，0 = 通过。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

from mccore import structure_io as S
from mctools import engine as E
from mctools import masks as M
from mctools import registry as R

EXIT_OK, EXIT_QA, EXIT_USAGE = 0, 1, 2


# ------------------------------------------------------------------ helpers
def _jsonify(text: str):
    t = str(text).strip()
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    try:
        return json.loads(t)
    except (json.JSONDecodeError, ValueError):
        return text


def _points(text):
    pts = []
    for chunk in str(text).replace("|", ";").split(";"):
        chunk = chunk.strip()
        if chunk:
            pts.append([float(v) for v in chunk.split(",")])
    return pts


def _vec3(text):
    return [float(v) for v in str(text).split(",")]


def _box(text):
    v = [int(round(float(x))) for x in str(text).split(",")]
    if len(v) != 6:
        raise SystemExit("--sel 需要 6 个数：x0,y0,z0,x1,y1,z1")
    return v


def _brush(text):
    if ":" in str(text):
        shape, _, radius = str(text).partition(":")
        return shape, float(radius)
    return str(text), 4.0


def _param_dict(pairs) -> dict:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise ValueError(f"--param 需要 k=v 形式：{p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = _jsonify(v)
    return out


def _report(rep: dict, as_json: bool, human: list) -> None:
    if as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        for line in human:
            print(line)


# ------------------------------------------------------------------ steps
def _step_from_args(a) -> dict:
    """把命令行参数拼成一个 step（与 apply 的 --step JSON 同构）。"""
    step: dict = {"tool": a.tool, "params": _param_dict(a.param)}
    if a.points:
        step["params"]["points"] = _points(a.points)
    if a.at:
        step["centers"] = [list(_vec3(x)) for x in a.at]
    shape, radius = _brush(a.brush)
    step["brush"] = {"shape": shape, "radius": radius}
    if a.sel:
        step["sel"] = a.sel
    if a.all:
        step["all"] = True
    if a.block:
        step["block"] = a.block
    if a.mask:
        step["mask"] = a.mask
    if a.seed is not None:
        step["params"].setdefault("seed", a.seed)
    return step


def _normalize_step(raw: dict, fallback_seed: int | None = None) -> dict:
    if not isinstance(raw, dict) or not raw.get("tool"):
        raise ValueError(f"step 必须是含 tool 的 JSON 对象：{raw!r}")
    step = dict(raw)
    step.setdefault("params", {})
    if not isinstance(step["params"], dict):
        raise ValueError("step.params 必须是对象")
    if fallback_seed is not None:
        step["params"].setdefault("seed", fallback_seed)
    return step


def _run_steps(world, palette, steps) -> tuple[list[dict], list[str]]:
    results, created = [], []
    for i, st in enumerate(steps):
        tool = R.get_tool(st["tool"])
        params = dict(st.get("params") or {})
        centers = st.get("centers")
        if centers and not (params.get("_center") or params.get("at")):
            params["_center"] = [float(v) for v in centers[-1]]
        brush = st.get("brush") or {}
        whole = bool(st.get("all"))
        sel = st.get("sel")
        if whole:
            sx, sy, sz = (int(world.shape[2]), int(world.shape[0]),
                          int(world.shape[1]))
            sel = [0, 0, 0, sx - 1, sy - 1, sz - 1]
        if tool.region in ("brush", "sel") and not centers and not sel and not whole:
            raise ValueError(
                f"第 {i + 1} 步「{tool.label}」没给作用范围："
                f"加 \"sel\":[x0,y0,z0,x1,y1,z1] / \"all\":true，"
                f"笔刷类工具还可以给 \"centers\":[[x,y,z]]（命令行对应 "
                f"--sel / --all / --at）")
        if tool.region == "own" and tool.id == "path" \
                and not params.get("points") and not centers:
            raise ValueError(f"第 {i + 1} 步「路径」需要 params.points 或 centers")
        if (tool.region == "own" and tool.bbox_fn is not None
                and not (params.get("at") or params.get("_center") or centers)):
            # 自带几何的工具里，有的必须有定位点（shape 的包围盒依赖 _center/at），
            # 有的不需要（field 用整张画布、glyph 的 at 默认 0,0,0）——
            # 让几何函数自己回答，不在这里一刀切要 --at（旧写法让 `run field`
            # 必须给一个完全用不上的定位点）。
            try:
                size = (int(world.shape[2]), int(world.shape[0]),
                        int(world.shape[1]))
                missing = tool.bbox_fn(params, size, None) is None
            except Exception:  # noqa: BLE001
                missing = False
            if missing:
                raise ValueError(
                    f"第 {i + 1} 步「{tool.label}」需要 centers"
                    f"（定位点，命令行 --at x,y,z）")
        res = E.apply(world, palette, tool.id, params, sel_box=sel,
                      centers=centers,
                      brush_shape=str(brush.get("shape") or "sphere"),
                      brush_radius=float(brush.get("radius") or 4.0),
                      seed=int(params.get("seed") or 0),
                      block=st.get("block"), mask=st.get("mask"))
        for s in res.get("new_states") or []:
            if s not in created:
                created.append(s)
        results.append({
            "step": i + 1, "tool": tool.id, "label": tool.label,
            "stats": res["stats"], "changed": int(res["changed"]),
            "bbox": res.get("bbox"), "box": res.get("box"),
            "notes": list(res.get("notes") or []),
        })
    return results, created


# ------------------------------------------------------------------ commands
def cmd_list(a) -> int:
    cat = R.catalog()
    if a.json:
        print(json.dumps(cat, ensure_ascii=False, indent=1))
        return EXIT_OK
    print(f"mctools：{cat['count']} 个工具 / {len(cat['groups'])} 组\n")
    for g in cat["groups"]:
        print(f"[{g['label']}]")
        for t in g["tools"]:
            ps = ", ".join(p["name"] for p in t["params"]) or "-"
            print(f"  {t['id']:18s} {t['label']:8s} {t['region']:5s} 参数: {ps}")
        print()
    print("笔刷形状：" + ", ".join(cat["brushShapes"]))
    print("\n参数详情：python -m mctools info <工具>；机器可读：--json")
    return EXIT_OK


def cmd_info(a) -> int:
    t = R.get_tool(a.tool)
    if a.json:
        print(json.dumps(t.to_dict(), ensure_ascii=False, indent=1))
        return EXIT_OK
    print(f"{t.label}（{t.id}）  区域={t.region}  掩码={'支持' if t.mask else '不支持'}")
    print(f"  {t.hint}")
    if t.params:
        print("\n参数：")
        for p in t.params:
            rng = ""
            if p.options:
                rng = " 可选: " + " / ".join(p.options)
            elif p.min is not None or p.max is not None:
                rng = f" 范围: {p.min} ~ {p.max}"
            print(f"  --param {p.name}=<{p.type}>  默认 {p.default!r}{rng}")
            if p.hint:
                print(f"        {p.hint}")
    return EXIT_OK


def cmd_select(a) -> int:
    d = S.read_structure(a.input)
    world = d["voxels"]
    size = (int(world.shape[2]), int(world.shape[0]), int(world.shape[1]))
    if a.at:
        x, y, z = (int(round(v)) for v in _vec3(a.at))
        sx, sy, sz = size
        if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
            raise ValueError("取样点越界")
        idx = int(world[y, z, x])
        if idx == 0:
            raise ValueError("该处是空气，没有可选的方块")
        m = world == idx
        if a.connected:
            m = _flood(world, (x, y, z), idx)
    else:
        from mccore.schem_io import state_str
        mc = M.MaskContext(world, origin=(0, 0, 0),
                           names=[state_str(p) for p in d["palette"]])
        m = M.compile_mask(a.mask, mc)
    n = int(m.sum())
    out = {"count": n, "bbox": None, "size": list(size)}
    if n:
        ys, zs, xs = m.nonzero()
        out["bbox"] = [int(xs.min()), int(ys.min()), int(zs.min()),
                       int(xs.max()), int(ys.max()), int(zs.max())]
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(f"命中 {n:,} 格" + (f"  包围盒 {out['bbox']}" if n else ""))
    return EXIT_OK


def _flood(b, start, idx):
    import numpy as np
    sy, sz, sx = b.shape
    same = b == idx
    seen = np.zeros(b.shape, dtype=bool)
    seen[start[1], start[2], start[0]] = True
    stack = [start]
    while stack:
        x, y, z = stack.pop()
        for dy, dz, dx in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
                           (0, 0, 1), (0, 0, -1)):
            ny, nz, nx = y + dy, z + dz, x + dx
            if (0 <= ny < sy and 0 <= nz < sz and 0 <= nx < sx and
                    not seen[ny, nz, nx] and same[ny, nz, nx]):
                seen[ny, nz, nx] = True
                stack.append((nx, ny, nz))
    return seen


def _do_qa(path: Path) -> dict:
    """跑结构质检（复用 mcqa.qa_check，取它的 --json 输出）。"""
    from mcqa import qa_check
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = qa_check.main([str(path), "--json"])
    try:
        rep = json.loads(buf.getvalue())
    except json.JSONDecodeError:
        rep = {"errors": [], "warnings": [], "parse_error": buf.getvalue()[:400]}
    rep["ok"] = not rep.get("errors") and rc == 0
    return rep


def _do_render(path: Path, views: str, scale: float, out_dir) -> list[str]:
    from mcrender import cli as RCLI
    prefix = (Path(out_dir) / path.stem) if out_dir else \
        (path.parent / path.stem)
    Path(prefix).parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        RCLI.main([str(path), "--views", views, "--scale", str(scale),
                   "--out", str(prefix), "--quiet"])
    return [ln.strip()[3:] for ln in buf.getvalue().splitlines()
            if ln.strip().startswith("-> ")]


def _write(path: Path, world, palette, d, *, name=None) -> Path:
    size = (int(world.shape[2]), int(world.shape[0]), int(world.shape[1]))
    S.write_structure(str(path), world, palette,
                      d.get("position") or (0, 0, 0), size,
                      metadata=d.get("metadata") or None,
                      name=name or d.get("metadata", {}).get("Name") or None,
                      data_version=d.get("data_version") or 0)
    return path


def _run_pipeline(a) -> int:
    src = Path(a.input)
    if not src.is_file():
        raise ValueError(f"没有文件 {src}")
    d = S.read_structure(str(src))
    world = d["voxels"]
    palette = [dict(p) for p in d["palette"]]

    if a.cmd == "run":
        steps = [_normalize_step(_step_from_args(a), a.seed)]
    else:
        raw = []
        for chunk in (a.step or []):
            if isinstance(chunk, str):
                try:
                    chunk = json.loads(chunk)
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"--step 不是合法 JSON（注意 shell 引号）：{e}") from e
            raw.append(chunk)
        if a.steps:
            loaded = json.loads(Path(a.steps).read_text(encoding="utf-8"))
            raw = (loaded if isinstance(loaded, list) else loaded.get("steps", [])) + raw
        if not raw:
            raise ValueError("apply 至少需要一步：--step '<JSON>' 或 --steps file.json")
        steps = [_normalize_step(s, a.seed) for s in raw]

    results, created = _run_steps(world, palette, steps)
    changed = sum(r["changed"] for r in results)
    size = [int(world.shape[2]), int(world.shape[0]), int(world.shape[1])]

    target = None
    backup = None
    if a.in_place:
        target = src
    elif a.out:
        target = Path(a.out)
    if target is not None and not a.dry_run:
        if target.resolve() == src.resolve():
            from mccore import backup as BK      # noqa: PLC0415
            # 按工作台「设置」里的备份策略（off / 保留最近 N 次 / 按天留存）
            backup = BK.backup_file(src, "tools")
        target.parent.mkdir(parents=True, exist_ok=True)
        _write(target, world, palette, d, name=a.name)

    rep = {
        "input": str(src), "output": str(target) if target and not a.dry_run
        else None,
        "backup": str(backup) if backup else None,
        "size": size, "steps": results, "changed": changed,
        "new_states": created,
        "dry_run": bool(a.dry_run),
    }
    human = [f"{r['step']}. {r['label']}: {r['stats']}"
             f"（改 {r['changed']:,} 格）" for r in results]
    if getattr(a, "stats", False):
        for r in results:
            human.append(f"   工作盒 {r['box']} · 改动包围盒 {r['bbox']}")
    for r in results:
        for n in r["notes"]:
            human.append(f"   注意: {n}")
    human.append(f"合计改动 {changed:,} 格；画布 {size[0]}×{size[1]}×{size[2]}"
                 + (f"；新增方块状态 {', '.join(created)}" if created else ""))
    if target and not a.dry_run:
        human.append(f"  → {target}" + (f"（原文件已备份到 {backup}）" if backup else ""))
    elif a.dry_run:
        human.append("  （--dry-run，未写盘）")

    rc = EXIT_OK
    if a.qa and target and not a.dry_run:
        qa = _do_qa(Path(target))
        rep["qa"] = qa
        n_err, n_warn = len(qa.get("errors") or []), len(qa.get("warnings") or [])
        human.append(f"QA: ERROR {n_err} / WARN {n_warn}"
                     + ("" if qa["ok"] else "  ← 不通过"))
        for e in (qa.get("errors") or [])[:5]:
            human.append(f"   ✗ {e}")
        if not qa["ok"]:
            rc = EXIT_QA
    if a.render and target and not a.dry_run:
        imgs = _do_render(Path(target), a.render, a.render_scale, a.render_out)
        rep["renders"] = imgs
        human.append("渲染: " + ", ".join(imgs))
    _report(rep, a.json, human)
    return rc


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mctools", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", aliases=["catalog"], help="列出全部工具")
    p.add_argument("--json", action="store_true", help="输出机器可读清单")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("info", help="看某个工具的参数表")
    p.add_argument("tool")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("select", help="测掩码/魔棒命中范围（不改数据）")
    p.add_argument("--in", dest="input", required=True)
    p.add_argument("--mask", help="掩码表达式，如 \"solid & y<64\"")
    p.add_argument("--at", help="取样点 x,y,z（魔棒）")
    p.add_argument("--connected", action="store_true", help="只取连通的同类方块")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_select)

    def common_steps(p, *, need_tool: bool):
        if need_tool:
            p.add_argument("tool")
        p.add_argument("--in", dest="input", required=True)
        p.add_argument("--out", help="输出文件；不写就 --dry-run")
        p.add_argument("--in-place", action="store_true",
                       help="原地覆盖（自动备份到 .cache/backups/tools/）")
        p.add_argument("--param", action="append", metavar="K=V", help="工具参数，可多次")
        p.add_argument("--sel", type=_box, help="框选 x0,y0,z0,x1,y1,z1")
        p.add_argument("--all", action="store_true", help="整张画布")
        p.add_argument("--at", action="append", metavar="X,Y,Z",
                       help="笔刷中心 / 定位点（可多次 = 一笔画）")
        p.add_argument("--points", help="路径控制点 \"x,y,z;x,y,z\"")
        p.add_argument("--brush", default="sphere:4", help="笔刷 shape:radius")
        p.add_argument("--block", help="当前方块（默认沿用文件里的主方块）")
        p.add_argument("--mask", help="掩码表达式，如 \"solid & y<64\"")
        p.add_argument("--seed", type=int, default=None)
        p.add_argument("--name", help="写盘时用的结构名")
        p.add_argument("--dry-run", action="store_true", help="只跑不写盘")
        p.add_argument("--stats", action="store_true",
                       help="额外打印工作盒 / 改动包围盒 / 新增方块状态")
        p.add_argument("--json", action="store_true", help="输出 JSON 报告")
        p.add_argument("--qa", action="store_true", help="写完跑结构质检（有 ERROR 则退出码 1）")
        p.add_argument("--render", metavar="VIEWS",
                       help="写完渲染，如 iso / iso,front（用 mcrender）")
        p.add_argument("--render-scale", type=float, default=3.0)
        p.add_argument("--render-out", help="渲染图输出目录（默认与输出文件同目录）")

    p = sub.add_parser("run", help="单步：在结构上跑一个工具")
    common_steps(p, need_tool=True)
    p.set_defaults(fn=_run_pipeline)

    p = sub.add_parser("apply", help="多步流水线（读入 → 顺序跑工具 → QA/渲染 → 写盘）")
    common_steps(p, need_tool=False)
    p.add_argument("--step", action="append", metavar="JSON",
                   help="一步的 JSON：{\"tool\":…, \"params\":{…}, \"sel\":[…], …}，可多次")
    p.add_argument("--steps", metavar="FILE", help="把步骤写成 JSON 文件（数组）")
    p.set_defaults(fn=_run_pipeline)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if getattr(a, "tool", None) and a.cmd in ("run", "info"):
        if a.tool not in R.TOOLS:
            print(f"没有工具 {a.tool!r}；可选：{', '.join(R.TOOLS)}", file=sys.stderr)
            return EXIT_USAGE
    if getattr(a, "in_place", False) and getattr(a, "out", None):
        print("--in-place 与 --out 不能同时用", file=sys.stderr)
        return EXIT_USAGE
    if a.cmd in ("run", "apply") and not a.dry_run and \
            not getattr(a, "in_place", False) and not getattr(a, "out", None):
        print("需要 --out（或 --in-place / --dry-run）", file=sys.stderr)
        return EXIT_USAGE
    try:
        return a.fn(a)
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
