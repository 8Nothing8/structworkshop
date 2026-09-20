---
name: minecraft-block-models
description: Minecraft 方块模型与不完整方块（半砖 slab、楼梯 stairs、墙 wall、栅栏 fence、栅栏门、玻璃板 pane、铁栏杆、门 door、活板门 trapdoor、锁链、灯笼、按钮、拉杆、末地烛等）的准确用法。用于：程序化生成/检查结构投影(.schem/.litematic)时写对方块状态（blockstate properties）；让墙/栅栏/玻璃板的连接状态、楼梯 shape、门的上半下半真正连起来；渲染 Minecraft 建筑投影；查询方块的合法属性与默认值。附带 mcrender 渲染器（真实方块模型 + 真实贴图）和 mckit.connect 连接状态求解器。
---

# Minecraft 方块模型 / 不完整方块

这个 skill 解决一个高频问题：**AI 程序化生成投影时，不完整方块（非整方块）只会写出默认状态**，
于是墙没有连接、楼梯 shape 全是 straight、玻璃板只有一根柱子、门只有下半截、锁链方向全错。
Minecraft 的**连接状态不会自动推导**——它由游戏在放置/更新时写入 blockstate，
结构文件里存的是**最终状态**，所以生成时必须自己算。

配套工具（都在 `packages/` 里，pip install -e . 后可直接 `python -m ...`）：

| 工具 | 作用 |
|---|---|
| `render_litematic.py` | 用真实方块模型 + 真实贴图渲染 `.schem` / `.litematic` → PNG（完整参数见 `packages/mcrender/README.md`） |
| `mcmodel.py` | blockstate/模型解析器：`python -m mcrender.model oak_stairs facing=east half=bottom shape=straight` |
| `mcassets.py` | 从互联网扒方块映射表 / blockstates / 模型 / 贴图并缓存 |
| `make_block_gallery.py` | 生成覆盖所有不完整方块状态的“方块画廊”投影，用于目视回归 |
| 本 skill 的 `packages/mckit/connect.py` | 根据邻接关系自动算墙/栅栏/玻璃板/楼梯/门的属性 |

## 1. 写方块状态的基本规则

结构文件(`.schem` / `.litematic`)的 palette 条目格式：

```json
{"Name": "minecraft:oak_stairs",
 "Properties": {"facing": "east", "half": "bottom", "shape": "straight", "waterlogged": "false"}}
```

- **属性名和值必须完全合法**（大小写敏感，值都是小写字符串）。非法属性/值会被解析器忽略并回退到默认状态，
  渲染出来就是“默认朝向”，通常不是你要的。
- 查合法属性与默认值：

```bash
python -m mcrender.assets --info oak_stairs polished_deepslate_wall iron_bars
# 或者读缓存里的映射表 .cache/mcassets/<version>/_blocks_summary.json
# {"oak_stairs": [ {属性: [合法值...]}, {属性: 默认值} ], ...}
```

- 属性可以省略，省略的用**默认值**（见上表第二项）。但连接类属性默认都是 `none`/`false`，
  所以**墙/栅栏/玻璃板/楼梯必须显式写**。
- 常用默认状态速查：

| 方块 | 默认状态 |
|---|---|
| `oak_slab` | `type=bottom, waterlogged=false` |
| `oak_stairs` | `facing=north, half=bottom, shape=straight, waterlogged=false` |
| `*_wall` | `up=true, 四向=none, waterlogged=false` |
| `*_fence` | 四向=false, waterlogged=false |
| `*_pane` / `iron_bars` | 四向=false, waterlogged=false |
| `*_door` | `facing=north, half=lower, hinge=left, open=false, powered=false` |
| `*_trapdoor` | `facing=north, half=bottom, open=false, powered=false, waterlogged=false` |
| `chain` | `axis=y, waterlogged=false` |
| `lantern` | `hanging=false, waterlogged=false` |
| `end_rod` | `facing=up` |
| `*_button` | `face=wall, facing=north, powered=false` |
| `lever` | `face=wall, facing=north, powered=false` |

## 2. 不完整方块分类速查（详细表见 references/incomplete-blocks.md）

### 半砖 slab
- 属性：`type=bottom|top|double`、`waterlogged`
- `double` 是整方块（两个半砖叠起来），不是“厚度翻倍”。
- 上下半砖不要靠 `type=top` + 手动 y 偏移来模拟，直接 `type=top`。

### 楼梯 stairs
- 属性：`facing`（楼梯**高的一侧朝外的方向**：`facing=east` = 台阶朝东）、
  `half=bottom|top`（下半/倒挂上半）、
  `shape=straight|inner_left|inner_right|outer_left|outer_right`、`waterlogged`
- `shape` 必须根据相邻楼梯算；否则转角处会缺角/穿模。算法见 `packages/mckit/connect.py  (python -m mckit.connect): stair_shape()`。
- `facing` 与 `shape` 的组合：`facing=east,shape=inner_left` 表示这个楼梯与它左边的楼梯形成内角。

