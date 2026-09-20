---
name: minecraft-ocr
description: PDF/扫描件导入语料库时的 OCR 质量管理技能（mckb 的取字环节）。当需要「下载/导入一部规范」「把 PDF 变成可检索文本」「OCR 识别不准」「表格数字变成碎片」「检查语料质量」时使用。判断该用哪种取字方式（文字层 / 本地 rapidocr / 升级到 pi_ocr 的 MinerU 或视觉模型 / 人工核对）、跑什么命令、以及哪些内容绝不能只信 OCR。
---

# 规范语料 OCR 与质量管理

## 现状（先认清工具）

**OCR 不是 skill 识别的，是代码里写死的一条路径**：`packages/mckb/extract.py` 的 `_ocr_pages()`
调用本地 `rapidocr_onnxruntime`，逐页 200 dpi 光栅化后识别。入口只有：

```
mckb ingest <pdf> --engine auto|text|ocr [--dpi 200]
```

- `auto`（默认）：先试 PDF 文字层（平均 ≥40 字/页 视为有文字层）→ 用 pymupdf 直接取，**最准、零成本**；
  没有文字层才落到 rapidocr。当前 8 部语料全是扫描件 → 实际全是 `extract=rapidocr`。
- `text`：强制只读文字层（扫描件会报错）——**能用就用它**。
- `ocr`：强制 rapidocr。

也就是说：**skill 不参与取字，只参与"选哪条路 + 事后质检 + 什么时候升级"**。本 skill 就是这两件事。

## 路由：先分页型，再决定用谁

| 情况 | 用什么 | 做法 |
|---|---|---|
| PDF 有文字层（电子版原生） | pymupdf | 默认 `--engine auto`，不要碰 OCR |
| 扫描件正文页、印刷清晰 | 本地 rapidocr | 默认路径，离线、快（约 1–3 s/页） |
| 冒烟体检发现**表页**（见下） | 先试 300 dpi 本地重扫 | `--engine ocr --dpi 300 --force` |
| 表页仍碎、公式页、密排小字 | **升级外部引擎** | `pi_ocr` 工具（用 `/ocr` 里配置的后端：MinerU / VLM / Ollama）→ 见「升级回路」 |
| 关键**数值**（防火间距、净宽、面积、层高） | 人工回原件 | `mckb read <release> --page N` 定位后，对照 `kb/releases/<release>/source.pdf` 原页 |

## 体检：先量，再修

```bash
python -m mckb audit                      # 全部 release 逐页字数分布
python -m mckb audit gb50016-2014-2018    # 单部
python -m mckb audit --json > audit.json  # 给脚本用
```

输出每部的「中位字数/页、待复核页、水印残留」，待复核页带猜测类型：

- `表` = 短行多 + 数字多 → **最危险**，表格行列关系很可能已被打散
- `图` = 只剩题注（"计算的各角度示意图"）→ 图里的信息本来就没进文本，接受但要知情
- `空` = 整页无字
- `少` = 字少但可能是正常稀疏页（封面、条文说明尾巴）→ 用 `read --page N` 看一眼即可

**audit 是"值得看一眼"的提示，不是失败判定**：`gb50016 p103` 只有 111 字但内容完整，属正常。

## 实测失败模式（2026-09，8 部 corpus 真实样本）

| 失败模式 | 实例 | 后果 |
|---|---|---|
| 表格被打散成乱序碎片 | `gb50016 p37` 表 3.4.1 防火间距表 → `14 / 丙、丁、戊类厂房 / 16 / 甲类厂房 / 多层`（**已修，见下**） | **行列关系丢失=数值不可用**，必须回原件 |
| 续表只剩残片 | `gb50016 p38` 续表（室外变配电站段）→ 25 字乱码（**已修**） | 同上 |
| 附录表丢结构 | `gbt50033 p31` 附录 A.0.2 光气候区表（**已修**）；`gb50763 p51-54` 附录 B/C 标志牌表（**已修**） | 只能人读 |
| 图页只剩题注 | `gbt50033 p33` | 图上信息不进语料 |
| 字符误识 | `住宅项自`←项目、`防珊塌`←防坍塌、`信急`←信息、`50016一2014`←`50016-2014` | 精确串匹配会漏；**条款号/数字要回原件** |
| 页边水印噪声 | `浏览专用`、`住房城乡建设部信息公开`、`息公开`…（8 部共 54 行，已清理） | 污染 chunk 与检索 |

水印判定是保守的：**整行只由水印词表构成才删**（`extract.is_watermark`），
"住房城乡建设部关于发布国家标准…"这类正文句子不会被误删。清理命令：

```bash
python -m mckb denoise <release|全部>   # 重写 full.md（去噪）→ 重切 chunk → 更新 sha256/manifest
```

## 单页修复配方（推荐用法：只救坏页，不重扫全书）

实测：`gb50016 p37` 的「表 3.4.1 防火间距」本地 rapidocr 只得 107 字乱序碎片；
同页 `pi_ocr`（mineru-pro）返回 1824 字**带行列结构的表**（甲乙丙丁戊厂房×一二三级×高层/裙房×一类二类，数值全部对位）。
所以不要整本重扫（458 页不现实），只补坏页：

