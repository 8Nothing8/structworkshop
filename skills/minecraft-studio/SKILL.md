---
name: minecraft-studio
description: structworkshop 本地可视化工作台（mcstudio）：模块库（标签=Obsidian 式属性表/批量打标/导入）、预览图渲染队列（为缺图的渲染 / 全部重新渲染，排队可取消）、3D 预览（deepslate 真实方块模型 + 贴图）、.schem/.litematic 结构编辑器（放置/擦除/替换/撤销/另存为模块/方块更新模拟：按邻居重算墙·栅栏·铁栏杆·玻璃板连接与楼梯 shape/压在模块上的格子就地改在模块实例上）、编辑器左侧属性窗口**随选中切换**（点模块中心小方块=那个模块的属性，没点=当前投影属性）、**打开即实例**（任何投影打开后都是可拖的装配实例；`builds/…`/上传的整幅实例伪包名 `@self`，存盘重开仍是实例）、**叠加层只在「移动/复制」显示**（切到编辑工具就收起，当普通方块用）、模块装配（多模块导入拼装/可堆叠/成品模块清单/高亮/Axiom 式 gizmo 拖动平移+三轴旋转/接口磁吸：矩形或圆形接口）、Axiom 式体素工具面板（29 个工具：基本体/路径/公式体/文字铭碑/噪声绘制/渐变/平滑/岩石化/破碎/融化/扭曲/掏空/重力/削平/盖章…，含掩码表达式与笔刷涂抹）、设置页（备份策略：不备份 / 按天留存 / 保留最近 n 次 + 备份现状/清空）。当用户要「看模块」「渲染/补模块预览图」「3D 预览」「按标签管理模块」「编辑投影/schematic 文件」「把结构存成模块」「把多个模块拼在一起」「移动/旋转导入的模块」「用工具打磨/上色/破碎/地形化结构」「让墙/栅栏/铁栏杆连起来・修楼梯转角缺角」「备份太多/占地方、不想留备份、备份保留几天/几次、改备份设置」时使用。
---

# mcstudio 可视化工作台

一句话：`python -m mcstudio serve --open` → `http://127.0.0.1:8617/`

> 渲染：编辑器用自己的 mesher（`web/renderer3d.js`，deepslate 只当模型烘焙库）——按调色板状态缓存烘焙、
> 只重建脏块、含 AO/流体/面明暗。`?renderer=deepslate` 可切回旧路径做 A/B。
> **运行时出现新状态也能画**：env 按引用拿 `blockStates/semantics`（换数组后要 `refreshEnvArrays()` 同步，
> 否则新下标越界 → `stateAt()` 退回 air），且资源集要把 blockstate 引用的**所有**模型都取下来
> （墙的 `east=tall` → `*_side_tall`；缺模型时 `getMesh` 抛异常会让**整块 chunk** 消失，
> 旧现象：墙/栅栏旁边放个方块 → 墙变透明；mesher 现在兜底：只丢那个状态的面）。
> 地面网格吃深度（不再穿过建筑画在模型上）；`firstInBox()` 射线没穿过画布就不给落点。
> 性能回归：`node tests/editor_mesh_bench.js`；渲染器 A/B：`node tests/renderer_ab.js "<chrome>" <url> <shots>`；
> 拾取/退化落点：`node tests/pick_raycast_smoke.js`。
>
> 打开/新建的上限（文件 MB / 格数 / 方块数 / 告警阈值）在**设置页 → 打开上限**里改，存
> `.cache/mcstudio/settings.json`；环境变量 `STRUCTWORKSHOP_MAX_STRUCTURE_*` 优先级更高（被锁的项页面会置灰）。
> 方块实体（箱子内容/告示牌文字）打开→保存**不会丢**：读进来带着原始 NBT，写盘时原样写回。
> 路径粒度：模块行的 `path` 是**包内相对**、`file` 是**仓库相对**（`packs/…`）；发给
> `/api/structure/open`（包括抽屉的「3D 查看 / 在编辑器中打开」）必须用 `file`。
> **方块实体渲染语义**：`/api/palette-info` 的 `has_elements` 只算**方块自身模型**的几何（特判表来的不算），
> 否则客户端会跳过特判渲染、也不下载 `entity/*` 贴图（实测：橙色旗帜放下去什么也看不到）；
> 墙/栅栏这类连接方块的新状态要能画，靠「blockstate 里引用的模型全部预取 + env 数组同步 + 单状态烘焙失败兜住」。

## 什么时候用

