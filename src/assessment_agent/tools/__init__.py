"""工具层导出。"""

from __future__ import annotations

from typing import List

from .base import Tool, ToolBox, ToolError, ToolUnavailable, json_safe
from .figure_rules import FigureRuleTool
from .figure_tools import FigureTools
from .figure_transform_tools import FigureTransformTool
from .logic_tool import Z3LogicTool
from .math_tool import PythonMathTool
from .series import NumberSeriesTool


def default_tools() -> List[Tool]:
    return [PythonMathTool(), NumberSeriesTool(), Z3LogicTool(), FigureTools(),
            FigureRuleTool(), FigureTransformTool()]


def default_toolbox() -> ToolBox:
    return ToolBox(default_tools())


__all__ = [
    "FigureRuleTool",
    "FigureTools",
    "NumberSeriesTool",
    "PythonMathTool",
    "Tool",
    "ToolBox",
    "ToolError",
    "ToolUnavailable",
    "Z3LogicTool",
    "default_toolbox",
    "default_tools",
    "json_safe",
]