```bash
# 1. audit 拿到坏页列表
python -m mckb audit gb50016-2014-2018          # → p37(表) p38(表) p179(表) …

# 2. 抽页成图（300 dpi，直接给 pi_ocr）
python - <<'PY'
import fitz, pathlib
out = pathlib.Path("kb/inbox/_ocr_check"); out.mkdir(parents=True, exist_ok=True)
doc = fitz.open("kb/releases/gb50016-2014-2018/source.pdf")
for n in (37, 38, 179):
    doc[n-1].get_pixmap(dpi=300).save(str(out / f"gb50016_p{n}.png"))
PY

# 3. 对每张图调 pi_ocr 工具（后端由 /ocr 配置：mineru / mineru-pro / ollama / …）拿回文本

# 4. 把外部文本存成只含坏页的 fix.md（每页前写 `<!-- page N -->`）→ kb/inbox/gb50016-fix.md
#    然后只替换这几页（页数/页码不变，引用不跑偏）
python -m mckb splice gb50016-2014-2018 --pages 37,38,179 --from kb/inbox/gb50016-fix.md

# 5. 校验 + 重建索引
python -m mckb verify && python -m mckb scan && python -m mckb index && python -m mckb audit gb50016-2014-2018
```

如果想把整本书换成另一个引擎的全文（页数必须一致），才用 `ingest --text`：

```bash
python -m mckb ingest kb/releases/gb50016-2014-2018/source.pdf --release gb50016-2014-2018 \
    --code "GB 50016-2014(2018年版)" --title "建筑设计防火规范" \
    --text kb/inbox/gb50016-fix.md --text-source mineru --force
```

注意：

- VLM 返回的 HTML 表格（`<table>`）直接整段保留；切块器按行切，表格会落成一条 chunk，
  定位（页码/行号）照样可用——检索时当“数值表所在页”的入口用。
- 千页大书只补 audit 标出的那十几页，其余保持 rapidocr：**性价比最高**，且 sha256 有记录可追溯。
- 补完重跑 `audit`，确认那些页不再落在待复核里。

### 修复后怎么核对（2026-09 已在 8 页上实践）

1. **数字多重集比对**（最有效）：修复前先存旧页的数字列表，修复后断言「旧 ⊂ 新」。
   实测 8 页：“丢失”的全部是**页脚印刷页码**（22/23/24/43/45/46/27）与乱码片段（0s<）——
   页码丢失属预期：引用靠 `<!-- page N -->` 锚点，不靠页脚。
2. **结构/常识抽查**：对照类表格应能交叉验证。表 3.4.1 实测：甲类厂房↔乙类单多层 = 12（双向一致）、
   甲类厂房↔民用建筑 = 25/50、丙类厂房↔高层民用建筑 = 20/15，与规范值对得上。
3. **audit 回测**：这些页应不再出现在待复核里。实测 gb50016 21→19 页、gb50763 7→3 页、gbt50033 3→2 页，
   剩下的全是章末页/封面/名录页（内容完整，不是损坏）。
4. **来源可追**：修复页正文里留了 `<!-- ocr:mineru-pro -->` 标记：
   `grep -n "ocr:mineru" kb/releases/*/full.md` 列出所有 VLM 来源的页。
5. 检索回归：拿新表里**旧文本根本没有**的词去搜（如“变压器总油量”“室外交配电”类），
   搜得到就说明新内容真的进了 FTS5 索引。

## 已知坑：行尾与 sha256（重要）

manifest 里的 sha256 是对**文件字节**算的，而 git 按 `.gitattributes` 的 `* text=auto eol=lf`
存 LF。Windows 上 Python 默认写 CRLF → 入库时算的 sha 与仓库里存的对不上：
**另一个机器 clone 下来跑 `mckb verify` 会全红**（不是文件坏了，是行尾不一致）。
已于 2026-09 修：mckb 全部写盘改用 LF（`extract.write_text_lf`），并对 8 部语料跑过
`python -m mckb rehash`（行尾转 LF + 重算 sha256）——现在 `git show HEAD:…/full.md` 的 blob
与 manifest 逐个一致。

- 不要手写 `write_text(..., encoding="utf-8")` 写入 `kb/releases/` 下的文件；用 `write_text_lf`。
- 手工改过 full.md（或从别处拷回）后，跑 `mckb rehash <release>` 重算哈希，否则 verify 会报
  `full.md sha256 不匹配`。

硬约束（脚本会拦）：

- 外部文本**必须带 `<!-- page N -->` 页码锚点**，且页数必须等于 `source.pdf` 页数，
  否则报错——页码是条款引用与 `read --page` 的坐标系，错位等于全废。
- `--text-source` 会写进 `manifest.artifacts["full.md"].extract`（如 `mineru` / `pi-ocr` / `manual`），
  便于日后知道哪部是哪个引擎产的。
- `--force` 重入库保留原 `created_at`，并保留页码锚点结构。
- 只有**整本文本**能入库（页数要一致）；单页修复要先拼回整份 full.md 再入库。

## 红线

1. **数值型条款最终依据永远是 `source.pdf`**，不是 `full.md`。用 `read --page N` 定位到页，
   再看原件；计划里写"标准号 + 条款号 + 页码"而不是 OCR 原句。
2. 检索（`search`/`readlist`）只用于**定位**，正文读 `full.md` 完整章节；OCR 错字会漏检，
   同一问题换 2–3 组词再搜一次。
3. 不要为了"看起来干净"批量改写 full.md——只跑 `denoise`（有判定规则、有回归），
   其它人工修正走 `--text` 重入库。
4. 版权：`source.pdf` 不进 git（`.gitignore`），`full.md`/`manifest` 可提交。

## 相关

- 语料库用法与检索流程：`skills/minecraft-building-codes/SKILL.md`
- 代码：`packages/mckb/extract.py`（取字/切块/入库）、`maintain.py`（audit/verify/lint/scan）
- 回归：`python tests/mckb_smoke.py`（含水印判定、外部文本导入、页数校验、audit）
