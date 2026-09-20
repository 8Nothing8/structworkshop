"""工具实现的公共声明：参数表与工具规格。

``Param`` 描述一个参数（Web 编辑器据此自动生成控件，CLI 据此校验），
``ToolSpec`` 描述一个工具（区域/是否支持掩码/几何包围盒/执行函数）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

__all__ = ["Param", "ToolSpec", "GROUPS"]

# id → 中文名（工具面板分组）
GROUPS = {
    "shape": "形状与路径",
    "paint": "绘制与上色",
    "deform": "形变与雕刻",
    "solid": "体块运算",
    "world": "地形与重力",
}


@dataclass
class Param:
    name: str
    label: str
    type: str = "int"                      # int|float|bool|enum|text|blocks|vec3
    default: object = 0
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple = ()                    # enum 用（值为字符串）
    label_of: dict = field(default_factory=dict)   # enum 值 → 中文
    hint: str = ""

    def to_dict(self) -> dict:
        out = {"name": self.name, "label": self.label, "type": self.type}
        if self.default is not None:
            out["default"] = self.default
        for k in ("min", "max", "step", "hint"):
            v = getattr(self, k)
            if v is not None and v != "":
                out[k] = v
        if self.options:
            out["options"] = list(self.options)
            out["labelOf"] = {str(k): v for k, v in self.label_of.items()}
        return out


@dataclass
class ToolSpec:
    id: str
    label: str
    group: str
    fn: Callable
    hint: str = ""
    region: str = "brush"                  # brush | sel | own
    params: tuple = ()
    mask: bool = True                      # 支持额外掩码表达式
    needs_block: bool = True               # 使用「当前方块」
    pad: int = 1                           # 工作盒外扩（读邻域用）
    bbox_fn: Callable | None = None        # region == own 时的几何包围盒
    grabbable: bool = False                # 支持多点点选（path 之类）

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "group": self.group,
            "groupLabel": GROUPS.get(self.group, self.group),
            "hint": self.hint, "region": self.region,
            "mask": bool(self.mask), "needsBlock": bool(self.needs_block),
            "grabbable": bool(self.grabbable),
            "params": [p.to_dict() for p in self.params],
        }
