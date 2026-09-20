"""仓库**派生产物**的一键重建 / 校验（给人和 CI 用）。

为什么要有一个统一入口
----------------------
仓库里有一批文件**不是手写的，是算出来的**：:

    packs/index.json            模块合并索引（id/尺寸/接口/标签/预览…）
    packs/<pack>/pack.json      包清单：provides / previews / files(sha256)
    packs/<pack>/catalog.md     该包的 Tier-0 目录
    packs/catalog.md            资产包总目录
    registry.json               组合 + 资产包 + 语料 的能力清单
    packs/<pack>/previews/*.png 模块预览图（需要 mcrender + mcassets）
    packages/mcrender/data/entity_models.json      方块实体几何表
    packages/mckit/data/center_cover.json          墙中心覆盖表
    packages/mcmaterials/data/{block_catalog,texture_stats,…}.json
    skills/minecraft-block-models/data/*.json  方块属性表
    kb/registry.json · kb/catalog.md · kb/index.sqlite

这些东西**必须由程序从源数据算出来**。手写/手改一定会在某台机器上和代码脱节，
而脱节的表现是别人 clone 下来 `pack validate` 一片红 —— 极难查。所以：

* 改完资产包 / 组合 / 模块 → 跑 ``python -m mccore.bootstrap``，别手改那些文件；
* CI 跑 ``python -m mccore.bootstrap --check``，只要有人手改过就退码 1。

用法::

    python -m mccore.bootstrap                # 纯本地层（不联网）：索引 + 清单 + 目录 + 注册表
    python -m mccore.bootstrap --all          # 再加预览图 + 方块数据表（首次要联网拉 mcassets）
    python -m mccore.bootstrap --previews     # 只补/重渲染预览图
    python -m mccore.bootstrap --data         # 只重建方块数据表（实体几何/材质/属性）
    python -m mccore.bootstrap --check        # 只校验「派生产物是否最新」；过期退码 1
    python -m mccore.bootstrap --check --json # 机器可读

``--check`` 只覆盖**纯本地层**（能在无网、无 mcassets 的机器上算出来），
需要联网才能重算的东西（预览图/数据表）不在校验范围 —— CI 里它们本来就不该重跑。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mccore import module_lib as ML
from mccore import pack as PK
from mccore import registry as REG
from mccore.paths import (has_compositions, has_packs, pack_dirs, repo_root,
                          write_text_lf)

#: 派生产物的**唯一序列化口径**。写盘和校验都走这里，避免「校验用的格式和
#: 写盘的格式不一样」这种最坑的自欺欺人。
ARTIFACTS = "见模块 docstring"

#: README 里那块「仓库现状」的标记：生成器只改这两个标记之间的内容。
README_BEGIN = "<!-- gen:inventory:begin -->"
README_END = "<!-- gen:inventory:end -->"

#: 总览 skill 里那块「命令地图」的标记。
#: 命令面是**从代码里扫出来的**（`ast` 读 argparse + docstring），
#: 所以加了新命令忘了写文档时，`bootstrap --check` 会直接报出来。
SKILL_BEGIN = "<!-- gen:cli-map:begin -->"
SKILL_END = "<!-- gen:cli-map:end -->"
SKILL_PATH = "skills/structworkshop-overview/SKILL.md"


# --------------------------------------------------------------- README 现状块
def _pkg_rows() -> list[tuple[str, str, int]]:
    """每个引擎包：``(名字, 一句话, .py 个数)``。

    「一句话」直接取该包 `README.md` 的第一行标题（去掉 ``# ``），
    所以改包简介只需改那个 README，不用同步两个地方。
    """
    out = []
    for d in sorted((repo_root() / "packages").iterdir()):
        rm = d / "README.md"
        if not (d.is_dir() and rm.is_file()):
            continue
        head = rm.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
        # 标题形如 “mccore — structworkshop 内核（层 0）”，去掉重复的包名前缀
        for sep in (" — ", " - ", " – "):
            if head.startswith(d.name + sep):
                head = head[len(d.name) + len(sep):]
                break
        out.append((d.name, head, len(list(d.glob("*.py")))))
    return out


def _composition_rows() -> list[tuple[str, str, str, int, int]]:
    """每个组合：``(id, 名称, 依赖的资产包, 提示词数, 预设数)``（from structure.json）。"""
    from mccore.paths import composition_dirs  # noqa: PLC0415
    out = []
    for cd in composition_dirs():
        try:
            e = json.loads((cd / "structure.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        packs = ", ".join(e.get("packs") or []) or "—"
        out.append((e.get("id", cd.name), e.get("name", ""), packs,
                    len(list((cd / "prompts").glob("*.md"))),
                    len(list((cd / "params").glob("*.json")))))
    return out


def _pack_rows() -> list[tuple[str, str, str, int, int]]:
    """每个资产包：``(id, 名称, 版本, 模块数, 有预览图的模块数)``（from pack.json）。"""
    from mccore.paths import pack_dirs  # noqa: PLC0415
    out = []
    for pd in pack_dirs():
        try:
            m = json.loads((pd / "pack.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        mods = m.get("provides", {}).get("modules", [])
        prev = m.get("previews", {})
        out.append((m.get("id", pd.name), m.get("name", ""),
                    m.get("version", ""), len(mods),
                    sum(1 for x in mods if prev.get(x))))
    return out


def inventory_text() -> str:
    """README 里那块「仓库现状」的**字节级内容**（写盘与校验共用）。

    以前这段是手写的（“引擎（9 个包）”、“5 个组合”）—— 改了代码就会和现实脱节。
    现在全部从 `packages/` `compositions/` `packs/` 现读现算。
    """
    pkgs = _pkg_rows()
    comps = _composition_rows()
    packs = _pack_rows()
    npy = sum(n for _n, _d, n in pkgs)
    lines = [
        README_BEGIN,
        "### 仓库现状（自动生成）",
        "",
        "> 本节的表与数字由 `python -m mccore.bootstrap` 从 `packages/` "
        "`compositions/` `packs/` 读出来。",
        "> **不要手写**：手写就会和现实脱节；"
        "`python -m mccore.bootstrap --check` 会在它过期时报错。",
        "",
        f"**引擎**：{len(pkgs)} 个包 / {npy} 个 `.py`",
        "",
        "| 包 | 作用 | `.py` |",
        "|---|---|---:|",
    ]
    lines += [f"| `{n}` | {d} | {c} |" for n, d, c in pkgs]
    lines += ["", f"**组合**（`compositions/`，可选内容）：{len(comps)} 个"]
    if comps:
        lines += ["", "| id | 名称 | 需要资产包 | 提示词 | 预设 |",
                  "|---|---|---|---:|---:|"]
        lines += [f"| `{i}` | {n} | {p} | {a} | {b} |" for i, n, p, a, b in comps]
    else:
        lines += ["", "> `compositions/` 是空的 —— 这是**可选内容**："
                  "把一套 compositions 拷到仓库根目录就能直接用（引擎不依赖它）。"]
    lines += ["", f"**资产包**（`packs/`，本地内容）：{len(packs)} 个"]
    if packs:
        lines += ["", "| id | 名称 | 版本 | 模块 | 有预览图 |",
                  "|---|---|---|---:|---:|"]
        lines += [f"| `{i}` | {n} | {v} | {a} | {b} |" for i, n, v, a, b in packs]
    else:
        lines += ["", "> `packs/` 是空的 —— 这是**本地内容**："
                  "把资产包拷进来或 `python -m mccore.pack create <名字>` 新建。"]
    lines += ["", README_END]
    return "\n".join(lines) + "\n"


def _readme_with_inventory() -> str | None:
    """把 README.md 里两个标记之间换成 ``inventory_text()``；返回**应该是**的全文。

    没有标记就返回 None（还没插入过，交给 ``--write-readme`` 一次性插入）。
    """
    rm = repo_root() / "README.md"
    if not rm.is_file():
        return None
    text = rm.read_text(encoding="utf-8")
    i, j = text.find(README_BEGIN), text.find(README_END)
    if i < 0 or j < 0 or j < i:
        return None
    return text[:i] + inventory_text() + text[j + len(README_END) + 1:]


# --------------------------------------------------------------- 命令地图
#: 分组规则：按包名归到「AI 干活时会想干什么」下面。没列到的自动归到「其它」。
_GROUPS = (
    ("看清仓库 / 自检", ("mccore.bootstrap", "mccore.registry",
                      "mccore.compose", "mccore.projects")),
    ("生成建筑（组合）", ("compositions/*",)),
    ("资产库（模块 / 包）", ("mccore.pack", "mccore.module_lib", "mccore.library",
                        "mcslice.slice", "mcslice.learn")),
    ("装配 / 分段建造", ("mccore.assemble", "mccore.stage_build")),
    ("格式转换", ("mccore.convert",)),
    ("体素工具", ("mctools", "mccore.fields")),
    ("渲染 / 出图", ("mcrender", "mcrender.gallery", "mcrender.sheet",
                   "mcrender.assets", "mcrender.block_index", "mcrender.model",
                   "mcrender.legacy_voxel")),
    ("质检 / 评审", ("mcqa.qa_check", "mcqa.walk_check", "mcqa.preview",
                   "mcqa.review_loop", "mcqa.vision_review")),
    ("规范语料库", ("mckb",)),
    ("材质 / 颜色", ("mcmaterials",)),
    ("建筑语法 / 方块语义", ("mckit.grammar", "mckit.connect", "mckit.update")),
    ("可视化工作台", ("mcstudio",)),
    ("自检小工具", ("tools/",)),
)


def _entry_summary(path: Path) -> str:
    """模块 docstring 的第一行（去掉“xx — ”之类前缀）。"""
    import ast  # noqa: PLC0415
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return ""
    doc = ast.get_docstring(tree) or ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line[:110]
    return ""


def _js_summary(path: Path) -> str:
    """JS 工具没有 docstring —— 取开头注释块里第一句有内容的话。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines()[:12]:
        line = line.strip().lstrip("/*").strip().rstrip("*/").strip()
        if line and not line.startswith(("'use strict'", "//")):
            return line[:110]
    return ""


