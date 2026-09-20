# 现代建筑分类体系（structworkshop 资产库参考）

> 用途：给资产包（packs）、模块（modules）、提示词（prompts）提供一套**可复用的分类坐标**。
> 原则：**先分维度，再填内容**——同一个建筑可以同时属于「时间 × 流派 × 地域 × 类型 × 形态 × 构造」多个坐标。

---

## 0. 十二个分类维度

| # | 维度 | 英文 | 说明 | 标签示例 |
|---|---|---|---|---|
| 1 | 时代/分期 | period | 时间坐标 | `period:interwar` |
| 2 | 流派/运动 | movement | 思想与形式谱系 | `movement:brutalism` |
| 3 | 地域/文化 | region | 地理 + 文化圈 | `region:japan` |
| 4 | 建筑类型 | typology | 功能 | `typology:office-tower` |
| 5 | 体量/形态 | form | 塔楼/体块逻辑 | `form:setback` |
| 6 | 结构体系 | structure | 承重与抗侧 | `structure:diagrid` |
| 7 | 立面/表皮 | facade | 外围护语言 | `facade:curtain-wall` |
| 8 | 材料/色彩 | material | 材质与色板 | `material:exposed-concrete` |
| 9 | 城市/场地关系 | urban | 与街道/广场/肌理 | `urban:podium-plaza` |
| 10 | 性能/可持续 | performance | 气候与能耗 | `performance:passive` |
| 11 | 室内/公共空间 | interior | 中庭/大堂/天空大堂 | `interior:atrium` |
| 12 | 装饰/摆件 | props | 人尺度元素 | `props:street-furniture` |

资产库落地时，**pack 按流派/地域切分，module 按"部位"切分，标签按上面 12 维打**。

---

## 1. 时间轴（1860s → 2020s）

| 阶段 | 时间 | 关键词 | 代表 |
|---|---|---|---|
| 前现代探索 | 1860–1900 | 芝加哥学派、工艺美术、新艺术 | 家庭保险大楼、红屋 |
| 早期现代主义 | 1900–1930 | 分离派、制造联盟、未来主义、风格派、构成主义、包豪斯、表现主义 | 施罗德住宅、包豪斯校舍、爱因斯坦塔 |
| 国际式 | 1930–1960 | CIAM、功能主义、少即是多、自由平面 | 萨伏伊别墅、西格拉姆大厦、联合国秘书处 |
| 战后现代主义 | 1945–1975 | 纪念性、粗野主义、结构表现、代谢派 | 马赛公寓、耶鲁美术馆、代代木体育馆、中银胶囊塔 |
| 晚期现代/高技 | 1960–1990 | 筒体革命、外露结构、设备表现 | 西尔斯大厦、蓬皮杜中心、香港汇丰、劳埃德大厦 |
| 后现代 | 1960s–1990s | 符号、拼贴、文脉、戏谑古典 | 波特兰市政厅、AT&T 大楼、斯图加特美术馆 |
| 解构 | 1980s–2000s | 断裂、错位、非线性几何 | 维特拉消防站、毕尔巴鄂古根海姆、柏林犹太博物馆 |
| 当代/新现代 | 1990s– | 极简、参数化、数字建造、生态、地域复兴 | 巴塞罗那馆式极简、广州西塔、苹果总部 |
| 2020s | 2020– | 新粗野、气候响应、自适应再利用、木高层、3D 打印混凝土、AI 生成 | 各类碳中和塔楼、CLT 高层 |

