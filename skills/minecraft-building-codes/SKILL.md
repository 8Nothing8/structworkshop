---
name: minecraft-building-codes
description: 规范语料库检索技能（mckb corpus）。规范全文以 release 管理（manifest + source.pdf + full.md + chunks.jsonl），用 SQLite FTS5/BM25 做条款级检索；当生成/修改建筑需要符合规范的尺寸依据（防火分区、安全出口与疏散、走道净宽、楼梯与电梯、采光、无障碍、超高层避难层等），或要查阅某部标准原文时使用。命令走 `python -m mckb ...`：readlist 生成应读清单、search 定位条款、read 打印完整章节、verify/lint 校验语料完整性。
---

# 规范语料库 · minecraft-building-codes

`kb/` 是只收**完整文档**的语料库：检索只负责**定位**，正文永远是
`kb/releases/<release>/full.md` 的完整章节。**禁止用摘录、二手结论或记忆里的条款号代替原文。**

## 工作流（写设计/装配计划前必做）

```
1. 看语料     python -m mckb catalog
2. 出清单     python -m mckb readlist --profile office --query "中庭 防火"
              → 每条形如 gb55037-2022 · 5.3 防火分区 · 5.3.1 · p.23 · L456-470
3. 读原文     对每条命中：python -m mckb read gb55037-2022 --section "5.3 防火分区"
              （或用 read 工具打开 kb/releases/<release>/full.md 的行号范围；
                读完整章节，需要时通读整篇）
4. 写依据     计划/设计说明里写「标准号 + 条款号 + 页码」，例：
              「走道净宽 ≥1.8m —— JGJ/T 67-2019 §x.x.x（p.xx）」
5. 复核       生成后再回读一遍相关条款，核对尺寸/数量（层数、出口数、梯段宽…）
```

## 命令速查

| 命令 | 作用 |
|---|---|
| `python -m mckb catalog` | 语料全览（Tier-0，release 一行） |
| `python -m mckb readlist --profile office [--query ...]` | 按项目画像列「应读文件+章节+行号」 |
| `python -m mckb search "避难层 设置" [--release X] [--limit N] [--json]` | 条款级检索，只返回位置 |
| `python -m mckb read <release> --section S \| --clause C \| --page N` | 打印完整章节/条款/页 |
| `python -m mckb verify [release]` / `lint` | sha256、页覆盖、chunk 一致性校验 |
| `python -m mckb audit [release]` | 逐页文本密度体检：哪些页值得重扫/复核（表/图/空） |
| `python -m mckb denoise [release…]` | 清理 full.md 里的水印噪声行并重切 chunk |
| `python -m mckb ingest … --text fix.md --text-source mineru` | 用外部 OCR（MinerU/VLM）文本入库，带页码校验 |
| `python -m mckb export --format jsonl\|llamaindex\|chroma\|txtai` | 导出给外部知识库 |

## 新增一部标准（完整文档流程）

```bash
python -m mckb fetch --id gb55031-2022          # 住建部公告附件（sources.json 登记）
python -m mckb fetch --url <公告页> --out kb/inbox/x.pdf   # 或任意公告页/直链
python -m mckb ingest kb/inbox/x.pdf --release gb55031-2022 \
    --code "GB 55031-2022" --title "民用建筑通用规范" --source-url "<url>"
python -m mckb scan && python -m mckb index && python -m mckb verify
```

- `ingest` 会把 PDF 原样存为 `source.pdf`，抽全文（无文字层自动 OCR）→ `full.md`（带
  `<!-- page N -->` 页码锚点）→ `chunks.jsonl`（条款/章节切片）→ `manifest.json`（sha256）。
- 扫描件 OCR 由 rapidocr 完成，`manifest.artifacts["full.md"].extract` 会记录来源（pymupdf/rapidocr）；
  条文识别可能有偏差，关键尺寸以 PDF 原件复核。
- **OCR 质量与升级路径（表格页碎掉怎么办、怎么换 MinerU／视觉模型、哪些页不能只信 OCR）
  见 `skills/minecraft-ocr`**；体检用 `mckb audit`，噪声清理用 `mckb denoise`。
- 版权：`source.pdf` 默认不进 git（见 `.gitignore`），`full.md`/manifest 可提交。

## 对接外部知识库

`mckb export` 输出 chunk 级语料（text + release/条款/页码/行号元数据），
LlamaIndex / LangChain / Chroma / txtai 均可直接挂载；调研与选型见
`kb/docs/ECOSYSTEM.md`。

## 语料库的派生产物

`kb/registry.json`、`kb/catalog.md`、`kb/index.sqlite` 都是**算出来的**：

```bash
python -m mckb scan && python -m mckb index   # 重建
python -m mckb verify && python -m mckb lint  # 校验
python -m mccore.bootstrap                    # 顺带把 registry.json 里的 corpus 段刷新
```

手改 `manifest.json` 会让 sha256 对不上；`full.md` 手改过要 `python -m mckb rehash <release>`。

## 相关

- 语料库说明：`kb/README.md`；来源清单：`kb/sources.json`；检索词导航：`kb/topics.json`
- 建筑审美/视觉验收：`skills/minecraft-building-design`
- 模块与接口拼接：`skills/minecraft-modular-building`
