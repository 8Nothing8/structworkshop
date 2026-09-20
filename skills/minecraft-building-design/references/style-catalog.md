# 六大风格速查库

用法:选定风格后,照抄「结构语法」搭骨架,从 palette 表按角色选方块。
所有方块名均已对照当前版本映射表校验(26.2 / DataVersion 4903)。
完整方块清单见 `skills/minecraft-block-models/data/all_blocks.json`。

---

## 1. 北欧中世纪(Norse/Medieval)

**关键词**:高坡屋顶、木构白墙、石基、烟囱、错落体块

**结构语法**:
- 石基座(0-2 层) + 木骨白墙(2-5 层) + 高坡屋顶(45-55°,出挑 1-2 格)
- 体块错落:主屋 + 侧翼 + 塔楼,不要一个方盒子
- 外露木骨架:墙面每 3-4 格一道竖木柱 + 横梁,白墙填格
- 窗:2×3 小窗 + 木百叶(活板门),窗台下半砖

| 角色 | 方块 |
|---|---|
| 石基座 | `cobblestone`, `stone_bricks`, `mossy_cobblestone`(底部点缀) |
| 木骨架 | `spruce_log`, `dark_oak_log`(转角/立柱) |
| 墙体 | `white_wool` / `oak_planks`,半木结构用 `white_terracotta` |
| 屋顶 | `spruce_stairs` / `dark_oak_stairs` + 同色 `_slab` 收边 |
| 烟囱 | `stone_bricks` + `stone_brick_wall`,顶部 `campfire` |
| 门窗 | `spruce_door`, `spruce_fence`, `glass_pane`(木框间) |
| 点缀 | `lantern(hanging=true)`, `hay_block`, `barrel`, `flower_pot` |

**翻车点**:屋顶坡度太缓(像现代平顶);木骨架间距不匀;玻璃板忘记写连接属性(变成柱子)。

## 2. 现代主义(Modernist)

**关键词**:水平线条、玻璃幕墙、白灰配色、平顶女儿墙、几何纯粹

**结构语法**:
- 体块:水平板楼 + 竖向塔楼咬合,体块之间留 2-4 格缝
- 立面:楼层线(每 4 格一圈 `light_gray_concrete`) + 玻璃带 + 白色实体墙三段横分
- 幕墙:玻璃大面积成带,不要每格一窗;竖挺每 4-6 格一道(白/深灰 1 格宽)
- 首层:架空或全玻璃,入口雨棚(白色半砖悬挑 2-3 格)

| 角色 | 方块 |
|---|---|
| 主体墙 | `white_concrete`, `light_gray_concrete` |
| 结构线脚 | `gray_concrete`, `smooth_quartz`, `quartz_slab` |
| 玻璃 | `glass`, `white_stained_glass`, `light_gray_stained_glass`, `tinted_glass` |
| 竖挺/框架 | `white_concrete`(细柱), `iron_bars`, `iron_trapdoor` |
| 基座/地面 | `smooth_stone`, `smooth_stone_slab`, `polished_deepslate` |
| 屋顶 | 平顶 + 女儿墙 1 格,设备间 `gray_concrete` |
| 灯光 | `sea_lantern`(内透), `end_rod`(轮廓) |

**翻车点**:玻璃用成实心彩色块(要留通透感);全楼纯白无灰层次(糊成一片);没有楼层线。

## 3. 赛博朋克(Cyberpunk)

**关键词**:黑底、霓虹、悬挑广告、管线、雨夜感

**结构语法**:
- 深色主体(`black_concrete`/`obsidian`) + 霓虹色带(`cyan`/`magenta` 1 格宽线脚)
- 竖向交通:外挂管线(`iron_bars`/`iron_chain` 竖向排布)、外露结构桁架
- 招牌:屋顶/墙面悬挑广告牌(发光方块条 + 深色边框),用 `iron_trapdoor` 做支架
- 底层:店铺门面密集开洞,二层以上收窄退台
- 霓虹密度:每面墙 1-2 条主色带,不要满墙发光

| 角色 | 方块 |
|---|---|
| 主体 | `black_concrete`, `gray_concrete`, `obsidian`(基座) |
| 霓虹 | `sea_lantern`, `redstone_lamp`, `cyan_concrete`/`magenta_concrete`(灯罩色块) |
| 玻璃 | `black_stained_glass`, `cyan_stained_glass`, `tinted_glass` |
| 结构/管线 | `iron_bars`, `iron_block`, `iron_chain(axis=y)`, `iron_trapdoor` |
| 金属表皮 | `copper_block`, `oxidized_copper`, `weathered_copper`, `polished_blackstone` |
| 点缀 | `crying_obsidian`, `amethyst_block`, `shroomlight` |

**翻车点**:霓虹满天星(每面墙限 1-2 条);纯黑无层次(用灰/氧化铜分块);广告牌悬挑无支架。

## 4. 日式(Japanese)

**关键词**:深色大檐、白墙木框、水平延展、庭院

