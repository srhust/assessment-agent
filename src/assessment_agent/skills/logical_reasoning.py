"""逻辑推理 Skill：排序比较类题目交给 Z3 约束求解。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..schemas import Question, Solution, Verification, VerificationCheck
from ..tools.logic_tool import Statement, _RankingModel, verify_answer
from .base import BaseSkill, SkillContext


class LogicalReasoningSkill(BaseSkill):
    id = "logical_reasoning"
    name_zh = "逻辑推理"
    description = "排序比较 / 真假话类逻辑题：把题干比较语句建模为 Z3 约束，判定选项是否必然为真"
    stage = 1
    requires = ("z3-solver",)

    def unavailable_reason(self) -> Optional[str]:
        try:
            import z3  # noqa: F401
        except ImportError:
            return "缺少 z3-solver，逻辑推理 Skill 不可用"
        return None

    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        invocation = ctx.toolbox.run(
            "z3_logic",
            stem=question.stem,
            options=[(option.label, option.text) for option in question.options],
            entities=question.meta.get("logic_entities"),
        )
        if not invocation.ok:
            return Solution(
                status="failed",
                skill=self.id,
                method="z3_constraints",
                error=invocation.error,
                tool_calls=[invocation],
            )
        payload: Dict[str, Any] = invocation.result
        steps: List[str] = [
            f"识别比较对象：{'、'.join(payload['entities'])}",
            f"解析约束：共 {len(payload['statements'])} 条",
        ]
        op_names = {
            ">": "高于",
            "<": "低于",
            "=": "等于",
            ">=": "不低于",
            "<=": "不高于",
            "max": "最高",
            "min": "最低",
            "not_max": "不是最高",
        }
        for statement in payload["statements"]:
            right = statement.get("right")
            steps.append(
                f"约束：{statement['left']} {op_names.get(statement['op'], statement['op'])} {right or ''}".strip()
            )
        intent = payload["intent"]
        if payload.get("order"):
            steps.append(f"唯一确定排序（高→低）：{' > '.join(payload['order'])}")
            return Solution(
                status="solved",
                skill=self.id,
                answer=" > ".join(payload["order"]),
                answer_text=" > ".join(payload["order"]),
                steps=steps,
                confidence=0.9,
                method="z3_constraints",
                tool_calls=[invocation],
                meta={
                    "entities": payload["entities"],
                    "statements": payload["statements"],
                    "intent": intent,
                    "entailed_options": payload.get("entailed_options", []),
                    "candidates": payload.get("candidates", []),
                },
            )
        if payload.get("answer_entity"):
            answer = payload.get("answer_label") or payload["answer_entity"]
            steps.append(f"{'最高' if intent == 'max' else '最低'}者是 {payload['answer_entity']}")
            return Solution(
                status="solved",
                skill=self.id,
                answer=answer,
                answer_text=question.option_text(answer) if payload.get("answer_label") else payload["answer_entity"],
                steps=steps,
                confidence=0.9,
                method="z3_constraints",
                tool_calls=[invocation],
                meta={
                    "entities": payload["entities"],
                    "statements": payload["statements"],
                    "intent": intent,
                    "entailed_options": [payload.get("answer_label")],
                },
            )
        label = payload.get("answer_label")
        if not label:
            return Solution(
                status="failed",
                skill=self.id,
                method="z3_constraints",
                error="模型未能给出唯一答案",
                steps=steps,
                tool_calls=[invocation],
            )
        steps.append(f"选项 {label} 在全部可行模型中成立（必然为真）")
        return Solution(
            status="solved",
            skill=self.id,
            answer=label,
            answer_text=question.option_text(label) or "",
            steps=steps,
            confidence=0.92 if len(payload.get("entailed_options") or []) == 1 else 0.7,
            method="z3_constraints",
            tool_calls=[invocation],
            meta={
                "entities": payload["entities"],
                "statements": payload["statements"],
                "intent": intent,
                "entailed_options": payload.get("entailed_options", []),
                "option_states": payload.get("option_states", []),
                "notes": payload.get("notes", ""),
            },
        )

    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        meta = solution.meta or {}
        entities = meta.get("entities")
        statements = meta.get("statements")
        if not entities or not statements:
            return None
        checks: List[VerificationCheck] = []
        try:
            statements_obj = [
                Statement(
                    op=str(item["op"]),
                    left=str(item["left"]),
                    right=(str(item["right"]) if item.get("right") else None),
                    source=str(item.get("source", "")),
                )
                for item in statements
            ]
            model = _RankingModel(entities)
            consistent = model.consistent(statements_obj)
            checks.append(
                VerificationCheck(
                    name="logic.constraints_consistent",
                    passed=consistent,
                    detail=f"{len(statements_obj)} 条约束在 Z3 中{'可满足' if consistent else '不可满足'}",
                )
            )
            recomputed = verify_answer(
                entities=entities,
                statements=statements_obj,
                intent=str(meta.get("intent", "entailment")),
                options=[[option.label, option.text] for option in question.options],
            )
            checks.append(
                VerificationCheck(
                    name="logic.recomputed_entailment",
                    passed=recomputed.get("answer_label") == solution.answer,
                    detail=(
                        f"独立重算答案 {recomputed.get('answer_label')} vs Solver {solution.answer}; "
                        f"必然为真选项 {recomputed.get('entailed')}"
                    ),
                )
            )
            if question.options and solution.answer:
                checks.append(
                    VerificationCheck(
                        name="logic.answer_in_options",
                        passed=solution.answer in question.option_labels(),
                        detail=f"答案 {solution.answer} 是否在选项内",
                    )
                )
            passed = all(check.passed for check in checks if check.severity == "blocking")
            repaired = recomputed.get("answer_label") if not passed else None
            return Verification(
                passed=passed,
                action="accept" if passed else ("repair" if repaired else "reject"),
                checks=checks,
                final_answer=solution.answer if passed else repaired,
                repaired_answer=None if passed else repaired,
                notes="Z3 重新建模 + 重新判定必然性",
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                VerificationCheck(name="logic.verify_error", passed=False, detail=f"{type(exc).__name__}: {exc}")
            )
            return Verification(passed=False, action="reject", checks=checks, notes="逻辑复核执行失败")
