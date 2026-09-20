---
name: minecraft-building-design
description: Minecraft 建筑设计大脑 + 视觉回环验收 SOP。当需要设计、生成、迭代 Minecraft 建筑（结构投影：默认 .schem 存储、兼容 .litematic 读取；numpy + structure_io/litematic_io 生成器）时使用：分阶段设计法（概念→体块→外壳→结构→细节）、用 render_litematic.py + vision_review.py 让 AI 亲眼看渲染图并据此修复、建筑比例/立面节奏/结构逻辑/材质纪律/夜景照明原则、六大风格速查库、导出前结构质检（悬浮块、门配对、方块属性合法性）。
---

# Minecraft 建筑设计 Skill

## 定位与分工

| Skill | 管什么 |
|---|---|
| `minecraft-block-models` | 方块状态**写对**（墙/栅栏/玻璃板连接、楼梯 shape、门上下半、属性合法性） |
| **本 skill** | 建筑**设计好**（体块/比例/立面/结构/室内/夜景）+ **视觉验收**（渲染→看→修循环）+ **结构质检** |

AI 生成建筑最大的短板是**看不见自己盖的东西**。本项目的对策不是让 AI 脑补,
而是把它变成一条闭环:**文本体素 → 真实贴图渲染 → 视觉模型批判 → 定点修复 → 重渲**。
所有环节都有现成工具,本 skill 把流程、标准、知识库固化下来。

## 七步标准工作流

```
1. 定需求     风格/尺寸/用途/环境 → 读 references/style-catalog.md 选风格语法
2. 查规范     规范语料库（skills/minecraft-building-codes）:
              python -m mckb readlist --profile office --query "..." →
              用 read 打开 kb/releases/<release>/full.md 的完整章节 →
              计划里写「标准号 + 条款号 + 页码」,无依据的尺寸不许落笔
3. 概念设计   先写轮廓线 + 体块图（三段式、主从、比例数字），再写代码
4. 写生成器   分层函数（地基→主体→屋顶→细节），numpy + packages/mccore/litematic_io.py；
              不完整方块最后用 minecraft-block-models 的 connect.py 补属性
5. 快速自检   qa_check.py（结构）+ 低倍率渲染，先别追求细节
6. 视觉回环   review_loop.py 一键渲染多视角→AI 批判→读报告→每轮修 ≤3 类问题→重渲
7. 验收交付   过「质量门禁」全项 → 与参考原图比较颜色 → 出正式图 → 写交付说明
```

**铁律:任何版本迭代完,必须至少渲染 6 个正交视角 + 1 张剖面 + 1 张夜景看过一遍,才能算完。**
只渲一个角度就交付是头号翻车源。

## 视觉回环 SOP(核心)

```bash
# 一键:渲染 6 视角 + AI 视觉批判,报告写到 review.md
python -m mcqa.review_loop 你的.schem --out review

# 加剖面和夜景(检查内部与照明)
python -m mcqa.review_loop 你的.schem \
    --cut x=112 --cut z=112 --night --out review

# 只看不评(快速渲染,不调 API)
python -m mcqa.review_loop 你的.schem --no-ai --scale 2
```

详细渲染矩阵、批判 prompt 模板、问题→修复词典见 `references/visual-review-sop.md`。

## 结构质检

```bash
python -m mcqa.qa_check 你的.schem
```

检查三项致命问题 + 统计:**① palette 方块名/属性合法性**(非法属性会被静默回退成默认状态);
**② 悬浮组件**(6 连通分量悬空,锁链/灯笼/火把等自带豁免);**③ 门配对**(上下半、facing/hinge/open 一致性)。
ERROR 必须修复才能交付,WARN 需要解释或修复。

## 质量门禁(交付前逐项打勾)

