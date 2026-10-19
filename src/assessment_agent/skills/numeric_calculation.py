"""数字计算 Skill：题干 -> 算术表达式 -> SymPy 精确求值 -> 选项匹配。

R2c：直接表达式抽取失败时，先过**自然语言数量关系理解层**
（``tools.word_problem``：百分比/折扣/速时距/单价/比例/逆百分比），
计算计划只是"关系→表达式"的结构化，数值一律由 Fraction/SymPy 确定性求值，
支持范围外如实失败——不允许猜答案。
"""

from __future__ import annotations

import math
import re as _re
from fractions import Fraction
from typing import List, Optional

from ..schemas import Question, Solution, Verification, VerificationCheck
from ..tools.math_tool import fraction_eval, match_option, option_value
from ..tools.word_problem import evaluate_plan, extract_word_problem
from .base import BaseSkill, SkillContext


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-9)


def _format_fraction(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{float(value):g}"


# ------------------------------------------------------------------ R6 任务B：操作数覆盖复核
# 通用不变式（与题型/答案无关）：**解必须是对全部题干数量的计算**。计划若遗漏题干
# 数量（如"先打 8 折、再降价 10%"只用了 8 折；"60 克加 20 克水"只用了浓度数），
# 无论其表达式内部算术多么自洽，都缺少完整题意证据——禁止释放，返回 unverified。
# 这不是题型模板：它不识别"折扣/浓度"语义，只做数值 token 的双向绑定核验。

_YEAR_RE = _re.compile(r"20\d{2}\s*年")  # 年份标签是时间标识而非可运算数量（通用豁免）


def _stem_numbers(stem: str) -> List[float]:
    text = _YEAR_RE.sub("", stem or "")
    out: List[float] = []
    for tok in _re.findall(r"\d+(?:\.\d+)?", text):
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def _plan_numbers(plan: Optional[dict], expression: Optional[str]) -> set:
    src_parts: List[str] = [expression or ""]
    if plan:
        for step in plan.get("steps", []):
            src_parts.extend(str(part) for part in step)
    consumed: set = set()
    for tok in _re.findall(r"\d+(?:\.\d+)?", " ".join(src_parts)):
        try:
            consumed.add(float(tok))
        except ValueError:
            continue
    return consumed


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
            # 直接表达式抽取失败 → 自然语言数量关系理解层（R2c），计算仍走 Fraction
            word_solution = self._solve_word_problem(question, ctx, options)
            if word_solution is not None:
                word_solution.tool_calls = [invocation] + list(word_solution.tool_calls or [])
                return word_solution
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
                # 直接抽取的算式可能本身抽错（如把"比…便宜20%"读成减 20）：
                # 结果与所有选项不匹配时，再走自然语言数量关系理解层（R2c）
                word_solution = self._solve_word_problem(question, ctx, options)
                if word_solution is not None:
                    word_solution.tool_calls = [invocation] + list(word_solution.tool_calls or [])
                    return word_solution
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

    # ------------------------------------------------ 应用题理解层（R2c）
    def _solve_word_problem(self, question: Question, ctx: SkillContext, options) -> Optional[Solution]:
        if not getattr(ctx.settings, "r2c_word_problem", True):
            return None
        plan = extract_word_problem(question.stem)
        if plan is None:
            return None  # 支持范围外：由上层返回原有诚实失败
        env, error = evaluate_plan(plan)
        if error or env is None:
            return Solution(
                status="failed",
                skill=self.id,
                method=f"word_problem:{plan['kind']}",
                error=f"数量关系抽取成功但确定性求值失败：{error}",
                meta={"word_problem": plan},
            )
        final = env[plan["final_var"]]
        # 把中间变量代入最后一步表达式，得到可独立复核的单一算式
        expression = plan["steps"][-1][1]
        for name, value in env.items():
            if name != plan["final_var"]:
                expression = _re.sub(rf"\b{name}\b", f"({value.numerator}/{value.denominator})", expression)
        steps = [f"关系抽取（{plan['kind']}）：{desc}" for _var, _expr, desc in plan["steps"]]
        steps.append(f"Fraction 精确求值 = {_format_fraction(final)}（数值 {float(final):g}）")
        if question.options:
            label = match_option(float(final), options)
            if label is None:
                return Solution(
                    status="failed",
                    skill=self.id,
                    method=f"word_problem:{plan['kind']}",
                    error=f"应用题计算结果 {_format_fraction(final)} 与所有选项均不匹配",
                    steps=steps,
                    meta={"word_problem": plan, "expression": expression, "value": float(final),
                          "exact": _format_fraction(final)},
                )
            steps.append(f"与选项比对，匹配到 {label}")
            return Solution(
                status="solved",
                skill=self.id,
                answer=label,
                answer_text=question.option_text(label) or "",
                steps=steps,
                confidence=0.9,
                method=f"word_problem:{plan['kind']}",
                meta={"word_problem": plan, "expression": expression, "value": float(final),
                      "exact": _format_fraction(final), "matched_label": label},
            )
        answer_text = _format_fraction(final)
        return Solution(
            status="solved",
            skill=self.id,
            answer=answer_text,
            answer_text=answer_text,
            steps=steps,
            confidence=0.85,
            method=f"word_problem:{plan['kind']}",
            meta={"word_problem": plan, "expression": expression, "value": float(final), "exact": answer_text},
        )

    # ------------------------------------------------------------ 独立复核
    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        expression = solution.meta.get("expression")
        if not expression:
            return None
        checks: List[VerificationCheck] = []
        repaired: Optional[str] = None
        plan = solution.meta.get("word_problem")
        # R6-B 操作数覆盖（blocking）：题干数量必须全部被计算计划绑定消费，否则拒绝释放
        stem_nums = _stem_numbers(question.stem)
        consumed = _plan_numbers(plan, solution.meta.get("expression"))
        uncovered = [n for n in stem_nums if not any(_close(n, c) for c in consumed)]
        if uncovered:
            checks.append(
                VerificationCheck(
                    name="numeric.operand_coverage",
                    passed=False,
                    severity="blocking",
                    detail=(f"题干数量 {sorted(set(uncovered))} 未出现在计算计划的操作数/步骤依据中"
                            f"（已消费 {sorted(consumed)}）——缺少完整题意证据，不释放"),
                )
            )
            return Verification(
                passed=False,
                action="unverified",
                checks=checks,
                final_answer=None,
                notes="操作数覆盖核验未通过：计划遗漏题干数量，禁止无完整计算证据的释放",
            )
        checks.append(
            VerificationCheck(
                name="numeric.operand_coverage",
                passed=True,
                severity="blocking",
                detail=f"题干 {len(stem_nums)} 个数量均被计划操作数绑定（{sorted(consumed)}）",
            )
        )
        if plan:
            # R2c：应用题计划链独立重放（关系步骤 → Fraction 重算），
            # 与下面的单一表达式 Fraction 求值是两条独立计算路径
            env, chain_error = evaluate_plan(plan)
            chain_ok = (
                chain_error is None
                and env is not None
                and solution.meta.get("value") is not None
                and _close(float(env[plan["final_var"]]), float(solution.meta["value"]))
            )
            checks.append(
                VerificationCheck(
                    name="word_problem.chain_recompute",
                    passed=chain_ok,
                    severity="blocking",
                    detail=(
                        f"计划链重放 {'=' if chain_ok else '≠'} Solver 结果（{plan['kind']}，"
                        f"{len(plan['steps'])} 步）" + ("" if chain_ok else f"：{chain_error or '数值不一致'}")
                    ),
                )
            )
            if not chain_ok:
                return Verification(
                    passed=False,
                    action="reject",
                    checks=checks,
                    final_answer=None,
                    notes="应用题计划链复核未通过，拒绝输出（不猜测）",
                )
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
