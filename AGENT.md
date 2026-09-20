# AGENT.md — structworkshop 架构详解

> 这份文档写给**要在仓库里干活的 AI 代理**（也写给第一次读代码的人）。
> 它讲的是「东西在哪、为什么这么分、改哪里、有哪些硬约束」，不是使用手册。
> 使用手册看 [README.md](README.md)；AI 的**入口**是 `skills/structworkshop-overview/SKILL.md`。

> **开工前（第一次在新工具里打开本仓库）**：`skills/` 是全部 skill 的**唯一来源**
> （Agent Skills 标准：`SKILL.md` + frontmatter）。若你所在的工具没有自动加载它们，
> 先按它的规矩把 `skills/` 注册到你自己的**项目 skill 目录**（pi → `.pi/skills/`，
> Claude Code → `.claude/skills/`，其他照它的文档），再读 `skills/structworkshop-overview/SKILL.md` 开工。

---

## 0. 30 秒版

structworkshop 把 Minecraft 结构当成**参数化数据**处理：

```
结构 = 参数 + 生成器 + 资产包(模块) + 规范(可选)
              │
              ├─ packages/   引擎：IO · 模块库 · 装配 · 渲染 · 质检 · 工具 · 工作台 · 语料
              ├─ compositions/<组合>/   一类建筑的做法（提示词 + build.py + 预设）
              ├─ packs/<资产包>/        可复用模块（.schem + ModuleSpec 接口）
              └─ kb/                    建筑规范语料（完整文档 + FTS5 条款检索）
```

一条命令跑通全链（不需要任何本地内容）：

```bash
pip install -e ".[runtime]"
python -m mccore.compose math-cube          # 生成 128³「数学域」
python -m mcqa.qa_check <产物.schem>         # 质检
python -m mcrender.cli <产物.schem> --views iso   # 出图
python -m mcstudio serve --open              # 浏览器里看/改/拼
```

**三条最容易犯的错**（详见 §14）：
1. 体素数组是 `(y, z, x)`，不是 `(x, y, z)`；
2. `packs/` `compositions/` `kb/` 是**可选内容**，引擎不许依赖它们存在（缺了要跳过/空态，不许崩）；
3. 派生产物（`registry.json`、`packs/index.json`、`pack.json`、`catalog.md`、README 现状块、
   skill 命令地图）**只能由 `python -m mccore.bootstrap` 生成**，手改会被 `--check` 拦下。

---

## 1. 分层与依赖

规则：**依赖只能自上而下**；同层之间原则上不互相 import；只有 **CLI 入口**（有 `__main__.py`
或 `main()`）才允许向上/同层懒导入去「编排」。这套规则是**可执行的**：`tests/packages_doc_smoke.py`
会读 `packages/ARCHITECTURE.md` 的例外表逐条核对，表里多一条少一条都红。

```
层 3 应用       mcstudio        mcmaterials      mckb
               （浏览器工作台） （材质实验室）    （规范语料库）
                     │              │              │
层 2 工具/验证  mctools   mcqa   mcslice
               （体素工具）（质检） （逆向/切块）
                     │        │        │
层 1 语法/视觉  mckit            mcrender
               （建筑语法·方块语义）（真实模型渲染）
                     │              │
层 0 内核       mccore ◄───────────┴──────────────┘
               （格式 IO · 模块库 · 装配 · 项目 · 注册表）
```

| 包 | 依赖 | `.py` / 行数 | 一句话 |
|---|---|---|---|
| `mccore` | **无** | 17 / 6260 | 内核：结构 IO、模块库、装配、组合运行器、项目层、注册表、备份、路径 |
| `mckit` | mccore | 7 / 1813 | 建筑语法（窗/楼层线/竖肋/女儿墙/楼梯/中庭）+ 方块更新模拟 + 办公套件 |
| `mcrender` | mccore（+mckit 仅 CLI） | 10 / 3284 | 真实方块模型 + 贴图的离线渲染（numba 光栅器） |
| `mctools` | mccore · mckit | 10 / 3546 | Axiom 式体素工具引擎（29 个工具 + 掩码 DSL），Web/HTTP/CLI 共用 |
| `mcqa` | mccore（+mcrender 出图） | 6 / 1098 | 质检、可行走性、ASCII 预览、视觉评审回环 |
| `mcslice` | mccore · mcrender | 3 / 632 | 逆向：建筑 → 模块（切块）/ 建筑 → 风格包（学习） |
| `mcstudio` | mccore · mcrender · mctools · mckit | 8 / 4278 + 9822 JS | 浏览器工作台：模块库 + 3D 编辑器 + 装配 + 工具面板 + 设置 |
| `mcmaterials` | mcrender | 10 / 1693 | 方块材质目录（颜色/透明度/质感）+ 参考图反查 + 色彩审计 |
| `mckb` | mccore.paths | 8 / 1661 | 规范语料库：release / chunk / FTS5(BM25) 条款检索 + OCR 流水线 |

**三条已登记的例外**（在 `packages/ARCHITECTURE.md` §2，新增必须登记）：
`mccore.stage_build → mcqa.vision_review`（分段建造的评审步骤）、
`mcrender/cli → mckit.update`（`--update-states` 只读补偿）、
`mctools/__main__ → mcqa`（`--qa` 自检）。

---

## 2. 数据模型（先记住这 7 个）

### 2.1 结构（structure dict）—— `.schem` / `.litematic` 的统一读模型

`mccore.structure_io.read_structure(path)` 返回：

