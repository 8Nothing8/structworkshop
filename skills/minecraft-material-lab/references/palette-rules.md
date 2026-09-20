# 材质规则速查（做旧 · 渐变 · 冲突规避）

> 规则 0：**有参考图就必须和原图比较颜色**（并排 + 量化：主色/明度分布/暖冷比），每次定色与交付前各一次。
>
> 规则 0b：选材前先 `python -m mcmaterials catalog --all`，再 `profile`/`match`；字段用
> `class`（质感）、`grain`（横/竖纹）、`alpha`（透明度）、`is_full`（是否完整方块）。
> 外墙 = `is_full=true`（玻璃等透明方块可用）；同表面不要混用相反质感/相反纹路。

## 一、选材顺序

1. `match` 参考照片：拿到 4-6 个主色与最接近方块（看 `d=` 距离，<30 基本同色）。
2. `ramp` 排明度：把候选排成"亮 → 暗"，确定基底/过渡/阴影/缝 四档。
3. 写权重：基底 30-50%，亮色 10-20%，过渡 15-25%，深色总计 ≤12%（否则墙会脏）。
4. 用稳定噪声（cell hash）而不是随机数：同坐标每次生成结果一致，便于复现与审查。

## 二、竖向渐变（楼层）

```
rel = (y - base) / height
下半段（rel<0.35）：权重往深色偏 +10%（雨水/尘土）
上半段（rel>0.8） ：权重往亮色偏 -8%（冲刷干净）
```

## 三、条状做旧（水渍）

- 起点：楼板带下一格、窗台下一格；
- 长度：2-5 格连续；概率：面 0.2（轻微做旧）~0.45（重度）；
- 颜色：`gray_concrete` / `deepslate` / `light_gray_terracotta` / `brown_terracotta`；
- 不要全墙均匀撒点——会变成"麻子"。

## 四、勒脚与过渡

| 部位 | 推荐 |
|---|---|
| 最底排 | `tuff` `cobblestone` `stone` `light_gray_terracotta` `brown_terracotta` 混搭 |
| 过渡排 | `stone` `gray_concrete` `mossy_cobblestone`(少量，潮湿感) |
| 墙面 | 回到暖浅灰混搭 |

## 五、冲突清单（暖灰苏式外墙）

- 冷：`diorite` `calcite` `white_concrete`(大面积)、`polished_diorite`
- 黄红：`sandstone` `smooth_sandstone` `granite` `brown_terracotta`(大面积)
- 绿：`mossy_cobblestone` `mossy_stone_bricks`（勒脚可少量）
- 太黑：`black_concrete`（缝用 `gray_concrete`/`deepslate` 就够）
- 高饱和金属：`cut_copper`（橙）、`weathered/oxidized_copper`（绿）——小面积或改用镀锌灰

## 六、细节件用法（不完整方块）

| 想要 | 用 |
|---|---|
| 百叶/格栅 | `iron_trapdoor`（facing/half 配对）、`repeater` |
| 仪表面板 | `comparator`、`daylight_detector` |
| 阀门/按钮 | `lever`、`stone_button`（face=wall） |
| 线缆/吊索 | `iron_chain`（axis=y） |
| 天线/拉杆 | `lightning_rod`、`end_rod`（facing） |
| 烟囱口/炉火 | `campfire`（lit 控制冒烟） |
| 检修平台 | `scaffolding` |
| 管道/落水管 | `light_gray_concrete`/`iron_bars` 组合，小面积 `exposed_copper` |

> 不完整方块的连接/朝向属性必须显式写对（见 `minecraft-block-models` skill）。
