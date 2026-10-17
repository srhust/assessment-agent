"""全局结构化数据模型（所有组件间传递的都是这些 Pydantic 对象）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "q") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class QuestionOption(BaseModel):
    label: str
    text: str = ""

    def as_line(self) -> str:
        return f"{self.label}. {self.text}".strip()


class Question(BaseModel):
    """一道题目的规范化表示。"""

    id: str = Field(default_factory=lambda: new_id("q"))
    stem: str = ""
    options: List[QuestionOption] = Field(default_factory=list)
    skill: Optional[str] = None
    question_type: str = "single_choice"
    image_path: Optional[str] = None
    source: str = "text"
    meta: Dict[str, Any] = Field(default_factory=dict)

    def option_labels(self) -> List[str]:
        return [option.label for option in self.options]

    def option_text(self, label: str) -> Optional[str]:
        for option in self.options:
            if option.label == label:
                return option.text
        return None

    def render(self) -> str:
        lines = [self.stem.strip()]
        lines.extend(option.as_line() for option in self.options)
        return "\n".join(line for line in lines if line)


class ToolInvocation(BaseModel):
    """一次工具调用的可追溯记录。"""

    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    result: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


class Solution(BaseModel):
    status: Literal["solved", "unsupported", "failed"] = "failed"
    skill: str = "unknown"
    answer: Optional[str] = None
    answer_text: Optional[str] = None
    steps: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    method: str = ""
    tool_calls: List[ToolInvocation] = Field(default_factory=list)
    error: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)

    def to_llm_payload(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "skill": self.skill,
            "answer": self.answer,
            "answer_text": self.answer_text,
            "steps": self.steps,
            "confidence": self.confidence,
            "method": self.method,
        }


class VerificationCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""
    severity: Literal["info", "warning", "blocking"] = "blocking"


class Verification(BaseModel):
    passed: bool = False
    action: Literal["accept", "repair", "reject"] = "reject"
    checks: List[VerificationCheck] = Field(default_factory=list)
    final_answer: Optional[str] = None
    repaired_answer: Optional[str] = None
    notes: str = ""
    duration_ms: float = 0.0

    def failing(self) -> List[VerificationCheck]:
        return [check for check in self.checks if not check.passed]


class RouteDecision(BaseModel):
    skill: str
    confidence: float = 0.0
    source: Literal["explicit", "rules", "llm", "fallback"] = "rules"
    scores: Dict[str, float] = Field(default_factory=dict)
    ambiguous: bool = False
    notes: str = ""


class ExtractionResult(BaseModel):
    """题图抽取结果（视觉模型 -> 结构化字段）。"""

    stem: str = ""
    options: List[QuestionOption] = Field(default_factory=list)
    question_type: str = "single_choice"
    skill_hint: Optional[str] = None
    has_diagram: bool = False
    raw_text: str = ""
    model: Optional[str] = None
    confidence: float = 0.0
    warnings: List[str] = Field(default_factory=list)


class RunResult(BaseModel):
    """一次端到端运行的完整结果与产物路径。"""

    run_id: str
    run_dir: str
    question: Question
    route: RouteDecision
    solution: Solution
    verification: Verification
    extraction: Optional[ExtractionResult] = None
    latency_ms: Dict[str, float] = Field(default_factory=dict)
    artifacts: Dict[str, str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None