| 键 | 含义 |
|---|---|
| `voxels` | `np.uint16` 数组，**形状 `(y, z, x)`**，索引 0 约定为空气 |
| `palette` | `list[dict]`：`{"Name": "minecraft:oak_stairs", "Properties": {"facing": "north", ...}}` |
| `size` | `(sx, sy, sz)` ← **注意与数组轴序相反** |
| `position` | 结构在世界里的原点（元数据，装配时会用） |
| `metadata` | 原样保留的 NBT 复合（装配清单 `StructworkshopModules` 就藏在这） |
| `data_version` / `version` / `sub_version` | 版本串（决定状态名解析） |
| `block_entities` | 方块实体 NBT 列表（箱子内容 / 告示牌文字…），读写往返原样保留 |
| `root` / `region_name` | 读取端附带的来源信息 |

存储格式是 **`.schem`（Sponge Schematic v2）**；`.litematic` 只作**兼容读**。
`.schem` 通常只有 `.litematic` 的 1/4–1/3（后者位打包破坏 gzip 字节对齐）。
转换：`python -m mccore.convert <文件或目录> [--to litematic] [--remove-source]`（写盘后逐格往返校验）。

### 2.2 调色板与状态串

- 调色板是**去重表**，状态用 dict 表示；`mccore.schem_io.parse_state` / `state_str` 是唯一的字符串↔dict 转换口径。
- 连接状态（墙/栅栏/玻璃板/楼梯）由 `mckit.update` 按邻居重算，见 §8。
- 工作台给前端的每状态语义（`layer/tint/liquid/special/has_elements/entity_geometry/fallback_texture/ao_occluder/waterlogged`）
  出自 `mcstudio/blocks.py` + `mcstudio/entity_assets.py`（与 `mcrender` 同源）。

### 2.3 ModuleSpec —— 模块 + 接口

模块 = **一个结构文件 + 一份 JSON spec**（sidecar `<stem>.module.json`，或内嵌在 `.litematic`
Metadata 的 `ModuleSpec` 里；两边不一致以 sidecar 为准）。

```jsonc
{
  "id": "corridor_x", "category": "corridor", "version": 1,
  "description": "…", "tags": ["corridor"],
  "grid": {"size": [sx, sy, sz], "position": [0, 0, 0]},
  "axis": "x", "flip": true,
  "ports": [
    {"id": "w", "type": "passage", "face": "west",
     "origin": [y0, u0], "size": [h, w], "tags": []},
    {"id": "vent", "type": "vent", "face": "west",
     "shape": "circle", "origin": [cy, cu], "size": [d, d]}
  ],
  "notes": ""
}
```

**接口（port）的关键约定**——它是「连接面」，不是「洞」：

* **匹配只看三件事**：面贴合（`west` ↔ `east`） + 面内外接矩形重叠 + 类型兼容。
  接口里**实心还是空心完全无所谓**（柱子分段对接、风道、窗洞共用一套）。
* 坐标：竖直面（west/east/north/south）`origin=[y0, u0]`（u 沿面水平方向），`size=[高, 宽]`；
  上下面（up/down）`origin=[x0, z0]`，`size=[宽(x), 深(z)]`。
* `shape`：`rect`（缺省，origin 是角）或 `circle`（origin 是**圆心**，size=[直径, 直径]）；
  圆形只是几何意图，引擎匹配仍走**外接矩形**（`module_lib.port_bbox` / 前端 `portBoxOf` 同一套）。
* 内置类型：`passage|door|stair_up|stair_down|redstone_in|redstone_out|fluid_in|fluid_out|
  item_in|item_out|power_in|power_out|shaft|anchor|light|vent|window|interface`；
  **类型可自写**（小写字母开头、`[a-z0-9_-]`、≤40 字符），兼容判定见 §5。
* 校验入口：`module_lib.validate_spec()`（只校验几何/格式，不看体素）。

### 2.4 资产包（pack）与索引

```
packs/<包>/
├── pack.json          清单：id/name/version/author/license/mc_versions/dependencies/
│                      engine_requires/provides{modules,styles,generator}/previews{}/files{sha256}
├── modules/<分类>/<id>.schem (+ <id>.module.json)
├── previews/<id>.png  模块缩略图（mcrender 出）
├── styles/*.json      风格包（mcslice.learn 产出）
└── catalog.md         Tier-0 目录（生成）
packs/index.json       全包合并索引（生成）
packs/catalog.md       总目录（生成）
```

- `pack.json` 的 `files` 是**逐文件 sha256**，`python -m mccore.pack validate` 会核对
  → 所以任何会改动 pack 内文件字节的操作（包括行尾！）都必须重跑 `bootstrap`。
- 检索分两级：`catalog.md`（人/AI 读的目录）+ `index.json`（机器读的 id/尺寸/接口/标签/预览）。

### 2.5 组合（composition）与项目（project）

```
compositions/<组合>/                 ← 「一类建筑怎么做」
├── structure.json                   身份卡：id/name/description/entry/skill/params/prompts/packs/tags/
│                                    outputs/runner/style_params/entry_points
├── SKILL.md                         AI 的方法索引（pi 通过 .pi/settings.json 发现）
├── build.py                         参数化生成器（唯一入口）
├── params/*.json                    参数预设
├── prompts/*.md                     提示词（设计意图 + frontmatter params）
├── plans/*.json                     装配/分段计划
└── projects/<项目>/                 ← 「一栋具体建筑」
    ├── project.json                 schema/composition/name/prompt/preset/params/note/created/updated/runs/artifacts
    ├── out/                         .schem + .layout.json（gitignore）
    ├── plans/  renders/             该项目的计划与出图
```

