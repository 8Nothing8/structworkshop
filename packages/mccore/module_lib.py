"""模块库（多包）：模块 = 结构文件 + ModuleSpec 接口元数据，散在 packs/<包>/modules/ 下。

A module = one structure file (storage: .schem; .litematic 也可读) + a ModuleSpec (JSON). The spec lives either in a
sidecar `<stem>.module.json` or embedded in the litematic Metadata tag
`ModuleSpec`(embed 命令写入,两边不同时以 sidecar 为准)。

Modules live inside **asset packs**:  ``packs/<pack>/modules/<category>/``.
The merged index is written to ``packs/index.json`` and every entry records
its owning pack.

Spec format (also documented in the skill references/module-spec.md):
  {
    "id": "corridor_x", "category": "corridor", "version": 1,
    "description": "...", "tags": ["corridor"],
    "grid": {"size": [sx, sy, sz], "position": [0, 0, 0]},
    "axis": "x", "flip": true,
    "ports": [
      {"id": "w", "type": "passage", "face": "west",
       "origin": [y0, u0], "size": [h, w], "tags": []},
      {"id": "round_vent", "type": "vent", "face": "west",
       "shape": "circle", "origin": [cy, cu], "size": [d, d]}
    ],
    "notes": ""
  }

Vertical faces (west/east/north/south): origin=[y0,u0] (u along the face,
horizontal), size=[h,w] (h vertical). Up/down faces: origin=[u0,v0]=[x0,z0],
size=[w,d] (w along x, d along z).

Port **shape** (optional, default ``rect``):

* ``rect``   — ``origin`` 是矩形的一个角，``size`` = 高×宽
* ``circle`` — ``origin`` 是**圆心**，``size`` = [直径, 直径]（外接正方形 = 直径）

圆形只是**几何意图**（画图/给人或 AI 看）：引擎匹配依旧按外接矩形
（``port_bbox()`` 把圆心展开成盒子），实心空心都不看。

Port types: passage|door|stair_up|stair_down|redstone_in|redstone_out|
fluid_in|fluid_out|item_in|item_out|power_in|power_out|shaft|anchor|light

Commands:
  scan                 rebuild packs/index.json
  list                 table of modules (--category/--tag/--query/--pack)
  inspect NAME         full spec + block stats
  create NAME --size 5x4x3 --category rooms [--pack modern-arch] [--from f]
  embed NAME           write the sidecar spec into the litematic Metadata
  extract FILE         extract embedded spec to a sidecar file
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np

from mccore import structure_io as S
from mccore.paths import (ensure_packs_dir, has_packs, pack_dirs,
                          pack_module_dirs, packs_dir, write_text_lf)

INDEX = packs_dir() / "index.json"

VALID_FACES = ("west", "east", "north", "south", "up", "down")
#: 接口形状：``rect`` = 两点围出的矩形（``origin`` = 角、``size`` = 高×宽）；
#: ``circle`` = 圆心 + 直径（**``origin`` 是圆心**，``size`` = [直径, 直径]）。
#: 缺省（旧数据没有 shape 字段）= ``rect``。
VALID_SHAPES = ("rect", "circle")
VALID_TYPES = ("passage", "door", "stair_up", "stair_down", "redstone_in",
               "redstone_out", "fluid_in", "fluid_out", "item_in", "item_out",
               "power_in", "power_out", "shaft", "anchor", "light",
               # 建筑上常用的两类「接口」：风道/风口（2×2 通风管那种）与窗
               "vent", "window",
               # 通用接口：**不管里面实心还是空心**，与任何类型都能接（只按尺寸/重叠配）——
               # 柱子分段、构件对接这种就标它
               "interface")
#: 自写类型必须匹配这个格式（小写字母开头，其余小写字母/数字/_/-）。
#: 刻意**不看是否在列表里**：内置只是推荐值，用户/项目可以自定义（如 cargo_dock）。
INTERFACE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
#: 自写类型长度上限（过长会撑坏 UI 列表与索引）。
INTERFACE_TYPE_MAX = 40


def valid_interface_type(t) -> bool:
    """类型合不合法（**不看是否内置**）。"""
    return (isinstance(t, str) and len(t) <= INTERFACE_TYPE_MAX
            and bool(INTERFACE_TYPE_RE.match(t)))


def valid_interface_shape(t) -> bool:
    """形状合不合法（缺省/空 = 矩形）。"""
    return t in (None, "", *VALID_SHAPES)


def port_shape(port: dict) -> str:
    """接口形状（缺省 rect）。"""
    s = port.get("shape") or "rect"
    return s if s in VALID_SHAPES else "rect"


def port_bbox(port: dict) -> tuple[list[int], list[int]]:
    """接口 → 面内**包围盒** ``(origin, size)``（引擎统一按盒子算）。

    矩形的 origin 就是角；圆形的 origin 是圆心，转成外接正方形：
    ``lo = 圆心 - (d-1)//2``（d 为偶数时圆心偏小一侧，写清楚就不含糊）。
    """
    o = [int(v) for v in port.get("origin", [0, 0])]
    s = [int(v) for v in port.get("size", [1, 1])]
    if port_shape(port) != "circle":
        return o, s
    d = max(1, min(s[0], s[1]))
    off = (d - 1) // 2
    return [o[0] - off, o[1] - off], [d, d]


def port_from_bbox(port: dict, origin: list[int], size: list[int]) -> dict:
    """面内**包围盒** → 接口（矩形的 origin 就是角；圆形的还原成圆心）。

    与 :func:`port_bbox` 互逆——旋转/变换后用它写回。
    """
    out = dict(port)
    if port_shape(port) != "circle":
        out["origin"], out["size"] = [int(v) for v in origin], [int(v) for v in size]
        return out
    d = max(1, min(int(size[0]), int(size[1])))
    off = (d - 1) // 2
    out["origin"] = [int(origin[0]) + off, int(origin[1]) + off]
    out["size"] = [d, d]
    return out


def spec_path_for(lt: Path) -> Path:
    return lt.with_suffix(".module.json")


def load_spec(lt: Path) -> dict:
    """Load module spec: sidecar wins, else embedded Metadata, else error."""
    sp = spec_path_for(lt)
    if sp.exists():
        return json.loads(sp.read_text(encoding="utf-8"))
    d = S.read_structure(str(lt))
    meta = d["metadata"]
    if "ModuleSpec" in meta:
        return json.loads(meta["ModuleSpec"])
    raise SystemExit(f"{lt}: 没有 module spec(sidecar 或嵌入 Metadata 都没有)")


def sync_grid(spec: dict, size: tuple[int, int, int]) -> bool:
    """把 spec 的 ``grid.size`` 对齐到实际结构尺寸（**返回是否改过**）。

    ``grid`` 是**派生数据**（结构多大就拿多大），但改画布尺寸/裁剪/工具原地改之后
    容易不同步 —— 模块库扫描会报 “grid.size [1, 5, 2] != 实际 [2, 5, 2]”。
    所以凡是**写 spec** 或**扫索引**的地方都顺手校正一下。
    """
    nx, ny, nz = (int(v) for v in size)
    want = [nx, ny, nz]
    grid = spec.get("grid")
    if isinstance(grid, dict) and grid.get("size") == want:
        return False
    pos = (grid or {}).get("position") if isinstance(grid, dict) else None
    spec["grid"] = {"size": want, "position": list(pos or [0, 0, 0])}
    return True


def validate_spec(spec: dict, size: tuple[int, int, int]) -> list[str]:
    """校验 spec。**只看几何/类型，不看体素实心空心**——接口是"接口"：

    柱子分段对接、实心构件对接、风道/通道这种空心开口，用同一套东西；
    匹配只看（面贴合 + 面内矩形重叠 + 类型兼容，见 ``assemble.types_compatible``）。
    """
    errs = []
    sx, sy, sz = size
    if spec.get("grid", {}).get("size") != [sx, sy, sz]:
        errs.append(f"grid.size {spec.get('grid', {}).get('size')} != "
                    f"实际 {list(size)}")
    for p in spec.get("ports", []):
        face = p.get("face")
        if face not in VALID_FACES:
            errs.append(f"接口 {p.get('id')}: 非法 face {face}")
            continue
        # type **不限定列表**：内置类型是推荐值，也可以是用户自写的（如
        # `cargo_dock`、`skybridge`）——只校验格式/长度，不必改代码才能用。
        ptype = p.get("type")
        if not valid_interface_type(ptype):
            errs.append(
                f"接口 {p.get('id')}: type 必须是小写字母开头、"
                f"只含小写字母/数字/_/-（最长 {INTERFACE_TYPE_MAX} 字符），"
                f"收到 {ptype!r}；内置推荐值：{', '.join(VALID_TYPES)}")
        if not valid_interface_shape(p.get("shape")):
            errs.append(
                f"接口 {p.get('id')}: shape 只能是 {' / '.join(VALID_SHAPES)}"
                f"（缺省 = rect），收到 {p.get('shape')!r}")
            continue
        # 圆形存的是「圆心 + 直径」——校验前先化成外接矩形（与引擎同一套换算）
        o, s = port_bbox(p)
        if (len(p.get("origin", [])) != 2 or len(p.get("size", [])) != 2
                or min(s) < 1):
            errs.append(f"接口 {p.get('id')}: origin/size 必须是 [a,b]/[h,w]")
            continue
        if port_shape(p) == "circle" and p["size"][0] != p["size"][1]:
            errs.append(f"接口 {p.get('id')}: 圆形接口的直径要相等"
                        f"（size 收到 {p['size']}）")
        if face in ("west", "east", "north", "south"):
            if not (0 <= o[0] < sy and o[0] + s[0] <= sy):
                errs.append(f"接口 {p.get('id')}: 竖直范围 {o[0]}..{o[0]+s[0]} "
                            f"超出高度 {sy}")
            lim = sz if face in ("west", "east") else sx
            if not (0 <= o[1] < lim and o[1] + s[1] <= lim):
                errs.append(f"接口 {p.get('id')}: 水平范围超出 {lim}")
        else:
            if not (0 <= o[0] < sx and o[0] + s[0] <= sx):
                errs.append(f"接口 {p.get('id')}: x 范围超出 {sx}")
            if not (0 <= o[1] < sz and o[1] + s[1] <= sz):
                errs.append(f"接口 {p.get('id')}: z 范围超出 {sz}")
    return errs


def block_stats(d: dict) -> dict:
    v = d["voxels"]
    pal = d["palette"]
    names = [e["Name"].removeprefix("minecraft:") for e in pal]
    filled = int((v != 0).sum())
    counts: dict[str, int] = {}
    for i, name in enumerate(names):
        n = int((v == i).sum())
        if n:
            counts[name] = n
    return {"filled": filled, "palette_names": counts}


def iter_modules():
    """Yield (pack_id, structure path) for every module in every pack.

    兼容两种格式;同名模块优先 ``.schem``(存储格式)。
    """
    for pd in pack_dirs():
        mdir = pd / "modules"
        if not mdir.is_dir():
            continue
        by_stem: dict[str, Path] = {}
        for suffix in (".litematic", S.PRIMARY_SUFFIX):   # .schem 后写覆盖
            for p in mdir.rglob("*" + suffix):
                by_stem[p.stem] = p
        for p in sorted(by_stem.values()):
            yield pd.name, p


def build_index(quiet: bool = True) -> dict:
    """扫出合并索引，**不写盘**（`--check` 靠它算「应该是什么样」）。"""
    index = {"modules": {}, "packs": {}}
    for pack, lt in iter_modules():
        try:
            spec = load_spec(lt)
            d = S.read_structure(str(lt))
            stats = block_stats(d)
        except (Exception, SystemExit) as e:  # load_spec 用 SystemExit 报错
            if not quiet:
                print(f"skip {lt}: {e}")
            continue
        size = d["size"]
        if sync_grid(spec, size):
            spec_path = spec_path_for(lt)
            if spec_path.exists():
                write_text_lf(spec_path, json.dumps(spec, ensure_ascii=False, indent=1))
                if not quiet:
                    print(f"fix grid: {lt.name} -> {list(size)}")
        errs = validate_spec(spec, size)
        entry = {
            "id": spec["id"],
            "pack": pack,
            "category": spec.get("category", lt.parent.name),
            "path": str(lt.relative_to(packs_dir())).replace("\\", "/"),
            "size": list(size),
            "blocks": stats["filled"],
            "palette": stats["palette_names"],
            "ports": [{"id": p.get("id"), "type": p.get("type"),
                       "face": p.get("face"), "size": p.get("size")}
                      for p in spec.get("ports", [])],
            "tags": spec.get("tags", []),
            "description": spec.get("description", ""),
            "taxonomy": spec.get("taxonomy", {}),
            "errors": errs,
        }
        pdir = packs_dir() / pack
        for cand, field in ((f"previews/{spec['id']}.png", "preview"),
                            (f"previews/{spec['id']}_iso.png", "preview_full")):
            p = pdir / cand
            if p.is_file():
                entry[field] = str(p.relative_to(packs_dir())).replace("\\", "/")
        index["modules"][spec["id"]] = entry
        index["packs"].setdefault(pack, {"modules": []})["modules"].append(
            spec["id"])
    if not index["modules"]:
        # 全新 checkout：`packs/` 里没有资产包。这是**正常状态**，不是错，
        # 但得让人知道接下来该做什么（以前这里直接写盘 -> FileNotFoundError）。
        if not quiet:
            print("还没有任何资产包（`packs/` 是本地内容，代码仓库不附带）。\n"
                  "  建一个： ``python -m mccore.pack create <名字>``\n"
                  "  导模块：``python -m mccore.pack import-modules <目录> --pack <名字>``")
    return index


def index_text(index: dict) -> str:
    """``packs/index.json`` 的**字节级内容**（写盘与校验共用同一条口径）。"""
    return json.dumps(index, ensure_ascii=False, indent=1)


def scan(quiet: bool = False) -> dict:
    """重建合并索引并写盘（`packs/index.json`）。"""
    index = build_index(quiet=quiet)
    write_text_lf(ensure_packs_dir() / INDEX.name, index_text(index))
    if not quiet:
        print(f"index: {len(index['modules'])} 个模块 / "
              f"{len(index['packs'])} 个包 -> {INDEX}")
    return index


def filter_entries(rows, *, category=None, tag=None, query=None, pack=None,
                   material=None, min_blocks=None, max_blocks=None,
                   has_port=None) -> list[dict]:
    """Structured filters shared by `module_lib list` and `pack search`."""
    out = []
    for r in rows:
        if category and r["category"] != category:
            continue
        if tag and tag not in r["tags"]:
            continue
        if pack and r.get("pack") != pack:
            continue
        if query:
            q = query.lower()
            if not (q in r["id"].lower() or q in r["description"].lower()
                    or any(q in t for t in r["tags"])):
                continue
        if material:
            if not any(material in k for k in r["palette"]):
                continue
        if min_blocks is not None and r["blocks"] < min_blocks:
            continue
        if max_blocks is not None and r["blocks"] > max_blocks:
            continue
        if has_port is not None and bool(r["ports"]) != has_port:
            continue
        out.append(r)
    return out


def missing_module_hint(mod_name: str) -> str:
    """模块找不到时该打印什么。

    `packs/` 是**本地内容**（仓库只带引擎 + 组合定义），所以「找不到模块」有
    两种完全不同的原因，提示也必须不一样 —— 否则会让人去跑一个没用的命令：

    * 一个包都没有 -> 告诉他 `packs/` 是空的，怎么把包弄进来；
    * 有包但没这个模块 -> 那才该重建索引 / 看清单。
    """
    if not has_packs():
        return (
            f"模块不存在: {mod_name}\n"
            "  原因：`packs/` 里没有任何资产包 —— 它是本地内容，代码仓库不附带。\n"
            "  做法：把资产包放进 packs/，或自己建一个：\n"
            "    python -m mccore.pack create <包名>\n"
            "    python -m mccore.pack import-modules <你的投影目录> --pack <包名>\n"
            "  （零依赖的组合不用资产包，比如： python -m mccore.compose math-cube）")
    ids = ", ".join(p.name for p in pack_dirs())
    return (f"模块不存在: {mod_name}（当前资产包：{ids}）\n"
            "  `python -m mccore.module_lib list` 看有哪些模块；"
            "索引过期就 `python -m mccore.pack scan` 重建")


def find_module(name: str) -> Path | None:
    """Search all packs by file stem, then fall back to the index."""
    for _pack, lt in iter_modules():
        if lt.stem == name:
            return lt
    if INDEX.exists():
        idx = json.loads(INDEX.read_text(encoding="utf-8"))["modules"]
        if name in idx:
            return packs_dir() / idx[name]["path"]
    return None


def cmd_scan(_a) -> None:
    scan()


def cmd_list(a) -> None:
    if not INDEX.exists():
        scan()
    idx = json.loads(INDEX.read_text(encoding="utf-8"))["modules"]
    rows = sorted(idx.values(), key=lambda r: (r.get("pack", ""),
                                               r["category"], r["id"]))
    rows = filter_entries(
        rows, category=a.category, tag=a.tag, query=a.query, pack=a.pack,
        material=a.material, min_blocks=a.min_blocks, max_blocks=a.max_blocks,
        has_port=False if a.no_port else a.has_port)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print(f"{'id':24} {'包':16} {'类别':12} {'尺寸':10} {'方块':>6}  "
          f"{'接口':30} 标签 / 预览")
    for r in rows:
        ports = ",".join(f"{p['id']}:{p['type']}:{p['face']}"
                         for p in r["ports"])
        err = " [!spec]" if r["errors"] else ""
        prev = r.get("preview", "-")
        print(f"{r['id']:24} {r.get('pack', ''):16} {r['category']:12} "
              f"{'x'.join(map(str, r['size'])):10} {r['blocks']:>6}  "
              f"{ports:30} {','.join(r['tags'])} / {prev}{err}")


def cmd_inspect(a) -> None:
    lt = find_module(a.name)
    if lt is None:
        raise SystemExit(f"没有模块 {a.name}")
    spec = load_spec(lt)
    print(json.dumps(spec, ensure_ascii=False, indent=1))
    d = S.read_structure(str(lt))
    print("\n-- 方块统计 --")
    for name, n in sorted(block_stats(d)["palette_names"].items(),
                          key=lambda kv: -kv[1]):
        print(f"  {n:>6}  {name}")
    errs = validate_spec(spec, d["size"])
    print("\n-- spec 校验 --")
    print("\n".join("  ! " + e for e in errs) if errs else "  OK")


def cmd_create(a) -> None:
    dims = [int(v) for v in a.size.split("x")]
    if len(dims) != 3 or min(dims) < 1:
        raise SystemExit("--size 用 长x高x宽 格式,如 5x4x3")
    sx, sy, sz = dims
    name = a.name
    cat = a.category or "custom"
    pack = a.pack
    d = packs_dir() / pack / "modules" / cat
    d.mkdir(parents=True, exist_ok=True)
    lt = d / (name + ".schem")
    if lt.exists():
        raise SystemExit(f"已存在: {lt}")
    if a.src:
        src = Path(a.src)
        if not src.exists():
            raise SystemExit(f"没有 {src}")
        data = S.read_structure(str(src))
        if tuple(data["size"]) != (sx, sy, sz):
            print(f"注意: 源尺寸 {data['size']} != {dims},以源为准")
        S.write_structure(str(lt), data["voxels"], data["palette"],
                          data["position"], data["size"], name=name)
    else:
        v = np.zeros((sy, sz, sx), dtype=np.uint16)
        S.write_structure(str(lt), v, [{"Name": "minecraft:air"}],
                          (0, 0, 0), (sx, sy, sz), name=name)
    spec = {
        "id": name, "pack": pack, "category": cat, "version": 1,
        "description": "", "tags": [],
        "grid": {"size": [sx, sy, sz], "position": [0, 0, 0]},
        "axis": "x", "flip": True,
        "ports": [],
        "notes": "AI 生成: 声明接口(出入口/红石/水道)后 module_lib embed",
    }
    write_text_lf(spec_path_for(lt),
        json.dumps(spec, ensure_ascii=False, indent=1))
    print(f"创建 {lt}")
    print(f"spec  {spec_path_for(lt)}")
    scan()


def cmd_embed(a) -> None:
    lt = find_module(a.name)
    if lt is None:
        raise SystemExit(f"没有模块 {a.name}")
    spec = load_spec(lt)
    d = S.read_structure(str(lt))
    errs = validate_spec(spec, d["size"])
    if errs:
        raise SystemExit("spec 校验失败:\n" + "\n".join(errs))
    meta = dict(d["metadata"])
    meta["ModuleSpec"] = json.dumps(spec, ensure_ascii=False)
    S.write_structure(str(lt), d["voxels"], d["palette"], d["position"],
                      d["size"], metadata=meta, name=spec["id"],
                      data_version=d["data_version"])
    print(f"已嵌入 ModuleSpec -> {lt}")


def cmd_extract(a) -> None:
    src = Path(a.file)
    d = S.read_structure(str(src))
    if "ModuleSpec" not in d["metadata"]:
        raise SystemExit(f"{src} 没有嵌入 ModuleSpec")
    spec = json.loads(d["metadata"]["ModuleSpec"])
    sp = spec_path_for(src)
    write_text_lf(sp, json.dumps(spec, ensure_ascii=False, indent=1))
    print(f"提取 -> {sp}")


def cmd_tags(a) -> None:
    """List tags (count / namespace) or rename/merge/delete them."""
    from mccore import library as LB  # noqa: PLC0415 (延迟导入避免环)
    if a.rename:
        old, _, new = a.rename.partition("=")
        if not new:
            raise SystemExit("--rename 用 旧名=新名 形式")
        r = LB.rename_tag(old, new)
        print(f"标签重命名: {r['from']} -> {r['to']}，影响 {r['affected']} 个模块")
        return
    if a.merge:
        src, _, dst = a.merge.partition("=")
        if not dst:
            raise SystemExit("--merge 用 源标签=目标标签 形式")
        r = LB.merge_tags(src, dst)
        print(f"标签合并: {r['from']} -> {r['to']}，影响 {r['affected']} 个模块")
        return
    if a.delete:
        r = LB.delete_tag(a.delete)
        print(f"已从 {r['affected']} 个模块移除标签 {r['tag']}")
        return
    rows = LB.tag_tree()
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print(f"{'标签':34} {'数量':>4}")
    for r in rows:
        print(f"{r['name']:34} {r['count']:>4}")
    print(f"\n{len(rows)} 个标签 / {LB.stats()['modules']} 个模块")


def cmd_tag(a) -> None:
    """Add/remove/replace tags on one or more modules."""
    from mccore import library as LB  # noqa: PLC0415
    ids = [n for n in a.names if n]
    if a.all:
        ids = sorted(LB.load_index().get("modules", {}))
    if not ids:
        raise SystemExit("给出模块名，或用 --all")
    set_to = None
    if a.set is not None:
        set_to = [t for t in a.set.split(",") if t.strip()]
    r = LB.set_tags(ids, add=a.add.split(",") if a.add else [],
                    remove=a.remove.split(",") if a.remove else [],
                    set_to=set_to)
    for k in ("updated", "unchanged", "missing"):
        if r[k]:
            print(f"{k}: {', '.join(map(str, r[k]))}")
    for e in r["errors"]:
        print(f"FAIL {e['id']}: {e['error']}")


def cmd_meta(a) -> None:
    """Edit description/category/notes of a module."""
    from mccore import library as LB  # noqa: PLC0415
    r = LB.set_meta(a.name, description=a.description, category=a.category,
                    notes=a.notes)
    print(f"{a.name}: 更新 {', '.join(r['changed']) or '无改动'}")


def cmd_rm(a) -> None:
    """Backup + remove a module (structure + sidecar)."""
    from mccore import library as LB  # noqa: PLC0415
    ids = [n for n in a.names if n]
    if a.all and a.category:
        idx = LB.load_index().get("modules", {})
        ids = sorted(m["id"] for m in idx.values()
                     if m.get("category") == a.category)
    if not ids:
        raise SystemExit("给出模块名（或 --category 配合 --all）")
    if not a.yes:
        print("将删除(备份到 .cache/backups/modules):", ", ".join(ids))
        print("确认请加 --yes")
        return
    for mid in ids:
        try:
            r = LB.delete_module(mid, backup=not a.hard)
            print(f"[ok] {mid} -> {r['backup']}")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {mid}: {e}")


def cmd_crop(a) -> None:
    """Trim empty outer layers of a module and update its spec."""
    from mccore import library as LB  # noqa: PLC0415
    for name in a.names:
        try:
            r = LB.crop_module(name)
            print(f"[ok] {name}: {r['from']} -> {r['size']}" if r["changed"]
                  else f"[skip] {name}: 无空气边界")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="模块资产包管理器(multi-pack)",
        epilog="模块目录: packs/<pack>/modules/<category>/<name>.schem")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="重建 packs/index.json")
    p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("list", help="列出模块")
    p.add_argument("--category")
    p.add_argument("--tag")
    p.add_argument("--query")
    p.add_argument("--pack")
    p.add_argument("--material", help="按主方块材料过滤(子串)")
    p.add_argument("--min-blocks", type=int)
    p.add_argument("--max-blocks", type=int)
    p.add_argument("--has-port", action="store_true", default=None)
    p.add_argument("--no-port", action="store_true", help="只看无接口模块(摆件/装饰)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("inspect", help="查看模块 spec + 方块统计")
    p.add_argument("name")
    p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("create", help="创建新模块(空或从现有投影)")
    p.add_argument("name")
    p.add_argument("--size", required=True)
    p.add_argument("--category")
    p.add_argument("--pack", default="modern-arch", help="目标资产包(默认 modern-arch)")
    p.add_argument("--from", dest="src")
    p.set_defaults(fn=cmd_create)
    p = sub.add_parser("embed", help="把 sidecar spec 写进 litematic Metadata")
    p.add_argument("name")
    p.set_defaults(fn=cmd_embed)
    p = sub.add_parser("extract", help="从 litematic 提取嵌入的 spec")
    p.add_argument("file")
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("tags", help="标签总览 / 重命名 / 合并 / 删除")
    p.add_argument("--rename", metavar="OLD=NEW")
    p.add_argument("--merge", metavar="SRC=DST")
    p.add_argument("--delete", metavar="TAG")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_tags)
    p = sub.add_parser("tag", help="给模块加/删/替换标签")
    p.add_argument("names", nargs="*")
    p.add_argument("--all", action="store_true", help="作用于全部模块")
    p.add_argument("--add")
    p.add_argument("--remove")
    p.add_argument("--set", help="整组替换(逗号分隔)")
    p.set_defaults(fn=cmd_tag)
    p = sub.add_parser("meta", help="改描述/分类/备注")
    p.add_argument("name")
    p.add_argument("--description")
    p.add_argument("--category")
    p.add_argument("--notes")
    p.set_defaults(fn=cmd_meta)
    p = sub.add_parser("rm", help="删除模块(默认备份到 .cache/backups)")
    p.add_argument("names", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--category", help="配合 --all 只删该分类")
    p.add_argument("--hard", action="store_true", help="直接删除不备份")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_rm)
    p = sub.add_parser("crop", help="裁掉模块外围空气层并更新 spec")
    p.add_argument("names", nargs="+")
    p.set_defaults(fn=cmd_crop)
    a = ap.parse_args()
    a.fn(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
