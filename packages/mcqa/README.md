# mcqa — 结构质检与评审（层 2）

生成完一座建筑之后，「**它能不能进游戏 / 好不好看**」这两问由本包回答：
结构质检（palette 合法性、悬浮块、门配对）、可行走性 BFS（能不能走到各个房间）、
ASCII 预览、以及把渲染图交给视觉模型的一键评审回环。

```bash
python -m mcqa.qa_check   build.schem            # 结构质检（有 ERROR 退出码 1）
python -m mcqa.walk_check build.schem --start 24,1,20 --probe 16,6,8   # 可达性
python -m mcqa.review_loop build.schem --views iso,front,top          # 渲染 + 视觉评审
```

## 分层位置

| | |
|---|---|
| **上游** | `mccore`（读结构、方块表）；评审回环额外调 `mcrender`（出图）与视觉模型 API |
| **下游** | `mctools`（`--qa` 自检）· `mcstudio`（工具执行后的自检）· 组合生成器的收尾步骤 |
| **反向边** | `mccore.stage_build` 的 CLI 在分段评审时懒导入 `mcqa.vision_review`（唯一一条 0→2 的边，见 `packages/ARCHITECTURE.md`） |
| **不做** | 不修结构（只报告问题，改不改由生成器/编辑器决定）、不生成建筑、不负责出图（那是 `mcrender`） |

## 模块

| 文件 | 作用 |
|---|---|
| `qa_check.py` | **结构质检**：① palette 逐条 lint（未知方块 / 非法属性会静默退回默认状态 → 渲染错）② 悬浮组件（6 连通块底部高于 y=0：自支撑的链/灯笼/藤蔓/树叶算 WARN，其余 ERROR）③ 门配对（上下半、facing/hinge/open 一致）④ 统计（尺寸/方块数/palette/空隙率）。`--json`、`--allow-float` |
| `walk_check.py` | **可行走性**：格子可通行=空气或门；可站立=可通行且下方实心；移动=同层四向 + 上一格（跳）+ 下一格（落）。打印可达楼层与每个探针（`--probe x,y,z`）是否可达 |
| `preview.py` | ASCII 预览：剪影 / 俯视 / 剖面（无图形环境时肉眼检查形体）。单文件看形体，`--base` 叠加对比「原建筑 vs 改完」（新增部分高亮） |
| `vision_review.py` | 直接调视觉模型的评审封装：`review(pngs, prompt, max_tokens=…) -> (文本, usage)` |
| `review_loop.py` | **一键评审回环**：多视图渲染 → 分块（每 8 张）送视觉模型 → 写 `<out>.md`；`--no-ai` 只出图 |

## 关键 API

| 名字 | 用途 |
|---|---|
| `qa_check.main(argv)` | 质检 CLI（返回退出码；`--json` 出机器可读报告） |
| `walk_check.bfs / build_masks / standable` | 可达性内核（组合生成器可当库用） |
| `vision_review.review` | 「出一组 PNG → 一段评审」的最小接口（`stage_build` 用的就是它） |
| `preview.outer_view / render_side / render_top / render_slice` | 文本视图 |

## 约定 / 不变量

* 质检的判据与 `mckit.connect` 的自支撑清单**同源**（哪些方块「本来就挂得住」），
  改一处要改两处（`compositions/math-cube/defloat.py` 也引用同一口径）。
* 判定用**方块名**而不是状态：`stairs` / `slab` 算支撑，`air` / `door` 算可通行。
* 评审需要外部模型凭证；**凭证一律走环境变量，代码里不许写路径也不许写 key**。
  解析顺序（`vision_review.api_key()`）：
  1. `STRUCTWORKSHOP_VISION_API_KEY`（或 `DEEPSEEK_API_KEY`）
  2. `STRUCTWORKSHOP_AUTH_FILE` 指向的 JSON（`{"deepseek": {"key": "sk-..."}}`）
  3. `~/.config/structworkshop/auth.json` / `~/.structworkshop/auth.json`

  端点与模型同理：`STRUCTWORKSHOP_VISION_API` / `STRUCTWORKSHOP_VISION_MODEL`
  （任何 OpenAI 兼容的 `/chat/completions` 都能换，不绑死某一家）。
  都没配就抛 `VisionConfigError`，调用方（`review_loop` / `mccore.stage_build`）
  **打印怎么配并跳过评审**（出图不受影响），不静默失败、也不把整条流程弄挂。

## 测试

```bash
python -m mcqa.qa_check "compositions/modern-skyscraper/out/现代摩天楼.schem"   # 手工
python tests/projects_smoke.py              # 组合生成后跑质检（间接覆盖）
python tests/math_exhibits_smoke.py         # 展品可达性（八层都能走到）
python tests/mctools_smoke.py               # mctools --qa 的自检链路
```
