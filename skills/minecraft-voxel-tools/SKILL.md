---
name: minecraft-voxel-tools
description: 用 mctools（Axiom 式体素工具，29 个）对**投影 / 结构文件**做参数化改造：基本体与曲线成形的形状、公式生成的体素（field）、噪声上色、渐变、平滑、岩石化、破碎、融化、扭曲、焊接、掏空、重力、削平、坡度化、挤出、盖章散布、文字刻碑（glyph），配合掩码表达式选区与框选。支持多步流水线、JSON 报告、结构质检（qa_check）与渲染出图（mcrender）自检闭环，可 `--in-place` 原地改并自动备份。当用户说「改一下这个投影 / schem」「给结构做风化/破碎/地形化/换材质」「批量/脚本化处理投影」「把这个模块再加工一下」，或需要在不打开浏览器的情况下用工具改体素时使用。Web 面板（mcstudio 的「AXIOM 工具」）与本 skill 共用同一套引擎。
---

# mctools：让 AI 直接改投影

一句话：`python -m mctools <list|info|select|run|apply>` —— 读 `.schem`/`.litematic` → 跑工具 → 可选 QA/渲染 → 写盘。

> 面板版（人看）在 `python -m mcstudio serve --open` → 结构编辑器 →「AXIOM 工具」；
> **代理/脚本请用 CLI**（不用起浏览器，输出可 `--json` 解析）。

## 什么时候用 / 不用

| 用 | 不用 |
|---|---|
| 用户给了具体投影/模块，要**加工**它（风化、破碎、上色、地形化、加细节） | 要从零**生成**一座建筑 → 用 `mccore.compose`（组合 + 提示词） |
| 要**批量/脚本化**处理多个结构 | 只是看/管理模块 → `mcstudio` 面板或 `mccore.module_lib` |
| 要**确定性可复现**的体素编辑（同 seed 同结果） | 手工放置几十个方块 → 面板画笔更快 |
| 需要"改完自己看一眼"的闭环（`--render`） | 需要实时手感（涂抹调参）→ 面板 |

## 30 秒上手

```bash
python -m mctools list                       # 29 个工具 + 分组 + 参数名
python -m mctools info rock                  # 参数表（类型/范围/默认/枚举）
python -m mctools select --in a.schem --mask "minecraft:stone" --json   # 先问命中范围

# 单步：在 (16,8,16) 半径 8 的球里做岩石化
python -m mctools run rock --in a.schem --out rocky.schem \
    --at 16,8,16 --brush sphere:8 --param noisiness=0.4 --qa --render iso

# 多步流水线（--step 是 JSON，可重复）：先噪声上色，再岩石化，最后渲染看图
python -m mctools apply --in a.schem --out b.schem \
  --step '{"tool":"noise_painter","sel":[0,0,0,63,15,63],"params":{"scale":8,"blocks":"minecraft:stone,minecraft:andesite"}}' \
  --step '{"tool":"rock","sel":[0,0,0,63,15,63],"params":{"noisiness":0.4}}' \
  --qa --render iso,front --json
```

## 三步工作流（照着做）

1. **看**：`mctools select`（有多少格 / 包围盒在哪）+ `mctools info <工具>`（参数表）。
   不熟悉的工具**先查参数**，别猜名字。
2. **改**：`mctools run`（单步）或 `mctools apply`（多步）。第一次先用 `--dry-run` 看数字，
   小范围 `--sel` 试，确认不炸再全量。大结构先局部，避免一次跑满全画布。
3. **验**：`--qa`（结构质检，有 ERROR 退出码 1）+ `--render iso`（出 PNG）。
   然后**看图**（渲染图可以直接读）：形状是不是想要的、有没有碎屑/悬空/材质糊成噪点。
   不满意就改参数重跑（`--in-place` 会备份，安全）。

## 工具决策表（意图 → 工具）

