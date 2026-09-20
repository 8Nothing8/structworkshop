"""mctools — Axiom 式体素工具引擎（纯 numpy，确定性，无 GUI 依赖）。

术语对齐 Axiom 6.x（参数名同名同义），实现是 structworkshop 自己的：
``noise`` 噪声场 / ``masks`` 掩码表达式 / ``shapes`` 笔刷与基本体 /
``tools`` 工具注册表（含参数表，供 Web 编辑器与 CLI 自动生成表单）。
"""
from mctools.registry import TOOL_GROUPS, TOOLS, catalog, get_tool  # noqa: F401
from mctools.engine import ToolContext, apply, plan_box, run  # noqa: F401

__all__ = ["TOOLS", "TOOL_GROUPS", "catalog", "get_tool", "ToolContext",
           "run", "apply", "plan_box"]