def _subcommands(path: Path) -> list[tuple[str, str]]:
    """从 ``add_parser("x", help="…")`` 里抽出子命令名 + 帮助。"""
    import ast  # noqa: PLC0415
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_parser"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            continue
        name = node.args[0].value
        if name.startswith("-"):
            continue
        help_ = ""
        for kw in node.keywords:
            if kw.arg == "help" and isinstance(kw.value, ast.Constant):
                help_ = str(kw.value.value)
        out.append((name, help_))
    # 去重（同一个名字可能出现在多个分支里）
    seen, uniq = set(), []
    for n, h in out:
        if n not in seen:
            seen.add(n)
            uniq.append((n, h))
    return uniq


def cli_map_text() -> str:
    """总览 skill 里那张「命令地图」的**字节级内容**（写盘与校验共用）。

    入口集合 = 带 ``__main__`` 的模块（引擎包 + tools/）；子命令从 argparse 扫。
    不是手写表格：加了一条命令、忘了写文档，``--check`` 就会报。
    """
    root = repo_root()
    rows: list[tuple[str, str, list[tuple[str, str]]]] = []
    for f in sorted(root.glob("packages/**/*.py")):
        src = f.read_text(encoding="utf-8", errors="replace")
        if '__main__' not in src:
            continue
        rel = f.relative_to(root).as_posix()          # packages/mccore/assemble.py
        mod = rel[len("packages/"):].removesuffix(".py").replace("/", ".")
        if mod.endswith(".__main__") or mod.endswith(".cli"):
            mod = mod.rsplit(".", 1)[0]              # mckb.cli -> mckb
        rows.append((mod, _entry_summary(f), _subcommands(f)))
    for f in sorted(root.glob("tools/*")):
        if f.suffix not in (".py", ".js"):
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        if f.suffix == ".py" and '__main__' not in src:
            continue
        summ = _entry_summary(f) if f.suffix == ".py" else _js_summary(f)
        rows.append((f"tools/{f.name}", summ, []))

    # 去重（mckb / mckb.cli 归并后可能重复）
    dedup: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for mod, summ, subs in rows:
        if mod in dedup:
            old_s, old_subs = dedup[mod]
            dedup[mod] = (old_s or summ, old_subs or subs)
        else:
            dedup[mod] = (summ, subs)

    used: set[str] = set()
    lines = [SKILL_BEGIN,
             "### 命令地图（自动生成，别手改）",
             "",
             "> 由 `python -m mccore.bootstrap` 用 `ast` 从代码里扫出来（带 `__main__` 的模块 + "
             "argparse 子命令）。",
             "> 加/改了命令就跑一次 bootstrap；忘了写文档 `--check` 会报。",
             ""]
    for title, keys in _GROUPS:
        hit = [(m, s, u) for m, (s, u) in sorted(dedup.items())
               if any(m == k or m.startswith(k) for k in keys)]
        if not hit:
            continue
        used |= {m for m, _s, _u in hit}
        lines.append(f"**{title}**")
        lines.append("")
        lines.append("| 命令 | 作用 |")
        lines.append("|---|---|")
        for m, summ, subs in hit:
            summ = summ.replace("|", "\\|")
            cell = f"`python -m {m}`" if not m.startswith("tools/") else \
                   (f"`python {m}`" if m.endswith(".py") else f"`node {m}`")
            if subs:
                cell += " " + ", ".join(f"`{n}`" for n, _h in subs)
            lines.append(f"| {cell} | {summ} |")
        lines.append("")
    rest = [(m, s, u) for m, (s, u) in sorted(dedup.items()) if m not in used]
    if rest:
        lines.append("**其它**")
        lines.append("")
        lines.append("| 命令 | 作用 |")
        lines.append("|---|---|")
        for m, summ, subs in rest:
            summ = summ.replace("|", "\\|")
            cell = f"`python -m {m}`" if not m.startswith("tools/") else \
                   (f"`python {m}`" if m.endswith(".py") else f"`node {m}`")
            if subs:
                cell += " " + ", ".join(f"`{n}`" for n, _h in subs)
            lines.append(f"| {cell} | {summ} |")
        lines.append("")
    lines.append(f"共 {len(dedup)} 个可执行入口。")
    lines.append(SKILL_END)
    return "\n".join(lines) + "\n"


