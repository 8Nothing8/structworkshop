"""JSON API routes for the mcstudio workbench."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from mccore import backup as BK
from mccore import library as LB
from mccore import structure_io as S
from mccore.paths import pack_dirs, packs_dir, repo_root
from mcstudio.server import Response, err

SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


# 打开闸门：过大的结构直接拒绝（避免浏览器/服务端卡死）。
#   在「设置 → 打开上限」里改（存 .cache/mcstudio/settings.json）；
#   环境变量 STRUCTWORKSHOP_MAX_STRUCTURE_MB / STRUCTWORKSHOP_MAX_STRUCTURE_CELLS /
#   STRUCTWORKSHOP_MAX_STRUCTURE_BLOCKS / STRUCTWORKSHOP_WARN_STRUCTURE_CELLS 优先级更高。
def _limits() -> dict:
    """每次请求读取（设置页改了立即生效，无需重启）。"""
    return BK.limits_effective()


def _limit_hint(key: str) -> str:
    """报错里告诉用户去哪儿放宽。"""
    return (f"可在工作台「设置 → 打开上限」里调整 "
            f"{BK.LIMIT_LABEL[key]}（或环境变量 {BK.LIMIT_ENV[key]}）")


def _limits_payload() -> dict:
    """给前端的生效值（开结构/新建时随 payload 下发）。"""
    lim = BK.limits_effective()
    out = {k: lim["values"][k] for k in BK.LIMIT_KEYS}
    out["source"] = lim["source"]
    out["env"] = lim["env"]
    return out


def _guard_structure_size(path: Path) -> None:
    """按文件大小拦截超大结构（读取前）。"""
    max_mb = float(_limits()["values"]["max_mb"])
    mb = path.stat().st_size / (1024 * 1024)
    if mb > max_mb:
        raise ValueError(
            f"结构过大：{path.name} 约 {mb:.1f} MB，超过上限 {max_mb:g} MB；"
            f"{_limit_hint('max_mb')}（或先切块/裁剪）")


def _guard_structure_cells(d: dict, path: Path):
    """按画布格数 + 实心方块数拦截（读取后），并对大结构给出告警。

    格数管「空壳」：一个 200×200×200 只有地板的结构也会拖慢传输/网格构建；
    方块数管真成本：真正决定渲染/编辑耗时的是实心块数量。
    """
    lim = _limits()["values"]
    max_cells, max_blocks = int(lim["max_cells"]), int(lim["max_blocks"])
    warn_cells = int(lim["warn_cells"])
    sx, sy, sz = (int(v) for v in d["size"])
    cells = sx * sy * sz
    voxels = d.get("voxels")
    blocks = int((voxels != 0).sum()) if voxels is not None else 0
    if cells > max_cells:
        raise ValueError(
            f"结构过大：{path.name} 尺寸 {sx}×{sy}×{sz} = {cells:,} 格，"
            f"超过格数上限 {max_cells:,}；{_limit_hint('max_cells')}（或先切块/裁剪）")
    if blocks > max_blocks:
        raise ValueError(
            f"结构过大：{path.name} 共 {blocks:,} 个方块，"
            f"超过方块数上限 {max_blocks:,}；{_limit_hint('max_blocks')}（或先切块/裁剪）")
    if cells > warn_cells:
        return {"cells": cells, "blocks": blocks, "size": [sx, sy, sz],
                "level": "warn"}
    return None


# ------------------------------------------------------------------ helpers
def _preview_urls(entry: dict) -> dict:
    out = {}
    for key, urlkey in (("preview", "thumb"), ("preview_full", "full")):
        rel = entry.get(key)
        if not rel:
            continue
        url = "/files/packs/" + rel
        # 预览图重渲染后**文件名不变** → 带上修改时间做版本号。
        # 否则同一个 URL 会被浏览器（以及页内图片缓存）继续用旧图：
        # 现象就是「重渲染成功、但模块库封面还是旧的」。
        try:
            url += "?v=" + str((packs_dir() / rel).stat().st_mtime_ns)
        except OSError:
            pass
        out[urlkey] = url
    return out


def _module_row(entry: dict) -> dict:
    row = dict(entry)
    row["preview_urls"] = _preview_urls(entry)
    # 仓库相对路径（``packs/<包>/modules/…``）：给「在编辑器中打开 / 3D 查看 / 保存」用。
    # ``path`` 是**包内相对**（``<包>/modules/…``，pack.json/catalog 与预览图 URL 用它），
    # 直接当仓库路径发给 /api/structure/open 会被权限拦住（“不允许访问: …”）。
    rel = str(entry.get("path") or "").replace("\\", "/")
    if rel and not rel.startswith(("packs/", "/")):
        rel = "packs/" + rel
    row["file"] = rel
    sx, sy, sz = entry.get("size", [0, 0, 0])
    row["volume"] = int(sx * sy * sz)
    return row


def _tag_groups(tags) -> list[set[str]]:
    """按命名空间把标签分组（``facade:a`` 与 ``facade:b`` 同组；无冒号的自成一组）。"""
    groups: dict[str, set[str]] = {}
    for t in tags:
        ns = t.split(":", 1)[0] if ":" in t else ""
        groups.setdefault(ns, set()).add(t)
    return list(groups.values())


def _filter_modules(rows, *, tags, tag_mode, q, pack, category, material,
                    untagged, has_port) -> list[dict]:
    """标签模式：``facet`` 同组任一/跨组全选、``and`` 全选、``or`` 任一。"""
    wanted = set(tags or ())
    groups = _tag_groups(wanted)
    out = []
    for r in rows:
        rtags = set(r.get("tags") or [])
        if wanted:
            if tag_mode == "or":
                if not (rtags & wanted):
                    continue
            elif tag_mode == "facet":
                if not all(g & rtags for g in groups):
                    continue
            elif not wanted.issubset(rtags):
                continue
        if untagged and rtags:
            continue
        if pack and r.get("pack") != pack:
            continue
        if category and r.get("category") != category:
            continue
        if q:
            needle = q.lower()
            hay = " ".join([r.get("id", ""), r.get("description", ""),
                            " ".join(r.get("tags") or []),
                            r.get("category", "")]).lower()
            if needle not in hay:
                continue
        if material:
            if not any(material.lower() in k.lower()
                       for k in (r.get("palette") or {})):
                continue
        if has_port is not None and bool(r.get("ports")) != has_port:
            continue
        out.append(r)
    return out


def _sort_modules(rows, sort: str) -> list[dict]:
    if sort == "blocks":
        return sorted(rows, key=lambda r: -r.get("blocks", 0))
    if sort == "size":
        return sorted(rows, key=lambda r: -r.get("volume", 0))
    if sort == "category":
        return sorted(rows, key=lambda r: (r.get("category", ""), r["id"]))
    if sort == "pack":
        return sorted(rows, key=lambda r: (r.get("pack", ""), r["id"]))
    return sorted(rows, key=lambda r: r["id"])


def _read_upload(app, req, key: str = "filename") -> Path:
    name = SAFE_NAME.sub("_", str(req.q(key) or f"upload_{int(time.time())}"))
    if Path(name).suffix.lower() not in S.SUPPORTED_SUFFIXES:
        raise ValueError("只支持 .schem / .litematic 上传")
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = app.uploads / f"{ts}_{name}"
    dst.write_bytes(req.body)
    return dst


# ------------------------------------------------------------------ routes
def register(app) -> None:
    r = app.route
    r("GET", "/api/state", api_state)
    r("GET", "/api/settings", api_settings)
    r("POST", "/api/settings", api_settings)
    r("POST", "/api/settings/clear", api_settings_clear)
    r("GET", "/api/packs", api_packs)
    r("GET", "/api/tags", api_tags)
    r("POST", "/api/tags/rename", api_tags_rename)
    r("POST", "/api/tags/merge", api_tags_merge)
    r("POST", "/api/tags/delete", api_tags_delete)
    r("GET", "/api/blocks", api_blocks)
    r("GET", "/api/blocks/picker", api_blocks_picker)
    r("POST", "/api/palette-info", api_palette_info)
    r("POST", "/api/blockdefs", api_blockdefs)
    r("GET", "/api/asset", api_asset)
    r("GET", "/api/files", api_files)
    r("GET", "/api/modules", api_modules)
    r("GET", "/api/modules/counts", api_modules_counts)
    r("GET", "/api/modules/{mid}", api_module_get)
    r("PATCH", "/api/modules/{mid}", api_module_patch)
    r("DELETE", "/api/modules/{mid}", api_module_delete)
    r("POST", "/api/modules/{mid}/preview", api_module_preview)
    r("POST", "/api/modules/{mid}/transfer", api_module_transfer)
    r("POST", "/api/modules/batch", api_modules_batch)
    r("POST", "/api/import", api_import)
    r("POST", "/api/import/upload", api_import_upload)
    r("POST", "/api/structure/open", api_structure_open)
    r("POST", "/api/structure/new", api_structure_new)
    r("GET", "/api/structure/{sid}", api_structure_state)
    r("GET", "/api/structure/{sid}/state", api_structure_state)   # 旧客户端兼容
    r("GET", "/api/structure/{sid}/voxels", api_structure_voxels)
    r("GET", "/api/structure/{sid}/blockentities", api_structure_block_entities)
    r("GET", "/api/structure/{sid}/layer", api_structure_layer)
    r("GET", "/api/structure/{sid}/stats", api_structure_stats)
    r("GET", "/api/structure/{sid}/download", api_structure_download)
    r("POST", "/api/structure/{sid}/ops", api_structure_ops)
    r("POST", "/api/structure/{sid}/tool", api_structure_tool)
    r("POST", "/api/structure/{sid}/select", api_structure_select)
    r("GET", "/api/tools", api_tools)
    r("POST", "/api/structure/{sid}/undo", api_structure_undo)
    r("POST", "/api/structure/{sid}/redo", api_structure_redo)
    r("GET", "/api/structure/{sid}/history", api_structure_history)
    r("POST", "/api/structure/{sid}/history/jump", api_structure_history_jump)
    r("GET", "/api/structure/{sid}/modules", api_structure_modules)
    r("POST", "/api/structure/{sid}/modules", api_structure_modules_add)
    r("POST", "/api/structure/{sid}/modules/update", api_structure_modules_update)
    r("POST", "/api/structure/{sid}/modules/remove", api_structure_modules_remove)
    r("POST", "/api/structure/{sid}/modules/bake", api_structure_modules_bake)
    r("POST", "/api/structure/{sid}/modules/detach", api_structure_modules_detach)
    r("POST", "/api/structure/{sid}/resize", api_structure_resize)
    r("POST", "/api/structure/{sid}/save", api_structure_save)
    r("POST", "/api/structure/{sid}/save-as", api_structure_save)
    r("POST", "/api/structure/{sid}/save-as-module", api_structure_save_module)
    r("POST", "/api/structure/{sid}/close", api_structure_close)
    r("GET", "/api/previews", api_previews)
    r("POST", "/api/previews/render", api_previews_render)
    r("GET", "/api/jobs/{jid}", api_job)
    r("GET", "/api/jobs", api_job_list)
    r("POST", "/api/jobs/{jid}/cancel", api_job_cancel)


# ------------------------------------------------------------------ library
def api_state(app, req) -> Response:
    idx = LB.load_index()
    packs = []
    for pd in pack_dirs():
        m = json.loads((pd / "pack.json").read_text(encoding="utf-8"))
        mods = idx.get("packs", {}).get(m["id"], {}).get("modules", [])
        packs.append({"id": m["id"], "name": m.get("name", m["id"]),
                      "description": m.get("description", ""),
                      "version": m.get("version", ""),
                      "modules": len(mods)})
    categories = sorted({m.get("category", "") for m in idx["modules"].values()})
    materials = {}
    for m in idx["modules"].values():
        for k in (m.get("palette") or {}):
            if k != "air":
                materials[k] = materials.get(k, 0) + 1
    top_materials = sorted(materials, key=lambda k: -materials[k])[:120]
    # 接口类型下拉的「已有类型」：内置推荐值 + 仓库里实际用过的（含用户自写的）。
    # 放在批量打标/导入时就刷新，免得自写类型到下一个人手里就"搜不到"了。
    used = sorted({str(p.get("type")) for m in idx["modules"].values()
                   for p in (m.get("ports") or []) if p.get("type")})
    return Response.json({"stats": LB.stats(), "packs": packs,
                          "categories": categories,
                          "materials": top_materials,
                          "tags": LB.tag_tree(),
                          "interface_types": used,
                          "sessions": app.sessions.list()})


# ------------------------------------------------------------------ settings


def _settings_payload(pruned: dict | None = None) -> dict:
    from mccore import backup as BK  # noqa: PLC0415

    return {"backup": BK.backup_policy(), "defaults": dict(BK.DEFAULTS),
            "modes": list(BK.MODES),
            "limits": {"count": BK.MAX_COUNT, "days": BK.MAX_DAYS},
            "structure": _structure_limit_spec(),
            "managed_kinds": list(BK.KINDS),
            "stats": BK.stats(), "pruned": pruned}


def _structure_limit_spec() -> dict:
    """设置页「打开上限」表单需要的全部信息（当前值/已存值/默认/范围/单位/来源）。"""
    lim = BK.limits_effective()
    return {
        "keys": list(BK.LIMIT_KEYS),
        "values": {k: lim["values"][k] for k in BK.LIMIT_KEYS},
        "saved": {k: BK.limits_policy()[k] for k in BK.LIMIT_KEYS},
        "defaults": {k: BK.LIMIT_DEFAULTS[k] for k in BK.LIMIT_KEYS},
        "range": {k: list(BK.LIMIT_RANGE[k]) for k in BK.LIMIT_KEYS},
        "labels": {k: BK.LIMIT_LABEL[k] for k in BK.LIMIT_KEYS},
        "units": {k: BK.LIMIT_UNIT[k] for k in BK.LIMIT_KEYS},
        "env": lim["env"], "source": lim["source"],
    }


def api_settings(app, req) -> Response:
    """GET → 当前设置 + 备份现状；POST {backup:{mode,count,days}, structure:{…}, prune?} → 保存。

    ``backup`` 是备份策略，``structure`` 是打开闸门（max_mb / max_cells / max_blocks /
    warn_cells，见 :data:`mccore.backup.LIMIT_KEYS`）；两个都可以单独发。
    保存后按新备份策略清理旧备份（``mode=off`` 不动已有备份，``prune:false`` 可跳过），
    返回里 ``pruned`` 报告删了几份/释放多少字节。
    """
    from mccore import backup as BK  # noqa: PLC0415

    if req.method == "GET":
        return Response.json(_settings_payload())
    body = req.json() or {}
    patch = body.get("backup")
    if patch is None:
        patch = {k: v for k, v in body.items()
                 if k in ("mode", "count", "days")}
    if not isinstance(patch, dict):
        raise ValueError("backup 必须是对象，例如 {mode:'count',count:20}")
    limits = body.get("structure") if isinstance(body.get("structure"), dict) else None
    if limits is None and any(k in body for k in BK.LIMIT_KEYS):
        limits = {k: v for k, v in body.items() if k in BK.LIMIT_KEYS}
    BK.save_settings(patch, limits=limits)
    pruned = None
    if body.get("prune", True):
        pruned = BK.prune(policy=BK.backup_policy())
    return Response.json(_settings_payload(pruned))


def api_settings_clear(app, req) -> Response:
    """POST → 清空「留底」式备份（structures / tools）；不动 modules/packs 的备份。"""
    from mccore import backup as BK  # noqa: PLC0415

    body = req.json() or {}
    kinds = body.get("kinds")
    if kinds:
        bad = [k for k in kinds if k not in BK.KINDS]
        if bad:
            raise ValueError(
                f"只能清 {'/'.join(BK.KINDS)}（{'/'.join(bad)} 是“删除时挪过去的原件”，不能在UI里清）")
    out = BK.clear(kinds)
    payload = _settings_payload()
    payload["cleared"] = out
    return Response.json(payload)


def api_packs(app, req) -> Response:
    return api_state(app, req)


def api_tags(app, req) -> Response:
    return Response.json({"tags": LB.tag_tree(), "stats": LB.stats()})


def api_tags_rename(app, req) -> Response:
    body = req.json()
    return Response.json(LB.rename_tag(body.get("old", ""), body.get("new", "")))


def api_tags_merge(app, req) -> Response:
    body = req.json()
    return Response.json(LB.merge_tags(body.get("src", ""), body.get("dst", "")))


def api_tags_delete(app, req) -> Response:
    body = req.json()
    return Response.json(LB.delete_tag(body.get("tag", "")))


def _query_filters(req) -> dict:
    """标签以外的公共筛选参数（列表与计数共用）。"""
    has_port = req.q("hasPort")
    return dict(q=req.q("q") or "",
                pack=req.q("pack") or "",
                category=req.q("category") or "",
                material=req.q("material") or "",
                untagged=req.q("untagged") in ("1", "true"),
                has_port=None if has_port in (None, "") else has_port in ("1", "true"))


def api_modules(app, req) -> Response:
    idx = LB.load_index()
    rows = [_module_row(m) for m in idx["modules"].values()]
    rows = _filter_modules(rows, tags=req.qlist("tag"),
                           tag_mode=(req.q("tagMode") or "and").lower(),
                           **_query_filters(req))
    rows = _sort_modules(rows, req.q("sort") or "id")
    total = len(rows)
    limit = int(req.q("limit") or 500)
    offset = int(req.q("offset") or 0)
    return Response.json({"total": total, "modules": rows[offset:offset + limit]})


def api_modules_counts(app, req) -> Response:
    """GET /api/modules/counts?tag=…&tag=… → 三种标签模式各有多少结果（前端提示用）。"""
    idx = LB.load_index()
    rows = [_module_row(m) for m in idx["modules"].values()]
    tags = req.qlist("tag")
    extra = _query_filters(req)
    return Response.json({mode: len(_filter_modules(rows, tags=tags,
                                                    tag_mode=mode, **extra))
                          for mode in ("facet", "and", "or")})


def api_module_get(app, req) -> Response:
    mid = req.params["mid"]
    entry = LB.find_entry(mid)
    path = LB.module_path(mid)
    if entry is None or path is None:
        return err(404, f"没有模块 {mid}")
    spec = LB.load_spec(path)
    d = S.read_structure(str(path))
    from mccore.module_lib import block_stats  # noqa: PLC0415
    return Response.json({"entry": _module_row(entry), "spec": spec,
                          "blocks": block_stats(d)["palette_names"],
                          "path": str(path.relative_to(repo_root())).replace("\\", "/")})

def api_module_patch(app, req) -> Response:
    mid = req.params["mid"]
    body = req.json()
    out = {"id": mid, "changes": {}}
    if any(k in body for k in ("description", "category", "taxonomy",
                               "notes", "ports")):
        out["changes"]["meta"] = LB.set_meta(
            mid, description=body.get("description"),
            category=body.get("category"), taxonomy=body.get("taxonomy"),
            notes=body.get("notes"), ports=body.get("ports"))
    if "tags" in body:
        out["changes"]["tags"] = LB.set_tags([mid], set_to=body["tags"])
    out["entry"] = _module_row(LB.find_entry(mid) or {})
    return Response.json(out)


def api_module_transfer(app, req) -> Response:
    """POST /api/modules/<mid>/transfer {to_pack, mode?, category?, new_id?, overwrite?}

    模块库的「复制 / 转移到别的资产包」：结构 + spec + 预览图一起走，
    move 时源模块进 ``.cache/backups/modules/``。两个包的 catalog/pack.json 会重建。
    """
    body = req.json() or {}
    to_pack = str(body.get("to_pack") or "").strip()
    if not to_pack:
        return err(400, "缺少 to_pack")
    mode = str(body.get("mode") or "copy")
    try:
        out = LB.transfer_module(
            req.params["mid"], to_pack, mode=mode,
            category=body.get("category") or None,
            new_id=body.get("new_id") or None,
            overwrite=bool(body.get("overwrite")))
    except (ValueError, FileExistsError) as e:
        return err(400, str(e))
    except FileNotFoundError as e:
        return err(404, str(e))
    out["entry"] = _module_row(LB.find_entry(out["id"]) or {})
    return Response.json(out)


def api_module_delete(app, req) -> Response:
    return Response.json(LB.delete_module(req.params["mid"],
                                          backup=req.q("hard") not in ("1", "true")))


def api_module_preview(app, req) -> Response:
    mid = req.params["mid"]
    entry = LB.find_entry(mid)
    if entry is None:
        return err(404, f"没有模块 {mid}")
    background = _check_background(req.json().get("background"))
    jid = app.jobs.submit("渲染预览", _render_previews_job, entry.get("pack"),
                          force=True, only=[mid], background=background,
                          report=True, channel=PREVIEW_CHANNEL)
    return Response.json({"job": jid, "background": background})


# ------------------------------------------------------------------ previews
# 预览图渲染队列：同一 channel 的任务在 JobRunner 里串行排队（一次只跑一个渲染）。
PREVIEW_CHANNEL = "preview"


# 允许的渲染背景（mcrender --background）
PREVIEW_BACKGROUNDS = ("dark", "black", "white", "transparent", "sky")


def _check_background(mode) -> str:
    mode = str(mode or "dark").strip().lower()
    if mode not in PREVIEW_BACKGROUNDS:
        raise ValueError(f"background 只能是 {' / '.join(PREVIEW_BACKGROUNDS)}")
    return mode


def _render_previews_job(p, pack, *, force=False, only=None,
                         background="dark") -> dict:
    """在后台渲染预览图，把进度/日志写回 job（供工具栏进度条与队列面板显示）。"""
    background = _check_background(background)
    scope = "全部重新渲染" if force else "只渲染缺图的"

    def report(done, total, mid, state):
        if state == "start":
            p.log(f"开始：{pack or '全部资产包'} · {scope} · 背景 {background} · "
                  f"共 {total} 个模块")
            p.set(0, total, "准备中")
        else:
            p.set(done, total, mid)

    rep = LB.render_previews(pack, force=force, only=only, online=True,
                             progress=report, background=background)
    if rep["rendered"]:
        try:
            from mccore import pack as PK  # noqa: PLC0415
            PK.rescan_manifests(LB.load_index(), quiet=True)
        except Exception as e:  # noqa: BLE001
            p.log(f"[warn] 刷新 pack.json 失败：{e}")
    p.log(f"完成：新渲染 {len(rep['rendered'])} · 跳过 {len(rep['skipped'])} · "
          f"失败 {len(rep['failed'])}")
    for it in rep["failed"][:5]:
        p.log(f"[FAIL] {it['id']}: {str(it['error']).splitlines()[-1][:200]}")
    return {"pack": pack, "force": force, "background": background,
            "rendered": rep["rendered"], "skipped": len(rep["skipped"]),
            "failed": rep["failed"]}


def api_previews(app, req) -> Response:
    """GET /api/previews → 各资产包预览图覆盖情况 + 渲染队列。"""
    idx = LB.load_index()
    packs, total, missing_total = [], 0, 0
    for pd in pack_dirs():
        m = json.loads((pd / "pack.json").read_text(encoding="utf-8"))
        ids = idx.get("packs", {}).get(m["id"], {}).get("modules", [])
        missing = [i for i in ids
                   if not (idx["modules"].get(i) or {}).get("preview")]
        total += len(ids)
        missing_total += len(missing)
        packs.append({"id": m["id"], "name": m.get("name", m["id"]),
                      "total": len(ids), "previews": len(ids) - len(missing),
                      "missing": len(missing), "missing_ids": missing[:500]})
    jobs = [j for j in app.jobs.list()
            if j.get("channel") == PREVIEW_CHANNEL]
    return Response.json({"packs": packs, "jobs": jobs,
                          "total": total, "missing": missing_total})


def api_previews_render(app, req) -> Response:
    """POST {pack?, scope: "missing"|"all"} → 排队一个预览渲染任务。"""
    body = req.json()
    pack = str(body.get("pack") or "").strip()
    scope = str(body.get("scope") or "missing").lower()
    if scope not in ("missing", "all"):
        raise ValueError("scope 只能是 missing（缺图的）或 all（全部重新渲染）")
    if pack and not (packs_dir() / pack / "pack.json").is_file():
        return err(404, f"没有资产包 {pack}")
    background = _check_background(body.get("background"))
    jid = app.jobs.submit("渲染预览", _render_previews_job, pack or None,
                          force=scope == "all", background=background,
                          report=True, channel=PREVIEW_CHANNEL)
    job = app.jobs.get(jid) or {}
    return Response.json({"job": jid, "pack": pack or None, "scope": scope,
                          "background": background,
                          "queued_ahead": job.get("queued_ahead")})


def api_modules_batch(app, req) -> Response:
    body = req.json()
    ids = [str(i) for i in body.get("ids") or []]
    if not ids:
        raise ValueError("ids 为空")
    report = {"tags": None, "category": None, "deleted": None}
    if body.get("delete"):
        report["deleted"] = [LB.delete_module(i) for i in ids]
        return Response.json(report)
    if any(k in body for k in ("addTags", "removeTags", "setTags")):
        report["tags"] = LB.set_tags(
            ids, add=body.get("addTags") or [],
            remove=body.get("removeTags") or [],
            set_to=body.get("setTags") if "setTags" in body else None)
    if body.get("category"):
        report["category"] = LB.set_category(ids, body["category"])
    return Response.json(report)


def api_import(app, req) -> Response:
    body = req.json()
    tags = body.get("tags") or []
    report = LB.import_files(
        [app.resolve_path(p) for p in (body.get("paths") or [])],
        body.get("pack"), category=body.get("category") or None,
        flat=bool(body.get("flat")), tags=tags,
        description=body.get("description") or "",
        overwrite=bool(body.get("overwrite")), trim=bool(body.get("trim")),
        dry_run=bool(body.get("dryRun")))
    return Response.json(report)


def api_import_upload(app, req) -> Response:
    """POST /api/import/upload?filename=&pack=&category=&tags=&... (raw body)."""
    src = _read_upload(app, req)
    try:
        report = LB.import_files(
            [src], req.q("pack") or "modern-arch",
            category=req.q("category") or None,
            tags=[t for t in (req.q("tags") or "").split(",") if t.strip()],
            description=req.q("description") or "",
            overwrite=req.q("overwrite") in ("1", "true"),
            trim=req.q("trim") in ("1", "true"))
        return Response.json(report)
    finally:
        src.unlink(missing_ok=True)


# ------------------------------------------------------------------ blocks
def api_blocks(app, req) -> Response:
    version = req.q("version")
    if not version:
        try:
            version = app.blocks.version_of(int(req.q("dv") or 4903))
        except Exception:  # noqa: BLE001
            version = None
    if not version:
        return err(400, "缺少 version/dv")
    out = app.blocks.search(req.q("q") or "", version,
                            limit=int(req.q("limit") or 40))
    return Response.json({"version": version, "blocks": out})


def api_blocks_picker(app, req) -> Response:
    """方块选择面板：全量方块 + 颜色 + 材质家族（一次取回，客户端本地筛选）。"""
    return Response.json(app.blocks.picker())


def api_palette_info(app, req) -> Response:
    body = req.json()
    states = body.get("states") or []
    version = body.get("version")
    if not version:
        try:
            version = app.blocks.version_of(int(body.get("dataVersion") or 4903))
        except Exception:  # noqa: BLE001
            version = None
    if not version:
        return err(400, "缺少 version/dataVersion")
    info = app.blocks.resolve([{"Name": s.split("[")[0],
                                "Properties": _props_of(s)} for s in states],
                              version)
    # 特判方块 entity/* + 液体流面：这些贴图不在任何模型里，要单独取（见 entity_assets.py）
    from mcrender.renderer import AO_LEVELS  # noqa: PLC0415
    return Response.json({"version": version, "info": info,
                          "textures": app.blocks.extra_textures(version),
                          # AO 四档表：编辑器与 mcrender 共用同一张（改这里两边一起变）
                          "aoLevels": [round(float(v), 4) for v in AO_LEVELS]})


def _props_of(state: str) -> dict:
    if "[" not in state or not state.endswith("]"):
        return {}
    body = state[state.index("[") + 1:-1]
    props = {}
    for pair in body.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            props[k.strip()] = v.strip()
    return props


def api_blockdefs(app, req) -> Response:
    """Batch lookup of block property tables (defaults) for the 3D viewer."""
    body = req.json()
    names = body.get("names") or []
    version = body.get("version") or req.q("version")
    if not version:
        try:
            version = app.blocks.version_of(int(body.get("dataVersion") or 4903))
        except Exception:  # noqa: BLE001
            version = None
    if not version:
        return err(400, "缺少 version/dataVersion")
    summary = app.blocks.summary(version)
    out = {}
    for n in names:
        n = str(n)
        base = n.replace("minecraft:", "")
        e = summary.get(base)
        key = n if ":" in n else "minecraft:" + base
        out[key] = {"properties": (e[0] if e else {}),
                    "default": (e[1] if e and len(e) > 1 else {})}
    return Response.json({"version": version, "blocks": out})


def api_asset(app, req) -> Response:
    rel = req.q("rel") or ""
    version = req.q("version")
    if not rel or ".." in rel:
        raise ValueError("非法 rel")
    if not version:
        try:
            version = app.blocks.version_of(int(req.q("dv") or 4903))
        except Exception as e:  # noqa: BLE001
            return err(400, f"无法确定版本: {e}")
    assets = app.blocks.assets(version=version)
    data = assets.fetch(rel)
    if data is None:
        return err(404, f"资源缺失: {rel}")
    ctype = ("application/json" if rel.endswith(".json") else
             "image/png" if rel.endswith(".png") else
             "application/octet-stream")
    if ctype == "application/json":
        ctype += "; charset=utf-8"
    return Response.bytes(data, ctype)


def api_files(app, req) -> Response:
    q = (req.q("q") or "").lower()
    roots = [("packs", repo_root() / "packs"),
             ("builds", repo_root() / "builds"),
             ("compositions", repo_root() / "compositions"),
             ("tests", repo_root() / "tests" / "fixtures")]
    rows = []
    for kind, root in roots:
        if not root.is_dir():
            continue
        for f in S.supported(root):
            rel = str(f.relative_to(repo_root())).replace("\\", "/")
            if q and q not in rel.lower():
                continue
            rows.append({"path": rel, "name": f.name, "kind": kind,
                         "size": f.stat().st_size,
                         "mtime": int(f.stat().st_mtime)})
    rows.sort(key=lambda r: -r["mtime"])
    return Response.json({"files": rows[:int(req.q("limit") or 300)]})


# ------------------------------------------------------------------ structure
def api_structure_open(app, req) -> Response:
    path = None
    if req.body and (req.q("filename") or req.headers.get("Content-Type") ==
                     "application/octet-stream"):
        src = _read_upload(app, req)
        path = src
    else:
        body = req.json()
        if body.get("path"):
            path = app.resolve_path(body["path"])
    if path is None:
        raise ValueError("缺少 path 或上传文件")
    _guard_structure_size(Path(path))
    d = S.read_structure(str(path))
    notice = _guard_structure_cells(d, Path(path))
    version = None
    try:
        version = app.blocks.version_of(int(d.get("data_version") or 4903))
    except Exception:  # noqa: BLE001
        version = None
    s = app.sessions.add(path, d, version=version)
    restore = s.restore_placements()
    if restore.get("restored"):
        s.dirty = False          # 还原装配只是恢复会话模型，文件本身未改
    payload = s.payload()
    payload["stats"] = s.stats()
    payload["restore"] = restore
    lim = _limits()["values"]
    payload["limits"] = {"max_mb": float(lim["max_mb"]),
                         "max_cells": int(lim["max_cells"]),
                         "max_blocks": int(lim["max_blocks"]),
                         "warn_cells": int(lim["warn_cells"]),
                         "source": _limits()["source"]}
    if notice:
        payload["notice"] = notice
    return Response.json(payload)


def api_structure_new(app, req) -> Response:
    """POST {size:[x,y,z], name?} —— 新建一个空画布（默认 16×16×16）。

    编辑器在没有打开任何结构时也能直接开工（不然所有工具都是空）。
    会话无路径，保存时会走「另存为」。
    """
    body = req.json() or {}
    from mcstudio.session import StructureSession  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    try:
        size = [int(v) for v in (body.get("size") or (16, 16, 16))]
    except (TypeError, ValueError):
        raise ValueError("size 必须是 [x,y,z]") from None
    if len(size) != 3 or min(size) < 1:
        raise ValueError("size 必须是 [x,y,z] 且每个 ≥1")
    if max(size) > StructureSession.MAX_SIDE:
        raise ValueError(f"单边最大 {StructureSession.MAX_SIDE}（你给的是 {max(size)}）")
    lim = _limits()["values"]
    if size[0] * size[1] * size[2] > int(lim["max_cells"]):
        raise ValueError(f"总格数超过上限 {int(lim['max_cells']):,}（{_limit_hint('max_cells')}）")
    sx, sy, sz = size
    name = str(body.get("name") or "untitled").strip() or "untitled"
    data = {
        "voxels": np.zeros((sy, sz, sx), dtype=np.uint16),
        "palette": [{"Name": "minecraft:air"}],
        "position": (0, 0, 0),
        "metadata": {"Name": name},
        "region_name": "Schematic",
    }
    s = app.sessions.add(None, data)
    return Response.json(s.payload())


def api_structure_state(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    payload = s.payload()
    payload["stats"] = s.stats()
    return Response.json(payload)


def api_structure_voxels(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    y0 = req.q("y0")
    y1 = req.q("y1")
    data = s.voxel_bytes(int(y0) if y0 not in (None, "") else None,
                         int(y1) if y1 not in (None, "") else None)
    return Response.bytes(data, "application/octet-stream",
                          headers={"X-Size": ",".join(map(str, s.size)),
                                   "X-Dtype": "uint16-le"})


def api_structure_block_entities(app, req) -> Response:
    """GET → 方块实体（渲染用子集）：``[[x,y,z,id,data],…]``。

    箱子内容/告示牌全文不在这里（太大且渲染用不到）；这里给的是旗帜图案、
    告示牌正反色这类形状数据。保存时完整 NBT 原样写回（见 session.save）。
    """
    s = app.sessions.get(req.params["sid"])
    limit = int(req.q("limit") or 4000)
    return Response.json({"block_entities": s.block_entities_json(limit=limit),
                          "total": len(s.block_entities)})


def api_structure_layer(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    return Response.bytes(s.layer_bytes(int(req.q("y") or 0)),
                          "application/octet-stream")


def api_structure_stats(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    return Response.json({"stats": s.stats(), "state": s.payload()})


def api_structure_ops(app, req) -> Response:
    """POST {ops, update}——``update=true`` 时改完按邻居重算连接状态。"""
    s = app.sessions.get(req.params["sid"])
    body = req.json()
    ops = body.get("ops") or ([body["op"]] if body.get("op") else [])
    return Response.json(s.apply_ops(ops, update=bool(body.get("update"))))


def api_structure_undo(app, req) -> Response:
    return Response.json(app.sessions.get(req.params["sid"]).undo_step())


def api_structure_redo(app, req) -> Response:
    return Response.json(app.sessions.get(req.params["sid"]).redo_step())


def api_structure_history(app, req) -> Response:
    """GET → 操作日志（undo/redo 栈的元信息 + 光标位置）。"""
    return Response.json(app.sessions.get(req.params["sid"]).history_payload())


def api_structure_history_jump(app, req) -> Response:
    """POST {index} → 回到第 index 步（0 = 刚打开时）；向前/向后都行。"""
    s = app.sessions.get(req.params["sid"])
    body = req.json()
    if body.get("index") is None:
        raise ValueError("缺少 index")
    return Response.json(s.jump_history(int(body["index"])))


# ------------------------------------------------------------------ assembly
def _module_response(s, r: dict) -> Response:
    return Response.json(r)


def api_structure_modules(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    return Response.json({"placements": s.placements_payload(),
                          "restore": getattr(s, "_restore_info", None)})


def api_structure_modules_add(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    body = req.json()
    r = s.add_modules(body.get("ids") or [], positions=body.get("positions"),
                      auto=body.get("auto", True))
    return _module_response(s, r)


def api_structure_modules_update(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    body = req.json()
    pid = str(body.get("pid") or "")
    if not pid:
        raise ValueError("缺少 pid")
    pos, rot = body.get("pos"), body.get("rot")
    rotx, rotz = body.get("rotx"), body.get("rotz")
    if body.get("snapPort"):
        snap = s.snap_position(pid, pos=pos, rot=rot, rotx=rotx, rotz=rotz,
                               max_dist=body.get("snapRadius", 2.5))
        if snap:
            pos, rot = snap
    r = s.move_placement(pid, pos=pos, rot=rot, rotx=rotx, rotz=rotz,
                         delta=body.get("delta") if pos is None else None)
    return _module_response(s, r)


def api_structure_modules_remove(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    body = req.json()
    return _module_response(s, s.remove_placements(body.get("pids") or []))


def api_structure_modules_bake(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    return _module_response(s, s.clear_placements(bake=True))


def api_structure_modules_detach(app, req) -> Response:
    """POST {id?, pack?, category?, ports?, markDirty?, record?} → 基地层整体转成一个装配实例（基地层清空）。

    「在编辑器里打开模块 / 打开任意投影」用：画布 = 内容尺寸，内容是可直接拖动/旋转的实例。
    没有 ``id``/``pack`` 时包名记 ``@self``（整幅投影自包装，见 ``session.SELF_PACK``）；
    打开文件那一刻的包装传 ``markDirty=false, record=false``：它只是会话模型的变换，
    文件没被改过，不该标「未保存」也不该进撤销栈。
    """
    s = app.sessions.get(req.params["sid"])
    body = req.json() or {}
    return _module_response(s, s.detach_base(
        mid=body.get("id"), pack=body.get("pack") or "",
        category=body.get("category") or "", ports=body.get("ports"),
        mark_dirty=bool(body.get("markDirty", True)),
        record=bool(body.get("record", True))))


def api_structure_resize(app, req) -> Response:
    """POST {size:[x,y,z]} 或 {fit:true} → 改画布尺寸（超出裁掉 / 不足补空气）。"""
    s = app.sessions.get(req.params["sid"])
    body = req.json()
    return Response.json(s.resize(body.get("size"), fit=bool(body.get("fit"))))


def api_structure_save(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    body = req.json()
    path = body.get("path")
    target = None
    if path:
        target = app.resolve_path(path, must_exist=False,
                                  prefixes=("packs", "builds", "compositions",
                                            "tests/fixtures", "dist",
                                            ".cache/mcstudio"))
    return Response.json(s.save(target, fmt=body.get("format"),
                                name=body.get("name")))


def api_structure_save_module(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    body = req.json()
    tags = body.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.split(",") if t.strip()]
    out = s.save_as_module(body["pack"], body["id"],
                           category=body.get("category") or "custom",
                           tags=tags,
                           description=body.get("description") or "",
                           overwrite=bool(body.get("overwrite")))
    return Response.json(out)


def api_structure_close(app, req) -> Response:
    sid = req.params["sid"]
    app.sessions.close(sid)
    return Response.json({"closed": sid})


def api_structure_download(app, req) -> Response:
    s = app.sessions.get(req.params["sid"])
    f = app.resolve_path(s.payload()["path"])
    return Response.bytes(f.read_bytes(), "application/octet-stream",
                          headers={"Content-Disposition":
                                   f'attachment; filename="{f.name}"'})


# ------------------------------------------------------------------ tools

def api_tools(app, req) -> Response:
    """GET /api/tools → 工具清单（含参数表，前端据此自动生成表单）。"""
    from mctools import registry as TR  # noqa: PLC0415
    return Response.json(TR.catalog())


def api_structure_tool(app, req) -> Response:
    """POST {tool, params, sel, centers, brush:{shape,radius}, block, mask, seed}."""
    s = app.sessions.get(req.params["sid"])
    body = req.json()
    name = str(body.get("tool") or "")
    if not name:
        raise ValueError("缺少 tool")
    brush = body.get("brush") or {}
    return Response.json(s.apply_tool(
        name, body.get("params") or {},
        sel_box=body.get("sel"),
        centers=body.get("centers"),
        brush_shape=str(brush.get("shape") or "sphere"),
        brush_radius=float(brush.get("radius") or 4.0),
        block=body.get("block") or None,
        mask=body.get("mask") or None,
        seed=int(body.get("seed") or 0),
        update=bool(body.get("update"))))


def api_structure_select(app, req) -> Response:
    """POST {mask} 或 {at:[x,y,z], connected} → 命中数与包围盒（框选/魔棒）。"""
    s = app.sessions.get(req.params["sid"])
    body = req.json()
    return Response.json(s.select_info(mask=body.get("mask") or None,
                                      at=body.get("at"),
                                      connected=bool(body.get("connected"))))


# ------------------------------------------------------------------ jobs
def api_job(app, req) -> Response:
    job = app.jobs.get(req.params["jid"])
    if job is None:
        return err(404, "没有任务")
    return Response.json(job)


def api_job_list(app, req) -> Response:
    return Response.json({"jobs": app.jobs.list()})


def api_job_cancel(app, req) -> Response:
    """取消**排队中**的任务；已在跑的任务不能取消（渲染子进程不可中断）。"""
    job = app.jobs.cancel(req.params["jid"])
    if job is None:
        return err(404, "没有任务")
    if job["state"] != "canceled":
        what = "已在运行" if job["state"] == "running" else f"已{job['state']}"
        return err(409, f"任务{what}，无法取消排队")
    return Response.json(job)
