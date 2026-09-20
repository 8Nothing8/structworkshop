# mckit — 建筑语法与方块语义（层 1）

「**怎么把房子写出来**」与「**方块该怎么连**」这两件事都在这里：
上半是建筑语法（幕墙单元、楼层线、竖肋、女儿墙、楼梯、广场），
下半是 Minecraft 非整方块的状态解算（墙/栅栏/玻璃板/楼梯/门/拉杆/红石），
外加两条通往几何的路（参数曲线、`.glb`/`.obj` 倒模成体素）。

```python
from mckit import connect as C, update as U
vox, pal = U.update_volume(vox, pal, box=(x0, y0, z0, x1, y1, z1))  # 墙/栅栏/楼梯按邻居重算
```

## 分层位置

| | |
|---|---|
| **上游** | `mccore`（结构读写、调色板） |
| **下游** | `mcrender`（`--update-states` 渲染前补状态）· `mctools`（连接类工具）· `mcstudio`（编辑器「方块更新」）· `compositions/*`（生成器直接用语法件） |
| **不做** | 不做 IO / 不渲染 / 不装配（那些是 mccore / mcrender 的事） |

## 模块

| 文件 | 作用 |
|---|---|
| `grammar.py` | 现代建筑语法：`Kit` + `facade_window` `floor_band_ring` `pier` `parapet` `stair_flight` `roof_box` `tank` `planter` `plaza_pave` |
| `connect.py` | **连接状态求解**：`wall_state` `fence_state` `pane_state` `stair_shape`/`stair_state` `door_pair` `trapdoor_state` `chain_state` `lantern_state` `end_rod_state` `button_state` `lever_state` …（只看邻居，不重写已有属性） |
| `update.py` | 把上面的解算接到「体素 + 调色板」上：`update_volume()` 就地改，`updated_copy()` 出副本，`family()`/`bare()` 认方块族 |
| `voxbrush.py` | 体素笔刷工具包：`Grid`、SDF 基本体（`sd_round_box` `sd_ellipsoid` `sd_capsule`）、`smoothstep` —— 程序化雕塑的基础 |
| `meshvox.py` | **三维模型 → 体素**：`load_gltf/load_obj/voxelize_model` + 材质映射（`mat_map_default/bone/metal/brass`）—— 枪之恶魔那条线的引擎 |
| `office.py` | 办公系列共享布局（历史件，现代建筑系列早期用过） |

## 命令行 / 自检

本包没有统一入口（它是库）。每个模块自带 `__main__` 自检或示例：

```bash
python -m mckit.connect            # 连接状态示例
python -m mckit.update             # 体素级更新示例
python -m mckit.grammar --self-test
```

## 关键 API

| 名字 | 用途 |
|---|---|
| `update.update_volume / updated_copy` | 按邻居重算墙/栅栏/玻璃板/楼梯等状态（**就地**改 palette 状态串） |
| `connect.wall_state / pane_state / stair_shape / door_pair` | 单个方块的状态解算（可独立调用） |
| `grammar.Kit` + 形态函数 | 生成幕墙/楼层线/女儿墙/楼梯/广场 |
| `meshvox.voxelize_model` | `.glb`/`.obj` → `(voxels, palette)`（`tools/voxelize_models.py` 的引擎） |
| `voxbrush.Grid` + SDF | 体素布尔/平滑/笔刷 |

## 约定 / 不变量

* **只改状态字符串里已有的属性**：手写/脚本设的 `E.state` 不会被覆盖（自动定向的边界）。
* 自支撑清单与 `mcqa` 判「悬浮」的口径一致（`is_full_solid` / `covers_center`）。
* **域专用件不进本包**：参数曲线与红石件曾是本包成员，现已随「只服务一个域的脚本归组合」
  的规则搬进 `compositions/math-cube/{curves,redstone}.py`（规则见 `packages/ARCHITECTURE.md` §4）。
  它们要是有了第二个消费者，就再提升为引擎件。
* `meshvox` 的贴图→材质映射是「明度斜坡」，不是贴图投影（Nucleation 那条备选路见
  `NUCLEATION-调研报告.md`）。

## 测试

```bash
python tests/blockstate_smoke.py        # 墙/栅栏/栏杆/玻璃板/楼梯规则 + 渲染前补状态
python tests/math_cells_smoke.py        # curves / redstone 断言
python tests/math_exhibits_smoke.py     # 数学域展品（曲线 + 红石时钟）
python -m mcrender.cli <schem> --views iso --update-states   # 渲染前按邻居补连接（只读）
```
