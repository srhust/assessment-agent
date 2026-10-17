"""Skill 基类与上下文。

Skill 约定：
- 只做“题意归一 + 选工具 + 组织步骤”，不做与工具重复的算术/推理；
- ``solve`` 必须返回 ``Solution``，除未实现能力外不得编造答案；
- ``verify`` 提供该题型专属的独立复核（可选）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..artifacts import RunArtifacts
from ..config import Settings
from ..knowledge import KnowledgeBase
from ..llm import ChatModel
from ..schemas import Question, Solution, Verification
from ..tools import ToolBox, default_toolbox


@dataclass
class SkillContext:
    settings: Settings
    llm: Optional[ChatModel] = None
    toolbox: ToolBox = field(default_factory=default_toolbox)
    knowledge: Optional[KnowledgeBase] = None
    artifacts: Optional[RunArtifacts] = None

    def knowledge_note(self, skill_id: str, max_chars: int = 1200) -> Optional[str]:
        if self.knowledge is None:
            return None
        return self.knowledge.load(skill_id, max_chars=max_chars)


class BaseSkill(ABC):
    id: str = "base"
    name_zh: str = "基础题型"
    description: str = ""
    stage: int = 1
    supported: bool = True
    requires: tuple = ()
    question_types: Sequence[str] = ()

    def unavailable_reason(self) -> Optional[str]:
        return None

    @abstractmethod
    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        """返回求解结果（结构化）。"""

    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        """题型专属复核，返回 None 表示交给通用校验。"""
        return None

    # ---------------------------------------------------------------- 辅助
    @staticmethod
    def option_pairs(question: Question) -> List[List[str]]:
        return [[option.label, option.text] for option in question.options]

    def unsupported(self, question: Question, reason: str, **meta: Any) -> Solution:
        return Solution(status="unsupported", skill=self.id, error=reason, method="declared_interface", meta=dict(meta))


class DeclaredSkill(BaseSkill):
    """第二阶段题型：接口已定义、能力未实现，返回 unsupported 而不是猜测答案。"""

    stage = 2
    supported = False
    planned_capabilities: tuple = ()
    planned_tools: tuple = ()

    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        note = ctx.knowledge_note(self.id)
        return Solution(
            status="unsupported",
            skill=self.id,
            answer=None,
            confidence=0.0,
            method="declared_interface",
            error=f"{self.name_zh}尚未实现（第一阶段 MVP 范围之外），不提供推测性答案",
            meta={
                "stage": self.stage,
                "planned_capabilities": list(self.planned_capabilities),
                "planned_tools": list(self.planned_tools),
                "knowledge_available": bool(note),
                "interface": f"skills.{self.id}.{type(self).__name__}.solve(question, ctx) -> Solution",
                "next_step": "第二阶段：接入对应工具/提示词后实现 solve，并按 data/ 目录补评测样本",
            },
        )

    def capabilities(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name_zh": self.name_zh,
            "stage": self.stage,
            "supported": self.supported,
            "planned_capabilities": list(self.planned_capabilities),
            "planned_tools": list(self.planned_tools),
        }