| 用户说 | 用哪块 |
|---|---|
| 看有哪些模块 / 按标签找 / 批量打标 | 模块库（左侧标签树 + 卡片多选批量） |
| 找不到某个模块/结构（包/分类/文件太多） | 各级都是**可搜索下拉**：输入即过滤 → 向下展开结果 → 选中/回车；编辑器“打开结构”按 q 搜仓库，装配面板可先筛**资产包**再搜模块 |
| 3D 看模块/结构、检查接口 | 卡片「3D 查看」或结构编辑器 |
| 模块没有预览图 / 预览图过时 | 模块库「渲染预览」工具栏：选范围 + 背景（白/黑/透明）→「为缺图的渲染」/「全部重新渲染」（进队列，可看进度、可取消排队）；单个模块在抽屉里点「重渲染预览」。封面 URL 自带 `?v=` 版本号，重渲染后会自动换新（不会被浏览器缓存钉住） |
| 把模块搬到另一个资产包 / 从一个包复制一份过去 | 模块库 → 点卡片打开抽屉 → 「复制 / 转移到别的资产包」（转移 = 源先备份到 `.cache/backups/modules/` 再删、**保留 id**；复制时 id 被占会自动加 `_2`；两包 catalog/pack.json 会重建） |
| 打开某个模块时改它的简介/分类/标签 | 编辑器**左侧**属性窗口：**点中模块中心的小方块** → 就是**那个模块**的属性（顶栏会多一行实例信息）；**没点中** → 显示**当前投影**的属性（路径/尺寸/方块数/画布框/实例数…）。打开的是 `packs/…/modules/…` 里的模块时，未选中也直接显示它的属性（预览图 + 描述/分类/标签/备注 + 接口表 → 「保存属性」写回 `.module.json`；「重渲染预览」走同一条队列） |
| 打开一个普通投影（`builds/…` / 上传的 .schem）想**整体**搬/转 | 打开就是**整幅实例**（伪包名 `@self`）：切「移动」拖中心小方块/三箭头即可；存盘后重开**还是**实例（体素不会被推两遍）。想彻底变普通体素：右栏「固化装配」 |
| 导入进来的东西挡视线 / 我只想当普通方块改 | 叠加层（脚框 + 中心小方块 + 三箭头）**只在「移动 / 复制」时画**：一切到放置/擦除/替换/框选/接口就全部收起（选中也清掉）。要再搬它 → 切回「移动/复制」，或点右栏「成品里的模块」里那一行 |
| 换个背景看模型 | 编辑器工具栏「背景」或 3D 预览弹窗里的「背景」：深底/黑底/白底/透明（透明时露棋盘格） |
| 改投影 / .schem 方块 | 结构编辑器（2D 俯视图层 + 3D 拾取 + 3D 准星预览；点到画布外会自动扩容，−X/−Y/−Z 侧会明说放不了）；**中键单击 = 吸取方块**（含状态，不用先切「吸管」），中键拖拽仍是平移 |
| 找方块（中文） | 方块面板搜索框**同时匹配 id 与中文别名**：搜「烟熏炉 / 高炉 / 侦测器 / 楼梯 / 蜡烛 / 头颅」都行；不列的只剩空气系/屏障/光/传送门/命令块 |
| 墙/栅栏/铁栏杆/玻璃板没连起来、楼梯转角缺角 | 编辑器选「放置」工具 → 下方**「方块更新」开关**（默认开）开着再放；已有的把状态补上：点「重算连接」（当前框选 / 未选则整幅）。**算的是合成结果**：内容在模块/整幅实例里也照样连（改动记成实例就地修改） |
| 撤销/回退到某一步 | 编辑器「操作日志」窗口：点任意一条回到那一步（或工具栏/Ctrl+Z 逐步撤销） |
| 备份占地方 / 不想留备份 / 备份留几天几次 | 顶栏第三个页签**「设置」**→ 备份策略（不备份 / 保留最近 n 次 / 按天留存）+「备份现状」「清空已有备份…」 |
| 投影太大打不开 / 想放开或收紧限制 | **「设置」→ 打开上限**：文件大小上限 / 结构格数上限 / 方块数量上限 / 大结构告警阈值（下次打开即生效；被环境变量锁的项会置灰并标注） |
| 把改好的结构存成带标签的模块 | 编辑器「存为模块」 |
| 把多个模块同时导入、拼接成一座建筑 | 编辑器「模块装配」：勾选多个模块 → 导入所选 → 自动沿 +X 排布 |
| 查看成品里包含哪些模块 / 高亮某一模块 | 编辑器「成品里的模块」清单（整幅实例那一行的包名显示为「整幅投影」）；点击/悬停即高亮线框、点一行会自动切回「移动」 |
| 移动/旋转导入的模块 | 选中模块 → 「移动」工具：Axiom 式 gizmo（半透明选框 + 三色实心箭头 + 三个旋转圆环）拖拽；2D 脚框拖动 / 方向键也可 |
| 用 Axiom 式工具改结构（成形/上色/形变/地形） | 编辑器「AXIOM 工具」面板：选工具（可搜索）→ 涂抹或框选 → 应用 |
| 只想批量改标签、不进 UI | `python -m mccore.module_lib tags/tag/meta/rm/crop` |
| 不进 UI 也要用这些工具（脚本/AI） | `python -m mctools run <工具> --in a.schem --out b.schem …` |

## 启动

```bash
python -m mcstudio serve --open          # 默认 8617，接口占用自动 +1
python -m mcstudio serve --port 8700     # 指定接口
```

只监听 `127.0.0.1`，仅本机可用，无鉴权；不要改绑 `0.0.0.0`。
自检页：`/static/selftest.html`（WebGL → 打开示例模块 → 贴图资源 → 读回中心像素）。

UI 布局/视觉审计（headless Chrome，无额外依赖）：

```bash
python -m mcstudio serve --port 8617 &
node tests/studio_ui_audit.js "C:/Program Files/Google/Chrome/Application/chrome.exe" \
     http://127.0.0.1:8617 <截图目录>       # 127 项检查 + 截图
node tests/pick_raycast_smoke.js             # 拾取算法（不需要服务）
python tests/backup_smoke.py                 # 备份策略 + /api/settings（不需要服务）
node tests/pick_raycast_smoke.js             # 鼠标拾取（DDA）与解析参考逐像素对比，不需要服务
```

覆盖：页面水平溢出、卡片与预览图加载、渲染预览工具栏（范围/缺图计数/两个选项）、抽屉在 main 内且不遮挡顶栏、3D 弹窗 canvas 不越界、
编辑器 2D 画布自适应（不拉伸）、调色板/层滑块、**Axiom 工具面板（目录/参数表单/应用+撤销/2D 拖框/3D 笔刷涂抹）**、
**3D 放置指哪放哪 + 负方向标红提示 + 缩小画布框不裁数据 / 框外可放 / 可撤销**、
**方块面板（色块/分类/搜索/只看完整方块/点选 + 旧服务端名单模式退化）+ 朝向控件（四向 / 16 档旋转 / 跟随视角 / R 键）
+ 掩码自定义下拉 + 编辑工具×AXIOM 工具互斥**、运行时 JS 异常。