CLI：`python -m mccore.compose <组合> [--prompt P] [--project N] [--set k=v] [--list-prompts] [--list-projects]`、
`python -m mccore.projects list|show|adopt`。生成走**子进程**（`build.py`），环境由
`mccore.paths.child_env()` 处理（把本仓库 `packages/` 钉在 `PYTHONPATH` 最前面，见 §14）。

### 2.6 装配清单（provenance）—— layout.json + Metadata

工作台/装配引擎的「成品由哪些实例组成」：

```jsonc
// <name>.layout.json（边车；.schem 还会把 ≤60KB 的清单内嵌进 Metadata.StructworkshopModules）
{"generator": "mcstudio", "version": 1, "name": "…", "size": [x,y,z], "frame": [x,y,z],
 "instances": [{"id": "corridor_x", "pack": "modern-arch", "pos": [x,y,z],
                "rot": 0, "rotx": 0, "rotz": 0, "size": [sx,sy,sz], "blocks": 123,
                "edits": {"loc_y,loc_z,loc_x": paletteIndex}}]}
```

* `edits` = **就地修改**（punch-through）：键是模块**局部坐标**，值是调色板下标（0 = 擦成空气）。
* 伪包名 **`@self`**：整幅投影（`builds/…`、上传的文件）包装成的单个实例。它的 `pos/rot`
  在清单里**一律写 0**（内容就是文件里的体素，位移早在保存时画进去了），重开时
  `restore_placements()` 把读进来的体素重新包一层 → 仍是可拖实例且体素不会被推两遍。
* 读取优先级：边车 → Metadata（新键 `StructworkshopModules`，改名前的 `McForgeModules` 兜底）。

### 2.7 规范语料库（release）

```
kb/releases/<release>/├── full.md       完整文本（OCR/取字后按页拼）
                     ├── chunks.jsonl   条款级切块（供 BM25 检索）
                     ├── manifest.json  页码/字符数/sha256/来源 URL/完整性
                     └── source.pdf     （gitignore：版权 + 体积）
kb/index.sqlite       FTS5 全文索引（gitignore）
kb/registry.json      语料清单（生成；路径一律仓库相对）
```

检索面：`python -m mckb catalog | readlist --profile <office|residential|general> --query … |
search "…" --limit N | read <release> --section "5.3 防火分区"`。

---

## 3. 生成流水线（需求 → 交付）

> 面向使用者的同一张图在 [README.md](README.md) 的「工作流」一节（带命令与通过标准）；
> 这一节是从**代码**角度看这条链由谁实现。

```
需求
 └─ 摸底      mccore.registry list / mccore.compose --list / mccore.pack list
 └─ 查规范    mckb readlist → read（计划里写「标准号 + 条款号」，别凭记忆）
 └─ 找素材    已有模块：pack search / module_lib list
             没有：mcslice.slice（从已有建筑切块 + 自动识别接口）
                    mcslice.learn（学风格包）
                    手写 packs/<包>/modules/...（配 module-spec.md）
 └─ 选材质    mcmaterials（目录 / 反查参考图 / 色彩审计 compare）
 └─ 写参数    compositions/<组合>/params/*.json 或 prompts/*.md（frontmatter params）
 └─ 生成      mccore.compose <组合> --project <项目> --prompt <提示词> --set k=v
 └─ 打磨      mctools（29 个工具，CLI/HTTP/Web 同一实现）
 └─ 验收      mcqa.qa_check（ERROR 退码 1） → walk_check → mcrender.cli → mcqa.review_loop
 └─ 修复循环  按评审定点改 → 回「生成」或「打磨」
 └─ 交付      builds/<作品>/ 归档；python -m mccore.bootstrap 重建派生产物
```

关键点：**AI 只声明拓扑与参数**（谁挨着谁、几层、什么风格），几何对齐、连接状态、渲染与质检交给引擎。

---

## 4. 包详解

### 4.1 `mccore`（内核，层 0，无内部依赖）

| 模块 | 职责 |
|---|---|
| `paths.py` | 仓库定位（`repo_root()` 从 `pyproject.toml` 往上找，`STRUCTWORKSHOP_ROOT` 可覆盖）、`packs_dir/compositions_dir`（**可能不存在**）、`pack_dirs()/composition_dirs()`（唯一该用来列目录的入口）、`ensure_*_dir()`、`write_text_lf()`、`child_env()` |
| `structure_io.py` / `schem_io.py` / `litematic_io.py` | 统一读模型 + 两种格式的读写（§2.1） |
| `module_lib.py` | 模块库：扫包、sidecar spec 读写、`validate_spec`、`port_bbox`、索引重建（`build_index`/`index_text`/`scan`）、`missing_module_hint()` |
| `library.py` | 更高层的库操作：抽屉/工作台用的模块 CRUD、标签规范化、预览渲染（`render_previews`）、批量打标 |
| `pack.py` | 资产包：`pack.json` 清单与 sha256、`create/import/import-modules/export/search/catalog/previews/validate` |
| `assemble.py` | 装配引擎（§5）：`Assembler`、`types_compatible`、`transform_port`、`rot_axis90`、计划文件 |
| `stage_build.py` | 分段建造：阶段叠加 + 逐检查点渲染 + 可选视觉评审 + `.stages.json` |
| `compose.py` | 组合运行器：找 `structure.json`、合并参数（预设/`--set`/提示词 frontmatter）、起子进程跑 `build.py`、写运行记录 |
| `projects.py` | 项目层：`project.json` 档案、产物清单、`record_run`（`portable_argv()` 保证记录可移植） |
| `registry.py` | 能力清单 `registry.json`（组合 + 资产包 + 语料），供 AI 发现「这仓库能造什么」 |
| `bootstrap.py` | **派生产物的一键重建/校验**（§13） |
| `convert.py` / `backup.py` | 格式迁移 / 备份策略（off / 最近 N 次 / 按天留存） |
| `fields.py` | 表达式 → 标量场 → 体素（`mctools field` 工具也用它） |