| 想要的效果 | 工具 | 关键参数 |
|---|---|---|
| 球/圆柱/锥/环/超椭球/棱柱，空心的 | `shape` | kind, radius, height, size, hollow, thickness, mode(add/fill/replace) |
| 沿曲线铺管道/线缆/吊桥（下垂） | `path` | points, curve(line/bezier/catmull/catenary), radius, hollow, sag, use_stairs_and_slabs |
| 数学曲面 / 隐式几何（球面/环面/双曲面/波场） | `field` | expr(f(x,y,z) 公式), mode(solid/shell/bands), op, threshold, shell, blocks（多档=色带） |
| 墙面刻字（含中文）铭牌 / 门牌 / 公式墙 | `glyph` | text, at, axis(x/z/y), px(字号), scale, depth, blocks(字色), back(底色) |
| 随机材质（幕墙、岩壁、铺装、旧墙） | `noise_painter` | noise(white/simplex/fbm/worley/voronoi/metaball/splatter), scale, blocks（可带权重 `stone*3`）, octaves, warp, probability_density |
| 高度/半径渐变（幕墙、夜灯、地层） | `gradient_painter` | gradient_shape(linear/spherical/radial_xz/plane), axis, blocks, dither |
| 整片刷成一种方块 / 边缘虚化 | `painter` | chance, soft_edge, only_existing |
| 灌满房间 / 替换连片同类材质 | `floodfill` | limit |
| 去噪点、长合缝隙、磨圆棱角 | `smooth` | mode(stable/grow/melt), strength, block_ratio |
| 方块堆 → 自然岩体（地形/废墟） | `rock` | noisiness, noise_radius, meld_strength, smoothing_stddev |
| 地震/爆炸错位 | `shatter` | axis(x/y/z/3d), scale, width, use_active_block |
| 钟乳石 / 融雪 / 蜡烛滴落 | `melt` | strength, chance |
| 风化表面、碎石边 | `roughen` | ratio, min_faces, mode |
| 扭曲的塔身 / 异形 | `distort` | distance_x/y/z, scale, iterations |
| 补模块之间的缝 / 补裂缝 | `weld` | strength, threshold |
| 材质交界处互渗（过渡色带） | `blend` | spread, warp |
| 笔刷堆料 / 削料 / 揉圆 | `sculpt` | mode(add/remove/smooth), strength |
| 填满区域 | `fill` | only_air |
| 批量换材质（含通配） | `replace` | from（`oak*`）, keep_props |
| 掏空做房子/穹顶 | `hollow` | thickness, open_top |
| 加粗构件 / 瘦身 | `grow` | amount（负=腐蚀） |
| 塌方 / 碎石堆 / 堆叠 | `gravity` | — |
| 抽水 / 抽岩浆 | `drain` | — |
| 一键明暗体积感 | `autoshade` | shade_blocks（明→暗）, axis, invert |
| 场地平整（到指定世界 Y） | `flatten` | level, mode(flatten/fill/both) |
| 笔刷抬升/压低/抹平地形 | `elevation` | mode(raise/lower/flatten), amount |
| 屋面 / 坡道 / 护坡 | `slope` | axis, mode |
| 加高墙体 / 岩柱 | `extrude` | amount（负=向下） |
| 散布树/灯柱/摊位（用资产包模块） | `stamp` | blueprint(模块 id), chance, min_spacing, random_yaw |

## 选区与掩码

**选区**（三选一，多数工具都吃）：
* `--sel x0,y0,z0,x1,y1,z1` 框选（坐标系与文件一致，原点 0,0,0）
* `--all` 整张画布（破坏性大的工具慎用）
* `--at X,Y,Z`（可重复）笔刷中心；`--brush sphere:8` 定形状半径 —— 笔刷类工具没给选区时**必须**给 `--at`

**掩码表达式**（`--mask`，在选区之内再筛一层；也可用在 step JSON 里）：

```
solid / air / surface(朝空气) / sky(上方全空) / edge / inside
y<64     x>z     (y-4)%8==0        坐标算术与比较（y 是世界高度）
above(stone)  below(air)  near(oak*)  neighbor(air)  adjacent(stone)
block(stone)  stone  oak*           裸方块名 = 完全匹配，* 通配
random(0.3)                        布尔：& | ! 以及 and/or/not；分号 = 交集
```

例：`--mask "solid & y<64 & !near(air)"`（y64 以下的内部方块）、`--mask "oak*"`（所有橡木）。

## step JSON 字段

```json
{ "tool": "noise_painter",
  "params": { "scale": 8, "blocks": "minecraft:stone,minecraft:andesite*0.5" },
  "sel":  [0,0,0,63,15,63],     // 或 "all": true
  "centers": [[16,8,16]],       // 笔刷中心（可多个 = 一笔画）
  "brush": { "shape": "sphere", "radius": 8 },
  "block": "minecraft:stone",   // 工具要用的「当前方块」
  "mask": "solid & y<64",       // 可选
  "seed": 7 }
```
`--steps recipe.json` 可以放一个 step 数组（复杂管线写成文件更清晰、可复用）。

## 常用配方

