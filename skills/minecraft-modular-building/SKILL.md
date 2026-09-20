---
name: minecraft-modular-building
description: Minecraft 模块化建造系统: 素材库(模块 = .schem + ModuleSpec 接口元数据，兼容 .litematic 读取)、接口式装配引擎(显式摆放或连接图自动吸附)、分段建造驱动器(逐阶段导出累计/增量投影,检查点渲染+视觉评审)。当用户要复用建筑模块(房间/走廊/楼梯/路灯/红石机器)、把模块拼成完整建筑、或分段搭建超大规模建筑时使用。
---

# Minecraft 模块化建造 Skill

把一个建筑拆成**模块**(素材)再**装配**(拼图),每段建造都**渲染给视觉模型看**。
这是「AI 能盖出有完整内部结构的复杂建筑」的关键:AI 只做两件事 ——
**声明拓扑**(谁连谁)和**评审渲染图**,几何对齐、旋转、碰撞全部交给引擎。

## 三个 skill 的分工

| Skill | 管什么 |
|---|---|
| `minecraft-block-models` | 方块状态写对(连接/朝向/属性) |
| `minecraft-building-design` | 建筑审美 + 设计原则 + 质量门禁 |
| `minecraft-building-codes` | **规范依据**:完整规范语料(`kb/`)+ 条款检索(`python -m mckb`) |
| **本 skill** | 模块化:定义/存储/检索/装配/分段建造 |

> 需要用**标签管理素材**、3D 看模块、直接改投影/`.schem` 时，用 `minecraft-studio` skill：
> `python -m mcstudio serve --open`（本地网页工作台，见 `packages/mcstudio/`）。

## 核心概念

**模块 = `.schem` + `ModuleSpec`**(sidecar `<stem>.module.json`,可 embed 进 Metadata)。
ModuleSpec 声明**接口**:走廊出入口、房间门、楼梯上下层口(带高度)、红石使能端/输出端、
风口/风道(`vent`,2×2 通风管那种)、窗(`window`)、竖井、
水道/漏斗进出口 —— 位置、尺寸、类型、朝向,全部在文件层面定义。

完整格式见 `references/module-spec.md`。

## 标准工作流

```
1. 需求分解   建筑 → 模块清单。先查素材库,缺的模块用生成器自己造
             (写法: numpy 填格 + litematic_io.write_litematic + 写 spec,
              参考 packs/modern-arch/module_gen.py)
2. 查规范     接口/通道/净宽等尺寸必须来自规范原文:
             python -m mckb readlist --profile office → 读 kb/releases/**/full.md 完整章节
             → 在计划里注明标准号+条款号(见 minecraft-building-codes)
3. 写装配计划 compositions/<结构>/plans/<name>.json:
             - 显式摆放: {"ref","module","pos","rot"}
             - 或连接图: ["A:w","C:e"](AI 只声明拓扑,引擎自动吸附)
4. 装配       python -m mccore.assemble plan.json --auto --out build.schem
             看布局 ASCII 图 + 连接校验报告,不对就改计划重跑
5. 分段建造   超大建筑用 compositions/<结构>/plans/<name>_staged.json:
             python -m mccore.stage_build plan_staged.json --out build
             每阶段导出累计+增量投影,检查点自动渲染给视觉模型评审
6. 质检交付   qa_check.py + 全视角渲染 + 评审,全过后交付
```

## 命令速查

| 命令 | 作用 |
|---|---|
| `python -m mccore.module_lib scan` | 重建素材库索引 |
| `python -m mccore.module_lib list [--category rooms] [--tag stone] [--query 走廊]` | 检索模块 |
| `python -m mccore.module_lib inspect room_basic` | 看 spec + 方块统计 |
| `python -m mccore.module_lib create 新模块 --size 5x4x3 --category rooms` | 建空模块 |
| `python -m mccore.module_lib embed room_basic` | 把 spec 写进 litematic Metadata |
| `python -m mccore.assemble plan.json --auto --out x.schem` | 装配(连接图自动吸附) |
| `python -m mccore.stage_build plan_staged.json --out x [--no-ai]` | 分段建造+检查点评审 |

## 装配计划(plan)格式

```json
{
  "name": "small_house",
  "auto": true,
  "modules": [
    {"ref": "A", "module": "room_basic"},
    {"ref": "C", "module": "corridor_x"}
  ],
  "place": [{"ref": "A", "pos": [0, 0, 0], "rot": 0}],
  "connections": [["A:w", "C:e"]]
}
```

- `ref` 是本次装配里的代号(ASCII 图里显示),`module` 是素材库 id
- `place` 显式摆放(装饰件、无接口模块必须用这个);`connections` 声明接口连接
- 连接格式 `"ref:接口id"`;自动模式下引擎按 4 旋转 × 吸附公式摆好并查碰撞
- 结果: `<out>.layout.json`(摆放/连接/冲突报告)+ 终端 ASCII 布局图

## 分段建造(staged plan)格式

```json
{
  "name": "tower",
  "stages": [
    {"id": "floor0", "note": "一层", "modules": [...], "place": [...],
     "connections": [...]},
    {"id": "floor1", "note": "二层", "modules": [...], "connections": [...]}
  ],
  "checkpoints": ["floor0"],
  "render": {"views": "iso,front,top", "scale": 3}
}
```