### 4.2 `mckit`（建筑语法 + 方块语义，层 1）

| 模块 | 职责 |
|---|---|
| `grammar.py` | 建筑语法积木：窗带 / 楼层线 / 竖肋 / 女儿墙 / 楼梯 / 中庭 / 广场 |
| `office.py` | 办公布局套件（标准层房间/走道生成） |
| `connect.py` | 连接状态求解的几何部分（谁和谁算连上） |
| `update.py` | `update_volume()` / `updated_copy()`：按邻居重算不完整方块状态（纯函数、幂等、只改连接属性） |
| `data/center_cover.json` | 墙 `up` 的判定表（「上方方块是否压住中心 2×2」），由 `tools/export_center_cover.py` 从模型几何导出 |
| `meshvox.py` / `voxbrush.py` | 体素网格/笔刷的共用小工具 |

### 4.3 `mcrender`（离线渲染，层 1）

`assets.py`（mcmeta 资源抓取 + 本地缓存 `.cache/mcassets/`）→ `model.py`（blockstate → 模型 → 带贴图四边形）
→ `renderer.py`（numba 软件光栅器，正交/透视、AO、cutout/translucent 分层、SSAA、泛光）。
`block_index.py`（机器可读方块索引）、`gallery.py` + `sheet.py`（视觉回归联络表）、
`legacy_voxel.py`（旧正交渲染器，A/B 用）、`data/entity_models.json`（方块实体几何表，见 §7）。
对齐原版的细节清单见 §7 —— **改动渲染器前先读那一节**。

### 4.4 `mctools`（体素工具引擎，层 2）

29 个工具 / 5 组（`python -m mctools list` 是权威清单）：

| 组 | 工具 |
|---|---|
| 形状与路径 | `shape`（19 种基本体，可空心）· `path`（直线/贝塞尔/Catmull-Rom/悬链线，可成管）· `field`（公式体 `f(x,y,z)`：占位/等值壳/分级色） |
| 绘制与上色 | `noise_painter`（7 种噪声，按**分位映射**分配方块，可给权重 `stone*3`）· `gradient_painter` · `painter` · `floodfill` · `glyph`（文字铭碑，含中文） |
| 形变与雕刻 | `smooth` · `rock` · `shatter` · `melt` · `roughen` · `distort` · `weld` · `blend` · `sculpt` |
| 体块运算 | `fill` · `replace`（支持 `oak*` 通配 + 保留属性）· `hollow` · `grow`(膨胀/腐蚀) · `gravity` · `drain` · `autoshade` |
| 地形与重力 | `elevation` · `flatten` · `slope` · `extrude` · `stamp`（把资产包模块按概率铺到表面） |

- 工具**声明式**：`registry.py` 里的参数表（名字/范围/默认/中文标签）**同时是 UI 的契约**（前端零硬编码）。
- **掩码 DSL**（`masks.py`，对齐 Axiom 的 `mask/elements`）：布尔 `& | !`、坐标算术比较
  （`y<64`、`(y-4)%8==0`、`x>z`）、空间谓词 `solid/air/surface/sky/edge/inside`、
  邻域 `above/below/near/neighbor/adjacent(B)`、方块名与通配 `oak*`、`random(0.3)`。
  坐标是**世界坐标**，所以在任意子区域求值一致。
- **确定性**：同 `seed` 同结果（回归 `tests/mctools_smoke.py`）。
- 三个入口同一实现：Web 面板（`POST /api/structure/{sid}/tool`）、HTTP、`python -m mctools run|apply`。

### 4.5 `mcqa` / `mcslice` / `mcmaterials`（层 2/3）

* `mcqa.qa_check`（调色板合法性/悬浮块/门配对，有 ERROR 退码 1）、`walk_check`（BFS 走门与楼梯）、
  `preview`（ASCII 剪影/俯视/剖面，无图形环境时看形体）、`vision_review`（直连 OpenAI 兼容视觉模型）、
  `review_loop`（渲染 → 分块送模型 → 写 `<out>.md`）。
* `mcslice.slice`（按网格/楼层切块 + 自动识别门/楼梯/窗/通道接口，可 `--register` 进资产包）、
  `mcslice.learn`（从建筑提取风格包：比例/材质/节奏）。
* `mcmaterials`：全量方块目录（颜色量化/透明度/质感）+ 家族索引 + 图表 + 参考图反查 +
  `mcmaterials compare`（**交付前必须与参考图比色**，ΔL 收敛是硬指标）。

### 4.6 `mcstudio`（工作台，层 3）—— 见 §6

### 4.7 `mckb`（规范语料库，层 3）—— 见 §12

---

## 5. 装配引擎（`mccore/assemble.py`）

```python
from mccore.assemble import Assembler
a = Assembler(region=(64, 32, 64))          # 或 Assembler(region, overlap=True) 允许堆叠
a.place("corridor_x", block, pos=(0, 0, 0), rot=0, flip=False)
a.report()                                   # 冲突 / 已用接口 /
```

