# mctools — Axiom 式体素工具引擎

把 [Axiom](https://axiom.moulberry.com/) 6.x 的工具体系翻译成 structworkshop 自己的 Python 实现：
**参数名尽量同名同义**，语义是逆向推出来的（Axiom 闭源，见下「调研依据」），目标是
「AI / 脚本能直接调用、结果确定可复现」，而不是逐位复刻。

同一套实现有三个入口：

| 入口 | 命令 |
|---|---|
| Web 面板 | `python -m mcstudio serve --open` → 结构编辑器 →「AXIOM 工具」 |
| HTTP | `GET /api/tools` · `POST /api/structure/<sid>/tool` · `POST /api/structure/<sid>/select` |
| CLI（AI / 脚本首选） | `python -m mctools list / info <工具> / select / run <工具> / apply` |

## 分层位置

| | |
|---|---|
| **上游** | `mccore`（结构读写、选区/工作盒）· `mckit`（连接状态与方块族）· `mcqa`/`mcrender`（**只在 CLI 的 `--qa` / `--render` 自检步骤**里懒导入） |
| **下游** | `mcstudio`（Web 工具面板就是同一套引擎：`registry.catalog()` 直出表单） |
| **不做** | 不做 UI、不调 AI、不自动扩容画布（贴边会被裁并提示）—— 引擎只保证「同参数同种子 → 逐格一致」 |

## 模块

```
noise.py     7 种噪声：white / simplex / fbm / worley / voronoi / metaball / splatter
             + 域扭曲、各向异性；与分块方式无关（结果只取决于世界坐标 + seed）
masks.py     掩码表达式 DSL（对齐 Axiom mask/elements 的词汇表）
shapes.py    笔刷形状（sphere/cube/cylinder/cone/capsule/octahedron/disk/point/supersphere）
             + 19 种参数化基本体（含 torus / supersphere / prism / ellipsoid / plane）
engine.py    ToolContext（局部子体 + 世界坐标 + 调色板 + 选区）、plan_box、build_sel、run、apply
tools.py     27 个 Axiom 对齐工具的实现 + 参数表（Param / ToolSpec）
expressive.py 我们的扩展：field（公式体）/ glyph（文字铭碑）（Specs 追加到同一张表）
registry.py  catalog()：直接喂给前端渲染表单的机器可读清单
spec.py      Param / ToolSpec 数据类
__main__.py  CLI
```

## 设计要点

1. **局部子体 + 世界坐标**：工具只在一个「工作盒」的副本上动手；掩码与噪声用世界坐标求值，
   所以同一表达式/种子在任意子区域结果一致。调用方只要在动手前压好工作盒的撤销快照，
   工具就不可能改到盒子外面 —— 这是 Web 端「一次工具 = 一个撤销步」的实现基础。
2. **选区 = 框 ∩ 掩码 ∩ 笔刷**：`region` 只有三种（`own` 自带几何 / `sel` 框选 / `brush` 笔刷涂抹），
   笔刷类在没有涂抹时退化为「整块选区」（WorldEdit 式用法）。
3. **确定性**：所有随机都走 `np.random.default_rng(seed)`；噪声按世界坐标哈希。
   同参数同种子 → 逐格一致（回归测试里用 sha256 卡住）。
4. **参数表即 UI**：`registry.catalog()` 把参数类型/范围/默认值/枚举一起下发，
   前端零硬编码生成表单；加工具只需要在 `tools.SPECS` 里加一条。

## CLI（代理用这个）

```bash
python -m mctools list [--json]                    # 目录（分组 + 参数名）
python -m mctools info <工具> [--json]             # 参数表：类型/范围/默认/枚举
python -m mctools select --in a.schem --mask "solid & y<64" --json   # 探命中范围（不改数据）
python -m mctools run <工具> --in a.schem --out b.schem [工具参数] [--qa] [--render iso] [--json]
python -m mctools apply --in a.schem --out b.schem \
        --step '<JSON>' --step '<JSON>' ... \
        [--steps recipe.json] [--qa] [--render iso,front] [--render-scale 6] [--json] \
        [--in-place（自动备份到 .cache/backups/tools/<ts>/）] [--dry-run] [--stats]
```

* step JSON：`{"tool","params","sel"|"all","centers","brush","points","block","mask","seed"}`
* 退出码：`0` 通过 / `1` QA 有 ERROR / `2` 参数问题
* `--json` 报告：`input/output/backup/size/steps[]/changed/new_states/qa/renders`
* 常用简写：`--param k=v`、`--sel x0,y0,z0,x1,y1,z1`、`--all`、`--at x,y,z`（可多次）、
  `--brush sphere:8`、`--points "x,y,z;x,y,z"`、`--block minecraft:stone`、`--mask "..."`、`--seed N`

代理视角的完整用法（决策表 / 掩码速查 / 常用配方 / 纪律）：
`skills/minecraft-voxel-tools/SKILL.md`。

## 29 个工具

| 组 | 工具 |
|---|---|
| 形状与路径 | `shape` `path` `field`（公式体，我们的扩展） |
| 绘制与上色 | `noise_painter` `gradient_painter` `painter` `floodfill` `glyph`（文字铭碑，我们的扩展） |
| 形变与雕刻 | `smooth` `rock` `shatter` `melt` `roughen` `distort` `weld` `blend` `sculpt` |
| 体块运算 | `fill` `replace` `hollow` `grow` `gravity` `drain` `autoshade` |
| 地形与重力 | `elevation` `flatten` `slope` `extrude` `stamp` |

`python -m mctools list` 看全量；`python -m mctools info <工具>` 看参数表。

## 调研依据（不是猜的）

从本机 `Axiom-6.0.1-for-MC26.2.jar` 静态提取（只读 class 名/字符串常量，**不反编译、不复制代码/资源**）：

| Axiom 包 | 类数 | 我们用到的信息 |
|---|---|---|
| `com/moulberry/axiom/tools/` | 163 | 34 个工具的族与参数名（`tool_presets` 的枚举/默认值） |
| `com/moulberry/axiom/noise/` | 11 | White/Simplex/Worley/VoronoiEdges/Metaball/Splatter/FBM/SimplexDomainWarp |
| `com/moulberry/axiom/operations/` | 28 | Fill/Hollow/Expand/FillNearest/Replace/Distort/SimulateGravity/Drain/GenerateColourField/Autoshade/Rebuild |
| `com/moulberry/axiom/brush_shapes/` | 19 | Sphere/Cube/Cylinder/Cone/Capsule/Octahedron/FlatDisk/SinglePoint + Simple/Separate/Rotated/Rounded 变体 |
| `com/moulberry/axiom/mask/elements/` | 23 | All/Any/Both/Either/Not、BlockCondition、BlockAbove/Below/Adjacent/Neighbor/Near、CoordMask、Solid、Surface、CanSeeSky、Biome、Angle、Offset、Selected、Constant、Lua |
| 算法线索 | — | `smooth`：`ClosestBlockMap` + `AsyncToolPatherMinSDF`（≈ 最近方块传播 + 有符号距离场）；`rock`/`sculpt`：`GaussianBlurTable`；`distort`：`NoiseVectorField`；`gradient_painter`：`BezierOperator` |

**差异与限制（诚实清单）**

* 语义是推出来的：参数名对齐，行为**近似**，不保证和游戏内逐位一致。
* 不复刻：`ruler`（改成 CLI `--stats` 输出尺寸）、`annotation`（游戏内标注）、
  `biome_painter` / `clentaminator`（需要生物群系与世界生成数据）、`script_brush`（我们本身就是脚本层）、
  `freehand/lasso_select`（用掩码/框选表达）。`text`（字体栅格化）、`modelling`（metaball 建模）、
  `slope`（Axiom 是切坡，我们做的是表面线性坡）、`use_stairs_and_slabs`（收边台阶：`facing` 按路径切线、
  `shape` 交给 `mckit.connect.stair_shape` 按相邻台阶算，`half` 仍固定 `bottom`）为简化版。
* 工具作用于**基地层**，且不会自动扩容画布（贴边会被裁并提示）。
  工作台里如果那一带被**模块实例**盖着，工具改的格子会**就地记到那个模块实例上**
  （响应里的 ``punched`` 计数）——模块与编辑不互斥，改动跟着模块移动/旋转。

## 回归

```bash
python tests/mctools_smoke.py        # 噪声/掩码/形状/29 个工具/确定性/CLI/文档一致性（95 项）
python tests/studio_tools_smoke.py   # HTTP 端到端：目录 → 执行 → 撤销 → 选区 → 保存（39 项）
node tests/studio_ui_audit.js [chrome] http://127.0.0.1:8617 [截图目录]  # 面板 UI（20 项）
```