### 墙 wall
- 属性：`up`（有没有中心柱）、`north/south/east/west=none|low|tall`、`waterlogged`
- 连接规则：
  - `tall`：邻居是**完整固体方块**或另一堵墙；
  - `low`：邻居是矮的东西（半砖、台阶、栅栏等，即“能连但不够高”）；
  - `none`：不连。
- `up` 规则（**对齐原版** `WallBlock#shouldRaisePost`）：① 上方是另一段墙 → 立柱；
  ② 四向**都不连**（孤立、墙头墙尾）→ 立柱；③ 否则看**上方方块的 shape 有没有压住中心 2×2 格**
  —— 整方块、压力板、火把、**花盆**、栅栏、半砖、告示牌…压住就立柱（判定表由
  `tools/export_center_cover.py` 从真实模型几何导出到 `packages/mckit/data/center_cover.json`）；
  ④ **例外**：north/south 或 east/west **两侧都 `tall`** 时顶面已被抬起，**不**立柱。
  旧实现只看“上方是不是整方块/墙”，于是「墙 + 花盆」会被算成 `up=false`：没有中心柱，
  而臂的内侧面在原版模型里本来就不存在 → 看上去像“高臂没渲染”。回归：`tests/blockstate_smoke.py`。
- `up=false` 且四向都是 `none` 是**没有几何**的非法状态（模型匹配为空，渲染器会报 no model）——
  不要让求解器产出它；上面的规则保证不会：孤立墙一定 `up=true`。
- 常见错误：只放一排 `*_wall` 而不写四向 → 每格都只有中心柱，看起来像一排柱子。

### 栅栏 fence
- 属性：`north/south/east/west=true|false`、`waterlogged`
- 栅栏只连**固体方块**和其他栅栏/栅栏门；不连玻璃、树叶、半砖等。
- 栅栏门 `*_fence_gate`：`facing`、`open`、`in_wall`（两侧是墙时 true）、`powered`。

### 玻璃板 pane / 铁栏杆 iron_bars
- 属性：`north/south/east/west=true|false`、`waterlogged`
- 连接固体方块、同类方块、铁栏杆（玻璃板与铁栏杆互连）。
- 不写四向 → 每格只有中心柱。

### 门 door
- 属性：`facing`、`half=upper|lower`、`hinge=left|right`、`open`、`powered`
- **门是两格高的**：`(x,y,z)` 放 `half=lower`，`(x,y+1,z)` 放 `half=upper`，
  两格的 `facing/hinge/open/powered` 必须一致。只放 lower 会渲染成半扇门。

### 活板门 trapdoor
- 属性：`facing`、`half=top|bottom`、`open`、`powered`、`waterlogged`
- 关着时是贴着所在方块上/下表面的板；`open=true` 时竖起来，`facing` 决定它贴哪一面。

### 其他薄/小方块
- `chain`：`axis=x|y|z`（锁链走向）、`waterlogged`
- `lantern` / `soul_lantern`：`hanging=true|false`（吊在天花板还是放地上）、`waterlogged`
- `end_rod`：`facing=up|down|north|south|west|east`
- `*_button`：`face=floor|wall|ceiling`、`facing`、`powered`
- `lever`：`face=floor|wall|ceiling`、`facing`、`powered`
- `grindstone`：`face=floor|wall|ceiling`、`facing`
- `*_carpet`、`*_snow`（`layers=1..8`）、`*_candle`（`candles=1..4`）、
  `sea_pickle`（`pickles=1..4`）、`turtle_egg`（`eggs=1..4`）、
  `flower_pot`、`torch`/`wall_torch`（`wall_torch` 有 `facing`）
- 植物类（`grass`、`fern`、`dandelion`、`sapling`…）：一般无属性或只有 `half`（高草）；
  渲染器按模型处理，注意它们只占 1 格、不能悬空（悬空不会渲染问题，只是不合理）。

## 3. 用 connect.py 自动算连接状态

**整张结构/一块区域**（体素 + 调色板）直接用体素级封装，别自己写循环：

```python
from mckit import update as BS
n = BS.update_volume(voxels, palette, box=(x0, y0, z0, x1, y1, z1), margin=1)  # 就地改
vox2, pal2, rep = BS.updated_copy(voxels, palette)   # 只读场景（渲染/预览）用
```

一次遍历就够（连接只看邻居**方块名**，楼梯 `shape` 只看邻居 `facing`），幂等、调色板自动去重、
`waterlogged`/`half`/`facing` 原样保留；栅栏门的 `in_wall` 不模拟。
CLI 里渲染前补一次：`python -m mcrender.cli <文件> --update-states`（只读，不改文件）。

---

逐个方块自己算时，`packages/mckit/connect.py` 提供纯函数，输入“某个位置是什么方块”的查询函数，输出该方块应写的完整属性。