- 每个 stage 只放自己的模块;引擎把全部 stage 累积进一个世界网格(增量装配)
- 每阶段导出 `x_s01_floor0.schem`(累计)+ `_diff.schem`(本阶段新增)
- `checkpoints` 里的阶段(以及最后一段)自动渲染 `render.views` 并调视觉模型评审,
  报告写到 `x_s01_floor0_review.md`;`--checkpoint-all` 每段都评
- 评审后按意见修改对应模块/计划,重跑 —— 这就是「堆一段、看一段、修一段」

## 调试速查

| 现象 | 原因与解法 |
|---|---|
| `模块不存在: xxx` | 先 `module_lib.py scan`,或名字打错(`inspect` 查) |
| 连接「面不贴合」 | 接口朝向错:门的开口方向 = face 方向;相邻模块的接口必须**面对面**(A:south ↔ B:north) |
| 自动装配选了 180° 旋转 | 说明 rot 0 会碰撞或接口方向不匹配,看 `.layout.json` 的 pos/rot |
| 「端点未放置」 | 该节点没有连到已放置图;给一个 `place` 或补一条连接 |
| 渲染图里门是墙 | **接口开口必须在体素里真的挖洞**(spec 声明了但生成器没挖) |
| 楼梯口对不上 | 接口 origin 的 y 必须按楼层算:下层口 y=1..2,上层口 y=5..6(层高 4) |
| 红石/水道不工作 | 接口类型要成对(redstone_out↔redstone_in),见 module-spec 兼容表 |

## 文件索引

### 接口怎么写（现在有 UI 了）

接口 = `{"id", "type", "face": west|east|north|south|up|down, "origin": [a1, a2],
"size": [s1, s2], "tags": []}`，**轴向约定与 `mccore.assemble.port_anchor3d` 绑死**：

| face | 面内第一轴 a1/s1 | 面内第二轴 a2/s2 | 接口格子所在层 |
|---|---|---|---|
| east / west | y（竖直） | z（水平） | x = sx−1 / x = 0 |
| north / south | y（竖直） | x（水平） | z = 0 / z = sz−1 |
| up / down | x | z | y = sy−1 / y = 0 |

**接口 = 接口（连接面）**：匹配只看「面对面 + 面内矩形重叠 + 类型兼容」，
**里面实心还是空心完全无所谓**——所以「一根柱子切成几段要接起来」就是在对接面上各标一个
同尺寸的接口（类型用 `interface`：万能，跟任何类型都能接）；风道/通道口、窗洞也是同一套东西，
只是类型不同（`vent` / `passage` / `window`）。实心接口区里全是方块照样能吸（实测见
`tests/assembly_smoke.py` 的 13/14）。

类型兼容表（`assemble.types_compatible`）：`interface` ↔ 任意；通道家族
（passage/door/stair_up/stair_down）互通；同名同类（passage/shaft/anchor）互通；
其余按 `COMPAT` 成对（redstone_in↔out、fluid_in↔out、item_in↔out、power_in↔out）。

两种作者化方式：

1. **可视化工作台**（推荐）：`python -m mcstudio serve --open` → 打开模块 →
   左侧「模块属性 → 接口」：① 用**「接口」工具**在同一个面上点两个角；② 直接填数字；
   ③ 点**「整面」**（整个面当一个接口，柱子对接用）/ **「从框选」**（把 3D 框选投到面上）/
   **「扫空洞」**（面上最大的非实心连通块，风道/窗洞不用手点）。
   类型里 `interface` = 通用接口（与任何类型都能接）。保存即写回
   `.module.json`；模块库抽屉里也能改（点行编辑、✕ 删）。
2. **脚本/CLI**：`PATCH /api/modules/<id> {"ports":[…]}`（服务端校验面/尺寸/类型），
   或直接改 sidecar JSON 后再 `python -m mccore.pack scan` 重扫索引。

⚠ 两点最容易踩：① **spec 里声明了接口，体素里也要真的挖洞**（`vent` 接口尤其——不然装配
上去是堵死的）；② 接口格子必须在**边界层**（x=0 / x=sx−1 / y=0 / y=sy−1 / z=0 / z=sz−1），
服务端越界会报“竖直范围/水平范围超出”。回归：`tests/assembly_smoke.py`（接口轴向 + 旋转）、
`tests/studio_smoke.py`（PATCH 校验 + 改尺寸后 grid 同步）、UI 审计（接口编辑器 5 项）。

- `references/module-spec.md` —— ModuleSpec 完整格式、接口类型/方向/兼容表、写模块的规则
- `references/staging-guide.md` —— 分段策略(按层/按翼/按功能)、检查点设置、评审 prompt、红石分段
- `kb/` + `python -m mckb` —— 规范语料库(完整文档 release + FTS5 条款检索),见 `skills/minecraft-building-codes`
- ``mccore.module_lib` / `mccore.assemble` / `mccore.stage_build` —— 引擎
- `packs/modern-arch/module_gen.py` —— 模块生成器示例(照它写自己的模块)
- `packs/<pack>/modules/` —— 资产包模块;`compositions/<结构>/plans/` —— 装配/分段计划

## 示例(可直接跑)

```bash
python -m mccore.assemble tests/fixtures/plans/small_house.json --auto --out small_house.schem
python -m mccore.stage_build tests/fixtures/plans/small_house_staged.json --out staged_house
python -m mcrender.cli small_house.schem --views iso,front,top --scale 3
```