**结构语法**:
- 台基(石 1-2 层)→ 木柱框架(柱距 4 格)→ 大出挑缓坡屋顶(檐挑 2 格)
- 墙体:木框 + 白墙/障子,横向推拉窗(玻璃板连成带)
- 屋顶:深色半砖/台阶,檐口下用 `dark_oak_trapdoor` 收边
- 庭院:碎石(`gravel`)+ 石灯笼(`polished_andesite` 柱 + `lantern`)+ 松(`spruce_leaves`)

| 角色 | 方块 |
|---|---|
| 台基 | `stone_bricks`, `polished_andesite`, `granite` |
| 柱/框架 | `dark_oak_log`, `spruce_log`(转角), `dark_oak_fence`(细柱) |
| 墙体 | `white_terracotta`, `white_wool`, `spruce_planks`(墙裙) |
| 障子窗 | `white_stained_glass` + `dark_oak_fence` 分格 |
| 屋顶 | `deepslate_tiles`, `blackstone`, `dark_prismarine` + 同色 `_slab`/`_stairs` |
| 檐口 | `dark_oak_trapdoor(half=top)`, `dark_oak_slab` |
| 点缀 | `lantern(hanging=true)`, `cherry_leaves`, `pink_petals`, `bamboo` |

**翻车点**:屋顶出挑不够(没有日式感);深色屋顶压在浅色墙上一刀切(缺檐口收边);门窗太西方(用玻璃带不用木门洞)。

## 5. 工业/蒸汽朋克(Industrial/Steampunk)

**关键词**:红砖、铸铁、铜、管道、高窗、烟囱群

**结构语法**:
- 红砖主体 + 铸铁(深色)柱梁外露 + 铜制构件点缀
- 高窗:2 宽 × 4-5 高,顶部拱券(台阶拱),铸铁窗棂(`iron_bars`)
- 屋顶:锯齿形(一排斜顶)或平顶带天窗;烟囱 2×2,比屋面高 8-15 格
- 管道:`iron_bars`/`iron_chain` 沿墙横走,拐角用 `iron_block`;阀门用 `piston`
- 地面:铁轨/木栈道(`oak_planks` + 枕木 `spruce_slab`)

| 角色 | 方块 |
|---|---|
| 主体 | `bricks`, `brick_stairs`/`_slab`(拱券/线脚), `brick_wall` |
| 结构 | `polished_blackstone`, `blackstone`, `iron_block`, `raw_iron_block` |
| 管道 | `iron_bars`, `iron_chain`, `iron_trapdoor`, `piston` |
| 铜构件 | `copper_block`, `cut_copper`, `oxidized_copper`, `weathered_copper` |
| 窗 | `gray_stained_glass`, `glass_pane`(铸铁框间), `iron_bars` |
| 地面 | `oak_planks`, `spruce_slab`, `cobbled_deepslate` |
| 灯光 | `redstone_lamp`, `lantern(hanging=true)`, `sea_lantern`(局部) |

**翻车点**:全楼红砖无深色结构线(糊);管道随意乱爬(沿柱梁走线);铜用得太多(≤10%)。

## 6. 废土(Wasteland)

**关键词**:破损、拼贴、沙尘、锈蚀、防御感

**结构语法**:
- 拼贴墙:大面 `terracotta` 打底,局部补丁(其他材质块 3-8 格一片)模拟修补
- 破损:墙面随机挖 1 格洞(不贯通)+ 边缘放 `cracked_stone_bricks`;转角缺角
- 层叠:新旧结构叠压(下半石砖 + 上半铁皮/木板),交接处有悬挑 1 格
- 防御:围栏(`iron_bars` 尖刺)、哨塔(四角)、门用 `iron_trapdoor` 拼
- 植物入侵:墙根 `mossy_cobblestone`,裂缝处 `oak_leaves` 探出

| 角色 | 方块 |
|---|---|
| 主体 | `terracotta`, `light_gray_terracotta`, `brown_terracotta`, `red_terracotta` |
| 破损/旧石 | `cracked_stone_bricks`, `mossy_stone_bricks`, `mossy_cobblestone`, `cobblestone` |
| 金属补丁 | `iron_bars`, `iron_trapdoor`, `gray_concrete`, `weathered_copper`, `oxidized_copper` |
| 木构 | `acacia_planks`, `oak_log`, `stripped_oak_log`(骨架) |
| 地面 | `coarse_dirt`, `dirt`, `gravel`, `sandstone`, `cut_sandstone`(残路) |
| 点缀 | `hay_block`, `chain`/`iron_chain`, `soul_lantern`, `cobweb`, `dead_bush` |

**翻车点**:破损做成均匀噪点(要有「片状补丁」逻辑);全图一个土色(用锈铜/灰混凝土提亮);破洞贯穿成真实漏洞(只挖表皮 1 格)。

---

## 混合风格规则

- 相邻风格要加**过渡带**:底层/基座用一种风格,上部渐变为另一种;直接换材质 = 贴图错误感
- 比例先行:两种风格拼合时先统一**体块语言**(横/竖、厚重/轻盈),再谈材质
- 保守策略:一个建筑只用一种风格 + 一种点缀色,先做纯再谈混
