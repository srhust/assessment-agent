"""资料分析 Skill（第二阶段）。"""

from __future__ import annotations

from .base import DeclaredSkill


class DataAnalysisSkill(DeclaredSkill):
    id = "data_analysis"
    name_zh = "资料分析"
    description = "图表/文字资料的数据读取与计算：增长率、占比、平均数、倍数、同比环比"
    stage = 2
    planned_capabilities = (
        "表格/图表数据抽取为结构化表（视觉模型 + 表格校验）",
        "增长率、比重、平均数、倍数等指标计算（复用 SymPy 工具）",
        "单位与量级一致性检查（万/亿、百分点 vs 百分比）",
        "近似估算与选项区间匹配",
    )
    planned_tools = ("视觉表格抽取", "python_math 工具", "指标知识库", "量级校验器")
