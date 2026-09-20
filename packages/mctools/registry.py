"""工具注册表：给 Web 编辑器 / CLI / skill 用的统一清单。

``catalog()`` 返回的东西直接喂给前端 —— 参数类型、取值范围、默认值都在里面，
所以新增一个工具只要在 ``tools.SPECS`` 里加一条即可，UI 无需改代码。
"""
from __future__ import annotations

from mctools.spec import GROUPS, Param, ToolSpec  # noqa: F401
from mctools.tools import BRUSH_SHAPES, SPECS

__all__ = ["TOOLS", "TOOL_GROUPS", "ORDER", "catalog", "get_tool",
           "BRUSH_SHAPES", "Param", "ToolSpec", "GROUPS"]

# id → ToolSpec
TOOLS: dict[str, ToolSpec] = {s.id: s for s in SPECS}

# 面板里的分组顺序
ORDER = ("shape", "paint", "deform", "solid", "world")
TOOL_GROUPS = [(g, GROUPS.get(g, g)) for g in ORDER
               if any(s.group == g for s in SPECS)]


def get_tool(name: str) -> ToolSpec:
    spec = TOOLS.get(str(name))
    if spec is None:
        raise ValueError(f"没有工具 {name!r}（可选 {', '.join(TOOLS)}）")
    return spec


def catalog() -> dict:
    """前端用的完整工具清单（按分组）。"""
    return {
        "groups": [
            {
                "id": gid,
                "label": label,
                "tools": [s.to_dict() for s in SPECS if s.group == gid],
            }
            for gid, label in TOOL_GROUPS
        ],
        "brushShapes": list(BRUSH_SHAPES),
        "count": len(SPECS),
    }
