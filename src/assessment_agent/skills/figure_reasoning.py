"""图形推理 Skill（第二阶段）。

工具接口已在 ``tools/figure_tools.py`` 预留（OpenCV + NumPy），
但规律识别本身尚未实现，因此这里如实返回 unsupported。
"""

from __future__ import annotations

from typing import Optional

from ..schemas import Question, Solution
from .base import DeclaredSkill, SkillContext


class FigureReasoningSkill(DeclaredSkill):
    id = "figure_reasoning"
    name_zh = "图形推理"
    description = "图形规律：旋转、翻转、叠加、对称、九宫格、折叠展开"
    stage = 2
    planned_capabilities = (
        "图形序列/九宫格的结构化特征提取（边数、对称轴、面积占比、位置）",
        "旋转、翻转、平移变换匹配",
        "叠加去同存异、去异存同",
        "对称性与开闭性判定",
    )
    planned_tools = ("figure_tools（OpenCV/NumPy 图像算子）", "图形特征知识库", "选项比对器")

    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        solution = super().solve(question, ctx)
        tool = ctx.toolbox.get("figure_tools") if ctx.toolbox.has("figure_tools") else None
        capabilities: Optional[dict] = None
        if tool is not None:
            try:
                capabilities = tool.run(operation="capabilities")  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - 仅用于展示能力状态
                capabilities = {"error": str(exc)}
        solution.meta["figure_tool_capabilities"] = capabilities
        solution.meta["tool_stage"] = 2
        return solution
