"""Skill 层：八个题型 Skill 的注册表。

Skill 只负责“题意归一 + 选择工具 + 组装步骤”，所有确定性计算都在 tools 层。
新增题型只需实现 ``BaseSkill`` 并在此注册。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .base import BaseSkill, SkillContext
from .data_analysis import DataAnalysisSkill
from .figure_reasoning import FigureReasoningSkill
from .language_reasoning import LanguageReasoningSkill
from .logical_reasoning import LogicalReasoningSkill
from .number_series import NumberSeriesSkill
from .numeric_calculation import NumericCalculationSkill
from .personality import PersonalityInterpretationSkill
from .verbal_understanding import VerbalUnderstandingSkill


def default_skills() -> List[BaseSkill]:
    return [
        VerbalUnderstandingSkill(),
        LanguageReasoningSkill(),
        NumericCalculationSkill(),
        DataAnalysisSkill(),
        NumberSeriesSkill(),
        FigureReasoningSkill(),
        LogicalReasoningSkill(),
        PersonalityInterpretationSkill(),
    ]


class SkillRegistry:
    def __init__(self, skills: Optional[Iterable[BaseSkill]] = None):
        self._skills: Dict[str, BaseSkill] = {}
        for skill in skills if skills is not None else default_skills():
            self.register(skill)

    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.id] = skill

    def get(self, skill_id: str) -> Optional[BaseSkill]:
        return self._skills.get(skill_id)

    def require(self, skill_id: str) -> BaseSkill:
        skill = self.get(skill_id)
        if skill is None:
            raise KeyError(f"未注册的 Skill: {skill_id}")
        return skill

    def ids(self) -> List[str]:
        return list(self._skills.keys())

    def all(self) -> List[BaseSkill]:
        return list(self._skills.values())

    def supported(self) -> List[BaseSkill]:
        return [skill for skill in self.all() if skill.supported]

    def describe(self) -> List[Dict[str, object]]:
        return [
            {
                "id": skill.id,
                "name_zh": skill.name_zh,
                "stage": skill.stage,
                "supported": skill.supported,
                "description": skill.description,
                "requires": list(skill.requires),
            }
            for skill in self.all()
        ]


__all__ = [
    "BaseSkill",
    "SkillContext",
    "SkillRegistry",
    "default_skills",
]
