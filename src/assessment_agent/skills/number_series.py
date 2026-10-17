"""数字推理 Skill：数列规律识别 + 选项匹配（MVP 支持末项 / 下一项）。"""

from __future__ import annotations

from fractions import Fraction
from typing import List, Optional

from ..schemas import Question, Solution, Verification, VerificationCheck
from ..tools.math_tool import match_option
from ..tools.series import find_blank_position, solve_series
from .base import BaseSkill, SkillContext


class NumberSeriesSkill(BaseSkill):
    id = "number_series"
    name_zh = "数字推理"
    description = "数列找规律：等差 / 等比 / 差分 / 奇偶分组 / 递推 / 幂次偏移，求下一项"
    stage = 1

    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        blank = find_blank_position(question.stem)
        if blank == "middle":
            return Solution(
                status="failed",
                skill=self.id,
                method="series_pattern",
                error="暂不支持中间缺项（括号不在末尾），第一阶段只求解“下一项/末项”",
            )
        invocation = ctx.toolbox.run("number_series", text=question.stem)
        if not invocation.ok:
            return Solution(
                status="failed",
                skill=self.id,
                method="series_pattern",
                error=invocation.error,
                tool_calls=[invocation],
            )
        payload = invocation.result
        agreement = bool(payload["agreement"])
        candidates = payload.get("candidates") or []
        raw_next = payload.get("next_value")
        raw_next_float = payload.get("next_value_float")

        # P0 边界：没有任何规律匹配时，工具返回 next_value=None；
        # 显式结构化失败，保留工具给出的具体原因，而不是 float(None) 抛 TypeError
        if raw_next is None or raw_next_float is None:
            reason = str(payload.get("note") or "没有匹配到任何已知规律")
            return Solution(
                status="failed",
                skill=self.id,
                method="series_pattern",
                error=f"数列求解失败：{reason}",
                steps=[f"解析数列：{', '.join(payload.get('sequence') or [])}"],
                tool_calls=[invocation],
                meta={
                    "sequence": payload.get("sequence") or [],
                    "agreement": agreement,
                    "no_pattern": True,
                    "candidates": [
                        {"name": c.get("name"), "next_value": c.get("next_value"), "priority": c.get("priority")}
                        for c in candidates
                    ],
                },
            )

        next_text = str(raw_next)
        next_value = float(raw_next_float)
        steps: List[str] = [f"解析数列：{', '.join(payload['sequence'])}"]
        if candidates:
            steps.append(f"匹配到 {len(candidates)} 条规律：")
            for candidate in candidates[:4]:
                steps.append(
                    f"{candidate['name']}：{candidate['explanation']}（下一项 {candidate['next_value']}）"
                )
        
        # 检查是否存在歧义
        is_ambiguous = not agreement and len(candidates) > 1
        
        if is_ambiguous:
            steps.append(f"注意：{payload['note']}")
            # 收集所有候选答案（使用 Fraction 保持精确性）
            candidate_answers = set()
            for candidate in candidates:
                try:
                    # 使用 Fraction 解析字符串格式的 next_value
                    value = Fraction(candidate["next_value"])
                    candidate_answers.add(value)
                except (ValueError, ZeroDivisionError, KeyError):
                    # 如果无法解析，跳过该候选
                    continue
            
            # 如果没有有效的候选答案，返回失败
            if not candidate_answers:
                return Solution(
                    status="failed",
                    skill=self.id,
                    method="series_pattern",
                    error="数列存在歧义，但无法解析任何候选答案",
                    steps=steps,
                    tool_calls=[invocation],
                    meta={
                        "sequence": payload["sequence"],
                        "agreement": agreement,
                        "ambiguous": True,
                        "candidates": [
                            {"name": c["name"], "next_value": c["next_value"], "priority": c["priority"]}
                            for c in candidates
                        ],
                    },
                )
            
            # 如果有选项，检查哪些候选答案匹配选项
            if question.options:
                matching_labels = []
                for cand_value in candidate_answers:
                    # 转换为浮点数用于匹配选项
                    label = match_option(float(cand_value), [(option.label, option.text) for option in question.options])
                    if label:
                        matching_labels.append((label, cand_value))
                
                # 如果只有一个候选答案匹配选项，使用该答案但标记为 ambiguous
                if len(matching_labels) == 1:
                    label, matched_value = matching_labels[0]
                    steps.append(f"虽然存在歧义，但只有一个候选答案匹配选项：{label}")
                    return Solution(
                        status="solved",
                        skill=self.id,
                        answer=label,
                        answer_text=question.option_text(label) or "",
                        steps=steps,
                        confidence=0.5,  # 降低置信度
                        method="series_pattern",
                        tool_calls=[invocation],
                        meta={
                            "sequence": payload["sequence"],
                            "next_value": str(matched_value),
                            "agreement": agreement,
                            "ambiguous": True,
                            "candidates": [
                                {"name": c["name"], "next_value": c["next_value"], "priority": c["priority"]}
                                for c in candidates
                            ],
                        },
                    )
                elif len(matching_labels) > 1:
                    # 多个候选答案都匹配选项，无法确定
                    steps.append(f"存在歧义：{len(matching_labels)} 个候选答案都匹配选项")
                    return Solution(
                        status="failed",
                        skill=self.id,
                        method="series_pattern",
                        error=f"数列存在歧义，{len(matching_labels)} 个候选答案都匹配选项，无法确定唯一答案",
                        steps=steps,
                        tool_calls=[invocation],
                        meta={
                            "sequence": payload["sequence"],
                            "agreement": agreement,
                            "ambiguous": True,
                            "candidates": [
                                {"name": c["name"], "next_value": c["next_value"], "priority": c["priority"]}
                                for c in candidates
                            ],
                        },
                    )
                else:
                    # 没有候选答案匹配选项
                    steps.append("存在歧义，且没有候选答案匹配选项")
                    return Solution(
                        status="failed",
                        skill=self.id,
                        method="series_pattern",
                        error="数列存在歧义，且没有候选答案匹配选项",
                        steps=steps,
                        tool_calls=[invocation],
                        meta={
                            "sequence": payload["sequence"],
                            "agreement": agreement,
                            "ambiguous": True,
                            "candidates": [
                                {"name": c["name"], "next_value": c["next_value"], "priority": c["priority"]}
                                for c in candidates
                            ],
                        },
                    )
            else:
                # 没有选项，但存在歧义
                steps.append("存在歧义，且没有选项可供参考")
                return Solution(
                    status="failed",
                    skill=self.id,
                    method="series_pattern",
                    error="数列存在歧义，无法确定唯一答案",
                    steps=steps,
                    tool_calls=[invocation],
                    meta={
                        "sequence": payload["sequence"],
                        "agreement": agreement,
                        "ambiguous": True,
                        "candidates": [
                            {"name": c["name"], "next_value": c["next_value"], "priority": c["priority"]}
                            for c in candidates
                        ],
                    },
                )
        
        steps.append(f"取优先级最高的规律，得到下一项 = {next_text}")

        labels = question.option_labels()
        label: Optional[str] = None
        if question.options:
            label = match_option(next_value, [(option.label, option.text) for option in question.options])
            if label is None:
                return Solution(
                    status="failed",
                    skill=self.id,
                    method="series_pattern",
                    error=f"数列推算结果 {next_text} 与所有选项均不匹配",
                    steps=steps,
                    tool_calls=[invocation],
                    meta={"sequence": payload["sequence"], "next_value": next_text, "agreement": agreement},
                )
            steps.append(f"匹配选项 {label}. {question.option_text(label) or ''}".strip())

        confidence = 0.9 if agreement else 0.62
        return Solution(
            status="solved",
            skill=self.id,
            answer=label if label is not None else next_text,
            answer_text=question.option_text(label) if label else next_text,
            steps=steps,
            confidence=confidence,
            method="series_pattern",
            tool_calls=[invocation],
            meta={
                "sequence": payload["sequence"],
                "next_value": next_text,
                "agreement": agreement,
                "ambiguous": is_ambiguous,
                "candidates": [candidate["name"] for candidate in candidates],
            },
        )

    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        raw_sequence = solution.meta.get("sequence")
        if not raw_sequence:
            return None
        checks: List[VerificationCheck] = []
        try:
            sequence = [Fraction(str(item)) for item in raw_sequence]
        except (ValueError, ZeroDivisionError):
            return None
        checks.append(
            VerificationCheck(
                name="series.sequence_parsed",
                passed=len(sequence) >= 3,
                detail=f"复算数列共 {len(sequence)} 项",
            )
        )
        try:
            holdout = solve_series(sequence[:-1])
            holdout_ok = holdout.next_value == sequence[-1]
            checks.append(
                VerificationCheck(
                    name="series.holdout_prediction",
                    passed=holdout_ok,
                    detail=(
                        f"用前 {len(sequence) - 1} 项预测末项：{holdout.next_value} vs 实际 {sequence[-1]}"
                        if holdout.next_value is not None
                        else "前 n-1 项未识别出规律"
                    ),
                    severity="warning",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                VerificationCheck(name="series.holdout_prediction", passed=False, detail=str(exc), severity="warning")
            )
        try:
            full = solve_series(sequence)
            claimed = Fraction(str(solution.meta.get("next_value")))
            candidate_values = {candidate.next_value for candidate in full.candidates}
            is_ambiguous = bool(solution.meta.get("ambiguous"))

            if is_ambiguous:
                # 歧义题校验：所选答案必须是独立复算得到的**有效候选之一**，
                # 不要求等于最高优先级结果（合理的非首选候选不应被拒）
                in_candidates = claimed in candidate_values
                checks.append(
                    VerificationCheck(
                        name="series.recompute",
                        passed=in_candidates,
                        detail=(
                            f"歧义数列：独立复算候选集合 = {sorted(str(value) for value in candidate_values)}，"
                            f"Solver 记录 = {claimed}（{'在候选内，接受 Solver 基于选项匹配的选择' if in_candidates else '不在候选集合内，答案无效'}）"
                        ),
                    )
                )
            else:
                consistent = full.next_value == claimed
                checks.append(
                    VerificationCheck(
                        name="series.recompute",
                        passed=consistent,
                        detail=f"独立复算下一项 = {full.next_value}，Solver 记录 = {claimed}",
                    )
                )
                if not consistent and full.next_value is not None:
                    label = match_option(
                        float(full.next_value), [(option.label, option.text) for option in question.options]
                    ) if question.options else None
                    return Verification(
                        passed=False,
                        action="repair",
                        checks=checks,
                        final_answer=label or str(full.next_value),
                        repaired_answer=label or str(full.next_value),
                        notes="以独立复算结果修正答案",
                    )
        except Exception as exc:  # noqa: BLE001
            checks.append(VerificationCheck(name="series.recompute", passed=False, detail=str(exc)))

        if question.options and solution.answer:
            checks.append(
                VerificationCheck(
                    name="series.answer_in_options",
                    passed=solution.answer in question.option_labels(),
                    detail=f"答案 {solution.answer} 是否在选项内",
                )
            )
        passed = all(check.passed for check in checks if check.severity == "blocking")
        return Verification(
            passed=passed,
            action="accept" if passed else "reject",
            checks=checks,
            final_answer=solution.answer if passed else None,
            notes="留出预测校验 + 全序列复算",
        )
