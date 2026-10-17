"""语言推理 Skill（第二阶段）。"""

from __future__ import annotations

from .base import DeclaredSkill


class LanguageReasoningSkill(DeclaredSkill):
    id = "language_reasoning"
    name_zh = "语言推理"
    description = "词语关系与语句逻辑：类比、反义/近义、English verbal reasoning、逻辑填空式推理"
    stage = 2
    planned_capabilities = (
        "词语关系分类（种属、组成、功能、因果、程度）",
        "类比题的关系对齐与一致性检查",
        "英文 verbal reasoning 的语义关系与选项排除",
        "反义/近义辨析的知识库检索",
    )
    planned_tools = ("LLM 文本推理", "词关系知识库", "选项一致性校验器")