## HTTP API（脚本/自动化）

| 方法 路径 | 作用 |
|---|---|
| `GET /api/state` | 统计 + 标签 + 资产包 + 分类 + 材料 |
| `GET /api/settings` | 备份策略 + 打开上限（含默认/范围/单位/来源）+ 备份现状（每类份数/占用/最新时间）+ 模式表/上限 |
| `POST /api/settings {backup:{mode,count,days}, structure:{max_mb,max_cells,max_blocks,warn_cells}, prune?}` | 存设置（校验范围；默认按新策略立刻清理旧备份，`pruned` 回报删了几份）；`structure` 改的是「打开结构/新建画布」的闸门，环境变量优先 |
| `POST /api/settings/clear` | 清空 structures/tools 的备份（modules/packs 不动） |
| `GET /api/modules?tag=&tagMode=facet\|and\|or&q=&pack=&category=&material=&untagged=&sort=` | 模块检索（`facet`＝同命名空间任一 / 跨命名空间全选，UI 默认） |
| `GET /api/modules/counts?tag=&tag=&…` | 三种标签模式各有多少结果（UI 提示/一键切换用） |
| `GET /api/modules/<id>` · `PATCH /api/modules/<id>` | 详情 / 改 description·category·tags·ports |
| `POST /api/modules/batch {ids, addTags, removeTags, setTags, category, delete}` | 批量 |
| `POST /api/tags/rename\|merge\|delete` | 标签治理（写回全部 sidecar 后重建索引） |
| `POST /api/import/upload?filename=&pack=&category=&tags=&trim=`（raw body） | 导入模块 |
| `POST /api/import {paths, pack, category, tags, description}` | 从仓库内路径导入 |
| `GET /api/previews` | 各资产包预览图覆盖（total / previews / missing / missing_ids）+ 渲染队列 |
| `POST /api/previews/render {pack?, scope: missing\|all, background?}` | 排队渲染预览图（`preview` 通道串行，返回 job id 与 `queued_ahead`；`background` ∈ white/black/transparent/dark/sky） |
| `POST /api/modules/<id>/preview {background?}` | 单模块重渲染（走同一队列，force + only） |
| `GET /api/jobs` · `GET /api/jobs/<jid>` | 任务列表 / 单个任务（state · progress{done,total,label} · log · result） |
| `POST /api/jobs/<jid>/cancel` | 取消**排队中**的任务（已在跑的返回 409；渲染子进程不可中断） |
| `POST /api/structure/open {path}` 或 `?filename=` + raw body | 打开结构，返回 `sid` |
| `GET /api/structure/<sid>/voxels[?y0=&y1=]` | uint16 LE 扁平体素（(y,z,x) 行序） |
| `GET /api/structure/<sid>/blockentities` | 方块实体（渲染用子集）：`{block_entities:[[x,y,z,id,data],…], total}`；完整 NBT 在保存时原样写回 |
| `POST /api/structure/<sid>/ops {ops:[…], update?}` | 编辑 `set/fill/erase/replace`（+`{type:"update"}` = 只重算连接），返回 `bbox`+`region`(base64)+`updated`(重算格数)；`update:true` = 改完按邻居重算连接（范围外扩 1 格） |
| `POST /api/structure/<sid>/undo\|redo` | 撤销/重做 |
| `GET /api/structure/<sid>/history` | 操作日志（每步 label/时间/影响包围盒 + 光标位置） |
| `POST /api/structure/<sid>/history/jump {index}` | 回到第 index 步（0 = 刚打开时）；前后都行 |
| `GET /api/tools` | Axiom 工具目录（分组 + 参数表 + 笔刷形状，UI 据此自动生成表单） |
| `POST /api/structure/<sid>/tool {tool, params, sel, centers, brush, block, mask, seed, update?}` | 跑一个工具（作用在基地层），返回 `bbox`+`region`+`stats`；`update:true` 同样按邻居重算连接 |
| `POST /api/structure/<sid>/select {mask}` 或 `{at:[x,y,z], connected}` | 掩码/魔棒选取 → 命中数与包围盒 |
| `POST /api/structure/<sid>/resize {size:[x,y,z]}` 或 `{fit:true}` | 改**画布框**（逻辑画布；不裁数据、不清撤销栈，保存时才按框裁；不足处补空气） |
| `GET /api/structure/<sid>/modules` | 装配实例清单（id/pos/rot/bbox） |
| `POST /api/structure/<sid>/modules {ids, auto}` | 多模块导入（auto=True 沿 +X 顺序拼） |
| `POST /api/structure/<sid>/modules/update {pid, pos\|delta, rot, rotx, rotz, snapPort, snapRadius}` | 平移（X/Y/Z 轴）· 三轴 90° 旋转 · 接口磁吸（半径内才吸） |
| `POST /api/structure/<sid>/modules/remove {pids}` · `/modules/bake` | 移除实例 / 固化装配 |
| `POST /api/structure/<sid>/save` / `save-as` / `save-as-module` | 保存（.schem 会带 layout.json + Metadata）/ 另存 / 写回资产包 |
| `GET /api/files` · `POST /api/palette-info` · `POST /api/blockdefs` · `GET /api/asset` | 结构清单 / 颜色+渲染 flags / 方块默认状态 / 贴图资源 |
| `GET /api/blocks/picker` | 方块面板数据：`{available, families, groups:[[id,标签,[家族下标]]], blocks:[[名,"#rrggbb",是否完整,[家族下标],贴图ref,tint]]}`（一次取回；`available=false`/404 时前端退化为「名单模式」） |
| `POST /api/structure/new {size:[x,y,z], name?}` | 新建空画布（默认 16³，单边 ≤1024）；会话无路径，保存走「另存为」 |

