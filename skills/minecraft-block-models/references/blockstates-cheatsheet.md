# Blockstate / 方块模型速查（渲染器内部约定）

这份文档解释 `mcrender.model` 如何把结构文件里的
`Name + Properties` 变成真实几何，以及 Minecraft 模型格式里那些容易踩的约定。
不需要读源码也能靠它排查“渲染出来形状不对”。

## 1. 解析链路

```
litematic palette entry {Name, Properties}
  └─ blockstates/<name>.json        变体 variants 或 multipart
       └─ models/block/<model>.json  可以继承 parent（链式）
            └─ textures: {"all": "block/oak_planks", ...}
                 └─ textures/block/oak_planks.png
```

- `variants`：键是 `prop=value,prop=value`。渲染器取**匹配属性最多**的那个变体；
  键为空字符串 `""` 匹配所有状态。
- `multipart`：每个部件 `when` 条件满足就叠加。`when` 可以是：
  - 单个字典：所有键都要匹配；
  - 列表：任一匹配（OR）；
  - `{"OR":[...]}` / `{"AND":[...]}`。
- 一个变体里可能有 `x` / `y` / `z` / `uvlock` / `weight`：
  - `x`/`y`/`z` 是绕方块中心的旋转，**Minecraft 里角度取反**（`x=90` 实际绕 x 轴转 -90°）；
  - `weight` 是随机权重（如草方块的花纹变体），渲染器取第一个。

## 2. 模型继承

```json
{"parent": "block/stairs",
 "textures": {"bottom": "block/oak_planks", "top": "block/oak_planks", "side": "block/oak_planks"}}
```

- `textures`：子模型覆盖父模型的同名变量；
- `elements`：子模型**整体替换**父模型的 elements（不是合并）；
- `ambientocclusion`：继承，子模型可覆盖；楼梯/玻璃板等模型常设为 `false`；
- `parent` 为 `builtin/entity`（箱子、床、告示牌等实体渲染方块）时没有 elements，
  渲染器会退化成纯色方块；
- 变量引用 `#all`、`#side` 可以多级套娃；最终必须落到 `block/...` 或 `item/...`。

## 3. elements（几何体）

```json
{"from": [0, 0, 0], "to": [16, 8, 16],
 "rotation": {"origin": [8, 8, 8], "axis": "y", "angle": 45, "rescale": true},
 "shade": true,
 "faces": {
   "north": {"uv": [0, 8, 16, 16], "texture": "#side",
             "cullface": "north", "tintindex": 0, "rotation": 90}
 }}
```

- 坐标单位是 **1/16 方块**（0~16）。
- `cullface`：如果该方向的邻居是**完整不透明方块**，这个面就不画（省掉看不见的面）。
  渲染器用“不透明整方块”网格判断。
- `tintindex`：0 表示用生物群系染色（草、树叶、水）；-1/缺省表示不染色。
- `rotation`（面的 `rotation`）是 **UV 旋转**，0/90/180/270。
- `shade:false`：该元素不做方向明暗（渲染器按 1.0 处理）。
- `rotation`（元素的 `rotation`）：绕 `origin` 旋转 `angle` 度；
  `rescale:true` 时 22.5° 缩放 0.5、45° 缩放 1/√2（否则旋转后出界）。

## 4. 默认 UV（没写 `uv` 时）

| 面 | 默认 uv `[u1,v1,u2,v2]` |
|---|---|
| down  | `[x0, 16-z1, x1, 16-z0]` |
| up    | `[x0, z0, x1, z1]` |
| north | `[16-x1, 16-y1, 16-x0, 16-y0]` |
| south | `[x0, 16-y1, x1, 16-y0]` |
| west  | `[z0, 16-y1, z1, 16-y0]` |
| east  | `[16-z1, 16-y1, 16-z0, 16-y0]` |

UV 原点在**左上角**，v 向下增大；`u` 选择器 0→u1、1→u2（旋转后同理）。

## 5. 面的顶点顺序

每个面 4 个顶点按 Minecraft `FaceBakery` 的“蝴蝶形”顺序（0,1,2,3），
两条对角线是 0–3 和 1–2。三角化必须用 `(0,1,2)+(2,1,3)` 或 `(0,1,3)+(0,3,2)`，
否则只画一半（本项目早期就踩过这个坑）。

```
up:    (0,1,1) (1,1,1) (0,1,0) (1,1,0)      # (x,y,z) 选择器
down:  (1,0,1) (0,0,1) (1,0,0) (0,0,0)
east:  (1,1,1) (1,0,1) (1,1,0) (1,0,0)
west:  (0,1,0) (0,0,0) (0,1,1) (0,0,1)
north: (1,0,0) (0,0,0) (1,1,0) (0,1,0)
south: (0,0,1) (1,0,1) (0,1,1) (1,1,1)
```

## 6. 渲染层

| 层 | 判定 | 渲染方式 |
|---|---|---|
| opaque | 贴图完全不透明 | 写深度缓冲，正常光照 |
| cutout | 贴图有 alpha=0 的像素 | alpha<0.5 丢弃（树叶、铁栏杆、玻璃板、栅栏） |
| translucent | 贴图有 0<alpha<255 的像素，或方块在玻璃/水/冰名单里 | 深度排序后混合（玻璃、水、冰、染色玻璃） |

渲染器还会对以下方块强制满亮 + 泛光（bloom）：荧石、海晶灯、菌光体、
蛙明灯、岩浆、灯笼、末地烛、发光浆果等。

## 7. 光照与 AO

- 面明暗用 Minecraft 固定值：上 1.0、下 0.5、南北 0.8、东西 0.6；
- 整方块面额外算**逐顶点 AO**（4 个角各采样 3 个邻居，`ao=0..3`，
  亮度 0.45/0.62/0.80/1.00），并按 AO 选择三角化对角线避免接缝；
- 不完整方块（模型元素）不做 AO，只做面明暗——和游戏里楼梯的观感接近
  （楼梯模型自带 `ambientocclusion:false`）。

## 8. 贴图

- 方块贴图基本都是 16×16；动画贴图是 16×N 的竖条，渲染器取**第一帧**；
- 所有用到的贴图打包成一张方形图集，最近邻采样 + 2~3 倍超采样抗锯齿；
- 缺贴图会显示成品红/黑格（用来一眼发现资源缺失）。

## 9. 版本 / 资源来源

- 资源来自 [mcmeta](https://github.com/misode/mcmeta)（从官方客户端 jar 提取的
  资源包 + 数据生成器报告），tag `<版本>-assets-tiny`；
- 映射表（所有方块的属性与默认值）在 `summary` 分支的 `blocks/data.json`；
- 缓存目录 `.cache/mcassets/<版本>/`，包含 `blockstates/`、`models/`、`textures/`、
  `_blocks_summary.json`；
- `--version` 可覆盖；默认按结构文件的 `MinecraftDataVersion` / `DataVersion` 自动映射
  （4903 → 26.2）。
