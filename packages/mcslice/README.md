# mcslice — 逆向：建筑 → 模块 / 风格包（层 2）

两个方向都是「**从已有建筑里把知识抽出来**」：

* `slice` —— 把一栋楼切成可复用模块（按楼层带切 / 按墙平面分解成房间与核心筒），
  自动识别开口生成接口（port），可选注册进资产包并生成装配计划（把楼再拼回去）。
* `learn` —— 从一栋楼里学出**风格包**：调色板与材料角色、层高、开间、窗律、形体比例。

```bash
python -m mcslice.slice "builds/天际线办公系列/models/办公板楼.schem" \
    --by grid --floor 1 --out-dir /tmp/sliced --report \
    --register --pack modern-arch --category f1 --plan /tmp/plan.json

python -m mcslice.learn "builds/天际线办公系列/models/高层写字楼.schem" \
    --out packs/modern-arch/styles/extracted.json --preview /tmp/palette.png
```

## 分层位置

| | |
|---|---|
| **上游** | `mccore`（结构 IO、模块 spec、`write_text_lf`、包目录）· `mcrender`（`learn --preview` 出调色板图） |
| **下游** | 无（叶子包）：产出的是资产包内容与装配计划，由 `mccore.assemble` / 组合生成器消费 |
| **不做** | 不做生成（那是 compositions 的事）、不改已有文件（`--register` 之外只写 `--out-dir`） |

## 模块

| 文件 | 作用 |
|---|---|
| `slice.py` | **切块**：`--by floor --pitch N` 切水平楼层带；`--detect grid --floor K` 把一层按墙平面分解成「房间/走廊/核心」；`detect_ports()` 在六个面上找「空气对空气」的开口推接口；`--register` 写进资产包（模块 + spec + `pack.json`）；`--plan` 生成装配计划；`--report` 打统计。辅助：`wall_planes` `intervals` `runs_1d` `prune_palette` `write_module` `floor_band` |
| `learn.py` | **风格学习**：`role_of()` 认材料角色、`floor_pitch()` 层高、`window_stats()` 窗律、`swatch_png()` 调色板预览；输出 `packs/<pack>/styles/<name>.json` |

## 关键 API

| 名字 | 用途 |
|---|---|
| `slice.detect_cells / detect_ports / classify` | 不落盘的分解与接口推断（被 `learn` 复用） |
| `slice.slice_floor / floor_band / write_module` | 切块与写模块 |
| `learn.main` | 风格包提取 CLI |

## 约定 / 不变量

* 接口（port）的轴向约定与 `mccore.assemble` 完全一致（面 + 偏移 + 尺寸 + 类型）；
  `mcstudio` 的接口编辑器改的就是这套。
* 切块产出**一定**带 sidecar `.module.json`，`grid` 与真实尺寸由 `module_lib.sync_grid` 校正。
* 调色板会裁剪（`prune_palette`）：模块只保留自己用到的状态，`palette[0]` 仍是空气。

## 测试

```bash
python tests/studio_tools_smoke.py    # 工作台里切块 / 学习相关的 HTTP 端到端
python tests/assembly_smoke.py        # 切出来的模块能被装配引擎拼回去
python -m mccore.pack validate        # --register 进包之后校验清单
```