* **兼容判定**（`types_compatible`）：同名可接；`*_in` ↔ `*_out`；`*_up` ↔ `*_down`；
  `interface` 与任何类型都能接；自写类型走前两条规则。
* **几何判定**：两个接口所在面必须相对贴合（`west`↔`east` …），且面内**外接矩形重叠**。
* **变换**：`transform_port()` 随实例的 `rot/rotx/rotz`（90° 步进）与 `flip` 一起变换
  （圆形还是圆形）；旋转由 `rot_axis90()`/`rot_dims()` 实现，变的是坐标轴顺序而不是逐格矩阵乘。
* **吸附**（工作台）：`Session.snap_position()` 在半径 2.5 格内找「让本模块某个接口与对侧接口对齐」的位置；找不到就自由摆放（不硬拽）。
* **堆叠**：默认严格碰撞校验；`overlap=True`（或计划里 `"overlap": true`、CLI `--overlap`）时后放的盖过先放的。
* **分段建造**（`stage_build.py`）：按阶段叠加 + 每阶段跑质检/渲染/可选视觉评审，产出 `.stages.json`。

---

## 6. 工作台运行时（mcstudio）

### 6.1 服务端：会话模型

`mcstudio/session.py` 的 `StructureSession` 是编辑器的心脏：

```
base       np.uint16 (y,z,x)   —— 基地层（画笔/工具直接写这里）
placements list[dict]          —— 装配实例（各自带 voxels/palette/edits/pos/rot）
voxels     np.uint16 (y,z,x)   —— **合成结果** = base 按放置顺序叠上 placements
frame      (fx,fy,fz)          —— 「画布框」：保存时按它裁剪（不裁会话数据）
undo/redo  ≤200 步的快照（box 区域的 vox + base + palette + placements 快照）
```

* `_recomposite(box)`：只在 box 范围内重算合成（不是全图）。
* `_punch_module_edits(box, before, after)`：把「落在实例覆盖范围内的基地层改动」记成该实例的
  `edits`（局部坐标 → 调色板下标）——这就是**模块与编辑工具不互斥**的实现（不用先「固化装配」）。
* `payload()`：给前端的会话状态（size/frame/outside/palette/position/format/data_version/
  dirty/can_undo/blocks/cells/placements/block_entities…）。
* 保存：`save()` 按画布框裁剪写 `.schem`（或 `.litematic`）+ 边车 `layout.json` + Metadata；
  同时把同名 `*.module.json` 的 `grid.size` 校正到实际尺寸并回传 `spec_warnings`。

### 6.2 编辑操作与历史

`POST /api/structure/{sid}/ops` 接受一批操作：

```jsonc
{"ops": [{"type": "set", "x": 1, "y": 2, "z": 3, "state": "minecraft:stone"},
         {"type": "fill"|"erase", "box": [x0,y0,z0,x1,y1,z1], "state": "…"},
         {"type": "replace", "from": "minecraft:stone", "to": "minecraft:oak_planks", "box": [ … ]},
         {"type": "update", "box": [ … ]},                      // 只重算连接状态
         {"type": "region", "mode": "move"|"copy", "box": [ … ], "delta": [dx,dy,dz]}],
 "update": true}                                                 // 顺带重算连接状态
```

响应：`bbox` + `region`(base64 压缩的回传片) + `palette` + `size`/`frame`/`outside` +
`dirty`/`can_undo` + `placements` + `punched`(就地改在实例上的格数) + `updated`(连接状态改动格数) + `warnings`。
前端拿到 `region` 只补打脏的那一片（不是整图重下）。

撤销/重做按 box 快照；`GET /history` + `POST /history/jump {index}` 支持「回到某一步」（操作日志窗口）。

### 6.3 连接状态重算（与 `mckit.update` 的接口）

**关键设计**：重算读的是**合成结果**（`_update_states()`），不是基地层 —— 因为导入进来的东西
可能在实例里（打开即整幅实例），只算基地层会出现「放了邻居，墙不连」。
差异写回各自那一层：实例盖着的格子记成实例 `edits`（跟着实例走），其余写回 `base`。
`apply_ops(update=True)` 与 `apply_tool(update=True)` 共用它。

### 6.4 打开即实例（`@self`）

* 入口：左上角「打开结构…」/「上传打开」/模块库「在编辑器中打开」→ 都走 `Editor.openPath()`。
* 资产包模块 → `POST /modules/detach {id, pack, ports}`（拿 spec 的接口）；
  **其它任何投影** → `POST /modules/detach {}`（伪包名 `@self`，无接口）。
* 打开这一步带 `markDirty=false, record=false`：不标「未保存」、不进撤销栈。
* 「要不要自动切到移动」按**换工具序号** `E.toolSeq` 判断（打开是异步的，用户可能已经切走）。

### 6.5 叠加层与交互约定

`Editor.moduleOverlayOn()` = 当前工具是 `move`/`copy` 时才画：模块脚框、中心小方块手柄、
三箭头 gizmo、2D 脚框。切到 `place/erase/replace/select/port` 就**全部收起并清选中**
（导入进来的结构就当普通方块用）；拾取本来就只在这两个工具里生效。
要再搬：切回移动/复制，或点右栏「成品里的模块」那一行（会自动切回「移动」）。

### 6.6 HTTP API 一览（完整表在 `packages/mcstudio/README.md`）

