---
name: minecraft-asset-library
description: structworkshop 资产库检索与分发技能。资产按包组织在 packs/<id>/，每包有 Tier-0 紧凑目录 catalog.md、Tier-1 结构化索引 packs/index.json、预览图 previews/ 与可分发 zip（python -m mccore.pack export/import）。当 AI 需要从素材库找模块（按类型/标签/材料/体量/接口）、看模块预览图、新增/导入/导出资产包、校验包完整性时使用。命令走 `python -m mccore.pack ...` 与 `python -m mccore.module_lib ...`。
---

# 资产库 · minecraft-asset-library

> 两个仓库世界观：`packs/<id>/` 是可分发**资产包**；`compositions/<id>/` 是**组合方式**（skill）。
> AI 先查库再动手：**Tier-0 全览 → Tier-1 过滤 → 看预览图 → 调用/装配**。

## 资产包结构

```
packs/<id>/
├── pack.json          # manifest: id/version/author/license/mc_versions/
│                      #   engine_requires/dependencies/provides/previews/files(sha256)
├── catalog.md         # Tier-0 紧凑目录（scan 自动生成）
├── modules/<category>/<name>.schem (+ .module.json)
├── styles/*.json      # 风格包（可选）
├── previews/<id>.png  # 模块预览缩略图（iso 渲染 + 256px 缩略）
└── module_gen.py      # 模块生成器（可选）
```

## 两级检索

### Tier-0 · 紧凑目录（给 AI 上下文全览）

```bash
python -m mccore.pack catalog            # 全库 -> packs/catalog.md
python -m mccore.pack catalog modern-arch     # 单包 -> packs/modern-arch/catalog.md
```

每行一模块：`id | 类别 | 尺寸 | 方块数 | 主材料 | 接口数 | 标签 | 描述 | 预览图`。
几百个模块也只需几 KB，直接放进对话上下文。

### Tier-1 · 结构化过滤（精确查询）

```bash
python -m mccore.pack search                  # 全库
python -m mccore.pack search modern-arch      # 限定包
python -m mccore.pack search modern-arch --query 走廊
python -m mccore.pack search modern-arch --category props --no-port
python -m mccore.pack search modern-arch --material sea_lantern --json
```

等价命令：`python -m mccore.module_lib list --pack <id> --tag ... --material ... --min-blocks ... --max-blocks ... --has-port --json`

### 看预览（视觉确认）

```bash
python -m mccore.pack previews modern-arch    # 给缺图的模块渲染 iso + 缩略图
# 之后用 read/pi 打开 packs/<id>/previews/<module>.png 给视觉模型确认
```

索引（`packs/index.json`）已含 `preview` / `preview_full` 字段，检索结果可直接给出图片路径。

## 分发（export / import）

```bash
python -m mccore.pack export modern-arch      # -> dist/modern-arch-1.0.0.zip
python -m mccore.pack export modern-arch --out <导出目录>

python -m mccore.pack import ./dist/modern-arch-1.0.0.zip      # 校验后安装
python -m mccore.pack import ./x.zip --dry-run                 # 只校验
python -m mccore.pack import ./x.zip --force                   # 覆盖（旧包备份到 .cache/backups）
```

导入时自动：校验 files 哈希 → 检查依赖 → 装进 `packs/<id>/` → 重建索引 + catalog。

## 批量导入模块投影

把一整个目录的 `.litematic` 批量入库（自动读尺寸、生成 spec、重名去重、内容相同跳过）：

```bash
# 先看清单（不落盘）
python -m mccore.pack import-modules <你的投影目录> --pack modern-arch --dry-run

# 正式导入：子目录名 → 分类；--flat 则全部放进 imported/
python -m mccore.pack import-modules <你的投影目录> --pack modern-arch --tags "imported"
python -m mccore.pack import-modules <你的投影目录> --pack modern-arch --category props --flat

# 可选：移动而非复制；把 spec 也写进 litematic Metadata
python -m mccore.pack import-modules <你的投影目录> --pack modern-arch --move --embed
```

导入后自动 `scan`（索引 + manifest + catalog）。再补两件事：

```bash
python -m mccore.pack previews modern-arch     # 1) 出预览图
# 2) 用编辑器补 sidecar 里的 description/tags/ports（摆件可留空）
```

## 校验与维护

```bash
python -m mccore.pack scan        # 重建 index + 刷新所有 pack.json/catalog
python -m mccore.pack validate    # 完整性：schema / 模块 spec / 校验和 / 依赖 / 预览
python -m mccore.pack list        # 包清单（版本/模块数/风格数/预览数）
python -m mccore.module_lib inspect mw_panel_fixed
```

## 标准 AI 工作流

1. `pack catalog` 拿全库目录（Tier-0），心里有数有哪些包/模块。
2. `pack search <pack> --query ...`（Tier-1）精确过滤出候选。
3. 打开候选模块的 `previews/<id>.png` 让视觉模型确认风格/体量是否合适。
4. 合适 → `module_lib inspect <id>` 拿接口/规格，交给 `mccore.assemble` 或 `compose` 使用；不合适 → 用生成器或 `mcslice.slice` 切块补库。
5. 新模块入库后跑 `pack scan`，缺图跑 `pack previews <pack>`。

## 相关

### 库里没有合适的模块 → 从已有建筑里造

```bash
# 把一栋已有建筑切成可复用模块 + 自动识别接口（门/楼梯/窗/通道）
python -m mcslice.slice "builds/天际线办公系列/models/办公板楼.schem" \
    --out packs/my-pack/modules --pack my-pack --min-blocks 40
# 从一栋建筑学出「风格包」（比例/材质/节奏），给新建筑当参照
python -m mcslice.learn "builds/天际线办公系列/models/高层写字楼.schem" --out styles/x.json

# 切块/导入之后**必须**重建派生产物，否则索引、catalog、sha256 全是旧的
python -m mccore.bootstrap
```

- **方块材质色表 / 参考反查 / 做旧规则**：`skills/minecraft-material-lab/`（`python -m mcmaterials chart|match|ramp`）
- **现代建筑分类体系与资产包规划**：`references/modern-architecture-taxonomy.md`（12 个分类维度 / 流派谱系 / 地域变体 / 高层结构与形态 / 立面体系 / 标签规范 / pack 规划）
- **现代建筑系列提示词**：`compositions/modern-skyscraper/`（12 份提示词：通用 + 九个流派，`python -m mccore.compose modern-skyscraper --list-prompts`）
- 资产包与两级检索：见上方命令；模块/接口/装配：`skills/minecraft-modular-building`
- 建筑审美/质检/视觉评审：`skills/minecraft-building-design`
- 方块状态：`skills/minecraft-block-models`
- 规范依据：`skills/minecraft-building-codes`（`kb/` 完整规范语料 + `python -m mckb` 检索）
- 参数化结构项目：`compositions/<id>/SKILL.md`

> 选模块时如果涉及尺寸（走道净宽、门洞、梯段宽…），先 `python -m mckb readlist --profile <项目类型>`
> 读完整条款再定，不要凭模块尺寸反推规范。