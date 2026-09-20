# structworkshop 包分层与整合决策

这份文档回答两个问题：**9 个包之间允许怎么依赖**，以及**哪些包/模块该合并、哪些必须分开**。
规则不是口号 —— `tests/packages_doc_smoke.py` 会按这里的契约做检查（每个包有 README、
README 列出全部模块、CLI 有文档），改结构时先改这份文档。

## 1. 分层

依赖只能**自上而下**（上层 import 下层，反之不行）。同层之间原则上不互相 import。

```
层 3 应用       mcstudio        mcmaterials      mckb
               （浏览器工作台） （材质实验室）    （规范语料库）
                     │              │              │
层 2 工具/验证  mctools   mcqa   mcslice           │
               （体素工具）（质检） （逆向/切块）     │
                     │        │        │           │
层 1 语法/视觉  mckit            mcrender          │
               （建筑语法·方块语义）（真实模型渲染）  │
                     │              │              │
层 0 内核       mccore ◄───────────┴──────────────┘
               （格式 IO · 模块库 · 装配 · 项目 · 注册表）
```

实测依赖矩阵（`import` 计数，含函数内懒导入）：

| 包 | 依赖 | 被谁依赖（入度） | Python 行数 | 角色 |
|---|---|---|---|---|
| `mccore` | **无 structworkshop 包** | 7 | 5754 | 内核 |
| `mckit` | mccore | 3 | 2309 | 建筑语法 + 方块语义 |
| `mcrender` | mccore（+ mckit 仅 CLI） | 4 | 3268 | 真实模型渲染 |
| `mctools` | mccore · mckit（+ mcqa/mcrender 仅 CLI） | 1 | 3540 | 体素工具引擎 |
| `mcqa` | mccore（+ mcrender 出图） | 2 | 929 | 质检与评审 |
| `mcslice` | mccore · mcrender | **0** | 632 | 逆向（建筑 → 模块/风格） |
| `mcstudio` | mccore · mcrender · mctools · mckit | **0** | 3979（+8474 JS） | 应用 |
| `mcmaterials` | mcrender | **0** | 1683 | 应用 |
| `mckb` | mccore.paths | **0** | 1650 | 独立域（规范语料） |

## 2. 跨层与同层横边的例外（全部登记在案，不许新增）

规则：**库代码只能向下依赖**；只有 **CLI 入口**（定义 `main()` 或 `__main__.py`）才允许
向上/同层懒导入去「编排」。`tests/packages_doc_smoke.py` 按这张表检查，表里多一条少一条都会红。

| # | 类型 | 边 | 位置 | 判断 |
|---|---|---|---|---|
| 1 | **真反向边**（层 0 → 层 2） | `mccore.stage_build` → `mcqa.vision_review` | `packages/mccore/stage_build.py:154`（`main()` 里的检查点分支） | 分段建造的**评审步骤**要调视觉模型。库函数不依赖。**保留**：换成注入会把「渲染 + 评审 + 报告」的编排拆到两处，得不偿失 |
| 2 | 同层横边（1 → 1） | `mcrender/cli.py` → `mckit.update` | 只为 `--update-states`（渲染前按邻居补连接，**只读**，不改文件） | **保留**：渲染器为「投影没写对状态」提供的补偿开关，属 CLI 层便利 |
| 3 | 同层横边（2 → 2） | `mctools/__main__.py` → `mcqa` | 只为 `--qa` 自检 | **保留**：CLI 是组合根 |

（`mctools/__main__.py` → `mcrender` 是 2 → 1 的**向下**依赖，不算例外。）

## 3. 跨包私有 API（已清理）

| 旧写法 | 现状 |
|---|---|
| `mcmaterials` 从 `mcrender.assets` import `_norm_ref` / `_collect_model_refs` | `mcrender.assets` 提供公开别名 `norm_ref` / `collect_model_refs`，`mcmaterials` 已改用公开名 |
| `mckb/extract.py` 自带一份 `write_text_lf`（与 `mccore.paths` 重复） | 收编为一份：`mckb.extract` 再导出 `mccore.paths.write_text_lf` |

规则：**下划线名不出包**。别的包要用，就在原包提升为公开 API（并在 README 里写出来）。

## 4. 「专门件」的判定规则（已执行）

**规则**：内核只装「**通用件**」—— 被**通用工具 / CLI** 用到，或被**≥2 个域**用到。
只服务一个域的脚本属于那个域，放在 `compositions/<域>/` 里。

先量了「谁在生产代码里真的 import 它」（AST 扫全仓库：packages / compositions / tools / packs 算生产，tests 不算）：

