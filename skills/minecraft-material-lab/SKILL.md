---
name: minecraft-material-lab
description: MC 方块材质实验室 — 把方块贴图变成「可看的色表/材质图表」，按参考照片的主色反查最接近的方块，并给出做旧/渐变/混搭规则。当你要选外墙/屋面/金属/室内材质、想把实拍参考（老照片/效果图）翻译成 Minecraft 方块、想避免颜色冲突或做旧做渐变时使用。命令走 `python -m mcmaterials ...`（chart / colors / match / ramp / families）。
---

# 方块材质实验室 · mcmaterials

> 选材质不靠猜：先看图表（贴图 + 平均色 + 明度），再按参考照片反查方块，最后按规则做混搭/渐变/做旧。

## 铁律（必须执行）

1. **有参考图时，任何定色/改色/交付前，必须把成品渲染与参考原图比较颜色**：
   ```bash
   python -m mcmaterials compare 成品渲染.png 参考图.jpg        --crop-a L,T,R,B --crop-b L,T,R,B --out data/charts/compare_xxx.png
   ```
   输出：并排对照图 + 主色/平均明度/明度标准差/暖冷比 + 差值（mine-ref）。
2. **比较结果写进交付说明**；没有比较记录的配色不算完成。
3. 比较不通过就继续改，不许用“看起来差不多”代替比较。

## 工具

| 命令 | 作用 |
|---|---|
| `python -m mcmaterials catalog --all` | **全量方块目录**（每个方块：颜色量化 / 透明度 / 质感指标 / is_full），写 `data/block_catalog.json`+`.md` |
| `python -m mcmaterials colors --md ...` | 从目录导出色表（均色/明度/粗糙度/纹路/alpha） |
| `python -m mcmaterials chart [--all] [--family X] [--class rough]` | 材质图表：每格 = 贴图 + 名称 + 平均色 + 明度；家族/类别/外墙明度排序，可分页 |
| `python -m mcmaterials match IMG --pool facade --class rough` | **参考照片 → 主色 → 最接近方块**（可按家族/外墙/质感类别过滤） |
| `python -m mcmaterials profile IMG` | 参考图颜色 + **质感画像**（边缘/粗糙度/纹路方向）→ 同类别方块建议 |
| `python -m mcmaterials compare 成品.png 参考.png` | 并排 + 主色/明度标准差/暖冷比 + 差值（交付审计） |
| `python -m mcmaterials ramp <family>` | 按明度排序（做渐变） |
| `python -m mcmaterials check` | 目录自检：块数/有颜色/完整块/透明度/质感类别分布/家族覆盖/木种×台阶楼梯告示牌 |
| `python -m mcmaterials catalog --recompute` | 不抓图，仅用贴图缓存重算目录（改了分类/字段后秒级刷新） |
| `python -m mcmaterials families` | 家族清单（策划家族 + 自动家族 + facade） |

图表（AI 直接看图选材质）：

```
data/charts/concrete.png        data/charts/stone.png       data/charts/roof.png
data/charts/wood.png            data/charts/metal.png       data/charts/glass.png
data/charts/detail.png          data/charts/retrofit.png    data/charts/interior.png
data/charts/walls_by_luminance.png   ← 外墙候选按明度排序（做渐变）
data/charts/match_*.png              ← 参考照片匹配报告
```

## 标准工作流（参考 → 方块）

```bash
# 1) 参考照片找主色并反查方块（可裁采样区域：L,T,R,B 0-1）
python -m mcmaterials match ref.jpg --crop 0.2,0.2,0.8,0.7 --colors 5 --topk 4 \
    --out data/charts/match_ref.png
# 2) 看图表确认质感（贴图是否太花/太蓝/太黄）
# 3) 用明度排序组"主色 → 过渡 → 深色做旧 → 勒脚"四段
python -m mcmaterials ramp stone --top 12
# 4) 写进生成器：混搭权重 + 噪声 + 竖向渐变 + 条状水渍（见下面的规则）
```

## 做旧 / 渐变 / 混搭规则（从赫鲁晓夫楼项目总结）