| 组 | 端点 |
|---|---|
| 状态/设置 | `GET /api/state` · `GET/POST /api/settings` · `POST /api/settings/clear` |
| 模块库 | `GET /api/packs` · `/api/tags`（rename/merge/delete）· `/api/modules`（counts/{mid}/batch/transfer/preview）· `PATCH/DELETE /api/modules/{mid}` · `POST /api/import`（+`/upload`） |
| 结构会话 | `POST /api/structure/open`（+`?filename=` 上传）· `/new` · `GET /api/structure/{sid}`（state/voxels/layer/stats/blockentities/download）· `POST /api/structure/{sid}/ops` · `/tool` · `/select` · `/undo` · `/redo` · `GET /history` · `POST /history/jump` · `/resize` · `/save` · `/save-as` · `/save-as-module` · `/close` |
| 装配 | `GET/POST /api/structure/{sid}/modules` · `/modules/update` · `/modules/remove` · `/modules/bake` · `/modules/detach` |
| 资源 | `GET /api/blocks`（+`/picker`）· `POST /api/palette-info` · `/blockdefs` · `GET /api/asset` · `/api/files` · `GET /api/tools` · `GET /api/previews` + `POST /api/previews/render` · `GET/POST /api/jobs/{jid}` |

安全边界：只绑 `127.0.0.1`；可读写路径限定 `packs/ builds/ compositions/ tests/fixtures/ dist/ .cache/mcstudio`。

### 6.7 前端（`packages/mcstudio/web/`）

| 文件 | 职责 |
|---|---|
| `index.html` | 三个视图（模块库 / 结构编辑器 / 设置）的骨架 + 各面板 DOM |
| `app.js` | 模块库 UI、抽屉、弹窗、toast、**`App.searchSelect()`**（全站「从长串里挑一个」的统一组件）、属性表解析（`parseProps/propsText`）、`App.api/post/patch` |
| `editor.js` | 编辑器状态机（`E`）：工具、绘制、框选/移动/复制、装配实例、接口编辑、属性面板、操作日志、叠加层显隐 |
| `viewer3d.js` | 视口：相机、拾取（DDA 体素射线 `raycast`/`firstInBox`）、覆盖层（画布框 / 选区手柄 / 模块手柄 / gizmo / 坐标轴标签 / 接口框 / 光标）、地面网格（吃深度） |
| `renderer3d.js` | **自研 mesher + GL 绘制**：按调色板状态缓存烘焙、只扫脏块的 16³ 格子、增量重建、透明 pass 从远到近 |
| `vendor/deepslate.umd.cjs` | **只当模型烘焙库**（blockstates/模型/贴图解析），不参与绘制 |
| `settings.js` | 设置页（备份策略 / 打开上限） |

自研 mesher 相对「每块重烘焙」的旧路径：首次建网格 6–14×，涂一格 208ms → **1ms 量级**
（数据与回归：`node tests/editor_mesh_bench.js`）。

---

## 7. 渲染管线（mcrender）与「和游戏对齐」的细节

**流程**：`assets.py`（按需从 mcmeta 镜像下载方块状态/模型/贴图到 `.cache/mcassets/`）
→ `model.py`（blockstate → variant/multipart → elements 盒 → 带 uv 的四边形）
→ `renderer.py`（numba 光栅：深度缓冲、最近邻采样、cutout 丢弃、translucent 从远到近、AO、SSAA）。

**必须对齐原版的细节**（两边共用同一套表，改一处要同步另一处）：

| 主题 | 规则 |
|---|---|
| 面明暗 | 上 1.0 / 下 0.5 / 南北 0.8 / 东西 0.6 |
| AO | 4 档（0.45/0.62/0.8/1.0），带四边形翻转消接缝 |
| 剔除 | 按**面自己的 `cullface`**（不是法线）—— 栅栏柱顶、灯笼端盖这类「没写 cullface」的面永远可见 |
| 流体 | 源 8/9 高、流动按 `level`、角点取相邻最大、侧面用 `*_flow`、液面锚定；`waterlogged=true` 补一层水面；水染群系水色 `#3F76E4`（岩浆不染） |
| 贴图 | mipmap 自己生成、alpha 取 **max**（避免树叶/铁栅栏变纱窗） |
| 方块实体 | 箱子/旗帜/头颅/潜影盒/装饰罐/钟/导管/铜傀儡在资源包里**没有方块模型** → 用 `tools/export_entity_models.js` 从 deepslate SpecialRenderers 导出的 `data/entity_models.json`（173 状态 / 81 贴图） |
| 墙的 `up` | `mckit/data/center_cover.json`（由模型几何导出）：上方方块压住中心 2×2 → 立柱 |

**性能参考**（`packages/mcrender/README.md` 有表）：225×320×225 高塔 ≈ 387k 块 / 670k 四边形，
热缓存 6s / 2 视图；512³ 体量 ≈ 951k 块 / 2.72M 四边形，17s / 1 视图。

---

## 8. 方块语义与建筑语法（mckit）

* **`update_volume(voxels, palette, box, margin=1, index_of=…)`**：就地重算 `box ± margin` 内
  不完整方块的连接状态。家族：`wall`（四向 + `up`）、`fence`、`pane`（铁栏杆/玻璃板互连）、
  `stairs`（只算 `shape`，不动 `facing`）。**纯函数、幂等**；`updated_copy()` 是不改原数组的版本。
* 返回 `{changed, cells, families, palette_added}`，调用方负责把新状态加进调色板（`index_of` 注入）。
* 只读用法：`python -m mcrender.cli <文件> --update-states`（渲染前补连接，**不改文件**）。
* 工作台里：选中放置/擦除/替换时出现「方块更新」开关（默认开）；「重算连接」按钮可对框选/整幅补一次。
* 建筑语法 `grammar.py` / `office.py`：给生成器用的高层积木（窗带、楼层线、竖肋、女儿墙、楼梯、中庭、广场、办公标准层）。