```python
import sys; sys.path.insert(0, r"packages/mckit")   # 纯函数在这里（不在 skill 目录里）
from connect import wall_state, fence_state, pane_state, stair_shape, door_pair

# at(x, y, z) 返回该坐标的方块名（不含命名空间），空气返回 None
props = wall_state("polished_deepslate_wall", (x, y, z), at)
# -> {"up": "true", "north": "tall", "south": "none", "east": "low", "west": "none", "waterlogged": "false"}
```

支持的族：`*_slab`、`*_stairs`、`*_wall`、`*_fence`、`*_fence_gate`、`*_pane`、`iron_bars`、
`*_door`、`*_trapdoor`、`chain`、`lantern`。

**推荐工作流**：先用一个 `(x,y,z) -> block` 的占位网格生成建筑，再对每个不完整方块调用对应函数
把属性补齐，最后写 `.schem`(或 `.litematic`)。这样连接状态一定和游戏里一致。

## 4. 渲染检查

```bash
# 标准三视图（等轴、正视、俯视），3 像素/方块
python -m mcrender.cli 你的投影.schem --views iso,front,top --scale 3

# 看某个不完整方块的几何（不渲染，直接 dump）
python -m mcrender.model polished_deepslate_wall up=true north=tall south=tall east=low west=low

# 列出投影里每个 palette 条目的解析结果（整方块/模型/回退）
python -m mcrender.cli 你的投影.schem --list

# 一次性把贴图/模型全部下载好，之后可 --offline
python -m mcrender.cli 你的投影.schem --prefetch

# 剖切看内部（隐藏 x < 112 的方块）
python -m mcrender.cli 你的投影.schem --views right --cut x=112
```

渲染器注意：
- 首次运行会从 `mcmeta`（GitHub）下载方块映射表 + blockstates + 模型 + 贴图，缓存在
  `.cache/mcassets/<版本>/`，之后离线可用。镜像：jsDelivr / raw.githubusercontent / raw.githack。
- 版本由 `.litematic` 的 `MinecraftDataVersion`(或 `.schem` 的 `DataVersion`)自动映射（如 4903 → Minecraft 26.2）；
  也可 `--version 1.21.4` 指定。
- 水/岩浆是流体，用 `water_still`/`lava_still` 贴图当整方块渲染；`waterlogged=true` 的方块
  **不会**额外画水（模型里没有水）。
- 缺失模型/贴图的方块会退化成纯色方块（不是崩溃）；`--list` 里 `render=fallback` 就是这种。

## 5. 常见坑

1. **只写方块名不写属性** → 全部默认状态：墙变柱子、玻璃板变柱子、楼梯全朝北、门只有下半。
2. **属性名写错**（如 `direction` 而不是 `facing`、`type=lower` 而不是 `half=lower`）→ 静默回退默认值。
3. **门只放一格** → 必须 lower + upper 两格。
4. **双半砖用 `type=top` 叠两个** → 应该用 `type=double`。
5. **栅栏连到玻璃/树叶** → 游戏里不连，生成时也别写 true。
6. **墙的 `low` 和 `tall` 混了** → 邻居是半砖/台阶用 `low`，完整方块用 `tall`。
7. **楼梯转角没算 shape** → 转角处出现缝隙/穿插；用 `connect.py: stair_shape()`。
8. **跨版本方块名** → 1.21 之后的方块名/属性可能有变化；用 `--info` 查当前版本。

## 6. 相关命令

| 命令 | 作用 |
|---|---|
| `python -m mckit.grammar` | 建筑语法（窗/楼层线/竖肋/女儿墙/楼梯/中庭/广场） |
| `python -m mckit.update` | 把 `mckit.connect` 的解算接到「体素 + 调色板」上（批量重算连接状态） |
| `python -m mcrender.cli x.schem --update-states` | 渲染前补一次连接状态（只读，不改文件） |
| `python -m mcrender.block_index --all` | 重建 `data/all_blocks.json` / `incomplete_blocks.json` |
| `python -m mcrender.gallery` | 生成「每种不完整方块各一份」的画廊结构（渲染回归用） |
| `python -m mcrender.sheet 画廊.schem out.png` | 画廊 → 带标签的联络表（一眼看全部方块画对没） |
| `python -m mccore.bootstrap --data` | 重建上面那几张数据表（首次要联网拉 mcassets） |

## 7. 文件

- `references/incomplete-blocks.md` — 每一类不完整方块的完整属性表 + 正确/错误示例。
- `references/blockstates-cheatsheet.md` — blockstate/模型格式、默认 UV、旋转约定、渲染层。
- `packages/mckit/connect.py` — 连接状态求解器（可直接 import）。
- `data/incomplete_blocks.json` — 从当前版本映射表提取的不完整方块清单（属性 + 默认值 + 包围盒）。
- `data/all_blocks.json` — 该版本全部 1196 个方块的合法属性与默认状态（AI 写投影时可直接查）。

渲染器完整文档：`packages/mcrender/README.md`。