1. **先定基底家族，再做 2-4 档明度梯度**：例如暖浅灰外墙 =
   `light_gray_concrete`(基底) + `white_terracotta`(亮) + `smooth_stone`/`stone`(过渡) +
   `light_gray_terracotta`/`tuff`(中间调) + `gray_concrete`/`deepslate`(缝/污渍)。
2. **颜色不冲突 = 同一色温 + 低饱和**：暖灰墙不要混 `diorite`/`calcite`(偏冷白)、
   `sandstone`/`granite`(偏黄红)、`mossy_*`(绿，除非勒脚潮湿)、`black_concrete`(太黑)。
3. **做旧要"条状"不要"斑点"**：从楼板带/窗台向下 2-5 格连续替换成深色（水渍/霉），
   密度沿高度递减（底层脏、顶层亮）。
4. **勒脚要有过渡**：最底一排用 `tuff/cobblestone/light_gray_terracotta/brown_terracotta` 混搭，
   上一排再回到墙色，避免生硬分界。
5. **窗从外面要"深"**：用 `gray_stained_glass`（透光但外看偏深），避免室内彩色织物透出来。
6. **金属件低对比**：镀锌件用 `iron_bars`/`iron_trapdoor`/`light_gray_concrete`；
   铜件只做小面积（`exposed_copper` 是低饱和旧铜，`cut_copper` 太橙、`weathered/oxidized` 发绿）。
7. **外墙材质口径**：用 `is_full=true`（完整方块）——**玻璃/透明方块也可以用**（`tinted_glass`、
   染色玻璃、玻璃板等），只排除 `technical` 方块；透明、发光是否上墙由设计决定。
8. **同一表面的"质感类别"要一致**：`class` 混用要成组（例如 smooth 与 matte 可相邻，
   polished 与 rough 不要直接混贴），需要按 `grain` 对齐纹路方向（横向纹路不要和竖向纹路拼）。
9. **细节件优先用不完整方块**（但不是只能用）：格栅=`iron_trapdoor`/`repeater`、
   面板=`daylight_detector`、仪表=`comparator`、阀门=`lever`、按钮=`stone_button`、
   线缆=`iron_chain`、天线臂=`lightning_rod`/`end_rod`、烟囱口=`campfire`、
   检修台=`scaffolding`；大体积仍用整块（`light_gray_concrete`/`iron_block` 等）。

## 应用实例（本仓库）

- 资产包 `packs/soviet-khrushchyovka/`（30 模块，含 retrofit 件）用本 skill 的色表选材：
  外墙混搭写进 `compositions/soviet-khrushchyovka/build.py::dress_facades()`（噪声权重 + 竖向渐变 + 条状水渍 + 板缝）。
- 对比报告：`data/charts/match_panel_ref.png`、`match_street_ref.png`
  （参考照片主色 → `light_gray_concrete`/`smooth_stone`/`stone`/`andesite`/`white_concrete`）。

## 相关命令

| 命令 | 作用 |
|---|---|
| `python -m mcmaterials catalog` | 重建 `data/block_catalog.json` / `texture_stats.json`（要 mcassets） |
| `python -m mcmaterials colors` | 重建 `data/block_colors.json` |
| `python -m mccore.bootstrap --data` | 上面两条 + 其他方块数据表，一把重建 |
| `python -m mccore.bootstrap --check` | 派生产物是否最新（手改过就退码 1） |

## 文件

- `data/block_catalog.json` — **全量方块目录**（1196 块，1193 块有颜色：颜色量化/透明度/质感/is_full）
  - 完整方块 572 · 建材外墙池(facade) 250 · 透明方块 459 · 家族 39 个
  - 质感类别：grained 332 · cutout 242 · rough 199 · textured 161 · emissive 107 · matte 58 · smooth 43 · technical 33 · translucent 21
- `data/block_catalog.md` / `block_colors.json|md` — 可读表（均色/明度/粗糙度/纹路/alpha）
- `data/family_index.json` — 家族 → 方块清单（策划家族 + 自动家族 + facade）
- `data/texture_stats.json` — 贴图级缓存（增量刷新）
- `data/charts/*.png` — 材质图表（家族/类别/外墙明度分页/参考匹配/审计对照）
- `packages/mcmaterials/` — 工具源码（catalog/textures/chart/match/compare/cli/families）
