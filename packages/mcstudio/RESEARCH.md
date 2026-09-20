# mcstudio 调研：现成的 Minecraft 结构查看/编辑器（2026-09）

结论：**不整体采用任何现成项目**，只 vendor `deepslate`（MIT，281KB UMD）做浏览器端三维渲染，
其余（模块库/标签、.schem 读写、编辑操作、保存安全）复用 structworkshop 自己的引擎。

## 浏览器端三维渲染

| 项目 | 星 / license | 能力 | 结论 |
|---|---|---|---|
| [misode/deepslate](https://github.com/misode/deepslate) | 255★ MIT | WebGL 方块模型/贴图渲染 + NBT；UMD 281KB 自带 gl-matrix/md5/pako；`StructureRenderer` 支持 `setStructure`/`updateStructureBuffers` 增量刷新，`StructureProvider` 可自定义 | **vendor 使用**（`web/vendor/deepslate.umd.cjs`） |
| [EndingCredits/litematic-viewer](https://github.com/EndingCredits/litematic-viewer) | 69★ 无 license | 浏览器投影查看器（deepslate + WASD + 分层滑条 + 材料表） | 只看思路，**不拷代码** |
| [Davide0995/schematic.viewer](https://github.com/Davide0995/schematic.viewer) | MIT，活跃 | `.litematic/.schematic/.nbt` 浏览器查看 + 资源包 + Y 切片 | UX 参照 |
| [EngineHub/SchematicWebViewer](https://github.com/EngineHub/SchematicWebViewer) | 77★ MIT | NPM 包，WorldEdit `.schem` + three.js，需要完整资源包（客户端 jar） | 备选方案（资源方案比 deepslate 重） |

## 桌面 / 编辑类工具

| 项目 | 星 / license | 能力 | 结论 |
|---|---|---|---|
| [albertchen857/LitematicaViewer](https://github.com/albertchen857/Litematica-viewer) | 120★ MIT | Python 桌面（customtkinter + pywebview + deepslate JS）：分析/替换方块/另存为/分层/3D | **编辑功能与桌面壳的最佳参照**；未采用其代码 |
| [jacklitstar/vscode-litematic-viewer](https://github.com/jacklitstar/vscode-litematic-viewer) | 3★ AGPL-3.0 | VS Code 内预览 `.litematic/.schem` | 不拷代码 |
| [A1Panda/litematic-viewer](https://github.com/A1Panda/litematic-viewer) | 4★ MIT | React+Express+MySQL 原理图上传/搜索/预览站 | 管理 UI 参照；架构过重 |
| 投影工坊《投影预览 V3》(B 站) | 闭源 | 预览 / 替换方块 / 降版本 | 证明需求存在；无法复用 |

## 格式 / 转换

| 项目 | license | 用途 |
|---|---|---|
| [LucasDower/ObjToSchematic](https://github.com/LucasDower/ObjToSchematic) | BSD-3 | `.schematic/.litematic/.schem/.nbt` 读写与转换，老格式映射参考 |
| [GoldenDelicios/Lite2Edit](https://github.com/GoldenDelicios/Lite2Edit) | MIT | litematic ↔ WorldEdit schematic 字段映射参考 |
| [maruohon/litematica](https://github.com/maruohon/litematica) | LGPL-3.0 | `.litematic` 格式权威（只读规范） |
| [SmylerMC/litemapy](https://github.com/SmylerMC/litemapy) | GPL-3.0 | Python 读写 litematic；license 有传染性，**不用** |
| [Sloimayyy/mcschematic](https://github.com/Sloimayyy/mcschematic) | Apache-2.0 | Python 写 `.schem/.nbt`；structworkshop 已有自己的 `schem_io`，仅备用 |

## 关键设计决定

1. **数据面在 Python**：`.schem`(Sponge v2/v3) 与 `.litematic` 由 `mccore.structure_io` 统一读写，
   浏览器只拿扁平 `Uint16Array + palette`，编辑以 op 形式回传服务端；避免在 JS 里重写格式层。
2. **自定义 `StructureProvider`**：直接读扁平数组并惰性产出 `{pos, state}`，不建 deepslate
   `Structure` 的稀疏对象数组，几十万方块级不会爆内存。
3. **资源按需取**：`/api/asset` 复用 `mcrender.assets.Assets`（mcmeta 镜像 + `.cache/mcassets` 磁盘缓存，
   首次联网后离线可用）；取不到时自动回退“纯色方块”模式。
4. **安全**：只绑 `127.0.0.1`；路径白名单（`packs/ builds/ compositions/ tests/fixtures/ dist/ .cache/mcstudio`）；
   保存前自动备份到 `.cache/backups/`。
5. **零新增 Python 依赖**：HTTP 用标准库；前端原生 JS，无构建步骤；deepslate/gl-matrix 静态 vendor（MIT）。

## 渲染修复（2026-09，路线 A：保留 deepslate 做模型烘焙）

方针：**deepslate 只当模型烘焙库**（`BlockDefinition/BlockModel/TextureAtlas/SpecialRenderers`，MIT），
网格与绘制逐步换成我们自己的实现；语义（图层/染色/遮挡/特判）统一由 Python 端的
`/api/palette-info` 下发（`mcstudio/blocks.py` + `mcstudio/entity_assets.py`，与 mcrender 同源）。

**已完成（本轮）**

- **自建 mesher + 绘制**（`web/renderer3d.js`，deepslate 只当烘焙库）——实测（`node tests/editor_mesh_bench.js`）：
  96k 块首次建网格 **98ms vs 1252ms（12.8×）**、单块增量 **0.9ms vs 43.7ms**、全量重建 76ms vs 1176ms；
  590k 块 919ms vs 13.1s；四边形数与 deepslate 一致（Δ0.0%，即“快”不是靠少画）。
  做法：按状态缓存烘焙 / 只扫脏块的 16³ 格子 / 平铺数组 + 每帧属性位置只查一次。
- 渲染保真：vanilla 四档面明暗 + 4 级 AO（带四边形翻转）、流体角点高度 + `*_flow` + waterlogged、
  **按面自己的 `cullface` 剔除**（从 deepslate 逐方向烘焙反推；按法线剔会把栅栏顶/端盖剔掉）、
  自生成 alpha-preserving mipmap、透明 pass 排序且不写深度。
- 特判渲染器按 `has_elements` 门控（床/告示牌不再叠旧几何）；`entity/*` / `*_flow` 贴图由语义表点名下载；
  缺贴图的面直接不画（品红棋盘 = 0 像素，A/B 里验证）。
- 关掉 deepslate 默认构建、却从不绘制的「隐形方块」线框（旧路径保留给 A/B，不再默认使用）。
- 语义单一来源：`/api/palette-info` 下发 `layer/tint/liquid/special/has_elements/ao_occluder/waterlogged`
  （与 mcrender 同源），修正了水/炼药锅不染色、普通玻璃板被当成 cutout（挡住后面的水）等表问题。
- 方块实体数据保留（读→存不丢 NBT）+ 渲染子集接口 `/api/structure/<sid>/blockentities`。

**待办**

1. AO 四档亮度值（0.45/0.62/0.80）与树叶 cutout 阈值（0.5）建议再用游戏截图校准一次；
   A/B 里与 deepslate 的差异主要就来自这两项（AO 是 deepslate 没有的，属于“更接近游戏”）。
2. ~~方块实体几何共用~~ ✅ 已做：`tools/export_entity_models.js` 导出 173 个状态 /
   `packages/mcrender/data/entity_models.json`，mcrender 画真实箱/旗/颅/盒/罐（回归 `tests/entity_models_smoke.py`）。
3. ~~mcrender 流体~~ ✅ 已做：按 `level` 高度 + 角点 + `*_flow` 侧面 + waterlogged
   （回归 `tests/fluid_smoke.py`）。
4. ~~旗帜图案层贴图~~ ✅ 已在编辑器里做：`McStudio3D.bannerPatternTextures()` 从 NBT 推出
   `entity/banner/<pattern>` 并并进 `extraTextures`（audit 有断言）；离线渲染器目前只画底色层。
5. 可选：把网格构建移到 Web Worker（目前是主线程时间分片，大结构首次加载会占用几帧）。

## 许可清单（vendor）

- `web/vendor/deepslate.umd.cjs` — deepslate 0.27.1, MIT (`web/vendor/LICENSE-deepslate.txt`)
- `web/vendor/gl-matrix.umd.js` — gl-matrix 3.4.3, MIT (`web/vendor/LICENSE-gl-matrix.txt`)