- [ ] 计划/交付说明里列了规范依据(标准号+条款号+页码),且确实读过 `kb/releases/**/full.md` 原文
- [ ] qa_check 无 ERROR,悬浮 WARN 已解释或修复
- [ ] 6 视角(iso/front/back/left/right/top)+ 1 剖面 + 1 夜景全看过
- [ ] 不完整方块属性由 connect.py 补齐(墙/栅栏/玻璃板/楼梯/门)
- [ ] 每面实墙都有开口(窗/门),无「实心盒子」
- [ ] 材质纪律:结构色单一、主色 ≥60%,变化来自几何而非随机噪点
- [ ] **有参考图时:渲染图必须与参考原图比较颜色**(并排 + 主色/明度分布/暖冷比量化),未比较不得交付
- [ ] 内部有楼板/交通/功能分区,不是空壳
- [ ] 夜景有照明分层(内透/轮廓/装饰),不是均匀撒豆
- [ ] 屋顶有出挑或檐口,不直接截断在墙面

## 十大常见失败模式(见到即修)

1. **均匀噪点糊墙** —— 随机混色当纹理。结构材料统一,图案只来自几何(线脚/层线/柱距)
2. **实心盒子** —— 外壳没开窗没门。每面墙都要有开口节奏
3. **悬浮体块 / 悬挑无支撑** —— 悬挑 >2 格要有托架/柱/斜撑
4. **门只有半扇 / 墙变柱子 / 楼梯全朝北** —— blockstate 默认值;必须过 connect.py
5. **屋顶浮空** —— 屋顶需要屋架(梁/檩条)或至少落在墙顶上,坡度见设计原则
6. **比例失调** —— 窗占半面墙、层高 3 格、底座比塔身窄
7. **壳漂亮内部空** —— 渲染剖面验收内部
8. **夜景一片黑或一片白** —— 光源要分层、密度克制
9. **风格混搭** —— 现代玻璃幕墙直接长在中世纪石墙上;过渡带或选边
10. **只渲一个角度就交付** —— 死角里藏着一堆问题
11. **尺寸无依据** —— 层高/走道宽/出口数/楼梯电梯靠拍脑袋。先跑 `python -m mckb readlist`,
    读完整章节再画图,计划里注明条款号

## 命令速查

| 命令 | 作用 |
|---|---|
| `python -m mcrender.cli x.schem --views iso,front,top --scale 3` | 快速三视图 |
| `python -m mcrender.cli x.schem --views hero --proj persp --scale 4 --ssaa 3` | 高清透视美图 |
| `python -m mcrender.cli x.schem --views right --cut x=112` | 剖面看内部 |
| `python -m mcrender.cli x.schem --background dark --bloom 1.0 --views iso` | 夜景 |
| `python -m mcrender.cli x.schem --list` | palette 解析结果(查 fallback) |
| `python -m mcqa.vision_review a.png b.png --prompt "..."` | 直接视觉批判 |
| `python -m mcqa.review_loop x.schem` | 一键回环(渲染+批判+报告) |
| `python -m mcqa.qa_check x.schem` | 结构质检 |
| `python -m mcqa.walk_check x.schem --start 24,1,20 --probe 16,6,8` | 可行走性：房间/探针走得到吗 |
| `python -m mcqa.preview x.schem --all` | **没图形环境**时用字符图看形体/剖面 |
| `python tools/ascii_view.py x.schem` | 同上（更老的小工具，剪影/剖面） |
| `python tools/whois.py x.schem 12 3 40` | 放大镜：看某个坐标附近有什么 |
| `python tools/render_iso_alpha.py x.schem` | 透明底多视图（贴参考图上比形） |
| `python tools/vreview.py --prompt-file p.txt 渲染.png 参考.jpg` | 直接视觉评审（不走回环） |
| `python -m mccore.bootstrap --check` | 派生产物是否最新（手改过就退码 1） |

## 文件索引

- `references/design-principles.md` —— 建筑学速查:体块/比例/立面/结构/屋顶/室内/夜景
- `references/style-catalog.md` —— 六大风格:关键词、结构语法、经过校验的方块 palette、常见翻车点
- `references/visual-review-sop.md` —— 渲染矩阵、批判 prompt 模板、问题→修复词典、迭代节奏
- `mcqa.qa_check` —— 结构质检(见上)
- `mcqa.review_loop` —— 一键视觉回环(见上)
- `kb/` + `python -m mckb` —— 规范语料库(完整文档 release + FTS5 条款检索),见 `skills/minecraft-building-codes/SKILL.md`

方块状态正确性(连接/朝向/属性)不在本 skill 范围内,见 `skills/minecraft-block-models/SKILL.md`。
