"""数字计算 Skill：题干 -> 算术表达式 -> SymPy 精确求值 -> 选项匹配。"""

from __future__ import annotations

import math
from typing import List, Optional

from ..schemas import Question, Solution, Verification, VerificationCheck
from ..tools.math_tool import fraction_eval, match_option, option_value
from .base import BaseSkill, SkillContext


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-9)


class NumericCalculationSkill(BaseSkill):
    id = "numeric_calculation"
    name_zh = "数字计算"
    description = "算术、百分比、比例类计算题：解析题干算式，用 SymPy 精确求值并匹配选项"
    stage = 1
    requires = ("sympy",)

    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        options = self.option_pairs(question)
        invocation = ctx.toolbox.run("python_math", text=question.stem, options=options)
        if not invocation.ok:
            return Solution(
                status="failed",
                skill=self.id,
                method="sympy_exact",
                error=invocation.error,
                tool_calls=[invocation],
            )
        payload = invocation.result
        value = float(payload["value"])
        exact = str(payload["exact"])
        expression = payload["expression"]
        steps = [
            f"从题干抽取算式：{expression}",
            f"SymPy 精确求值：{exact}",
            f"数值结果：{value:g}",
        ]
        if question.options:
            label = payload.get("matched_label")
            if label is None:
                return Solution(
                    status="failed",
                    skill=self.id,
                    method="sympy_exact",
                    error="计算结果与所有选项均不匹配",
                    tool_calls=[invocation],
                    meta={"expression": expression, "value": value, "exact": exact},
                )
            steps.append(f"与选项比对，匹配到 {label}. {question.option_text(label) or ''}".strip())
            return Solution(
                status="solved",
                skill=self.id,
                answer=label,
                answer_text=question.option_text(label) or "",
                steps=steps,
                confidence=0.95,
                method="sympy_exact",
                tool_calls=[invocation],
                meta={"expression": expression, "value": value, "exact": exact, "matched_label": label},
            )
        answer_text = exact if payload.get("is_integer") else f"{value:g}"
        steps.append("题目无选项，直接输出计算结果")
        return Solution(
            status="solved",
            skill=self.id,
            answer=answer_text,
            answer_text=answer_text,
            steps=steps,
            confidence=0.85,
            method="sympy_exact",
            tool_calls=[invocation],
            meta={"expression": expression, "value": value, "exact": exact},
        )

    # ------------------------------------------------------------ 独立复核
    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        expression = solution.meta.get("expression")
        if not expression:
            return None
        checks: List[VerificationCheck] = []
        repaired: Optional[str] = None
        try:
            recomputed = float(fraction_eval(str(expression)))
            checks.append(
                VerificationCheck(
                    name="math.independent_recompute",
                    passed=True,
                    detail=f"Fraction 独立求值 = {recomputed:g}（不依赖 SymPy 路径）",
                )
            )
        except Exception as exc:  # noqa: BLE001 - 交叉验证失败即视为未通过
            recomputed = float("nan")
            checks.append(
                VerificationCheck(
                    name="math.independent_recompute",
                    passed=False,
                    detail=f"独立求值失败: {exc}",
                )
            )

        claimed = solution.meta.get("value")
        if claimed is not None and not math.isnan(recomputed):
            matched = _close(float(claimed), recomputed)
            checks.append(
                VerificationCheck(
                    name="math.claim_consistent",
                    passed=matched,
                    detail=f"Solver 结果 {float(claimed):g} vs 复核 {recomputed:g}",
                )
            )
            if not matched:
                pairs = [(option.label, option.text) for option in question.options]
                repaired = match_option(recomputed, pairs) or f"{recomputed:g}"
        else:
            checks.append(
                VerificationCheck(name="math.claim_consistent", passed=False, detail="缺少 Solver 数值结果")
            )

        if question.options and solution.answer:
            labels = question.option_labels()
            in_options = solution.answer in labels
            checks.append(
                VerificationCheck(
                    name="numeric.answer_in_options",
                    passed=in_options,
                    detail=f"答案 {solution.answer} {'在' if in_options else '不在'}选项 {labels} 中",
                )
            )
            if in_options and not math.isnan(recomputed):
                value = option_value(question.option_text(solution.answer) or "")
                if value is not None:
                    same = _close(value, recomputed)
                    checks.append(
                        VerificationCheck(
                            name="answer.matches_recompute",
                            passed=same,
                            detail=f"选项 {solution.answer} 数值 {value:g} vs 复核 {recomputed:g}",
                        )
                    )
                    if not same:
                        repaired = match_option(recomputed, [(o.label, o.text) for o in question.options]) or repaired

        passed = all(check.passed for check in checks if check.severity == "blocking")
        if passed:
            action = "accept"
        elif repaired and repaired != solution.answer:
            action = "repair"
        else:
            action = "reject"
        return Verification(
            passed=passed,
            action=action,
            checks=checks,
            final_answer=solution.answer if passed else repaired,
            repaired_answer=repaired if action == "repair" else None,
            notes="SymPy 求值 + Fraction 独立求值 + 选项数值一致性校验",
        )
