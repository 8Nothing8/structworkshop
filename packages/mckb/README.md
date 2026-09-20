# mckb — 规范语料库（层 3 · 应用）

中国建筑规范（GB / JGJ / CBT）的**全文语料 + 条款级检索**。存在的理由是纪律：
AI 写设计说明/装配计划时，规范依据必须来自**完整文档**，不许凭记忆编标准号与条款号。

```bash
python -m mckb readlist --profile office --query "走道 净宽"   # 该读哪几份的哪几节
python -m mckb search "避难层 设置" --limit 3                   # 命中（返回位置，不返回结论）
python -m mckb read gb55037-2022 --section "5.3 防火分区"        # 读完整章节原文
```

## 分层位置

| | |
|---|---|
| **上游** | `mccore.paths`（定位 `<repo>/kb/`，并复用 `write_text_lf` —— manifest 按字节算 sha256）。其余全靠第三方（`PyMuPDF` + `rapidocr-onnxruntime` 是可选 extra `kb`） |
| **下游** | 无（叶子包）。它是给 **AI/人**查的，不被别的包 import |
| **不做** | 不生成建筑、不参与渲染；**不收摘录/二手结论**；不做矢量化图纸理解 |

## 目录（`<repo>/kb/`）

```
kb/
├── registry.json     每部标准的元数据（编号/名称/版本/页数/状态）
├── catalog.md        人读目录
├── index.sqlite      SQLite FTS5(BM25) 条款索引（CJK 单字切分，查询走 bigram/松散匹配）
├── sources.json      公告附件来源（住建部 / openstd / 直链）
├── topics.json       主题 → 标准的映射（readlist --profile 用）
├── docs/             配套说明
├── inbox/            待入库的 PDF / 外部 OCR 的修复文本
├── export/           导出给外部 KB（jsonl / llamaindex / chroma / txtai）
└── releases/<release>/
    ├── source.pdf        原件（只读，存档用）
    ├── full.md           忠实文本渲染（含 `<!-- page N -->` 页标记，**唯一真源**）
    ├── chunks.jsonl      条款/章节级切片（带页 + 行锚点）
    ├── manifest.json     元数据 + artifacts（sha256 / pages / chunks）
    └── SHA256SUMS        原件与派生文件校验和
```

## 模块

| 文件 | 作用 |
|---|---|
| `paths.py` | `kb/` 下所有位置的规范定位（`iter_releases()` 之类） |
| `fetch.py` | 从公告/公开源下载**完整** PDF（镜像 + 重试），写 `inbox/` 并登记 `sources.json` |
| `extract.py` | **入库流水线**：PDF → `full.md` → `chunks.jsonl` → `manifest.json`（中文去空格、水印判定、页标记、条款切分）；`splice` 单页修复、`rehash` 手改后重算哈希、`denoise` 去噪、`rechunk` 重切 |
| `index.py` | FTS5 索引与检索：`build()` / `search()` / `read()` / `stats()`；CJK 单字切分 + BM25 |
| `maintain.py` | 运维：`scan`（重建 registry/catalog）`verify`（sha256 + 页覆盖 + 片段覆盖）`lint` `audit`（逐页体检：哪些页值得重扫/复核）`export` `stats` |
| `cli.py` | `python -m mckb <command>` 统一入口（18 个子命令） |
| `__main__.py` | `python -m mckb` 的入口垫片（直接转 `cli.main`） |

## 命令行

```bash
python -m mckb fetch --id gb55031-2022                       # 下载新标准（住建部公告附件）
python -m mckb ingest kb/inbox/x.pdf --release gb55031-2022 \
    --code "GB 55031-2022" --title "民用建筑通用规范"          # 入库（OCR 回退 rapidocr）
python -m mckb scan && python -m mckb index && python -m mckb verify
python -m mckb audit                                          # 表页/图页体检
python -m mckb splice gb55031-2022 --pages 4,10 --from kb/inbox/gb55031-fix.md
python -m mckb rehash gb55031-2022                            # 手改 full.md 后重算 sha256
python -m mckb catalog | stats | lint | export --format jsonl
```

## 约定 / 不变量

* **全文主义**：`source.pdf` 必须完整；`full.md` 是忠实渲染，不许改写/摘要。
* **行尾锁 LF**：`manifest.json` 与 `SHA256SUMS` 按字节算 sha256 → 写入统一走
  `mccore.paths.write_text_lf`（本包曾自带副本，现已收编为一份）。
* **只答位置，不答结论**：`search` 返回条款坐标，`read` 才给原文；引用必须写标准号 + 条款号。
* **OCR 质量链路**：碎表页/误识别条款号 → 外部 OCR 只补坏页（`splice`），换整本才用 `--text`（页数必须一致）。
  取字质量的升级路径见 skill `skills/minecraft-ocr/SKILL.md`。

## 相关 skill

| skill | 用途 |
|---|---|
| `skills/minecraft-building-codes/` | 检索纪律：readlist → read 完整章节 → 计划里写引用 |
| `skills/minecraft-ocr/` | 取字质量与升级路径（何时该换 OCR 引擎） |

## 测试

```bash
python tests/mckb_smoke.py    # 切分 / FTS5 CJK 检索 / 水印判定 / 外部文本 / splice / 行尾 / audit
python tests/lf_smoke.py      # 行尾不变式（manifest 能对新 clone 全绿的前提）
python -m mckb verify         # 全语料 sha256 + 页/片段覆盖
```
