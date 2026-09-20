# ModuleSpec 规范(模块元数据)

模块 = `<name>.schem`(兼容 `.litematic`)+ `<name>.module.json`(sidecar,优先)。
`module_lib.py embed` 可把 spec 写入 litematic 的 `Metadata.ModuleSpec`(单文件分发用)。

## 完整示例

```json
{
  "id": "stair_2floor",
  "category": "stairs",
  "version": 1,
  "description": "5x7x4 楼梯间: 北墙下口(y1-2)与上口(y5-6)",
  "tags": ["stairs", "stone", "vertical"],
  "grid": {"size": [5, 7, 4], "position": [0, 0, 0]},
  "axis": "x",
  "flip": false,
  "ports": [
    {"id": "entry", "type": "passage", "face": "north",
     "origin": [1, 1], "size": [2, 2], "tags": ["lower"]},
    {"id": "exit", "type": "passage", "face": "north",
     "origin": [5, 1], "size": [2, 2], "tags": ["upper"]},
    {"id": "duct", "type": "vent", "face": "up",
     "shape": "circle", "origin": [2, 2], "size": [3, 3]}
  ],
  "notes": ""
}
```

- `grid.size` = `[x, y, z]`(长、高、宽),必须与结构文件区域一致
- `axis` = 模块主轴(文档用),`flip` 保留
- 接口 `origin`/`size` 的含义取决于 `face`（`shape` 缺省 = `rect`）：

| face | origin | size |
|---|---|---|
| west / east | `[y0, z0]` | `[高度, z向宽度]` |
| north / south | `[y0, x0]` | `[高度, x向宽度]` |
| up / down | `[x0, z0]` | `[x向宽度, z向深度]` |

`shape: "circle"` 时 `origin` 是**圆心**、`size` = `[直径, 直径]`（例：`origin:[2,2], size:[3,3]`
= 直径 3、半径 1 格的圆）。圆形只是**几何意图**（给人/AI 看、画出来是个圆环）：
引擎匹配依旧按**外接矩形**（`module_lib.port_bbox()` 把圆心展开成盒子），实心空心都不看，
90° 旋转后圆还是圆（`assemble.transform_port`）。**接口只是参考**，可以不按接口拼。

## 方向约定

- 局部坐标: +x 东、+y 上、+z 南;`face` 是**开口朝哪边**(门开在南墙 → face=south)
- 相邻模块连接必须是**面对面**:A 的 south 接口 ↔ B 的 north 接口(OPP 规则)
- 旋转:引擎只做 90° 步进(rot 0/1/2/3,顺时针俯视);旋转后面与 origin 由
  `assemble.transform_port` 自动换算,写 spec 时不用管

## 接口类型与兼容表

| 类型 | 含义 | 可与 |
|---|---|---|
| `passage` | 人行通道(门洞/走廊口) | passage, door, stair_up, stair_down |
| `door` | 带门的通道 | 同上 |
| `stair_up` / `stair_down` | 楼梯的上/下口 | passage 类 |
| `shaft` | 竖井(电梯/管道井) | shaft |
| `redstone_in` / `redstone_out` | 红石使能端 / 输出端 | 互配 |
| `power_in` / `power_out` | 机械能/电源 | 互配 |
| `fluid_in` / `fluid_out` | 水道/岩浆 | 互配 |
| `item_in` / `item_out` | 漏斗/物品管道 | 互配 |
| `anchor` | 对齐标记(不连通) | anchor |
| `light` | 灯光(信息用) | — |

红石时序:模块的 spec `notes` 里写清楚(如 `"enable: 高电平≥2tick, 输出: 4tick 脉冲"`)。
引擎不仿真时序 —— 复杂红石机器先单独渲染 + 单测,再入模块库。

## 写模块的规则(血泪)

1. **接口开口必须在体素里真的挖洞**。spec 声明了 port 但墙是实心的 = 渲染图里没有门。
2. air 必须是 palette 索引 0(`demo_modules.build` 已处理;自己写时注意)。
3. 门的上下半、墙/栅栏连接等 blockstate 问题 → 过 `minecraft-block-models` 的 connect.py。
4. 楼梯接口高度要对齐:层高 4 时,下层口 `origin=[1,·]`、上层口 `origin=[5,·]`。
5. 模块内不能有「悬挑着但无支撑」的装饰 —— 否则质检会报(锁链/灯笼/树叶自动豁免)。
6. 写完 `module_lib.py embed` 前先 `scan`,索引里有 `[!spec]` 标记说明 spec 校验失败。
