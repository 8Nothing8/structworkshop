# mcmaterials — 方块材质实验室（层 3 · 应用）

把方块贴图变成 **可看的图表 / 可查的色表**，并按参考照片反查方块，用于 MC 建筑的选材、
做旧、渐变与配色纪律。配套 skill：`skills/minecraft-material-lab/`。

```bash
python -m mcmaterials catalog                    # 全量方块材质目录（颜色量化/透明度/质感）
python -m mcmaterials chart                      # 材质图表 -> skills/minecraft-material-lab/data/charts/
python -m mcmaterials colors --md out.md         # 平均色表 (JSON/MD)
python -m mcmaterials match ref.jpg --crop 0.2,0.2,0.8,0.7 --colors 5 --topk 4 --pool walls
python -m mcmaterials ramp concrete --top 12     # 明度排序（做渐变）
python -m mcmaterials families                   # 家族清单
python -m mcmaterials compare 出图.png 参考.jpg   # 渲染图 vs 参考图的配色审计
```

## 分层位置

| | |
|---|---|
| **上游** | `mcrender`（借它的资源缓存与 `Assets` 读贴图/方块模型引用；贴图缺了自动走镜像下载） |
| **下游** | 无（叶子应用）。产出给人看，也给 AI 选材时查 |
| **不做** | 不生成建筑、不改结构文件、不做渲染（只读贴图 + 出图表） |

## 模块

| 文件 | 作用 |
|---|---|
| `catalog.py` | **全量方块材质目录**：每块 → 平均色/HSV/明度/透明度/质感指标 → `block_catalog.json` + `block_catalog.md`；带稳健镜像抓取（`prefetch_robust` / `fetch_into_cache`） |
| `textures.py` | 从 `.cache/mcassets` 读方块贴图（`block_texture` / `average_color` / `luminance`） |
| `chart.py` | 拼贴图表：贴图 + 名称 + 平均色块 + 明度（家族图表 / 分页拼版 / 外墙明度排序） |
| `match.py` | 参考照片量化主色 → 最近方块（加权 RGB 距离）+ 对比报告图 |
| `compare.py` | 出图 vs 参考图的**配色审计**（指标 + 并排对照图） |
| `families.py` | 家族定义（concrete/stone/roof/wood/metal/glass/detail/retrofit/interior）与做旧组合建议 |
| `net.py` | 独立于会话缓存的镜像抓取（多个 mcmeta 镜像 + 重试） |
| `cli.py` | 命令行入口（`catalog` / `colors` / `chart` / `match` / `profile` / `compare` / `ramp` / `families` / `check`） |
| `__main__.py` | `python -m mcmaterials` 的入口垫片 |

## 输出

- `skills/minecraft-material-lab/data/block_colors.json` — 候选方块的 avg/hex/明度
- `skills/minecraft-material-lab/data/block_catalog.json` / `.md` — 全量方块材质目录
- `skills/minecraft-material-lab/data/charts/*.png` — 家族图表 + 明度排序 + 参考匹配报告

## 约定 / 不变量

* 颜色一律按**平均色 + 明度**判，不做感知色差（Oklab 只在参考匹配的距离里用）。
* 「做旧/渐变」的推荐来自 `families.py` 的人工编排，不是算法聚类 —— 要改风格先改那张表。
* 只读 `mcrender` 的资源缓存；贴图缺失时才联网，且走独立镜像链（不受会话缓存影响）。

## 测试

```bash
python -m mcmaterials check                      # 目录/图表一致性自检
python tests/studio_tools_smoke.py               # 工作台侧引用（间接）
```
