"""性格测评解释 Skill（第二阶段）。"""

from __future__ import annotations

from .base import DeclaredSkill


class PersonalityInterpretationSkill(DeclaredSkill):
    id = "personality"
    name_zh = "性格测评解释"
    description = "性格/职业倾向量表的解释说明（非临床诊断，按量表维度做知识型解读）"
    stage = 2
    planned_capabilities = (
        "量表维度释义（如尽责性、外向性、成就动机）",
        "基于维度组合的行为倾向说明与岗位匹配提示",
        "答题一致性/社会赞许性提示（仅提示，不作诊断）",
        "解释边界声明：不提供医疗或心理诊断结论",
    )
    planned_tools = ("量表知识库", "LLM 解释生成", "合规声明模板")