## CLI 等价能力（无 UI 时）

```bash
python -m mccore.module_lib tags                        # 标签总览（计数/命名空间）
python -m mccore.module_lib tag a b --add glass --remove old
python -m mccore.module_lib tags --rename old=新名      # --merge src=dst / --delete tag
python -m mccore.module_lib meta room_basic --description "…" --category rooms
python -m mccore.module_lib crop room_basic             # 裁掉外围空气层
python -m mccore.module_lib rm room_basic --yes         # 删除（默认备份到 .cache/backups）
python -m mccore.pack import-modules <你的投影目录> --pack modern-arch --tags imported
python -m mccore.pack previews gun-models               # 只渲染缺预览图的模块
python -m mccore.pack previews gun-models --force       # 全部重新渲染（慢）
python -m mccore.convert x.schem --to .litematic        # 单文件格式转换
python -m mctools list                                  # Axiom 工具目录（29 个）
python -m mctools run <工具> --in a.schem --out b.schem # 无 UI 也能用同一套工具
```

## Axiom 工具面板（29 个工具）

面板位置：结构编辑器右侧「AXIOM 工具」。后端是 `packages/mctools`（纯 numpy，确定性），
Web / CLI / 脚本共用一套实现，参数名尽量与 Axiom 6.x 同名同义。

界面按 Axiom 的 Minecraft 原生 GUI 质感做（2px 斜切边框 + 窗口标题条 + 方形图标槽位 +
凹陷滑块/数值盒；绿色=已启用、蓝色=当前项；模块库/抽屉/弹窗同一套语汇）：分组槽位
（形状/绘制/形变/体块/地形）+ 工具搜索框（跨分组按 id/名称/说明过滤）+ 工具槽位（内置内联 SVG 图标）+
「标签 | 滑块 | 数值盒」参数行 + 笔刷形状图标槽位。

### 用法（三步）

1. **选工具**：分组页签（形状与路径 / 绘制与上色 / 形变与雕刻 / 体块运算 / 地形与重力）
   → 点工具名，参数表单按工具自动生成（默认值、范围、枚举都在表单里）。
2. **取作用区**（三种，取决于工具）：
   - `笔刷类`（噪声绘制/平滑/岩石化/融化/粗糙化/焊接/雕刻/地形升降）：在 3D 里**按住涂抹**
     （一笔 = 一个撤销步；绿色线框是笔刷范围），或先在 2D 俯视图里拖一格点一下；
     也可以在框选后直接点「应用」（= 对整块选区生效，WorldEdit 式）。
   - `框选类`（破碎/扭曲/填充/替换/掏空/膨胀/重力/排液/自动明暗/削平/坡度化/挤出/盖章）：
     左侧「框选」按钮或面板里的「两点框选/魔棒/按掩码选」定好选区 → 点「应用」
     （不框选就点「整张画布」）。
   - `自带几何`（基本体/路径）：直接在 3D 里点一下即成型；「路径」是**多点点选**采控制点
     → 点「应用」生成（Esc 清空采点）。**公式体 `field` 与文字铭碑 `glyph` 不吃点选**
     （`field` 的几何由公式自己定、默认作用于整张画布，不用定位；`glyph` 用参数里的 `at` 定位，
     默认 `0,0,0`）——选好直接点「应用」，想限制范围就先框选。
3. **看结果**：状态栏给出「改了多少格」，橙色提示 = 需要注意（贴到画布边界被裁 / 有格子被模块覆盖 /
   笔刷落点不在选区内）。`Ctrl+Z` 一次撤销整个工具。

### 工具清单（速查）

