# mcstudio — 本地可视化工作台（层 3 · 应用）

浏览器里的 Minecraft 结构工作台：**模块库**（标签树 / 批量打标 / 拖拽导入 / 预览图渲染队列）、
**3D 预览**（真实方块模型 + 贴图）、**结构编辑器**（放置/擦除/替换/撤销/框选移动复制/模块手柄）、
**模块装配**（多模块拼装 + 接口磁吸 + gizmo 拖动旋转）、**AXIOM 工具面板**（29 个工具，
与 `mctools` 同一套引擎）、**设置**（备份策略）。

```bash
python -m mcstudio serve --open         # http://127.0.0.1:8617/
python -m mcstudio serve --port 8700    # 端口占用自动 +1；只绑 127.0.0.1
# 自检页：/static/selftest.html   （WebGL / deepslate 资源 / 结构读取）
```

## 分层位置

| | |
|---|---|
| **上游** | `mccore`（会话/结构/模块库/装配）· `mcrender`（贴图与方块模型烘焙）· `mctools`（工具引擎）· `mckit`（方块更新模拟） |
| **下游** | 无（叶子应用） |
| **不做** | 不是公网服务（**只监听 127.0.0.1**）；不做生成算法（那是 compositions）；不改包外文件（见下白名单） |

## 模块

| 文件 | 作用 |
|---|---|
| `server.py` | 标准库 HTTP 服务：JSON API 路由 + 静态 Web 应用；`ALLOWED_PREFIXES = ("packs", "builds", "compositions", "tests/fixtures", …)` 之外一律拒绝访问；端口占用自动 +1 |
| `__main__.py` | `python -m mcstudio [serve] [--port N] [--open]` 的入口垫片 |
| `api.py` | 全部 JSON 路由（状态/设置/包/标签/模块/结构打开保存/工具/渲染/方块目录…） |
| `session.py` | **编辑会话**（1554 行）：打开结构、体素操作、撤销/重做、选区、模块实例、保存与另存为、方块更新重算 |
| `blocks.py` | 方块目录：搜索、2D 颜色、3D 渲染标记、贴图/拾取用的方块清单 |
| `entity_assets.py` | 方块实体 / 特判方块的**贴图清单**（编辑器 3D 用） |
| `jobs.py` | 后台任务运行器：预览图批量渲染等长任务的进度、排队、取消 |
| `web/` | 前端：`index.html` + `app.js`（模块库）+ `editor.js`（编辑器，**自研 mesher** `renderer3d.js`）+ `viewer3d.js`（3D 预览）+ `settings.js`；`vendor/deepslate` **只当模型烘焙库**（MIT） |

## 安全边界（重要）

* 只绑 `127.0.0.1`，无鉴权、无外网暴露设计 —— 不要改成 `0.0.0.0`。
* 可读写路径限定 `packs/ builds/ compositions/ tests/fixtures/ dist/ .cache/mcstudio`；
  打开对话框走 `/api/files` 的 `q` 搜索，越界路径直接拒绝。
* 原地保存前按设置页的备份策略备份（`mccore.backup`；可配「不备份 / 按天留存 / 保留最近 N 次」）。
* 超大结构有打开闸门（`MAX_CELLS`），超限拒绝并提示，避免浏览器与服务端一起被拖死。

## 关键 API / HTTP 面（脚本可直连）

| 路由 | 用途 |
|---|---|
| `GET /api/state` · `GET/POST /api/settings` | 工作台状态与设置（含备份策略） |
| `GET /api/packs` · `GET /api/modules` · `POST /api/modules/*` | 模块库（搜索/标签/元数据/转移/删除/预览） |
| `GET /api/previews` · `POST /api/previews/render` | 预览图缺图统计 / 提交渲染（`scope: missing\|all`，排队串行） |
| `POST /api/structure/open` · `/save` · `/new` · `/tool` · `/select` | 编辑会话（工具与选区，一步一个撤销步） |
| `POST /api/structure/<sid>/modules/*` | 模块装配（导入/移动/旋转/固化/脱离） |
| `GET /api/tools` | Axiom 工具目录（`mctools.registry.catalog()` 直出，前端零硬编码） |

## 设计要点