> 参考：Wikipedia [Timeline of architectural styles](https://en.wikipedia.org/wiki/Timeline_of_architectural_styles)、[Modern architecture](https://en.wikipedia.org/wiki/Modern_Architecture)、[International Style](https://en.wikipedia.org/wiki/International_Style)。

---

## 2. 流派/运动详表（按谱系排列）

> 每条：**时期 · 地域** → 核心主张 / 形式语言 / 构造材料 / 代表作品 / 资产库关键词

1. **芝加哥学派 Chicago School**（1880s–1900s·美国芝加哥）→ 钢框架 + 大玻璃、三段式立面、防火技术 / 箱形框架、宽窗 / 家庭保险大楼 / `structure:steel-frame`, `facade:three-part`
2. **新艺术 / 青年风格派 Art Nouveau**（1890s–1910s·比利时/法国/德奥）→ 有机曲线、装饰一体化 / 铁艺、彩色玻璃 / 塔塞尔公馆、米拉公寓 / `movement:art-nouveau`
3. **维也纳分离派 Vienna Secession**（1897–1910s·奥地利）→ 几何装饰 + 白墙、整体艺术 / 大理石、金属饰面 / 分离派展览馆 / `movement:secession`
4. **未来主义 Futurism**（1909–1930s·意大利）→ 速度、机械、动态 / 宣言先行、极少落地 / 圣埃利亚"新城市"图 / `movement:futurism`
5. **德意志制造联盟 Werkbund**（1907–1930s·德国）→ 工业化标准、类型化 / 标准化构件 / 法古斯工厂 / `movement:werkbund`
6. **风格派 De Stijl**（1917–1931·荷兰）→ 红黄蓝 + 黑白灰、正交分解 / 板片、线条 / 施罗德住宅 / `movement:de-stijl`
7. **构成主义 Constructivism**（1919–1930s·苏俄）→ 结构即形式、社会工程 / 钢、玻璃、宣传性体量 / 塔特林塔（未建）、工人俱乐部 / `movement:constructivism`
8. **包豪斯 Bauhaus**（1919–1933·德国）→ 艺术与技术统一、功能优先 / 钢+玻璃+混凝土 / 德绍包豪斯校舍 / `movement:bauhaus`
9. **表现主义 Expressionism**（1910s–1920s·德国/荷兰）→ 雕塑化体量、情绪 / 砖、混凝土 / 爱因斯坦塔、阿姆斯特丹学派 / `movement:expressionism`
10. **国际式 International Style**（1920s–1970s·欧美→全球）→ 体积而非体量、规则性、无装饰 / 幕墙、白墙、带窗 / 萨伏伊、西格拉姆、范斯沃斯 / `movement:international`
11. **功能主义 Functionalism**（1930s·北欧/中欧）→ 形式服从功能、社会住宅 / 实用构造 / 赫尔辛基奥林匹克体育场 / `movement:functionalism`
12. **粗野主义 Brutalism**（1950s–1970s·英国→全球）→ 材料即装饰、雕塑性混凝土、社会理想 / 清水混凝土、巨柱、粗骨料 / 巴比肯、波士顿市政厅、国家剧院 / `movement:brutalism`
13. **结构主义/十次小组 Team X & Structuralism**（1950s–1970s·欧洲）→ 批判 CIAM、簇群与生长 / 网格、单元重复 / 阿姆斯特丹孤儿院 / `movement:structuralism`
14. **代谢派 Metabolism**（1960–1970s·日本）→ 建筑如生物可代谢、舱体、巨构 / 钢舱体、核心筒 + 插件 / 中银胶囊塔、大阪世博会 / `movement:metabolism`
15. **晚期现代主义 Late Modernism**（1960s–1980s·全球）→ 技术乐观、结构表现 / 筒体、巨型框架 / 西尔斯、汉考克 / `movement:late-modern`
16. **高技派 High-Tech**（1970s–1990s·英/法/意）→ 暴露结构与设备、工业美学 / 外露钢、管、网格 / 蓬皮杜、汇丰、劳埃德 / `movement:high-tech`
17. **后现代主义 Postmodernism**（1960s–1990s·欧美）→ 符号、历史拼贴、双重译码 / 色彩、拱、装饰面 / 波特兰市政厅、AT&T / `movement:postmodern`
18. **解构主义 Deconstructivism**（1980s–2000s·欧美）→ 断裂、倾斜、非理性几何 / 钛板、异形钢 / 古根海姆毕尔巴鄂、维特拉 / `movement:deconstructivism`
19. **新理性主义 Neo-Rationalism**（1960s–1980s·意大利）→ 类型学、城市记忆 / 砌体、几何原型 / 圣卡塔尔多墓地 / `movement:neo-rationalism`
20. **极简主义 Minimalism**（1980s–·全球）→ 极少构件、光与体量 / 白墙、清水混凝土、玻璃 / 巴塞罗那馆式极简、瓦尔斯温泉 / `movement:minimalism`
21. **新现代/新密斯 Neo-Modern**（1990s–·全球）→ 回归现代语法、高完成度 / 单元幕墙、精细节点 / 各类高端塔楼 / `movement:neo-modern`
22. **批判地域主义 Critical Regionalism**（1980s–·全球）→ 现代性 + 地方性，反符号拼贴 / 地方材料 + 现代构造 / 巴瓦、柯里亚、巴拉甘 / `movement:critical-regionalism`
23. **热带现代主义 Tropical Modernism**（1950s–·东南亚/南亚/非洲/拉美）→ 遮阳、通风、深挑檐、开敞 / 混凝土 + 百叶 + 水院 / 巴瓦、柯里亚 / `movement:tropical-modern`
24. **参数化主义 Parametricism**（2000s–·全球）→ 连续差异、算法生成、流线型 / 异形板、GRC、BIM / 广州大剧院、扎哈作品、北京大兴 / `movement:parametric`
25. **数字建构/Blob/Digital**（1990s–2010s）→ 自由曲面、数控加工 / 双曲面板、3D 打印 / 香奈儿流动艺术馆 / `movement:digital`
26. **生态/可持续 Sustainable / Green**（1990s–·全球）→ 被动优先、性能驱动 / 绿墙、遮阳、木结构、光伏 / 各类绿建认证作品 / `movement:sustainable`
27. **新粗野主义 Neo-Brutalism**（2010s–·全球）→ 粗野语言 + 数字肌理 + 社区性 / 3D 打印混凝土、粗面 / 当代公共建筑 / `movement:neo-brutalism`
28. **自适应再利用 Adaptive Reuse**（2000s–·全球）→ 保留结构、功能置换 / 新旧并置 / 伦敦泰特现代、汉堡易北爱乐 / `movement:adaptive-reuse`

> 参考：ArchitectureCourses [Modern Architectural Styles](https://www.architecturecourses.org/learn/modern-architectural-styles)、维基百科 [后现代主义建筑](https://zh.wikipedia.org/zh-hans/%E5%90%8E%E7%8E%B0%E4%BB%A3%E4%B8%BB%E4%B9%89%E5%BB%BA%E7%AD%91)、有方 [高技派建筑简史](https://www.archiposition.com/items/20180525105015)、Dezeen [Parametricism](https://www.dezeen.com/2026/05/21/parametricism-architecture-feature/)。

---

## 3. 地域谱系（同一流派的地区变体）

### 3.1 欧洲
- **德国**：制造联盟 → 包豪斯 → 战后重建（玻璃盒 + 粗野）
- **荷兰**：风格派 → 结构主义（网格簇群）→ 当代 MVRDV/OMA
- **法国**：柯布西耶 → 粗野混凝土 → 高技（蓬皮杜）
- **意大利**：未来主义 → 新理性主义 → 高技 + 极简（米兰）
- **英国**：粗野主义 → 高技（罗杰斯/福斯特）→ 后现代
- **西班牙/葡萄牙**：白墙 + 拱 + 地域性（莫内奥、西扎）
- **北欧（芬/瑞/丹/挪/冰）**：砖木 + 暖色 + 人性化尺度 + 坡屋顶，自然采光（阿尔托）
- **中东欧/苏联**：构成主义 → 社会主义现代主义（对称、纪念性、马赛克）→ 南斯拉夫纪念碑
- **瑞士**：极致构造 + 混凝土极简（博塔、卒姆托）

### 3.2 美洲
- **美国**：芝加哥学派 → 国际式 → 晚期现代（SOM/筒体）→ 后现代 → 参数化
- **加拿大**：北方针叶林现代主义（木结构、雪荷载）
- **巴西**：里约学派 → 保利斯塔学派（混凝土 + 遮阳板 + 热带）→ 尼迈耶（曲线、巴西利亚）
- **墨西哥**：巴拉甘（色彩 + 光墙）→ 当代几何混凝土
- **阿根廷/智利**：混凝土表现主义、地震工程
- **加勒比/中美洲**：热带现代 + 殖民底色

### 3.3 亚洲
- **日本**：前川国男 → 丹下健三 → 代谢派 → 安藤（清水混凝土）→ 妹岛/西泽（白色轻透）→ 隈研吾（弱建筑）
- **中国**：近代折衷 → 建国后民族形式/苏式 → 改革开放玻璃塔 → 当代参数化（大兴机场、广州西塔）
- **韩国/台湾地区/香港**：高密度塔楼 + 山地/临海适应
- **东南亚（新/泰/越/印尼/马）**：热带现代，遮阳、骑楼、通风庭院
- **南亚**：柯里亚（管式住宅）、多西（砖拱）、巴瓦（斯里兰卡庭院）
- **中东**：黎凡特石材 + 海湾玻璃塔 + 伊斯兰几何遮阳（Mashrabiya 变体）

### 3.4 非洲 / 大洋洲
- **北非**：殖民现代 + 地域（埃及、摩洛哥，气候适应）
- **西非/东非**：热带现代 + 本土材料（土、木、编织）
- **南非**：粗野 + 高密度
- **澳/新西兰**：轻钢木 + 大挑檐 + 气候响应（悉尼歌剧院为特例）

> 参考：Wikipedia [Critical regionalism](https://en.wikipedia.org/wiki/Critical_regionalism)、[Tropical Modernism](https://en.wikipedia.org/wiki/Tropical_Modernism)、Nippon.com [代谢派](https://www.nippon.com/cn/images/i00057/)、ArchDaily [Gulf Modernism](https://www.archdaily.com/1041515/oil-glass-and-identity-gulf-modernism-between-global-image-and-local-climate)。

---

## 4. 建筑类型学

| 大类 | 细分 | 设计要点 |
|---|---|---|
| 办公/总部 | 单租户总部、多租户塔楼、园区 | 标准层、核心筒、幕墙、大堂 |
| 住宅 | 板楼、塔楼、围合街区、联排、学生宿舍 | 朝向、阳台、公共空间 |
| 酒店 | 商务酒店、度假酒店 | 客房模数、裙房大堂 |
| 文化 | 博物馆、美术馆、剧院、图书馆 | 无柱大跨、光控制 |
| 教育 | 大学、中小学、幼儿园 | 庭院、连廊 |
| 医疗 | 医院、诊所 | 洁污流线、设备层 |
| 交通 | 机场、车站、地铁、码头 | 大跨结构、导向性 |
| 体育 | 体育场、体育馆 | 大跨屋盖 |
| 商业 | 购物中心、综合体 | 中庭、动线 |
| 市政/宗教 | 市政厅、法院、教堂、清真寺 | 象征性、公共性 |
| 工业 | 厂房、仓储、数据中心 | 模块化、设备 |
| 景观/设施 | 观景塔、桥梁、公园服务 | 结构即地标 |

---

## 5. 高层形态与结构体系

### 5.1 竖向形态
- 等截面棱柱 prismatic
- 退台 setback（逐段收进）
- 收分 tapered / 锥形（斜柱连续收进）
- 纺锤形（先扩后收）
- 扭转 twisting（逐层旋转）
- 切削/斜切（规则几何体切削）
- 悬挑/架空/连体
- 自由曲面 freeform

### 5.2 抗侧结构体系（Fazlur Khan 谱系）
1. 刚性框架 rigid frame
2. 剪力墙 shear wall
3. 框架-剪力墙
4. 框架-核心筒 frame + core（最常见）
5. 筒体 tube：框筒 / 支撑筒 / 斜交网格筒 diagrid
6. 筒中筒 tube-in-tube
7. 束筒 bundled tube（西尔斯大厦）
8. 巨型框架/巨柱 mega frame / mega column
9. 外伸臂 outrigger + 腰桁架 belt truss
10. 悬挂/张拉体系

### 5.3 核心筒与竖向交通
- 中心核心 / 偏心核心 / 分离核心 / 中庭核心
- 天空大堂 sky lobby、避难层、双层轿厢电梯
- 设备层/机电夹层、风力发电层

### 5.4 高度等级（CTBUH）
高层 < 300m；超高层 ≥ 300m；巨高层 ≥ 600m

> 参考：土木在线[超高层结构体系](https://bbs.co188.com/thread-10335579-1-1.html)、[锥形体型与结构](https://bbs.co188.com/thread-10331206-1-1.html)。

---

## 6. 立面 / 表皮体系

| 体系 | 说明 | 资产库模块 |
|---|---|---|
| 打孔窗 punched | 实墙 + 洞口 | 实墙板 + 窗洞 |
| 横向带窗 ribbon | 通长玻璃带 | `*_ribbon_window` |
| 整面幕墙 curtain wall | 框架式/单元式 | `*_panel_fixed`、转角单元 |
| 双层皮 double-skin | 呼吸幕墙、腔体 | 双层单元、空腔 |
| 遮阳 brise-soleil | 竖向/横向翼、百叶 | `*_sunshade_fin`、百叶板 |
| 粗野混凝土 | 清水/粗骨料、巨柱 | `*_concrete_panel`、巨柱 |
| 参数化表皮 | 异形板、渐变 | 渐变板序列 |
| 媒体立面 | LED/灯光 | 灯带板 |
| 垂直绿化 | 绿墙、种植槽 | `green_wall`、花池 |
| 开敞外廊/阳台 | 空中花园 | 挑台、阳台、栏杆 |

**色板纪律**：一个塔楼最多 3 个主色 + 1 个强调色；同流派用同一色板。

---

## 7. 性能 / 可持续 / 气候响应

- 被动式：朝向、体形系数、遮阳、自然通风、热质、天井烟囱效应
- 高性能围护：Low-E、双层皮、光伏一体化 BIPV、电致变色
- 绿化：屋顶花园、垂直绿化、雨水回收
- 材料：低碳混凝土、CLT 木结构高层、再生钢、耐候钢
- 标准：LEED / BREEAM / 中国绿建三星 / 被动房
- 趋势：碳中和、隐含碳、自适应再利用、模块化拆卸

> 参考：[Adaptive reuse & circular city](https://doi.org/10.3389/fbuil.2025.1561982)。

---

## 8. 标签体系（落到 pack/module 上）

统一标签前缀，便于检索：

```
period:      pre-modern | early-modern | international | postwar | late-modern | postmodern | contemporary | 2020s
movement:    chicago | art-nouveau | secession | werkbund | de-stijl | constructivism | bauhaus |
             expressionism | international | functionalism | brutalism | structuralism | metabolism |
             late-modern | high-tech | postmodern | deconstructivism | neo-rationalism | minimalism |
             neo-modern | critical-regionalism | tropical-modern | parametric | digital |
             sustainable | neo-brutalism | adaptive-reuse
region:      de | nl | fr | it | uk | es | nordic | soviet | us | ca | br | mx | ar | cl |
             jp | cn | kr | sea | in | me | af | au
typology:    office-tower | residential | hotel | cultural | education | healthcare | transport |
             sports | commercial | civic | industrial | landscape
form:        prismatic | setback | tapered | spindle | twisted | cantilever | freeform | bundle
structure:   rigid-frame | shear-wall | frame-core | tube | bundled-tube | diagrid | mega-frame |
             outrigger | suspension
facade:      punched | ribbon | curtain-wall | double-skin | brise-soleil | exposed-concrete |
             parametric-skin | media | green-wall | balcony
material:    white-concrete | gray-concrete | exposed-concrete | glass | steel | brick | wood |
             stone | terracotta | corten | copper
performance: passive | green-roof | green-wall | bipv | cross-ventilation | low-carbon
interior:    atrium | sky-lobby | lobby | courtyard | double-height
props:       street-furniture | vehicle | signage | planter | lamp | bench
```

---

## 9. 映射到 structworkshop 资产库

### 9.1 现有 `packs/modern-arch`（20 模块）

| 类别 | 模块 |
|---|---|
| facade | `mw_panel_fixed`、`mw_ribbon_window`、`mw_corner_glass`、`sunshade_fin`、`green_wall` |
| corridor | `modern_corridor` |
| rooms | `modern_office`、`modern_lobby` |
| core | `elevator_core` |
| stairs | `stairwell_modern` |
| bridge | `sky_bridge` |
| structure | `concrete_column_modern`、`atrium_balcony` |
| roof | `roof_crown` |
| props | `street_lamp_modern`、`car_sedan`、`car_van`、`armor_panel`、`bench_modern`、`billboard` |

对应分类坐标：`period:contemporary` · `movement:neo-modern/minimalism` · `typology:office-tower` · `facade:curtain-wall/ribbon` · `material:white-concrete/glass`。

### 9.2 建议的 pack 规划（每个流派/地域一包）

| pack | 风格 | 必备模块部位 |
|---|---|---|
| `international-style` | 国际式 | 白墙带窗、自由平面、屋顶花园、底层架空 |
| `brutalist-modern` | 粗野 | 清水混凝土板、巨柱、雕塑楼梯、塔座 |
| `high-tech` | 高技 | 外露桁架、斜撑、设备表现、玻璃中庭 |
| `postmodern` | 后现代 | 拱、符号构件、色彩饰面、拼贴立面 |
| `parametric` | 参数化 | 渐变板、双曲面板、流线体块、鳍 |
| `nordic` | 北欧 | 砖木、坡屋顶、暖色、人性尺度、庭院 |
| `soviet-modern` | 苏联/东欧 | 对称、纪念性、马赛克、混凝土 |
| `tropical-modern` | 热带 | 深遮阳、外廊、水院、白墙、绿植 |
| `metabolism` | 代谢 | 舱体插件、巨构框架、连接体 |
| `neo-brutalism` | 新粗野 | 3D 打印肌理、粗面、社区广场 |
| `sustainable` | 生态 | 绿墙、遮阳、木构、光伏、屋顶花园 |

### 9.3 模块"部位"命名规范

```
<部位>_<风格变体>_<序号>
facade   :  facade_intl_panel / brut_core / trop_brise
core     :  core_elev_2 / core_stair
floor    :  floor_std / floor_sky_lobby
structure:  struct_column / struct_diagrid
roof     :  roof_crown / roof_garden / roof_mech
room     :  room_office / room_lobby / room_atrium
props    :  prop_lamp / prop_car / prop_bench / prop_sign
```

### 9.4 与提示词/composition 的关系

- **资产包 = 词汇表**（有哪些部件可用）
- **composition = 语法**（如何拼成一类建筑）
- **prompt = 风格指令**（用哪套词汇 + 哪些参数 + 配色纪律 + 验收标准）
- 三者组合：`movement:high-tech` 的词汇包 + `typology:office-tower` 的 composition + `prompts/glass-tower.md` 的提示词

---

## 10. 快速选择表

| 用户需求 | 推荐 pack | 关键模块 | 参考流派 |
|---|---|---|---|
| 白玻璃现代塔 | `modern-arch` | panel/ribbon/corner/lobby | neo-modern |
| 粗野混凝土巨构 | `brutalist-modern` | core/巨柱/雕塑楼梯 | brutalism |
| 骨架外露科技感 | `high-tech` | 桁架/斜撑/中庭 | high-tech |
| 曲线流线体型 | `parametric` | 渐变板/鳍/自由体块 | parametric |
| 温暖人性住宅 | `nordic` | 砖木/坡屋顶/庭院 | nordic |
| 热带高湿气候 | `tropical-modern` | 遮阳/外廊/水院 | tropical-modern |
| 纪念性对称 | `soviet-modern` | 对称裙房/纪念柱 | socialist-modern |
| 舱体巨构 | `metabolism` | 舱体/核心塔 | metabolism |
| 绿色低碳 | `sustainable` | 绿墙/木构/光伏 | sustainable |
| 老楼改造 | `adaptive-reuse` | 新旧并置板 | adaptive-reuse |

---

## 11. 主要来源

- Wikipedia: [Modern architecture](https://en.wikipedia.org/wiki/Modern_Architecture) · [International Style](https://en.wikipedia.org/wiki/International_Style) · [Timeline of architectural styles](https://en.wikipedia.org/wiki/Timeline_of_architectural_styles) · [Critical regionalism](https://en.wikipedia.org/wiki/Critical_regionalism) · [Tropical Modernism](https://en.wikipedia.org/wiki/Tropical_Modernism)
- 中文维基: [现代主义建筑](https://zh.wikipedia.org/wiki/%E7%8E%B0%E4%BB%A3%E4%B8%BB%E4%B9%89%E5%BB%BA%E7%AD%91) · [后现代主义建筑](https://zh.wikipedia.org/zh-hans/%E5%90%8E%E7%8E%B0%E4%BB%A3%E4%B8%BB%E4%B9%89%E5%BB%BA%E7%AD%91)
- 有方: [高技派建筑简史](https://www.archiposition.com/items/20180525105015)
- Nippon.com: [代谢派](https://www.nippon.com/cn/images/i00057/) · MOT TIMES 代谢派专题
- ArchDaily: [Gulf Modernism](https://www.archdaily.com/1041515/oil-glass-and-identity-gulf-modernism-between-global-image-and-local-climate)
- 土木在线: [超高层结构体系](https://bbs.co188.com/thread-10335579-1-1.html) · [锥形体型与结构](https://bbs.co188.com/thread-10331206-1-1.html)
- MDPI: [Parametricism assessment](https://doi.org/10.3390/buildings14092656) · [Adaptive reuse](https://doi.org/10.3389/fbuil.2025.1561982)