| 想干什么 | 工具 | 关键参数 |
|---|---|---|
| 生成球/圆柱/锥/环/超椭球/棱柱… | 基本体 `shape` | kind、radius、height、size、hollow、mode |
| 沿曲线铺管道/线缆/吊桥 | 路径 `path` | curve（line/bezier/catmull/**catenary**）、radius、hollow、sag |
| 数学曲面 / 隐式几何（球面/环面/双曲面/波场） | 公式体 `field` | expr（`f(x,y,z)`）、mode（solid/shell/bands）、op、threshold、shell、blocks（多档=色带） |
| 墙面刻字 / 铭牌 / 公式墙（含中文） | 文字铭碑 `glyph` | text、at、axis（x/z/y）、px、scale、depth、blocks（字色）、back（底色） |
| 随机材质（幕墙、岩壁、铺装） | 噪声绘制 `noise_painter` | noise（7 种）、scale、blocks（可带权重 `stone*3`）、octaves、warp |
| 高度/半径渐变（幕墙、夜灯、地层） | 渐变上色 `gradient_painter` | gradient_shape、axis、blocks、dither |
| 整体刷色 / 边缘虚化 | 实心绘制 `painter` | chance、soft_edge |
| 灌满房间 / 替换连片材质 | 连通填充 `floodfill` | limit |
| 去噪点、长合缝隙、磨圆 | 平滑 `smooth` | mode（grow/melt/stable）、strength、block_ratio |
| 方块堆 → 自然岩石 | 岩石化 `rock` | noisiness、noise_radius、meld_strength、smoothing_stddev |
| 地震/爆炸错位 | 破碎 `shatter` | axis（x/y/z/**3d**）、scale、width、use_active_block |
| 钟乳石 / 融雪 / 蜡烛 | 融化 `melt` | strength、chance |
| 风化表面、碎石边 | 粗糙化 `roughen` | ratio、min_faces、mode |
| 扭曲塔身 / 异形 | 扭曲 `distort` | distance_x/y/z、scale、iterations |
| 补模块缝 / 补裂缝 | 焊接 `weld` | strength、threshold |
| 交界处材质互渗 | 材质混合 `blend` | spread、warp |
| 堆料 / 削料 / 揉圆 | 雕刻 `sculpt` | mode、strength |
| 填满区域 | 填充 `fill` | only_air |
| 批量换材质 | 替换 `replace` | from（支持 `oak*`）、keep_props |
| 掏空做房子/穹顶 | 掏空 `hollow` | thickness、open_top |
| 加粗 / 瘦身 | 膨胀·腐蚀 `grow` | amount（负=腐蚀） |
| 塌方 / 碎石堆 | 重力 `gravity` | — |
| 抽水/抽岩浆 | 排液 `drain` | — |
| 一键明暗体积感 | 自动明暗 `autoshade` | shade_blocks、axis、invert |
| 场地平整 | 削平 `flatten` | level（世界 Y）、mode |
| 屋面 / 坡道 | 坡度化 `slope` | axis、mode |
| 加高墙 / 岩柱 | 挤出 `extrude` | amount（负=向下） |
| 散布树/灯柱/摊位 | 盖章散布 `stamp` | blueprint（模块 id）、chance、min_spacing、random_yaw |

### 掩码表达式（所有工具的「掩码」输入框通用）

```
solid / air / surface（朝空气的面）/ sky / edge / inside
y<64   x>z   (y-4)%8==0   坐标算术与比较
above(stone) below(air) near(oak*) adjacent(stone) neighbor(air)
block(stone)  stone  oak*   （裸方块名 = 完全匹配，* 通配）
随机 random(0.3)      布尔 & | ! 以及 and/or/not，分号 = 交集
```

> 代理/AI 直接改文件（不开浏览器、可批量、可 `--json`）见 skill
> `skills/minecraft-voxel-tools/SKILL.md`：`python -m mctools apply …`

### HTTP / CLI（不进 UI 也能用）

```bash
# 看目录与参数表
python -m mctools list
python -m mctools info rock

# 在结构上跑工具（与面板同一套实现）
python -m mctools run noise_painter --in a.schem --out b.schem     --sel 0,0,0,31,20,31 --param scale=8     --param "blocks=minecraft:stone,minecraft:andesite*0.5"
python -m mctools run rock --in a.schem --out rocky.schem --at 16,8,16 --brush sphere:8
python -m mctools run path --in a.schem --out arc.schem     --points "4,12,4;30,12,30" --param curve=catenary --param radius=2

# 代理首选：多步流水线 + 质检 + 出图（详见 minecraft-voxel-tools skill）
python -m mctools apply --in a.schem --out b.schem     --step '{"tool":"noise_painter","all":true,"params":{"scale":8}}'     --qa --render iso

# 走 HTTP（浏览器里做的每一步都能脚本化）
curl -X POST http://127.0.0.1:8617/api/structure/<sid>/tool      -d '{"tool":"smooth","params":{"mode":"stable","strength":2},"sel":[0,0,0,15,15,15]}'
```

### 纪律 / 注意

- 工具作用在**基地层**：模块（装配实例）盖着的那部分，改动的格子会**就地记到那个模块实例上**
  （响应里的 `punched` 计数；`✎N` 角标写进「成品里的模块」清单）——模块与编辑工具**不互斥**，
  不用先「固化装配」，改动跟着模块移动/旋转。
- 装配/生成侧要**堆叠**模块：`Assembler(region, overlap=True)` 或 `python -m mccore.assemble plan.json
  --overlap`（plan 里也可写 `"overlap": true`）；后放的模块在重叠区盖过先放的。
- 工作区（Axiom 工具）**不会自动扩容**（与画笔不同）：贴到数据范围边界会被裁掉并提示。
- 噪声类工具的 `种子` 相同 → 结果完全一致（可复现、可 diff）。
- 换材质时按 `minecraft-material-lab` 的色阶纪律（同色温同明度），别把干净模型刷成灰噪点。

## 设置（顶栏第三个页签）

目前只有**备份策略**：保存时怎么给旧文件留底。落盘 `<仓库>/.cache/mcstudio/settings.json`。

| 模式 | 含义 |
|---|---|
| 不备份 | 保存直接覆盖；**已有备份保留不动**（要腾地方点「清空已有备份…」，带确认） |
| 保留最近 N 次 | 默认 20。按**文件**分别计数，超出的最旧份数自动删（空目录一起收掉） |
| 按天留存 | 默认 7 天。同一文件每天最多留一份（当天再存就更新当天那份），超过天数的删 |

- 页面还会显示**备份现状**（structures / tools / modules / packs 各多少份、占多少、最新一份时间，
  并标注哪些受策略管）和备份目录（`.cache/backups`）。
- **保存设置会立刻按新策略清理**已有备份（toast 报「删了 N 份、释放 X」）；`POST` 带 `prune:false` 可跳过。
- 策略管到的位置：**结构保存 / 另存为**（编辑器）、**mctools apply --in-place**（CLI 读同一份设置）。
  **不受管**：删模块、资产包 `--force` 覆盖 —— 它们本身就是「把旧的挪到 `.cache/backups`」，
  不挪就等于真删，所以永远照旧执行（也不会被「清空已有备份」动到）。
- 保存结构的 toast 会说明结果：`（旧文件已备份）` / `（未留底：设置里关闭了备份）`。

## 可搜索下拉（通用交互）

原来那些"一长串 `<select>`"全换成了 `App.searchSelect()`（`web/app.js`，可复用）：

- **模块库**：资产包 / 分类 / 渲染范围；**结构编辑器**：打开结构（服务端 `/api/files?q=`）、模块装配的资产包筛选。
- 交互：输入即过滤（后接服务端搜索时 180ms 防抖）→ **向下展开**结果（`▾` 提示、选项右侧带 hint，如"42 个模块"）→
  ↑↓ 选择、回车/点击选中、Esc/× 清空、点外部收起；**不把没选中的输入当成条件**，避免筛选条件跟着半边词乱跳。
- 已删除："材料"筛选输入框（没用；后端 `material` 参数仍在，只有 UI 入口撤了）。
- 回归：UI 审计里"渲染范围下拉可搜 / 资产包下拉可搜索 / 分类 + 材料已移除 / 打开结构可搜索 / 资产包装配筛选"5 项。

## 编辑与画布

- **3D 准星**：鼠标悬停即显示将要操作的格子（绿=放置、红=擦除、蓝=替换、黄=吸管、
  橙=越界放置会自动扩容、深红=该方向没格子放不了），底部信息栏同时显示坐标与方块。
  鼠标→体素走 `VoxelViewer.raycast()`（Amanatides & Woo DDA，纯函数）：`cell` = 命中格，
  `place` = 射线经过的最后一个空格（恒 = `cell + normal`，即紧贴命中面的那一格），
  斜视角/棱边不再放偏或点不着（回归：`node tests/pick_raycast_smoke.js`）。
- **自动扩容**：在**数据范围**外放置方块不再被丢弃 —— 服务端按需向 +X/+Y/+Z 扩容后写入，
  前端整幅重载（上限 `MAX_SIDE=1024` / 40M 格，超出会明确报错）。扩容**不改画布框**。
  **−X/−Y/−Z 侧不行**（原点固定在 (0,0,0)）：准星标深红 + 状态栏说「放不上去」，
  不发无效请求（服务端也会回明确报错而不是含糊的「空选区」）。
- **画布范围面板 = 「画布框」**：X/Y/Z 三条滑块（**左键拖动、右键点击直接输入数值**）+「应用范围」+
  「按内容收缩」。画布框是**逻辑画布 / 导出窗口**：
  - **缩小框不裁数据**（不再像以前那样直接丢掉框外内容、也不清撤销栈）；框外内容照旧显示/可编辑/可往里面放东西；
  - **保存时才裁**：保存 / 另存为 / 存为模块 都按画布框写盘（响应带 `cropped` 格数，弹窗会提醒）；
  - 框外有东西时：2D 压暗 + 青色虚线框、3D 青色线框、状态栏「画布框 …（框外 N 块，保存时裁掉）」；
  - 改框算一步操作，进操作日志（`画布范围`，可撤销/重做；只换框不碰数据）；
  - 放大框会把数据范围扩到框内；`fit` 按内容（非空气格 ∪ 模块包围盒）缩框，若有模块伸到框外只给警告不再报错。
- **画布框面板**：X/Y/Z 三条滑块（**左键拖动、右键点击直接输入数值**）+「应用范围」+「按内容收缩」。
  见上「画布范围面板 = 画布框」：缩小只改框不裁数据，保存时才按框裁。
- **打开闸门**：超过阈值直接拒绝并给出提示（默认 24 MB / 20,000,000 格；超过 8,000,000
  格给黄色告警）。环境变量可调：`STRUCTWORKSHOP_MAX_STRUCTURE_MB`、`STRUCTWORKSHOP_MAX_STRUCTURE_CELLS`、
  `STRUCTWORKSHOP_WARN_STRUCTURE_CELLS`，**改完无需重启**（每次请求读取）。
- 静态资源已设为 `no-cache`，避免浏览器继续跑旧 JS。
- **方块面板（当前方块）**：贴图网格（1180 个方块，贴图来自 `block_catalog.json` 的 `textures`，
  用 `/api/asset?rel=textures/<ref>.png` 懒加载；草地/树叶/水等群系染色块叠 `mix-blend-mode: multiply`，
  失败退平均色）+ **17 个分类芯片**（全部/功能方块/石砖/混凝土/陶瓦/木材/金属/玻璃/羊毛织物/灯具发光/
  植物/半砖/楼梯/墙栅栏/门窗/告示牌旗帜/其他）+「只看完整方块」+ 本地搜索（**id + 中文别名**：烟熏炉/高炉/
  侦测器/楼梯/蜡烛/头颅…）+ 最近 12 个。**搜索框只是筛选器**：选完自动清空。HTTP：`GET /api/blocks/picker`。
  分类口径：材质家族（`family_index.json`）只看**贴图**，所以磁石/红石火把会被归进「石砖」、
  侦测器/合成器/铁轨/按钮**根本没家族**；现在按**方块名 + 目录数据**补语义芯片（`_semantic_kinds()`）：
  功能方块（红石元件 + 功能设备）、灯具发光（`emissive` 110 个，含全 17 种蜡烛）、植物、告示牌旗帜、门窗；
  **每个方块至少一个芯片**（剩 74 个进「其他」），且能放的全在（水/岩浆/火/刷怪笼/宝库/头颅；
  只排除空气系/屏障/光/传送门/命令块 `PICKER_EXCLUDE`）。调色块：旗帜/头颅用游戏里真的那张
  （`entity/banner/banner_base` + 染料染色 / `entity/<mob>`）。回归：`python tests/picker_smoke.py`。
- **新建 / 空画布（不自动建）**：工具栏「新建」（默认 16³，可改尺寸/名字）→ 无路径空画布，保存走「另存为」；
  **打开编辑器时不再自动建画布**（没开文件就是空的，状态栏提示去「打开结构…」/「新建」）。
  空画布没有实心块可采，所以 3D 放置有退化落点 `VoxelViewer.firstInBox()`（射线进画布的第一格，
  准星同样预览），第一块也能直接放；**射线没真的穿过画布时一律不给落点**（否则鼠标在建筑外的背景上
  也会夹出一个边界格：绿框长在建筑上、点下去放一块被邻居完全包住的方块 → 看着「放了没渲染」）。
  HTTP：`POST /api/structure/new {size, name}`。
- **接口编辑**（模块特有）：模块属性窗口里有**接口编辑器**——列表（形状/面/id/类型/尺寸）+ 表单
  （面、**形状**、位置、id、类型、标签）。**形状可选**：`矩形（两点）`（两个对角点的坐标）或
  `圆形（圆心 + 直径）`（圆心坐标 + 直径 d 格）；圆存的是「圆心 + 直径」，引擎匹配仍按**外接矩形**
  ——接口只是给装配/AI 的**参考**，实心空心都无所谓。定接口四招：**「接口」工具**在 3D 同一个面上
  点两个角（矩形；圆形时第一个点定圆心、第二个点让圆内接那个框，青色预览框/圆环）、直接填数字、
  **「从框选」**、或**「扫空洞」**（扫当前面上的非实心连通块，把最大的开口填进来）。保存走
  `PATCH /api/modules/<id>`（服务端校验面/形状/尺寸/类型）；类型除门/通道/楼梯/竖井/红石/水电接口外，
  还有 `vent`（风口/风道，2×2 通风管那种）与 `window`。约定与装配引擎一致：
  `east/west` → origin=[y,z]、`north/south` → origin=[y,x]、`up/down` → origin=[x,z]，size=[尺寸1,尺寸2]。
  回归：UI 审计 6 项（加载/两点取矩形/六面换算/扫空洞/形状切换与圆心换算/保存写回）+ `tests/library_smoke.py`。
- **标签 = 属性表（Obsidian 式）**：模块库抽屉与编辑器「模块属性」里的标签框是**多行属性表**：
  第一行默认是 `tag: a; b; c`（纯标签，`;` 分隔），其后可以自己加属性行 `属性: 值1; 值2`——
  落盘时变成命名空间标签 `属性:值`（存储仍是扁平 `spec.tags`，`:` 语义不变，标签树照旧按命名空间分组）。
  空行忽略、`#` 开头是注释；旧格式（逗号分隔的一整行）粘进来也能识别。
  解析/序列化：`App.parseProps()` / `App.propsText()`（`packages/mcstudio/web/app.js`）。
- **模块属性（左侧）**：打开 `packs/<包>/modules/…/<id>.schem` 时**左侧**出现「模块属性」窗口——
  预览图 + 包/分类/尺寸/方块/朝向 + **描述/分类/标签/备注**（「保存属性」→ `PATCH /api/modules/<id>`
  写回 `.module.json`）+ 接口表（附接口校验报错）+「重渲染预览」（与模块库同一条串行队列）。
  打开非模块文件时自动隐藏；脚本入口 `Editor.refreshModulePanel()` / `Editor.moduleRefFromPath(path)`。
- **朝向 / 半砖 / 旋转**：带 `facing`/`rotation`/`axis`/`half`/`type` 的方块选中后出现控件——
  **半砖**（下/上/双层＝`type`）、**上下**（楼梯 `half=bottom|top`，门的 upper/lower 不算）、
  `facing` 四/六向、`rotation` 16 档 22.5°（`0=南 4=西 8=北 12=东`，含 ↺/↻）、`axis` X/Y/Z；
  控件底部列全部属性值读数，改水没水/shape 用下方属性下拉。**「跟随视角」默认开**：
  放置时按相机视线定向（楼梯=视线方向；墙上牌子/火把=点击面；箱子/炉子/机关=朝放置者；
  告示牌/旗帜=正面朝你；原木=按点击面定轴）；**R / Shift+R** 手动转一格（手动会关掉「跟随视角」）；
  脚本可用 `Editor.autoProps(hit)` / `Editor.stateForPlace(hit)`。
- **搜索是精确优先**：`/api/blocks` 按「完全相同 → 前缀 → 包含」排序（旧行为是纯子串 + 字母序截断，
  导致点 `stone` 拿到 `blackstone`）；客户端找不精确匹配时不会偷用列表第一项，拿不到就明确报错。
- **编辑工具 × AXIOM 工具互斥**：同时只能武装一个。选 AXIOM 工具 → `E.tool='ax'`、编辑工具按钮退位、
  「方块更新」开关隐藏、提示写“AXIOM 工具接管 3D 左键——编辑工具已停用”；选放置/擦除/替换/吸管/移动 →
  自动 `AX.spec = null`（面板状态栏写“已取消”）。**「框选」例外**：两边共用的选区模式（sel 类工具靠它框范围），不算冲突。
- **掩码下拉**：AXIOM 面板的掩码输入框是与其它选择栏同一套质感的自定义下拉（预设 + 语法速查，输入即筛选、
  点一下填入、Esc/点外面关闭、点「应用」自动收起）——不再是原生 `datalist`。
- 画笔（放置/擦除/替换）作用于**基地层**；如果那一格被**模块实例**盖着，改动会**就地记到那个模块实例上**
  （前端提示「压在模块上的 N 格已就地改在模块实例上（跟着模块走）」）——模块与编辑**不互斥**，
  不用先「固化装配」；要直接改模块文件本身也可以用「在编辑器中打开」+ 「存为模块」。
- **方块更新模拟（连接状态）**：选「放置」（或擦除/替换）后，工具窗口下出现
  **「方块更新（按邻居重算连接）」开关**（默认开，只对这三种笔刷工具显示）：
  - 开着时：每次落笔/拖笔完成后，服务端把**改动区外扩 1 格**范围内不完整方块的状态按邻居重算
    （墙的四向+`up`、栅栏、铁栏杆/玻璃板、楼梯 `shape`），邻居一起进撤销与操作日志；
    关掉则只写这一格（旧行为）。
  - **「重算连接」按钮**：给当前框选（未框选则整幅）补一次历史状态——切片/导模来的模块
    四向属性往往是过期的，用这个一键补齐；没有可改的格子时**不进历史也不标脏**。
  - Axiom 工具面板的「应用」也读这个开关（把方块换成 `glass_pane` / `iron_bars` 时会自动连起来）。
  - 依据：`mckit/update.py`（纯函数 `update_volume` / `updated_copy`）；HTTP `update:true` 或
    `{"type":"update"}` 操作；渲染侧 `mcrender.cli --update-states`（只读）。细节见
    `minecraft-block-models` skill。

## 模块装配（拼装）

- 装配模型 = **基地层 base + 实例清单 placements**；体素始终是两者按顺序合成的结果，
  因此移动/旋转/删除模块不会留下“鬼影”。
- 多模块导入：模块搜索框下方可勾选多个模块 →「导入所选」，自动沿 +X 顺序拼接
  （每个接在上一模块右边）。
- 成品清单：「成品里的模块」列出每个实例；点击选中（3D 线框 + 2D 脚框高亮），
  悬停可预览；选中会自动切到「移动」工具。选中后出现 X/Y/Z 坐标滑块
  （左键拖动、右键输入数值，与 3D 拖动实时联动）。
- **Axiom 式 gizmo**（选中模块即出现，随镜头缩放保持屏幕尺寸恒定）：
  - 半透明选框 = 模块包围盒；三色实心箭头 = **红 X / 绿 Y / 蓝 Z 拖动平移**（1 格步进）；
  - 三个同色圆环 = **绕该轴 90° 步进旋转**（箭头方向留缺口，避免和轴拖抢）；
  - 悬停时对应手柄高亮；拖动时以按下瞬间的中心为参考，不会因 gizmo 跟随而漂移。
  - 旋转后模块保持**包围盒中心不动**；X/Z 轴旋转会置换尺寸（写入 `rotx`/`rotz`）。
  - 另有：2D 俯视图拖脚框、方向键（←→ X / ↑↓ Z / PgUp·PgDn Y，Shift ×4）、
    「定位」输入绝对坐标、「⟲ 旋转90°」按钮（绕 Y）。
- **接口吸附（磁吸）**：勾选「接口吸附」后，移动/旋转结束时只在**附近 2.5 格**内
  自动对齐到其它模块的对侧兼容接口（south↔north / east↔west），超出半径则自由摆放；
  API 可用 `snapRadius` 调整。绕 X/Z 翻转后接口语义不成立，自动跳过吸附。
- 持久化：保存 `.schem` 时写 `<name>.layout.json`（边车，权威）+ Sponge `Metadata`
  里的 `StructworkshopModules` JSON 字符串；`.litematic` 只靠边车。重新打开后自动还原清单
  （模块文件必须在资产包内；缺失的会列在 `restore.missing`）。
- 基地层剥离：打开带清单的文件时，模块占据的区域从基地层“掏掉”（模块底下的原始方块
  无法还原，这是扁平体素格式的固有代价）。
- 「固化装配」把当前成品体素变成新的基地层并清空清单；若不想保留清单但体素不变，用它。

## 典型工作流

**A. 看模块 / 挑素材**
```bash
python -m mcstudio serve --open            # 打开 http://127.0.0.1:8617/
```
库页签 → 搜索/筛选 → 点卡片看 3D → 抽屉里改标签与接口 → 保存。
缺预览图：库工具栏「为缺图的渲染」排队；批量打标用标签属性表。

**B. 编辑一栋建筑**
```bash
python -m mcstudio serve                    # 然后 URL 带 ?view=editor&open=<仓库相对路径>
```
打开 → 用 29 个工具打磨（面板里选工具 + 掩码）→ 墙/栅栏连接不对手动「重算连接」→
存盘（自动备份）→ 需要就「另存为模块」沉淀进资产包。

**C. 拼装**
导入多个模块 → 拖 gizmo 平移/旋转 → 接口磁吸对齐 → 固化装配或直接存 `.schem`。

**D. 改完资产包**（重要）
```bash
python -m mccore.bootstrap                  # 重建 pack.json / catalog / 索引
```
在 UI 里改了模块元数据/预览图，服务端会自己重刷；但在 UI **外面**动了文件就得手动跑。

## 安全红线

1. 保存前自动备份旧文件到 `.cache/backups/`（结构）与 `.cache/backups/modules/`（删除模块）。
2. 可读写路径白名单：`packs/ builds/ compositions/ tests/fixtures/ dist/ .cache/mcstudio`。
3. 模块存储格式是 `.schem`（Sponge v2），`.litematic` 只作为兼容读写格式。
4. 一次编辑 op 批次 = 一个撤销步；大范围 replace 会返回整块 region，注意结构体积。
5. 3D 资源首次使用需联网（mcmeta 镜像），之后走 `.cache/mcassets` 离线；取不到资源自动纯色模式。
6. 不要在 UI 打开时改同一文件的另一份（服务端会话持有内存副本，保存以会话为准）。
