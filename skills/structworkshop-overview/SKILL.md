---
name: structworkshop-overview
description: structworkshop（结构工坊）的总览与入口：这个仓库有哪些命令、缺数据时怎么办、从需求到交付的完整工作流、派生产物为什么不能手写、各专项 skill 怎么分工。当用户说「structworkshop」「结构工坊」「这个仓库怎么用 / 有哪些命令」「怎么开始 / 从哪下手」「生成一座建筑 / 造个东西」「跑不起来 / 报错说没有模块」「registry / catalog / 索引怎么重建」「新增了模块/资产包/组合之后要做什么」，或者你不确定该用哪条命令、该看哪个 skill 时，先读这个。
---

# structworkshop 总览 · 从这里开始

**一句话**：把 Minecraft 建筑当**参数化结构**处理 —— 共享引擎 + 可复用资产包 + 每类建筑一个组合。
一个结构 = **参数 + 生成器 + 资产包 + SKILL.md**；AI 只声明拓扑与参数，几何对齐、渲染、质检交给引擎。

## 0. 先认清：哪些东西在仓库里，哪些不在

**在**：引擎（`packages/`，9 个包）、全套冒烟测试、体素工具链、各专项 skill。

**不在**（都是**本地/可选内容**，故意不进仓库）：

| 目录 | 是什么 | 不在时会怎样 |
|---|---|---|
| `packs/` | 资产包（模块 `.schem` + 预览图） | 引用资产包的组合跑不出东西；命令会**明确告诉你缺哪个模块** |
| `compositions/` | 组合（一类建筑的定义 + 运行档案） | `compose --list` 说「一个组合都没有」；相关测试**跳过**（退出码 2） |
| `builds/` | 成品作品集归档 | 只影响需要成品做基准的浏览器审计 |
| `kb/` | 规范语料库（国标等） | `mckb` 命令说没语料 |
| `.cache/` | Minecraft 资源抽取 / 截图 / 备份 | 首次渲染联网自动拉 |

**即插即用**：把 `packs/` `compositions/` 拷到仓库根目录就能用，**不需要改任何配置**：

```bash
python -m mccore.bootstrap     # 拷进来之后跑这一条，派生产物全部重建
python -m mccore.compose --list
```

**引擎本身不依赖上面任何一个** —— 缺了它们命令照跑，只是给出「怎么把数据弄进来」的提示。

## 1. 三秒自检（装对了没有）

```bash
python -c "import mccore; print(mccore.__file__)"   # 必须是本仓库的 packages/，不是别的 checkout
python -m mccore.bootstrap --check                  # 派生产物是否最新（0 = 好）
python -m mccore.compose math-cube                  # 零依赖生成一座建筑（不需要任何资产包）
```

> **多个 checkout 的坑**：机器上若还有另一个目录装过 `pip install -e .`，它会盖住这一份
> （命令看起来正常、产物却写进另一个仓库）。不对就在**本目录**重跑 `pip install -e .`，
> 或临时 `PYTHONPATH=packages python -m ...`。
>
> **发给别人 / 换台机器跑**：那份「解压就能用」的说明在 [INSTALL.md](../../INSTALL.md) ——
> 双击 `启动工作台.bat`（macOS/Linux 是 `./启动工作台.sh`）会在**本文件夹内**建 `.venv`、
> 装依赖、起服务，不写系统 `site-packages`（删文件夹 = 卸载）。重新打一份干净发行包：
> `python tools/make_release.py --zip`（默认剔掉 `.git/ .github/ .cache/ .venv/`，并扫绝对路径残留）。

<!-- gen:cli-map:begin -->
### 命令地图（自动生成，别手改）

> 由 `python -m mccore.bootstrap` 用 `ast` 从代码里扫出来（带 `__main__` 的模块 + argparse 子命令）。
> 加/改了命令就跑一次 bootstrap；忘了写文档 `--check` 会报。

**看清仓库 / 自检**

