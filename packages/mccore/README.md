# mccore — structworkshop 内核（层 0）

全仓库唯一碰「**文件格式 / 模块库 / 装配引擎 / 项目档案 / 能力注册表**」的地方。
别的包都可以没有，这个包不能没有：它把 Minecraft 建筑定义成 `voxels[y, z, x] + palette`
两个 numpy 对象，之后所有工具、渲染、质检、切块都只跟这两个对象打交道。

```python
from mccore import structure_io as S
d = S.read_structure("a.schem")      # .schem / .litematic 按扩展名分派
S.write_structure("b.schem", d["voxels"], d["palette"], d["position"], d["size"])
```

## 分层位置

| | |
|---|---|
| **上游（本包依赖谁）** | 只有第三方（`numpy`；`.litematic` 读写的 `nbt`）。**不 import 任何 structworkshop 包** |
| **下游（谁依赖本包）** | mckit · mcrender · mctools · mcqa · mcslice · mckb · mcstudio（7 个） |
| **不做** | 不渲染、不做建筑语法、不起服务、不管规范语料、不写方块更新规则（那些在 mckit / mcrender / mcstudio / mckb） |

唯一的例外写在 `stage_build.py`：分段建造的**评审步骤**在 `main()` 里懒导入
`mcqa.vision_review`（层 2）。它只影响那个 CLI，库函数不依赖 —— 见 `packages/ARCHITECTURE.md`。

## 模块

| 文件 | 作用 |
|---|---|
| `structure_io.py` | **全流程统一入口**：`read_structure()` / `write_structure()` 按扩展名分派、`sniff_format()`、`primary_path()`/`latest_path()`、`convert()` |
| `schem_io.py` | Sponge Schematic v2（`.schem`）读写 —— **存储格式** |
| `litematic_io.py` | Litematica（`.litematic`）读写 —— **兼容输入**，需要时反向导出 |
| `convert.py` | `python -m mccore.convert <文件或目录>` 格式转换 / 存量迁移，写盘后做往返逐格校验 |
| `module_lib.py` | 模块库（多包）：`.module.json`（spec）扫描/校验/`grid` 同步、`iter_modules()`、`find_module()`、CLI 子命令 |
| `library.py` | 模块库**写操作**：标签树（重命名/合并/删除）、元数据、导入、预览、删除、复制/转移、`.cache/backups/modules` 备份 |
| `pack.py` | 资产包：`pack.json` 清单 + `sha256` 校验、`catalog.md`、两段检索、`--export/--import` 分发 |
| `assemble.py` | **装配引擎**：模块放置、接口（port）兼容判定与吸附、旋转/缩放、冲突报告、`.layout.json` |
| `stage_build.py` | 分段建造驱动：按阶段叠加 + 逐检查点渲染 + 可选视觉评审 + `.stages.json` |
| `registry.py` | `registry.json`：扫描 `compositions/*/structure.json` + 资产包清单，`validate` |
| `compose.py` | **组合运行器**：`python -m mccore.compose <组合>`，提示词 frontmatter → 参数 → 调 `build.py` |
| `projects.py` | 项目层：`compositions/<id>/projects/<项目>/`（`project.json` 档案 + `out/` `plans/` `renders/`） |
| `paths.py` | 仓库路径解析（`STRUCTWORKSHOP_ROOT` 可覆盖）+ **`write_text_lf()`**（跨检出稳定的 LF 写盘）+ `child_env()`（给子进程钉住本仓库的 `packages/`）+ `pack_dirs()` / `ensure_packs_dir()` / `composition_dirs()` / `ensure_compositions_dir()`（`packs/` `compositions/` 都是可选内容，可整个缺席） |
| `bootstrap.py` | **派生产物的唯一重建入口**：`registry.json` / `packs/index.json` / `pack.json` / `catalog.md` / README 现状块 / 预览图 / 方块数据表。`--check` 逐字节校验（CI 用它拦「手改派生产物」） |
| `backup.py` | 备份策略：不备份 / 按天留存 / 保留最近 N 次（配置落 `.cache/mcstudio/settings.json`） |
| `fields.py` | 标量场 / 隐式曲面：表达式 → 体素（`mask` / `shell` / `bands`），`ast` 白名单求值，**无 eval**。引擎件：`mctools` 的 `field` 工具与数学域组合都在用 |

