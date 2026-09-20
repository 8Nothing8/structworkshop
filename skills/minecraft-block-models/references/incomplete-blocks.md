# 不完整方块完整属性表

所有属性名/值都按 **Minecraft Java 26.2**（数据版本 4903）实测。
写结构文件时属性可以省略，省略的取**默认值**；但连接类属性默认都是“不连”，
所以生成建筑时必须显式写。

> 通用：`waterlogged=true|false` 出现在几乎所有不完整方块上，表示方块内部是否含水。
> 本渲染器不额外渲染 waterlogged 的水（模型里没有水），但属性要写对，否则进游戏不对。

---

## 1. 半砖 slab

| 属性 | 值 | 说明 |
|---|---|---|
| `type` | `bottom` / `top` / `double` | 下半砖 / 上半砖 / 双层（整方块） |
| `waterlogged` | `true` / `false` | |

```json
{"Name":"minecraft:polished_deepslate_slab",
 "Properties":{"type":"top","waterlogged":"false"}}
```

- 双层半砖是**一个整方块**，用 `type=double`，不要用两个 `bottom` 叠起来。
- 竖直方向：`bottom` 占 y=0~0.5，`top` 占 y=0.5~1。
- 双半砖 `double` 的模型是整方块（渲染器会走整方块快速路径）。

## 2. 楼梯 stairs

| 属性 | 值 | 说明 |
|---|---|---|
| `facing` | `north` / `south` / `west` / `east` | 楼梯**高的一侧**朝向；`facing=east` 表示台阶从西向东升高 |
| `half` | `bottom` / `top` | 正常 / 倒挂（贴在天花板下） |
| `shape` | `straight` / `inner_left` / `inner_right` / `outer_left` / `outer_right` | 转角形状 |
| `waterlogged` | `true` / `false` | |

**shape 必须根据邻居楼梯算**，否则转角处缺角：

```python
# 前方（facing 方向）的楼梯与自身 facing 垂直 -> outer_left / outer_right
# 后方（facing 反方向）的楼梯与自身 facing 垂直 -> inner_left / inner_right
# 否则 straight
# 左右判定：邻居 facing == CCW[facing] 则为 *_left，否则 *_right
# CCW: north->west, west->south, south->east, east->north
```

用 `packages/mckit/connect.py  (python -m mckit.connect): stair_state(pos, facing, at, facing_at=...)` 直接算。

常见错误：
- 只写 `facing` 不写 `shape` → 转角处两个楼梯互相穿插。
- `facing` 理解反了：`facing` 是**台阶升高的方向**，不是玩家看的方向。

## 3. 墙 wall

| 属性 | 值 | 说明 |
|---|---|---|
| `up` | `true` / `false` | 是否有中心柱（上方是完整方块或另一堵墙时为 true） |
| `north` `south` `east` `west` | `none` / `low` / `tall` | 四向连接 |
| `waterlogged` | `true` / `false` | |

连接规则：

| 邻居 | 连接 |
|---|---|
| 完整固体方块、另一堵墙 | `tall` |
| 栅栏、栅栏门、半砖、楼梯、玻璃板/铁栏杆 | `low` |
| 空气、其他 | `none` |

- 一整排墙：中间每格 `north=tall,south=tall,up=true`；两端 `north=tall,south=none`。
- 转角：`north=tall,east=tall`。
- **只写墙的方块名不写四向** → 每格都只有中心柱（因为默认四向 none、up=true）。

## 4. 栅栏 fence

| 属性 | 值 |
|---|---|
| `north` `south` `east` `west` | `true` / `false` |
| `waterlogged` | `true` / `false` |

连接规则：**只连完整固体方块 + 其他栅栏 + 栅栏门**。
不连玻璃、树叶、半砖、楼梯、墙、玻璃板。

- 一排栅栏：中间 `north=true,south=true`，两端只连内侧。
- 栅栏转角：`north=true,east=true`。

## 5. 栅栏门 fence_gate