| 命令 | 作用 |
|---|---|
| `python -m mccore.bootstrap` | 仓库**派生产物**的一键重建 / 校验（给人和 CI 用）。 |
| `python -m mccore.compose` | 组合运行器：一类建筑 = 一个文件夹（structure.json + build.py + params/ + prompts/ + SKILL.md）。 |
| `python -m mccore.projects` `list`, `show`, `adopt` | 项目层：一个组合下的「一栋具体建筑」= compositions/<id>/projects/<项目>/（档案 + 产物）。 |
| `python -m mccore.registry` `scan`, `list`, `validate` | 能力清单 registry.json：扫 compositions/*/structure.json + 资产包清单，供 AI 发现「这仓库能造什么」。 |

**资产库（模块 / 包）**

| 命令 | 作用 |
|---|---|
| `python -m mccore.module_lib` `scan`, `list`, `inspect`, `create`, `embed`, `extract`, `tags`, `tag`, `meta`, `rm`, `crop` | 模块库（多包）：模块 = 结构文件 + ModuleSpec 接口元数据，散在 packs/<包>/modules/ 下。 |
| `python -m mccore.pack` `catalog`, `search`, `export`, `import`, `import-modules`, `previews`, `list`, `scan`, `validate` | 资产包管理：pack.json 清单 + sha256 校验 + Tier-0/1 两级检索 + zip 分发。 |
| `python -m mcslice.learn` | 从已有建筑学出「风格包」（比例 / 材质 / 节奏）。 |
| `python -m mcslice.slice` | 把已有建筑切成可复用模块，并自动识别接口（门 / 楼梯 / 窗 / 通道）。 |

**装配 / 分段建造**

| 命令 | 作用 |
|---|---|
| `python -m mccore.assemble` | 模块装配引擎：按接口（port）把结构模块摆到一起，含兼容判定、吸附与冲突报告。 |
| `python -m mccore.stage_build` | 分段建造驱动：按阶段叠加 + 逐检查点渲染 + 可选视觉评审 + .stages.json。 |

**格式转换**

| 命令 | 作用 |
|---|---|
| `python -m mccore.convert` | 结构格式转换 / 存量迁移。 |

**体素工具**

| 命令 | 作用 |
|---|---|
| `python -m mccore.fields` | 标量场 / 隐式曲面：表达式 → 体素。 |
| `python -m mctools` `list`, `info`, `select`, `run`, `apply` | 命令行入口：``python -m mctools <list\|info\|select\|run\|apply>``。 |

**渲染 / 出图**

| 命令 | 作用 |
|---|---|
| `python -m mcrender` | 用真实方块模型 + 真实贴图渲染结构（.schem / .litematic）→ PNG。 |
| `python -m mcrender.assets` | Minecraft 资源抓取 + 本地缓存（渲染要的方块状态 / 模型 / 贴图都从这里来）。 |
| `python -m mcrender.block_index` | 从下载的方块映射表生成机器可读的方块索引（属性 / 默认值 / 不完整方块）。 |
| `python -m mcrender.gallery` | 生成「画廊」结构：每种不完整方块各来一份，用来回归渲染器。 |
| `python -m mcrender.legacy_voxel` | 旧版正交体素渲染器：结构 → PNG（已被 mcrender.renderer 取代，保留做 A/B）。 |
| `python -m mcrender.model` | 方块模型 → 带贴图的四边形（blockstate → 模型 → 贴图 的解析）。 |
| `python -m mcrender.sheet` | 把画廊每一列渲染成带标签的特写 → 拼成一张联络表（视觉回归用）。 |

**质检 / 评审**

| 命令 | 作用 |
|---|---|
| `python -m mcqa.preview` | ASCII 预览：没有图形环境时用文字看形体（剪影 / 俯视 / 剖面）。 |
| `python -m mcqa.qa_check` | 结构质检：palette 合法性 / 悬浮块 / 门配对（有 ERROR 退码 1）。 |
| `python -m mcqa.review_loop` | 一键评审回环：多视图渲染 → 分块送视觉模型 → 写评审报告 <out>.md。 |
| `python -m mcqa.vision_review` | 直接调 OpenAI 兼容的视觉模型做评审（一组 PNG → 一段意见），不经过任何编排层。 |
| `python -m mcqa.walk_check` | 可行走性：BFS 穿过门与楼梯，检查各房间 / 探针能不能走到。 |

**规范语料库**

| 命令 | 作用 |
|---|---|
| `python -m mckb` `fetch`, `ingest`, `audit`, `splice`, `rehash`, `denoise`, `scan`, `catalog`, `index`, `rechunk`, `search`, `read`, `readlist`, `verify`, `lint`, `export`, `stats` | 规范语料库 CLI：release / manifest / chunk 管理 + SQLite FTS5 条款检索。 |

**材质 / 颜色**

| 命令 | 作用 |
|---|---|
| `python -m mcmaterials` `catalog`, `colors`, `chart`, `match`, `profile`, `compare`, `ramp`, `families`, `check` | mcmaterials CLI - 全量方块材质目录 / 图表 / 参考匹配 / 审计。 |

**建筑语法 / 方块语义**

| 命令 | 作用 |
|---|---|
| `python -m mckit.connect` | 连接状态求解器：墙 / 栅栏 / 玻璃板 / 楼梯按邻居算出正确的 blockstate 属性。 |
| `python -m mckit.grammar` | 建筑语法套件：立面 / 楼层线 / 屋顶 / 地面细节。 |
| `python -m mckit.update` | 方块更新模拟：把 ``mckit.connect`` 的属性解算接到「体素 + 调色板」上。 |

**可视化工作台**

| 命令 | 作用 |
|---|---|
| `python -m mcstudio` `serve` | 本地可视化工作台的入口：python -m mcstudio serve [--port N] [--open]。 |

**自检小工具**

| 命令 | 作用 |
|---|---|
| `python tools/ascii_view.py` | ASCII 查看器：无图形环境下快速检查 .schem/.litematic 的形体与剖面。 |
| `python tools/export_center_cover.py` | 导出「上方压着这个方块时，墙要不要立中心柱」的方块名表。 |
| `node tools/export_entity_models.js` | 从 deepslate 的 SpecialRenderers 导出「方块实体」几何 → packages/mcrender/data/entity_models.json |
| `python tools/lf_check.py` | 行尾守卫：把仓库里的文本文件钉死在 **LF**（默认只检查，`--fix` 才动手）。 |
| `python tools/make_release.py` | 打发行包：把仓库整理成「解压就能用」的文件夹（可选再压成 zip）。 |
| `python tools/render_iso_alpha.py` | Isometric multi-view renders with a *true* transparent background. |
| `python tools/voxelize_models.py` | 把下载的 3D 模型批量倒模成 structworkshop 模块（`.schem` + `.module.json`）。 |
| `python tools/vreview.py` | 视觉评审：直接调视觉模型看图（渲染图 / 参考图），不经过任何 agent 编排层。 |
| `python tools/which_copy.py` | 这台机器上「哪一份 structworkshop 会被 python 用到」—— 一眼看清，并找出悬空的旧登记。 |
| `python tools/whois.py` | 看某个世界坐标附近有什么（无图形环境的放大镜）。 |

**其它**

| 命令 | 作用 |
|---|---|
| `python -m mccore.schem_io` | Sponge Schematic v2（.schem）读写 —— 本仓库的**存储格式**。 |
| `python -m mccore.structure_io` | Structure format dispatcher — structworkshop 全流程统一入口。 |

共 43 个可执行入口。
<!-- gen:cli-map:end -->

## 2. 工作流：从需求到交付

```
需求
 ├─ 0) 摸底：python -m mccore.registry list        # 有哪些组合 / 资产包（自动生成的能力清单）
 │        python -m mccore.compose --list          # 有哪些组合
 │        python -m mccore.pack list               # 有哪些资产包
 │
 ├─ 1) 查规范（要合规尺寸时）  → skill: minecraft-building-codes
 │        python -m mckb readlist --profile office --query "走道 净宽"
 │        python -m mckb search "避难层 设置" --limit 3
 │        python -m mckb read <release> --section "5.3 防火分区"     # 读**完整章节**再下结论
 │
 ├─ 2) 找素材  → skill: minecraft-asset-library
 │        python -m mccore.pack catalog / search / previews
 │        库里没有 → mcslice.slice 从已有建筑切块 | mcslice.learn 学风格 | 按模板造
 │
 ├─ 3) 选材配色  → skill: minecraft-material-lab
 │        python -m mcmaterials match 参考图.jpg --pool facade
 │
 ├─ 4) 写/改参数与拓扑
 │        组合：改 params/*.json 或 prompts/*.md frontmatter  → skill: <组合名>
 │        自由拼装：写 plan.json（模块 + 位置/旋转）          → skill: minecraft-modular-building
 │
 ├─ 5) 生成
 │        python -m mccore.compose <组合> [--prompt X] [--set k=v]
 │        python -m mccore.compose <组合> --project <项目名>     # 落进项目档案，可复现
 │        python -m mccore.assemble plan.json --out build.schem
 │        python -m mccore.stage_build plan.json --checkpoints ...   # 超大建筑分段建造
 │
 ├─ 6) 打磨（可选）  → skill: minecraft-voxel-tools / minecraft-studio
 │        python -m mctools list / info <tool> / apply --in x.schem --steps ...
 │
 ├─ 7) 验收（**必须**，别只看代码）
 │        python -m mcqa.qa_check  out.schem        # 结构合法性（有 ERROR 退码 1）
 │        python -m mcqa.walk_check out.schem --start x,y,z --probe a,b,c
 │        python -m mcqa.preview    out.schem --all # 没图形环境时用字符看形体
 │        python -m mcrender.cli    out.schem --views iso,front,top --scale 3
 │        python -m mcqa.review_loop out.schem      # 渲染 + 视觉模型评审回环
 │
 ├─ 8) 按评审定点修复 → 回到 4/5，循环到收敛
 │
 └─ 9) 交付
          python -m mccore.convert out.schem --to litematic   # 需要 Litematica 时
          python -m mccore.pack import-modules <目录> --pack <包>   # 沉淀成可复用模块
```

**纪律**：验收不是可选项。几何对不对、墙有没有连起来、门配没配对、比例好不好看 ——
**先跑 `qa_check` + 出图 + 看一眼**，再谈交付。视觉评审见 `minecraft-building-design` 的 SOP。

## 3. 仓库管理：派生产物**只能生成，不能手写**

仓库里有一批文件是**算出来的**：`registry.json`、`packs/index.json`、`packs/*/pack.json`、
各级 `catalog.md`、预览图、方块数据表、README 的「仓库现状」块、本 skill 的命令地图。

**手写/手改一定会在某台机器上和代码脱节**，而脱节的表现是别人 clone 下来一片红。所以：

```bash
python -m mccore.bootstrap            # 改完资产包/组合/模块 → 跑这条（纯本地，不联网）
python -m mccore.bootstrap --all      # 连预览图 + 方块数据表（首次要联网拉 mcassets）
python -m mccore.bootstrap --check    # 只校验：有人手改过就退码 1（CI 跑的就是这条）
```

`--check` 是**逐字节**比对「应该是什么」和磁盘上的内容。**别绕过去手改**；
需要改内容就改源数据（`structure.json` / `pack.json` / 模块文件 / 代码），再跑 bootstrap。

## 4. 分层与边界（改代码前必读）

- 分层规则、哪层能 import 哪层、哪些东西**不许**进引擎：`packages/ARCHITECTURE.md`（唯一口径）。
- **域专用件不进引擎**：只服务某一类建筑的算法放 `compositions/<id>/` 自己目录里
  （例：数学域的 `cells / curves / redstone / defloat` 就在 `compositions/math-cube/`）。
- 契约是可执行的：`python tests/packages_doc_smoke.py`（分层 + 文档）、`python tests/lf_smoke.py`（行尾）。
- **文本一律 LF**：仓库按字节算 sha256，写盘走 `mccore.paths.write_text_lf`。

## 5. 缺东西时的行为（别慌）

| 现象 | 含义 | 怎么办 |
|---|---|---|
| `模块不存在: X` + 「`packs/` 里没有任何资产包」 | 没拷资产包 | 把 `packs/` 拷进来 → `python -m mccore.bootstrap` |
| `compositions/ 里一个组合都没有` | 没拷组合 | 把 `compositions/` 拷进来 → bootstrap |
| 测试 **退出码 2** | **跳过**，不是失败 | 缺本地数据；补上数据就会真跑 |
| `pack validate` 报 sha256 对不上 | 文件被改过 / 行尾不是 LF | `python tools/lf_check.py --fix`；别手改 `pack.json` |
| 产物写进了**别的目录** | 机器上另一个 checkout 盖住了 | 见 §1 的「多个 checkout 的坑」 |

## 6. 专项 skill 索引（该看哪个）

| 你要干的事 | 看这个 skill |
|---|---|
| 写对方块状态（墙/栅栏/楼梯/半砖连起来）· 渲染器与资源缓存 | `minecraft-block-models` |
| 设计建筑（分阶段法 / 比例 / 立面 / 材质纪律 / 风格库 / 视觉验收 SOP） | `minecraft-building-design` |
| 复用模块拼装 · 接口吸附 · 分段建造超大建筑 | `minecraft-modular-building` |
| 找模块 / 建包 / 导包 / 校验包 / 渲染预览图 | `minecraft-asset-library` |
| 选外墙材质 · 参考图反查方块 · 做旧渐变 | `minecraft-material-lab` |
| 用工具改体素（噪声/破碎/地形化/公式体/刻字） | `minecraft-voxel-tools` |
| 浏览器里看 / 编辑 / 装配 / 批量打标 / 备份策略 | `minecraft-studio` |
| 查规范条款（防火分区/疏散/净宽/避难层…） | `minecraft-building-codes` |
| PDF 取字 / OCR 质量 / 表格碎掉怎么办 | `minecraft-ocr` |
| **某一类建筑怎么生成**（参数、提示词、工序） | `compositions/<组合>/SKILL.md`（拷进 compositions 后自动可见） |

## 7. 两条最容易犯的错

1. **只改代码不出图就交付** —— 几何错误在代码里看不出来。跑 `qa_check` + 出图 + 看一眼。
2. **手改派生产物** —— `registry.json` / `pack.json` / `catalog.md` / README 的计数，
   改了当场看不出问题，下一个人 clone 下来全红。跑 `bootstrap`。