def _skill_with_map() -> str | None:
    """总览 skill 里两个标记之间换成 ``cli_map_text()``；没标记返回 None。"""
    p = repo_root() / SKILL_PATH
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8")
    i, j = text.find(SKILL_BEGIN), text.find(SKILL_END)
    if i < 0 or j < 0 or j < i:
        return None
    return text[:i] + cli_map_text() + text[j + len(SKILL_END) + 1:]


def sync_skill(quiet: bool = False) -> int:
    """刷新总览 skill 里的命令地图。"""
    want = _skill_with_map()
    if want is None:
        if not quiet:
            print(f"  {SKILL_PATH} 里没有 cli-map 标记，跳过")
        return 0
    p = repo_root() / SKILL_PATH
    if p.read_text(encoding="utf-8") == want:
        return 0
    write_text_lf(p, want)
    if not quiet:
        print(f"  {SKILL_PATH} 命令地图已刷新")
    return 1


# --------------------------------------------------------------- 纯本地层
def sync_local(quiet: bool = False) -> dict:
    """重建「不需要联网」的那一层：索引 → 包清单 → 目录 → 注册表 → README 现状块。"""
    stats = {}
    idx = ML.scan(quiet=quiet)                    # packs/index.json
    stats["modules"] = len(idx["modules"])
    stats["packs"] = len(idx["packs"])
    pds = PK.rescan_manifests(idx, quiet=quiet)   # pack.json + 各级 catalog.md
    stats["manifests"] = len(pds)
    REG.cmd_scan(argparse.Namespace())            # registry.json
    stats["compositions"] = len(REG.collect()["compositions"])
    stats["readme"] = sync_readme(quiet=quiet)    # README 的「仓库现状」块
    stats["skill"] = sync_skill(quiet=quiet)      # 总览 skill 的命令地图
    stats["kb"] = _sync_kb(quiet=quiet)           # kb/ 有就顺带重建
    return stats