| 属性 | 值 |
|---|---|
| `facing` | `north` / `south` / `west` / `east` |
| `open` | `true` / `false` |
| `in_wall` | `true` / `false`（两侧是墙时为 true，会让门栏降低一点） |
| `powered` | `true` / `false` |

## 6. 玻璃板 pane / 铁栏杆 iron_bars

| 属性 | 值 |
|---|---|
| `north` `south` `east` `west` | `true` / `false` |
| `waterlogged` | `true` / `false` |

连接规则：**完整固体方块 + 同类（玻璃板/铁栏杆互连）**。
不连栅栏、墙、树叶。

- 不写四向 → 每格只有一根中心柱。
- 一整面玻璃幕墙：`north=true,south=true`（沿南北向延伸的那一面）。

## 7. 门 door

| 属性 | 值 |
|---|---|
| `facing` | `north` / `south` / `west` / `east` |
| `half` | `upper` / `lower` |
| `hinge` | `left` / `right` |
| `open` | `true` / `false` |
| `powered` | `true` / `false` |

**门占两格**：`(x,y,z)` 放 `half=lower`，`(x,y+1,z)` 放 `half=upper`，
两格的 `facing/hinge/open/powered` 必须完全一致。只放 lower 会渲染成半扇门。

`hinge` 决定门轴向哪边转：`hinge=left` 表示从门的正面看，合页在左边。

## 8. 活板门 trapdoor

| 属性 | 值 |
|---|---|
| `facing` | `north` / `south` / `west` / `east` |
| `half` | `top` / `bottom` |
| `open` | `true` / `false` |
| `powered` | `true` / `false` |
| `waterlogged` | `true` / `false` |

- 关着（`open=false`）：贴在方块的下表面（`half=bottom`）或上表面（`half=top`）。
- 打开（`open=true`）：竖起来贴住 `facing` 那一面。
- 活板门只占一格，不需要配对。

## 9. 其他小方块

| 方块 | 属性 | 默认 |
|---|---|---|
| `chain` / `waxed_*_chain` | `axis=x|y|z`，`waterlogged` | `axis=y` |
| `lantern` / `soul_lantern` | `hanging=true|false`，`waterlogged` | `hanging=false` |
| `end_rod` | `facing=up|down|north|south|west|east` | `up` |
| `*_button` | `face=floor|wall|ceiling`，`facing`，`powered` | `face=wall,facing=north` |
| `lever` | `face=floor|wall|ceiling`，`facing`，`powered` | `face=wall,facing=north` |
| `grindstone` | `face=floor|wall|ceiling`，`facing` | `face=wall,facing=north` |
| `*_carpet` | 无（1/16 厚） | |
| `snow` | `layers=1..8` | `layers=1` |
| `*_candle` | `candles=1..4`，`lit`，`waterlogged` | `candles=1` |
| `sea_pickle` | `pickles=1..4`，`waterlogged` | `pickles=1` |
| `turtle_egg` | `eggs=1..4`，`hatch=0..2` | `eggs=1` |
| `torch` / `soul_torch` | 无 | |
| `wall_torch` / `soul_wall_torch` | `facing=north|south|west|east` | `north` |
| `ladder` | `facing`，`waterlogged` | `north` |
| `flower_pot` | 无 | |
| `pink_petals` | `facing`，`flower_amount=1..4` | `facing=north` |

## 10. 生成时的推荐流程

1. 先用一个 `dict[(x,y,z)] = block_name`（或 numpy 占位网格）把建筑摆出来，
   不完整方块先只记“种类 + 朝向”这样的最小信息。
2. 对每个不完整方块调用 `connect.py` 里对应的函数，得到完整属性：
   - 墙 → `wall_state`，栅栏 → `fence_state`，玻璃板 → `pane_state`，
     楼梯 → `stair_state`，门 → `door_pair`，活板门/锁链/灯笼 → 对应函数。
3. 把 `(block, props)` 去重写进结构文件的 palette。
4. 渲染检查：
   ```bash
   python -m mcrender.cli build.schem --views iso,front,top --scale 3
   ```
5. 用 `--list` 检查有没有 `render=fallback`（说明模型缺失/方块名写错）。
