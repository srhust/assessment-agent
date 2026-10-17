"""言语理解 Skill（第二阶段）。

第一阶段只声明接口：不调用模型、不编造答案，明确返回 unsupported。
"""

from __future__ import annotations

from .base import DeclaredSkill


class VerbalUnderstandingSkill(DeclaredSkill):
    id = "verbal_understanding"
    name_zh = "言语理解"
    description = "中文文段理解：选词填空、语句排序、主旨概括、意图判断"
    stage = 2
    planned_capabilities = (
        "题干选项抽取后的语感/逻辑词线索标注",
        "选词填空：搭配、语义轻重、感情色彩、语境提示词分析",
        "语句排序：指代链、关联词、话题一致性约束",
        "主旨概括：论题-论点结构提取与选项排除",
    )
    planned_tools = ("LLM 文本推理（带结构化证据）", "语料知识库检索", "选项排除校验器")