```bash
# 公式体（field）：几何由公式自己定（**世界坐标**，用 `**` 求幂、`^` 不可用）
# 球壳：以 (32,32,32) 为心、半径 24、壳厚 2
python -m mctools run field --in a.schem --out sphere.schem \
    --param 'expr=length(x-32,y-32,z-32)-24' --param mode=shell --param shell=2 \
    --param blocks=minecraft:white_concrete

# 公式体（多档色带）：颜色 = 场值高低（blocks 给几档就分几级，自动抖动去色带）
python -m mctools run field --in a.schem --out wave.schem \
    --param 'expr=sin(x/6)+sin(z/6)' --param mode=bands \
    --param 'blocks=minecraft:blue_concrete,minecraft:light_blue_concrete,minecraft:white_concrete'

# 刻一块中文铭牌（axis=x 朝 X / z 朝 Z / y 平铺地面）
python -m mctools run glyph --in a.schem --out plaque.schem \
    --param text=数学域 --param at=0,4,0 --param axis=x \
    --param px=16 --param scale=2 --param blocks=minecraft:polished_blackstone

# 旧石墙：噪声换材质 → 粗糙化 → 轻平滑（材质别超过 3 档，否则糊）
python -m mctools apply --in wall.schem --out wall_old.schem \
  --step '{"tool":"noise_painter","sel":[0,0,0,47,11,47],"params":{"noise":"fbm","scale":6,"blocks":"minecraft:stone,minecraft:mossy_cobblestone*0.35,minecraft:cracked_stone_bricks*0.25"}}' \
  --step '{"tool":"roughen","sel":[0,0,0,47,11,47],"params":{"ratio":0.25,"min_faces":2}}' \
  --step '{"tool":"smooth","sel":[0,0,0,47,11,47],"params":{"mode":"stable","strength":1,"block_ratio":0.75}}' \
  --qa --render iso

# 灾难现场：沿 Y 切片错位 + 缝隙填实 + 岩石化
python -m mctools apply --in tower.schem --out tower_broken.schem \
  --step '{"tool":"shatter","sel":[0,0,0,31,80,31],"params":{"axis":"y","width":4,"scale":5,"use_active_block":true,"seed":11}}' \
  --step '{"tool":"rock","sel":[0,0,0,31,80,31],"params":{"noisiness":0.35,"smoothing_stddev":2}}' --render iso

# 地形：岩石化 + 削平 + 坡度
python -m mctools apply --in terrain.schem --out terrain2.schem \
  --step '{"tool":"rock","all":true,"params":{"noisiness":0.5,"noise_radius":10}}' \
  --step '{"tool":"flatten","sel":[0,0,0,95,40,95],"params":{"level":20,"mode":"both"}}'

# 散布树木（模块库里的模块当蓝图）
python -m mctools run stamp --in park.schem --out park2.schem --all \
  --param blueprint=tree_basic --param chance=0.5 --param min_spacing=8
```

> `field` / `glyph` 是**我们自己的扩展**（Axiom 没有），区域是「整张画布」——不需要 `--sel`；
> `field` 的几何完全由公式决定（不用 `--at`），坐标是**世界坐标**，可用
> `length(dx,dy,dz)` / `sin/cos/sqrt/exp/log/abs/min/max/clamp/floor/sign/noise/fbm` 等；
> `glyph` 用 `at` 定位置（默认 `0,0,0`）。想限制范围就放在 `apply` 流水线里加 `mask`，
> 或先 `resize` 出小画布。

## 纪律（血泪教训）

1. **工具作用于"基地层"**：带装配清单（modules）的文件里，模块占的格子会被盖回去；
   要改模块内容先固化装配（面板「固化装配」）或在模块文件本身上改。
2. **画布不会自动扩容**：贴到边界会被裁掉并提示；先 `resize`/裁好范围（面板「画布范围」），
   或让工具只作用于内部区域。
3. **确定性**：同 `seed` 同参数 → 逐格一致。要复现刚才的效果就记下 seed。
4. **材质纪律**：换材质按 `minecraft-material-lab` 的色阶走（同色温、同明度阶、≤3 档），
   噪声类工具最容易把干净模型刷成灰噪点 —— 出图后用眼睛（或 `tools/vreview.py`）复核。
5. **破坏性工具**（`shatter` / `melt` / `distort` / `gravity` / `extrude`）先在**小选区** `--dry-run`
   看数字，再全量；`--in-place` 会自动备份到 `.cache/backups/tools/<时间戳>/`。
6. **QA 必须过**：`--qa` 报 ERROR（非法 blockstate / 悬空组件 / 门配对）时退出码 1 —— 别忽略，
   要么修参数重跑，要么明确告诉用户"这是刻意悬浮"（`--allow-float` 是 qa_check 的选项）。
7. **大结构**：先 `--sel` 局部跑，确认效果再扩到全画布；`fbm`/`worley` 等噪声按世界坐标求值，
   局部与全量结果一致，可以放心分块处理。

## 退出码与输出

* `0` 通过；`1` QA 有 ERROR；`2` 参数/用法问题（信息在 stderr）。
* `--json` 报告字段：`input/output/backup/size/steps[]/changed/new_states/qa/renders`
  （每步含 `stats` 中文描述、`changed` 改动格数、`bbox` 实际改动包围盒、`notes` 注意事项）。

## 相关命令

| 命令 | 作用 |
|---|---|
| `python -m mccore.fields --expr "sin(x/4)*cos(z/4) - y/6" --mode iso --out wave.schem` | 公式体：表达式 → 体素（`field` 工具用的同一套引擎，可离线先试） |
| `python tools/voxelize_models.py models/glb --out packs/<包>/modules --pack packs/<包> --map auto` | 把下载的 `.glb` 模型批量倒模成体素模块 |
| `python -m mccore.bootstrap` | 改完资产包/模块后重建派生产物（索引/清单/目录） |

## 相关

* 引擎与差异清单：`packages/mctools/README.md`（含"哪些 Axiom 工具没复刻、为什么"）
* 面板用法：`skills/minecraft-studio/SKILL.md`
* 质检：`python -m mcqa.qa_check <file> --json`；渲染：`python -m mcrender.cli <file> --views iso,front`
* 回归：`python tests/mctools_smoke.py`（引擎+CLI）、`python tests/studio_tools_smoke.py`（HTTP/面板）