def sync_readme(quiet: bool = False) -> int:
    """把 README.md 里两个标记之间的「仓库现状」重写一遍。

    标记还没插入过（首次）时什么也不做 —— 用 ``--write-readme`` 插一次，
    之后 bootstrap 就会一直维护它。返回 1 表示写过了。
    """
    want = _readme_with_inventory()
    if want is None:
        if not quiet:
            print("  README 里还没插 `gen:inventory` 标记 —— 先跑 "
                  "`python -m mccore.bootstrap --write-readme` 插一次")
        return 0
    rm = repo_root() / "README.md"
    if rm.read_text(encoding="utf-8") == want:
        return 0
    write_text_lf(rm, want)
    if not quiet:
        print("  README「仓库现状」块已刷新")
    return 1


def insert_readme_block(quiet: bool = False) -> int:
    """一次性：在 README 的「仓库里有什么 / 没有什么」之后插入生成块。

    幂等 —— 已经有标记就只刷新内容。
    """
    rm = repo_root() / "README.md"
    text = rm.read_text(encoding="utf-8")
    if README_BEGIN in text and README_END in text:
        return sync_readme(quiet=quiet)
    anchor = "## 五分钟上手"
    if anchor not in text:
        print(f"  找不到插入锚点「{anchor}」—— 请手动插入 {README_BEGIN} … {README_END}")
        return 0
    text = text.replace(anchor, inventory_text() + "\n" + anchor, 1)
    write_text_lf(rm, text)
    if not quiet:
        print("  已在 README 插入「仓库现状」生成块")
    return 1


