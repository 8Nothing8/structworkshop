# Minecraft 投影渲染器（真实方块模型 + 真实贴图）

把 `.litematic` / `.schem` 渲染成 PNG。和之前的纯色体素渲染不同，这个渲染器会：

- 从 `.litematic` 的 `MinecraftDataVersion`(或 `.schem` 的 `DataVersion`)自动选 Minecraft 版本（如 4903 → 26.2）；
- 从互联网（[mcmeta](https://github.com/misode/mcmeta)）下载**方块映射表 + blockstates + 方块模型 + 16×16 贴图**并缓存；
- 按真正的 blockstate / 模型继承 / multipart / 旋转 / UV 规则，把每个 palette 条目展开成
  **真实的方块几何**——半砖就是半高、楼梯就是台阶、墙有中心柱和侧翼、玻璃板是薄片、门是两格；
- 用 numba 软件光栅器做正交/透视投影、深度缓冲、贴图采样、AO、透明混合、泛光、超采样。

## 分层位置

| | |
|---|---|
| **上游** | `mccore`（结构读写、调色板）；`mckit` **只在 CLI 层**用于 `--update-states`（渲染前按邻居补连接，只读） |
| **下游** | `mcmaterials`（借资源缓存读贴图）· `mcslice`（`learn --preview` 出调色板图）· `mctools`（`--render` 自检出图）· `mcstudio`（贴图与模型烘焙） |
| **不做** | 不做体素工具、不做质检、不写结构（渲染是**只读**的，`--update-states` 也只影响本次渲染） |

## 快速开始

```bash
# 在仓库根下执行（示例用 tests/fixtures/small_house.schem 这种相对路径）
# 三视图（等轴 / 正视 / 俯视），3 像素/方块
python -m mcrender.cli tests/fixtures/small_house.schem --views iso,front,top --scale 3

# 高清单张（透视 + 3 倍超采样）
python -m mcrender.cli build.schem --azimuth 35 --elevation 22 \
    --proj persp --scale 4 --ssaa 3 --out hero

# 先下载好全部资源，之后可离线渲染
python -m mcrender.cli build.schem --prefetch
python -m mcrender.cli build.schem --offline --views iso

# 剖切看内部（隐藏 x < 112 的方块）
python -m mcrender.cli build.schem --views right --cut x=112

# 检查 palette 里每个方块解析成什么
python -m mcrender.cli build.schem --list

# 导出该投影的方块映射表（属性/默认值/包围盒/贴图）
python -m mcrender.cli build.schem --index palette_index.json
```

## 命令行参数

| 参数 | 说明 |
|---|---|
| `--views` | 逗号分隔：`iso` `iso2` `iso3` `iso4` `front` `back` `left` `right` `top` `hero` `hero2`，预设组 `all` `orbit` `elevations`，或 `az:el`（如 `35:22`） |
| `--azimuth` / `--elevation` | 单张自定义角度（覆盖 `--views`） |
| `--scale` | 像素/方块（默认 3） |
| `--ssaa` | 超采样倍数 1–4（默认 2） |
| `--proj` | `ortho`（默认，等轴测）或 `persp`（透视） |
| `--background` | `sky`（渐变天空，默认）/ `dark` / `transparent` |
| `--bloom` | 泛光强度（默认 0.5，0 关闭） |
| `--no-ao` | 关闭整方块的环境光遮蔽 |
| `--cut AXIS=COORD` | 隐藏该轴上坐标小于 COORD 的方块；可重复 |
| `--version` | 强制资源版本（如 `1.21.4`），默认按数据版本自动映射 |
| `--cache` | 资源缓存目录（默认 `.cache/mcassets`） |
| `--offline` | 只用缓存，不联网 |
| `--prefetch` | 只下载资源不渲染 |
| `--list` | 列出 palette 解析结果并退出 |
| `--index JSON` | 导出方块映射表并退出 |
| `--sheet` | 额外输出多视图拼版 |

输出文件名：`<out>_<view>.png`（`--out` 默认是输入文件名 + `_r`）。

## 资源从哪来 / 缓存

| 内容 | 来源 |
|---|---|
| 方块映射表（属性 + 默认值） | mcmeta `summary` 分支 `blocks/data.json`（按版本 tag `<版本>-summary`） |
| blockstates / 模型 / 贴图 | mcmeta tag `<版本>-assets-tiny`（从官方客户端 jar 提取的资源包） |

- 缓存目录：`.cache/mcassets/<版本>/`，含 `blockstates/`、`models/`、`textures/`、
  `_blocks_summary.json`、`_missing.json`。
- 三个镜像自动切换：jsDelivr → raw.githubusercontent → raw.githack；
  某个镜像网络失败会在本次会话中跳过，404 则直接判定资源不存在。
- 首次渲染一个投影大约要下载 100–400 个小文件（几 MB），之后完全离线。
- 想换镜像或加镜像：改 `packages/mcrender/assets.py` 里的 `MIRRORS`。
- 资源缓存的**公开读接口**给别的包用：`Assets`（`mcmaterials.textures` 就是这么取贴图的）。
- 与网页端自研 mesher 的关系：**两套并存** —— 本包是出图用的 CPU 光栅器（语义全：流体/方块实体/AO/泛光），
  `mcstudio/web/renderer3d.js` 是交互用的快速 mesher（详情见 `packages/mcstudio/README.md`）。

## 整方块（遮挡 / AO）的判定

从 2026-09 起，遮挡与 AO 看的是**方块体自身**的 6 个面，而不是「模型恰好 6 个四边形」：
草方块之类带 overlay 的方块（6 个整面 + 4 个侧面 overlay，共 10 个四边形）以前既不算遮挡
（邻面不剔除）也不吃 AO —— 一块草地的每格都会画全部 6 面，而且更平。现在
`ModelResolver` 会把方块体（`cube_textures`）与附加面分开：遮挡看 `cube_textures` 的 alpha，
快速路径仍要求严格 6 面。其他带 overlay/附加面的方块（雪层、双面植物之类）同理受益。

## 不完整方块（半砖/楼梯/墙/栅栏/玻璃板/门/活板门…）

这是本渲染器的重点。几何来自真正的方块模型，所以：

- 楼梯的 `shape=inner_left/outer_right`、墙的 `north=tall/low`、玻璃板的四向连接、
  门的上半下半，都会按 palette 里的属性真实渲染；
- 属性缺省时用**默认状态**，所以“墙不连、玻璃板只有柱子、门只有下半”这类问题
  是投影数据本身的问题，不是渲染器的问题。

写投影时怎么把属性写对，见项目 skill：

```
skills/minecraft-block-models/SKILL.md
```

其中 `packages/mckit/connect.py` 可以根据邻接关系自动算出墙/栅栏/玻璃板/楼梯/门的属性。

## 渲染管线（排查用）

```
read_structure                     读取 palette + (y,z,x) 体素数组（.schem / .litematic）
  └─ ModelResolver.resolve_block   每个 palette 条目 -> BakedBlock（真实几何）
       ├─ blockstates variants/multipart 匹配属性
       ├─ models parent 链合并、texture 变量解析
       ├─ element 旋转 / rescale、面 uv / uv 旋转、cullface
       └─ 识别“整方块” -> 走快速路径
  └─ TextureAtlas                  全部贴图打包成图集（动画贴图取第一帧）
  └─ build_scene                   体素 -> 世界空间四边形
       ├─ 整方块：按方向批量提取可见面 + 逐顶点 AO
       └─ 非整方块：逐条目展开模型元素，按不透明整方块剔除 cullface
  └─ render_view
       ├─ 投影（正交/透视）+ 背面剔除 + 三角化
       ├─ numba 光栅器：深度缓冲、最近邻采样、cutout 丢弃、translucent 混合
       └─ 泛光 + 超采样降采样
```

## 性能参考（本机，Python 3.13 / numba 0.61）

> 两个大件是**基准体型**（不是任何具体作品）：225×320×225 的高塔与 512³ 的大体量，
> 用来说明「方块数 / 四边形数 / 首次（含下载）/ 热缓存」大概是什么量级。

| 结构 | 方块数 | 四边形数 | 首次（含下载） | 热缓存 |
|---|---|---|---|---|
| 方块画廊（80×6×23） | 61 | ~750 | ~25 s | <1 s |
| 高层塔楼基准（225×320×225） | 387 k | 670 k | ~30 s | 6 s / 2 视图 |
| 大体量基准（512×256×512） | 951 k | 2.72 M | ~34 s | 17 s / 1 视图 |

## 已知限制

- **实体方块（block entity）**：箱子、旗帜、头颅、潜影盒、装饰罐、钟、导管、铜傀儡等由游戏的
  特殊渲染器绘制，资源包里没有几何 —— 现在用 `tools/export_entity_models.js` 从 deepslate
  （MIT）导出的 `data/entity_models.json` 建真实几何（`--list` 里 `notes` 会写 `entity-model`）；
  床/告示牌在 1.21.4+ 已经是普通方块模型，走模型路径。导出表里没有的仍退化成纯色。
- **流体**：水/岩浆按 `level` 给高度（源 8/9、流动 (8-level)/9、下落 1.0），角点取相邻 3×3 的
  最大值，侧面用 `*_flow` 且贴图锚在液面；`waterlogged=true` 的方块额外补一层水面；
  水染群系水色 `#3F76E4`（岩浆贴图本身有色，不染）。见 `python tests/fluid_smoke.py`。
- 模型 `uvlock` 只做了近似；`weight` 随机变体取第一个；动画贴图取第一帧。
- 半透明排序是逐三角形的（极端交叠场景可能有轻微顺序错误）。
- 内存：场景几何约 110 B/四边形，三角化时再翻一倍；200 万四边形大约需要 1 GB 内存。
  超大投影可先用 `--cut` 剖切，或降 `--ssaa`。

## 文件

| 文件 | 作用 |
|---|---|
| `cli.py` | 命令行入口（`python -m mcrender.cli`）：视图解析、剖切、`--list` / `--index` / `--sheet` |
| `assets.py` | 资源下载 + 缓存 + 版本映射 + 映射表（`Assets` / `auto_assets()` / `MIRRORS`） |
| `model.py` | blockstate / 模型解析 → 四边形（`ModelResolver`，可单独 CLI dump 几何） |
| `renderer.py` | 贴图图集 + 几何装配 + numba 光栅器 + 相机（`build_scene` / `render_view` / `Camera` / `TextureAtlas`） |
| `entity_models.py` | 方块实体几何表（箱子/床/告示牌/旗帜/头颅/潜影盒/装饰罐/钟/导管/铜傀儡） |
| `block_index.py` | 生成方块索引（`all_blocks.json` / `incomplete_blocks.json`） |
| `gallery.py` | 生成覆盖各类非整方块状态的画廊结构（视觉回归的载体） |
| `sheet.py` | 画廊逐列特写拼版（接触表） |
| `legacy_voxel.py` | 纯色体素正交渲染器（老路径，仍用于极快预览） |
| `mccore/structure_io.py` | 双格式分派：`read_structure()` / `write_structure()`（`.schem` 存储 + `.litematic` 兼容） |
| `tools/export_entity_models.js` | 从 deepslate（MIT）导出方块实体几何到 `data/entity_models.json` |
| `tools/render_iso_alpha.py` | 透明底多视图出图（发布用） |

## 测试

```bash
python tests/render_bg_smoke.py      # 背景（深底/黑底/白底/透明）与液体流面
python tests/fluid_smoke.py          # 流体高度/流动贴图/水色染色/岩浆不染
python tests/entity_models_smoke.py  # 方块实体几何表规模与解析
python tests/blockstate_smoke.py     # 渲染前按邻居补连接（--update-states，只读）
python tests/previews_smoke.py       # 模块预览图批量渲染（队列）
node tests/renderer_ab.js [chrome] <url> [截图目录]   # 与网页端渲染器 A/B
```