## 命令行

```bash
python -m mccore.compose <组合> [--list] [--list-prompts] [--list-projects]
                               [--project 名] [--prompt 名] [--set k=v ...]   # 生成一座建筑
python -m mccore.pack      list | scan | validate | catalog | search | export | import | import-modules | previews
python -m mccore.module_lib scan | list | inspect | create | embed | extract | tags | tag | meta | rm | crop
python -m mccore.projects  list <组合> | show <组合> <项目> | adopt <组合> [项目...]
python -m mccore.registry  scan | list | validate
python -m mccore.bootstrap [--all] [--previews] [--data] [--check] [--write-readme]
python -m mccore.convert   <文件或目录> [--to litematic] [--remove-source]
python -m mccore.assemble  <plan.json> --out x.schem [--auto]
python -m mccore.fields    --expr "sin(x/4)*cos(z/4) - y/6" --mode iso --out wave.schem
```

## 关键 API

| 名字 | 用途 |
|---|---|
| `structure_io.read_structure / write_structure` | 双格式读写（**除渲染外的所有包都从这里进**） |
| `structure_io.convert(p, to=…)` | 单文件格式转换（往返校验） |
| `module_lib.iter_modules / load_spec / validate_spec / sync_grid` | 模块与接口声明 |
| `library.set_tags / set_meta / find_entry / module_path` | 模块库写操作（工作台与 CLI 共用） |
| `pack.load_manifest / cmd_validate / refresh_manifest` | 资产包清单与校验 |
| `assemble.Assembler / run_plan / write_assembly` | 产物装配 |
| `compose.main` / `projects.create / record_run` | 组合与项目档案 |
| `paths.repo_root / write_text_lf` | 路径与行尾 |

## 约定 / 不变量

* **体素数组**：`voxels[y, z, x]`，`dtype=int32`，值是 palette 下标；`palette[0]` 永远是空气。
* **原点**：钉在 `(0,0,0)`，不支持负坐标（导入外部数据要先做偏移归一化）。
* **存储格式**：一律 `.schem`；`.litematic` 只作兼容输入（体积是前者的 3–4 倍）。
* **文本写盘**：git 跟踪的产物必须走 `paths.write_text_lf`，否则 Windows 上写出的 CRLF
  会让 `pack.json` / `kb` manifest 的 sha256 在新 clone 上全红（守门：`tests/lf_smoke.py`）。
* **接口（port）**：面 + 偏移 + 尺寸 + 类型 + id，装配时按类型兼容 + 体积不重叠吸附。

## 什么该进内核（判定规则）

内核只装「**通用件**」：被**通用工具 / CLI** 用到，或被**≥2 个域**用到。
只服务某一个域的脚本属于那个组合，放在 `compositions/<域>/` 里（例：数学域的
`cells.py` / `curves.py` / `redstone.py` / `defloat.py` 就在 `compositions/math-cube/`）。

按这条规则，`fields.py` 留在内核：它是 `mctools` 的 `field` 工具（29 个通用工具之一）的引擎，
不是数学域专属。规则、例外与历史迁移见 `packages/ARCHITECTURE.md` §4。

## 测试

```bash
python tests/formats_smoke.py      # .schem 存储 / .litematic 兼容、往返逐格一致
python tests/library_smoke.py      # 模块库：grid 校正 / spec 写回 / 标签
python tests/assembly_smoke.py     # 装配：接口吸附 / 画布框 / 打开闸门
python tests/projects_smoke.py     # 组合 · 提示词 · 项目三层
python tests/backup_smoke.py       # 备份策略
python tests/lf_smoke.py           # 行尾不变式（LF）
python tests/math_cells_smoke.py   # cells / fields / defloat / curves / redstone
python -m mccore.pack validate && python -m mccore.registry validate   # 全库冒烟
```