def _sync_kb(quiet: bool = False) -> int:
    """``kb/`` 存在才重建语料库索引（它是可选内容，不在代码仓库里）。

    **走子进程，不 import mckb** —— `mckb` 是层 3，`mccore` 是层 0，
    静态 import 就是一条 0→3 的反向依赖（`packages/ARCHITECTURE.md` §2 不许）。
    bootstrap 本来就是「在 CLI 边界上编排各个包」，那就把边界守干净。
    """
    import subprocess  # noqa: PLC0415
    from mccore.paths import child_env  # noqa: PLC0415

    if not (repo_root() / "kb").is_dir():
        return 0
    ok = 0
    for args in (["scan"], ["index"]):
        r = subprocess.run([sys.executable, "-m", "mckb", *args],
                           cwd=str(repo_root()), env=child_env(),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            ok += 1
            continue
        print(f"  kb/ 重建失败（mckb {' '.join(args)}，exit {r.returncode}）"
              " —— 语料库是可选内容，继续")
        for ln in ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-2:]:
            print(f"        {ln[:150]}")
        return 0
    return 1 if ok == 2 else 0


# --------------------------------------------------------------- 需要资源的那层
def sync_previews(pack: str | None = None, force: bool = False,
                  quiet: bool = False) -> int:
    """补/重渲染模块预览图（需要 mcrender + `.cache/mcassets`）。"""
    from mccore import library as LB  # noqa: PLC0415
    if not has_packs():
        if not quiet:
            print("  跳过预览图：`packs/` 里没有资产包")
        return 0
    rep = LB.render_previews(pack, force=force,
                             progress=(None if quiet else _progress))
    if not quiet:
        print(f"  预览图：新渲染 {len(rep['rendered'])} · 跳过 {len(rep['skipped'])}"
              f" · 失败 {len(rep['failed'])}")
    for it in rep["failed"][:5]:
        print(f"    [FAIL] {it.get('id')}: {str(it.get('error'))[:160]}")
    # 预览图变了 -> pack.json 的 previews/files(sha256) 必须跟着重算
    if rep["rendered"]:
        PK.rescan_manifests(ML.build_index(), quiet=True)
        if not quiet:
            print("  预览图有变化 -> 已重刷 pack.json / catalog.md")
    return len(rep["rendered"])


def _progress(done: int, total: int, mid: str, state: str) -> None:
    if state in ("rendered", "failed"):
        print(f"    [{done}/{total}] {mid} {state}")


def sync_data(quiet: bool = False) -> int:
    """重建「从 Minecraft 资源算出来」的方块数据表。

    三张表三个来源，都要 `.cache/mcassets`（首次联网拉）：
      * `packages/mcrender/data/entity_models.json`  ← tools/export_entity_models.js（需要 node）
      * `packages/mckit/data/center_cover.json`      ← tools/export_center_cover.py
      * `packages/mcmaterials/data/*` + `skills/.../data/*` ← mcrender.block_index / mcmaterials
    """
    import subprocess  # noqa: PLC0415
    from mccore.paths import child_env  # noqa: PLC0415

    tools = repo_root() / "tools"
    jobs: list[tuple[str, list[str]]] = [
        ("方块属性表 all_blocks/incomplete_blocks",
         [sys.executable, "-m", "mcrender.block_index", "--all"]),
        ("材质目录 block_catalog/texture_stats",
         [sys.executable, "-m", "mcmaterials", "catalog"]),
        ("方块颜色表 block_colors",
         [sys.executable, "-m", "mcmaterials", "colors"]),
        ("墙中心覆盖表 center_cover",
         [sys.executable, str(tools / "export_center_cover.py")]),
    ]
    js = tools / "export_entity_models.js"
    if js.is_file() and _which("node"):
        jobs.append(("方块实体几何 entity_models",
                     ["node", str(js)]))

    ok = 0
    for label, cmd in jobs:
        r = subprocess.run(cmd, cwd=str(repo_root()), env=child_env(),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            ok += 1
            if not quiet:
                print(f"  ok   {label}")
        else:
            print(f"  FAIL {label}（exit {r.returncode}）")
            tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-3:]
            for ln in tail:
                print(f"        {ln[:150]}")
    return ok