---

## 9. 质检、视觉评审与材质审计

```bash
python -m mcqa.qa_check build.schem                  # 结构质检（ERROR → 退码 1）
python -m mcqa.walk_check build.schem --start x,y,z --probe x,y,z   # 可行走性 BFS
python -m mcqa.preview build.schem --views --top --slices           # 文字看形体（可 --base 叠加对比）
python -m mcrender.cli build.schem --views iso,front --scale 3      # 出图
python -m mcqa.review_loop build.schem                              # 渲染 → 视觉模型 → <out>.md
python -m mcmaterials compare 成品.png 参考.jpg --crop-a … --crop-b …  # 色彩审计（ΔL）
```

* **视觉回环是硬要求**：配色/形体必须「看得见」。`tools/vreview.py` 直连视觉 API 做并排评审。
* 每条工具命令都有 `--qa` / `--render` 自检开关（`mctools apply --qa --render iso`），
  改了结构之后**别只看退出码**。

---

## 10. 逆向：切块与风格学习（mcslice）

```bash
python -m mcslice.slice builds/<作品>.schem --by grid --floor 1 --out-dir /tmp/sliced \
    --report --register --pack modern-arch --category f1 --plan /tmp/plan.json
python -m mcslice.learn builds/<作品>.schem --out packs/<包>/styles/extracted.json --preview /tmp/palette.png
```

切块会**自动识别接口**（门/楼梯/窗/通道）→ 产出 `.module.json`；`--register` 直接进资产包
（记得随后 `python -m mccore.bootstrap` 重建索引与清单）。

---

## 11. 扩展食谱

| 想加什么 | 改哪里 | 之后必须跑 |
|---|---|---|
| 一个模块 | `packs/<包>/modules/<分类>/<id>.schem` + `<id>.module.json`（格式见 `skills/minecraft-modular-building/references/module-spec.md`） | `python -m mccore.bootstrap`（索引/清单/catalog）+ `pack validate` |
| 一个资产包 | `python -m mccore.pack create <名字>` → `import-modules` | 同上 |
| 一类建筑（组合） | 新建 `compositions/<id>/{structure.json, SKILL.md, build.py, params/, prompts/}` | `bootstrap` + `registry validate` + 生成一次跑 `qa_check` |
| 一个提示词 | `compositions/<id>/prompts/<名字>.md`（frontmatter 写 params） | `compose <id> --prompt <名字>` 跑一次 |
| 一个工具 | `packages/mctools/tools.py`（参数表 + 实现）→ `registry.py` | `python tests/mctools_smoke.py`；参数表即 UI 契约 |
| 一条方块更新规则 | `packages/mckit/update.py`（+ 需要表就放 `mckit/data/`，用 `tools/export_*` 生成） | `python tests/blockstate_smoke.py` |
| 一个 HTTP 端点 | `packages/mcstudio/api.py`（`r(...)` 注册 + handler） | `python tests/studio_smoke.py` |
| 一个命令 | 模块加 `main()`/`__main__.py` | `python -m mccore.bootstrap`（命令地图）+ `python tests/skills_smoke.py`（**必须写进某个 skill**） |
| 一部规范 | `python -m mckb fetch --id …` → `ingest` → `audit` → `scan && index && verify` | `python tests/mckb_smoke.py` |
| 改渲染样子 | `packages/mcrender/renderer.py`（离线）与 `web/renderer3d.js`（交互）**两边** | `python tests/fluid_smoke.py` · `entity_models_smoke.py` · `node tests/renderer_ab.js` |

---

## 12. 规范语料库（mckb）细节

* 流水线：`fetch`（住建部公告附件）→ `ingest`（PDF → 逐页取字 → `full.md` + `chunks.jsonl` + `manifest.json`）
  → `audit`（逐页体检：哪些页值得重扫）→ `scan`/`index`（FTS5）→ `search`/`readlist`/`read`。
* 表格页碎掉、条款号误识 → 外部 OCR（MinerU / 视觉模型）只补坏页：`mckb splice <release> --pages 4,10 --from fix.md`；
  整本换文本才用 `rehash`（手改 `full.md` 后必须跑，行尾统一 LF）。
* 依赖在 extra 里：`pip install -e ".[kb]"`（PyMuPDF + rapidocr）。**不要**把 mckb 依赖并进主路径。
* 检索纪律：计划里要写「标准号 + 条款号」，别凭记忆；`readlist --profile office` 是推荐的起手式。

---

## 13. 契约与守卫（都是可执行的）