* **自研 mesher 而不是直接吃 deepslate**：按调色板状态缓存烘焙（96k 块 / 27 状态只烘焙 27 次）、
  只扫脏块 16³ 格子、每帧属性位置只查一次 —— 实测首次建网格快 12–14×，涂一格 208ms → 1ms 量级
  （渲染器那一侧的数据见 `packages/mcrender/README.md` 的性能表，
  交互这一侧的网格数据见 `AGENT.md` §6.7，回归 `node tests/editor_mesh_bench.js`）。
* **deepslate 只当烘焙库**：blockstates/模型/贴图解析用它，网格与绘制是我们自己的 `web/renderer3d.js`；
  `?renderer=deepslate` 可切回旧路径做 A/B。
* **可搜索下拉**：所有「从一长串里挑一个」的入口都是 `App.searchSelect()`（输入即过滤、↑↓、Esc），
  避免长列表原生 select。
* **预览图不会被缓存钉住**：文件名不变，`/api/modules` 给的 `preview_urls` 带 `?v=<mtime_ns>` 版本号。
* **两种路径粒度别搞混**：模块行的 `path` 是包内相对（给预览 URL、`pack.json` 用），
  `file` 是仓库相对（「3D 查看 / 在编辑器中打开」必须用 `file`）。
* **打开即实例（`@self`）**：任何投影打开后都是一个可拖的装配实例 —— 资产包模块拿 spec 接口，
  其余投影用伪包名 `@self`（`session.SELF_PACK`）整幅包一个。重开时按「读进来的体素原样
  再包一层」还原，所以 `provenance()` 里 `@self` 的 `pos/rot` 一律写 0（否则内容会被推两遍）。
  打开那一步传 `markDirty=false, record=false`：只是会话模型的变换，不算未保存、不占撤销栈。
* **叠加层只在「移动 / 复制」画**：模块脚框 / 中心手柄 / 三箭头 / 2D 脚框都过 `moduleOverlayOn()`；
  切到编辑类工具就全收起（选中也清掉），导入进来的东西就**当普通方块用**。
  打开是异步的，「要不要切到移动」按换工具序号（`E.toolSeq`）判，不抢用户已选的工具。
* **连接状态重算看合成结果**（`session._update_states`）：基地层 + 实例合成后再跑 `mckit.update`，
  差异写回各自那一层（实例盖着的格子记成实例就地修改）—— 否则打开投影后放一格邻居，
  实例里的墙不会跟着连。
* **滚动条写在 `:root`**（`scrollbar-color` 是继承属性）+ `color-scheme: dark`：弹窗 / 设置页 /
  可搜索下拉 / 代码块这些不在白名单里的容器也跟右栏同一套深色（旧写法漏了一片）。

## 回归（Web 端占大头）

```bash
python tests/studio_smoke.py                 # HTTP API 主链路
python tests/studio_tools_smoke.py           # 工具 HTTP 端到端（目录→执行→撤销→选区→保存，67 项）
python tests/previews_smoke.py               # 预览渲染队列（排队串行 / 进度 / 取消 / scope）
python tests/picker_smoke.py                 # 方块面板分类与搜索
python tests/backup_smoke.py                 # 备份策略
python tests/blockentity_smoke.py            # 方块实体保留（读写往返 / 保存写回 / 不悬空）
python tests/render_bg_smoke.py              # 视口/出图背景切换
node tests/editor_tools_smoke.js             # 编辑器工具面板 + 打开即实例/属性面板/叠加层/滚动条（48 项，不需资产包）
node tests/studio_ui_audit.js [chrome] http://127.0.0.1:8617 [截图目录]   # UI 布局审计（127 项）
node tests/assembly_gizmo_audit.js [chrome] http://127.0.0.1:8617 [截图目录]  # 装配 gizmo 真拖拽
node tests/pick_raycast_smoke.js             # 鼠标拾取
node tests/editor_mesh_bench.js              # 自研 mesher 性能预算
node tests/renderer_ab.js [chrome] <url> [截图目录]   # 渲染器 A/B
```

UI 审计需要本机 Chrome/Chromium 与一个已启动的服务；其余 Python 冒烟自带临时服务。

调研与复用边界见 `packages/mcstudio/RESEARCH.md`；用法全解见仓库根 `README.md` 的
「可视化工作台（mcstudio）」章节与 skill `skills/minecraft-studio/SKILL.md`。