def _which(name: str) -> str | None:
    import shutil  # noqa: PLC0415
    return shutil.which(name)


# --------------------------------------------------------------- 校验
def _expected_local() -> dict[str, str]:
    """算出「纯本地层每个派生产物**应该是**什么内容」（不写盘）。"""
    idx = ML.build_index(quiet=True)
    want: dict[str, str] = {
        "packs/index.json": ML.index_text(idx),
        "registry.json": json.dumps(REG.collect(), ensure_ascii=False, indent=1),
    }
    pds = pack_dirs()
    for pd in pds:
        m = PK.refresh_manifest(pd, idx=idx, write=False)
        want[f"packs/{pd.name}/pack.json"] = (
            json.dumps(m, ensure_ascii=False, indent=2) + "\n")
        want[f"packs/{pd.name}/catalog.md"] = (
            "\n".join(PK.write_catalog(pd, idx, write=False)) + "\n")
    want["packs/catalog.md"] = PK.master_catalog_text(pds, idx)
    readme = _readme_with_inventory()
    if readme is not None:
        want["README.md"] = readme
    skill = _skill_with_map()
    if skill is not None:
        want[SKILL_PATH] = skill
    return want


def check(json_out: bool = False) -> int:
    """校验纯本地层是否最新。返回退出码（0 = 全一致）。"""
    root = repo_root()
    want = _expected_local()
    stale: list[str] = []
    missing: list[str] = []
    for rel, text in sorted(want.items()):
        p = root / rel
        if not p.is_file():
            missing.append(rel)
            continue
        # 与 write_text_lf 同口径：utf-8 + LF
        have = p.read_bytes()
        if have != text.encode("utf-8"):
            stale.append(rel)
    if json_out:
        print(json.dumps({"stale": stale, "missing": missing,
                          "checked": len(want)}, ensure_ascii=False, indent=1))
    else:
        for rel in missing:
            print(f"  缺   {rel}")
        for rel in stale:
            print(f"  过期 {rel}")
        if stale or missing:
            print(f"\n{len(stale)} 个过期 / {len(missing)} 个缺失"
                  f"（共校验 {len(want)} 个派生产物）")
            print("这些都是**算出来的**，别手改 —— 跑 `python -m mccore.bootstrap` 重建。")
        else:
            print(f"派生产物都是最新的（{len(want)} 个）")
    return 1 if (stale or missing) else 0


# --------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="重建 / 校验仓库的派生产物（别手写这些文件）",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--all", action="store_true",
                    help="连预览图 + 方块数据表一起（首次要联网拉 mcassets）")
    ap.add_argument("--previews", action="store_true", help="只补/重渲染预览图")
    ap.add_argument("--data", action="store_true", help="只重建方块数据表")
    ap.add_argument("--pack", default=None, metavar="NAME",
                    help="预览图只处理这个包")
    ap.add_argument("--force", action="store_true",
                    help="预览图全部重渲染（默认只补缺图的）")
    ap.add_argument("--check", action="store_true",
                    help="只校验是否最新（CI 用；过期退码 1）")
    ap.add_argument("--write-readme", action="store_true",
                    help="一次性：往 README 里插入「仓库现状」生成块（幂等）")
    ap.add_argument("--json", action="store_true", help="机器可读输出（配合 --check）")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)

    if a.check:
        return check(json_out=a.json)

    if a.write_readme:
        insert_readme_block(quiet=a.quiet)
        return 0

    print(f"仓库根: {repo_root()}")
    if not has_packs():
        print("  `packs/` 里没有资产包（本地内容）—— 只重建 registry.json")
    if not has_compositions():
        print("  `compositions/` 里没有组合（本地内容）—— 组合相关项会是空的")

    if a.previews or a.data:
        if a.previews:
            sync_previews(a.pack, force=a.force, quiet=a.quiet)
        if a.data:
            sync_data(quiet=a.quiet)
        return 0

    stats = sync_local(quiet=a.quiet)
    print(f"本地层: {stats['modules']} 模块 / {stats['packs']} 包 / "
          f"{stats['manifests']} 清单 / {stats['compositions']} 组合"
          + ("  (+ kb)" if stats["kb"] else "")
          + ("  (+ skill 命令地图)" if stats.get("skill") else ""))
    if a.all:
        sync_previews(a.pack, force=a.force, quiet=a.quiet)
        sync_data(quiet=a.quiet)
    else:
        print("预览图与方块数据表要联网/资源缓存，用 `--all` 或 `--previews` / `--data`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