| 契约 | 守卫 | 说明 |
|---|---|---|
| **派生产物必须由程序算** | `python -m mccore.bootstrap --check`（CI 里跑） | 覆盖 13 项：`packs/index.json`、各 `pack.json`/`catalog.md`、总目录、`registry.json`、README 现状块、总览 skill 命令地图、`kb/registry.json`…（需要联网的预览图/数据表不在校验范围） |
| **每个命令都要有 skill 讲** | `python tests/skills_smoke.py` | 45 个入口 + 47 个子命令全覆盖；skill frontmatter 合法；路径无坏链；命令地图与代码同步；`.pi/settings.json` 的 skills 路径存在 |
| **行尾全 LF** | `python tests/lf_smoke.py` + `python tools/lf_check.py [--fix]` | `pack.json`/`kb manifest` 按字节算 sha256，CRLF 会让新 clone 全红；写入点只走 `mccore.paths.write_text_lf` |
| **依赖只能向下** | `python tests/packages_doc_smoke.py` | 读 `packages/ARCHITECTURE.md` 的例外表核对；顺带检查每个包 README 是否列全 `.py`、组合 `entry_points` 是否悬空 |
| **缺可选内容要跳过** | 各冒烟里的 `tests/_support.py`（退码 **2**） | 纯代码 checkout 上 `packs/ builds/ compositions/ kb/` 都不在；**退码 2 = 跳过，不是失败** |
| **CI** | `.github/workflows/ci.yml` | 3 OS × py3.10/3.12；先跑「无本地内容也能干活」的六条命令，再跑 LF/派生/skill/全量冒烟，最后校验 wheel 里带上了运行时数据 |

---

## 14. 约定与陷阱

1. **轴序 `(y, z, x)`**：`voxels[y, z, x]`；`size` 是 `(sx, sy, sz)`。写 `voxels[x, y, z]` 是最常见的 bug。
2. **可选内容**：`packs/`、`compositions/`、`builds/`、`kb/` 都可能不存在。列目录用
   `pack_dirs()` / `composition_dirs()`（已判 `is_dir()`），别直接 `iterdir()`；写路径才 `ensure_*_dir()`。
3. **本机路径不进仓库**：记录类字段（`project.json` 的 `runs[].argv`、`kb/registry.json` 的 `kb_dir`）
   一律仓库相对/中性写法（`mccore.projects.portable_argv`、`mckb.maintain._rel`）。绝对路径换台机器就废。
4. **改文件字节要重建派生产物**：动过 `packs/**` 里任何文件（包括改行尾）都要 `bootstrap`。
5. **子进程只继承环境变量、不继承 `sys.path`**：从别的目录/别的 checkout 调用时用
   `mccore.paths.child_env()`，否则可能 import 到另一份 editable 安装，产物写进别人的仓库。
6. **异步导入别抢工具**：`wrapAsInstance` 用 `E.toolSeq` 判断用户是否已经换过工具（历史 bug：
   打开模块回来把「接口」工具抢成「移动」，两点定接口直接失效）。
7. **接口不是洞**：匹配只看面/矩形/类型；别在匹配里加「必须空心」之类的臆测规则。
8. **`edits` 是局部坐标**：就地修改跟实例的 `rot/rotx/rotz` 一起变换（`_rot_edits`/`_rot_index`）。
9. **报错要指向解法**：缺资产包时提示怎么建包，缺组合时提示「这是可选内容 / 先跑 math-cube」
   （见 `module_lib.missing_module_hint`、`compose.py`）。
10. **`--check` 与手改**：`registry.json`/`packs/index.json` 这类文件冲突了不要手工 merge，重跑生成器。

---

## 15. 术语表

| 词 | 含义 |
|---|---|
| **组合（composition）** | 一类建筑的做法：`compositions/<id>/`（提示词 + 生成器 + 预设 + SKILL.md） |
| **项目（project）** | 一栋具体建筑：`compositions/<id>/projects/<名>/`（档案 + 产物 + 运行记录，可复现） |
| **资产包（pack）** | 可复用素材集合：`packs/<包>/`（模块 + 预览图 + 风格包 + 生成器） |
| **模块（module）** | 一个结构文件 + ModuleSpec（带接口）；存 `.schem` |
| **规格（spec / ModuleSpec）** | 模块的 sidecar JSON：尺寸、朝向、接口、标签、描述 |
| **接口（port）** | 连接面：面 + 面内矩形/圆 + 类型；匹配只按几何 + 类型 |
| **实例（placement）** | 装配进画布的一个模块摆放（pos/rot/size/edits）；伪包名 `@self` = 整幅投影 |
| **就地修改（punch-through）** | 编辑工具压在被实例盖住的格子时，改动记到实例 `edits` 上（跟着实例走） |
| **固化装配（bake）** | 把实例的体素烙进基地层、清空清单（从此不再是实例） |
| **画布框（frame）** | 保存时裁剪的范围（`size` 是数据范围；框外内容仍留在会话里） |
| **投影** | 泛指一个结构文件（`.schem`/`.litematic`）——从游戏/Litematica 导出的东西 |
| **规范 release** | `kb/releases/<release>/`：一份完整标准文档的取字结果 + 条款切块 + manifest |

---

## 16. 文档地图

| 想了解 | 看 |
|---|---|
| 怎么用（安装 / 快速开始 / 目录 / 契约） | [README.md](README.md) |
| **架构与内部实现**（本文） | `AGENT.md` |
| 包分层与依赖规则 | [`packages/ARCHITECTURE.md`](packages/ARCHITECTURE.md) |
| 某个包的职责/上游/不做/CLI/API | `packages/<包>/README.md` |
| AI 的入口（仓库有什么/命令地图/端到端工作流） | `skills/structworkshop-overview/SKILL.md` |
| 具体领域的做法（10 个 skill 都在这里） | `skills/minecraft-*/SKILL.md` |
| 一类建筑怎么做 | `compositions/<id>/SKILL.md` + `prompts/*.md` |
| 模块 spec 格式细节 | `skills/minecraft-modular-building/references/module-spec.md` |
| 工作台 UI 手册 | `packages/mcstudio/README.md` + `README.md` 的工作台一节 |
| 怎么贡献 / 提 PR | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 环境变量 | [.env.example](.env.example) |