| 模块 | 生产消费者 | 判定 | 处置 |
|---|---|---|---|
| `mccore/cells.py` | **0**（只有冒烟测试） | 数学域（四维胞体投影） | → `compositions/math-cube/cells.py` |
| `mccore/defloat.py` | `compositions/math-cube/build.py` | 只服务这一栋楼（悬浮装饰加固） | → `compositions/math-cube/defloat.py` |
| `mckit/curves.py` | `compositions/math-cube/*` | 参数曲线（数学域） | → `compositions/math-cube/curves.py` |
| `mckit/redstone.py` | `compositions/math-cube/*` | 红石构件（数学域展品用） | → `compositions/math-cube/redstone.py` |
| `mccore/fields.py` | `mctools/expressive.py`（**通用** `field` 工具）+ 数学域 | 通用件：「表达式 → 场 → 体素」是一项**能被任何建筑用到的能力** | **留内核** |

搬迁代价（已付）：

* 导入改成组合本地（`import curves as C`）—— `kit.py` / `exhibits.py` 像 `build.py` 一样
  自己把自己目录放上 `sys.path`，所以谁先导入都能用；
* `tests/math_{cells,exhibits}_smoke.py` 加上组合目录；
* `structure.json` 的 `entry_points`、组合 SKILL.md、包 README、根 README 同步。

**反向规则同样重要**：一个组合件要是有了**第二个消费者**或**通用工具**依赖，
就该提升为引擎件（提升时补 README/测试）。当前没有跨组合 import。

## 5. 物理合并候选（要拍板）

| 候选 | 收益 | 代价 / 风险 | 建议 |
|---|---|---|---|
| `mcslice`（632 行，入度 0）→ 并入 `mckit` | 少一个包；「切块/风格学习」与「建筑语法」同属「结构语义」 | 改 `python -m mcslice.slice/learn` 两个已文档化的 CLI；README/skill/registry 全要跟改 | **可合**，等你说一声 |
| `mcmaterials` → 并入 `mcrender` | 贴图/资源代码不再跨包 | 渲染器要背上 PIL 图表、家族编排、参考图匹配等「选材」逻辑；入度 0 的独立应用合并收益有限 | 不建议 |
| `mckb` → 并入任何包 | —— | 它是**完全独立**的域（建筑规范语料），重依赖 `PyMuPDF`/`rapidocr`（可选 extra `kb`）；并入内核会把这些依赖带进主路径 | **绝对不要合**，它是「外挂知识库」 |
| `mcstudio` → 并入任何包 | —— | 3979 行 Python + 8474 行 JS 的应用层，且是唯一有「安全边界」的包（只绑 127.0.0.1 + 路径白名单） | 保持独立 |
| `mcqa` → 并入 `mccore` | 少一个包 | 质检（含视觉模型调用）不该是内核的一部分；`mctools` 也用它 | 保持独立 |
| `mctools` → 并入 `mckit` | —— | 3540 行的工具引擎 + 参数表即 UI 的契约，独立价值高 | 保持独立 |

**总体判断**：包数不是问题，**边界模糊才是问题**。本轮的整合动作选的是「让边界变清楚」而不是「减少包数」：
README 分层契约 + 公开 API + 去重 + 死代码清理 + 一个分层冒烟测试。
唯一值得物理合并的是 `mcslice → mckit`（等你拍板）。

## 6. 本轮已落地的整合

| 动作 | 位置 | 效果 |
|---|---|---|
| 行尾收编为一份实现 | `mccore.paths.write_text_lf` ← `mckb.extract` 再导出 | 按字节哈希的 manifest 不再因两份实现而漂移 |
| 跨包私有名 → 公开 API | `mcrender.assets.norm_ref` / `collect_model_refs` | `mcmaterials` 不再 import 下划线名 |
| **删掉 184 行死代码** | `mcmaterials/catalog.py` | 5 个函数（`block_entry` / `build` / `write_markdown` / `write_family_index` / `prefetch_robust` / `cached_json`）在文件后半段被整段重定义，前半段是死代码；已删前半段并把 `mcmaterials.net` 的导入提到顶部 |
| 每个包一份 README（含分层位置） | `packages/*/README.md` | 边界、上游下游、CLI、API、测试都写在包门口 |
| 契约可执行 | `tests/packages_doc_smoke.py` | README 缺文件/缺模块/缺 CLI 文档、依赖方向违规、`structure.json` 的 `entry_points` 指向不存在的文件 → 冒烟红 |
| **域专用件归位** | `compositions/math-cube/{cells,curves,redstone,defloat}.py` | 4 个「只服务数学域」的脚本从 `mccore` / `mckit` 搬回组合；引擎只剩通用件（§4） |

## 7. 验证

```bash
python tests/packages_doc_smoke.py     # 本文件描述的契约
python tests/lf_smoke.py               # 行尾不变式
python -m mccore.pack validate && python -m mccore.registry validate
python -m mcmaterials check           # 材质目录自检（方块颜色/透明度/质感表）
```
