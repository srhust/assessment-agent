"""Verifier：独立校验求解结果。

校验分三层：
1. 结构校验（状态、答案合法性、选项存在性）；
2. 题型专属复核（Skill.verify：SymPy 之外的独立算术、留出预测、Z3 重建模）；
3. 可选的 LLM 交叉校验（默认关闭，配置 ``VERIFIER_USE_LLM=true`` 启用）。

校验器可以给出 ``repair``（可修正答案）或 ``reject``（拒绝），但绝不静默修改
Solver 的原始输出——修正结果单独记录在 ``repaired_answer``。
"""

from __future__ import annotations

import time
from typing import List, Optional

from .config import Settings
from .llm import ChatModel, LLMError
from .prompts import VERIFIER_SYSTEM, verifier_user_prompt
from .schemas import Question, Solution, Verification, VerificationCheck


class Verifier:
    def __init__(self, settings: Settings, registry=None, llm: Optional[ChatModel] = None):
        self.settings = settings
        self.registry = registry
        self.llm = llm

    def verify(self, question: Question, solution: Solution, ctx=None) -> Verification:
        started = time.perf_counter()
        checks: List[VerificationCheck] = []
        repaired: Optional[str] = None

        checks.append(
            VerificationCheck(
                name="solution.status",
                passed=solution.status == "solved",
                detail=f"求解状态 = {solution.status}" + (f"（{solution.error}）" if solution.error else ""),
            )
        )
        checks.append(
            VerificationCheck(
                name="solution.answer_present",
                passed=bool(solution.answer),
                detail=f"答案 = {solution.answer!r}",
            )
        )
        if question.options:
            checks.append(
                VerificationCheck(
                    name="answer.in_options",
                    passed=solution.answer in question.option_labels() if solution.answer else False,
                    detail=f"选项集合 {question.option_labels()}",
                )
            )
        checks.append(
            VerificationCheck(
                name="solution.confidence",
                passed=solution.confidence >= self.settings.verifier_min_confidence,
                detail=f"置信度 {solution.confidence:.2f}，阈值 {self.settings.verifier_min_confidence:.2f}",
                severity="warning",
            )
        )

        skill_action: Optional[str] = None
        if solution.status == "solved":
            skill = self.registry.get(solution.skill) if self.registry is not None else None
            if skill is not None:
                try:
                    skill_verification = skill.verify(question, solution, ctx if ctx is not None else _ctx_stub(self))
                except Exception as exc:  # noqa: BLE001 - 复核异常必须记录为失败
                    checks.append(
                        VerificationCheck(
                            name=f"{skill.id}.verify_error",
                            passed=False,
                            detail=f"题型复核执行异常：{type(exc).__name__}: {exc}",
                            severity="blocking",
                        )
                    )
                    skill_verification = None
                if skill_verification is not None:
                    checks.extend(skill_verification.checks)
                    skill_action = skill_verification.action
                    if skill_verification.repaired_answer:
                        repaired = skill_verification.repaired_answer
                    if skill_action == "unverified":
                        # R2c：独立复核无法确认时明确标记 unverified——绝不默认通过，
                        # 也不按普通 reject 冒充“验证器发现了错误”，两者归因不同
                        checks.append(
                            VerificationCheck(
                                name="verify.unverified_action",
                                passed=False,
                                detail="独立复核无法确认候选答案，按未验证处理（不通过、不计入误放）",
                            )
                        )
            else:
                checks.append(
                    VerificationCheck(
                        name="skill.registered",
                        passed=False,
                        detail=f"未找到 Skill: {solution.skill}",
                    )
                )

        if self.settings.verifier_use_llm and self.llm is not None and self.settings.llm_enabled and solution.status == "solved":
            crosscheck = self._llm_crosscheck(question, solution)
            if crosscheck is not None:
                checks.append(crosscheck)
                # LLM 判定错误时记录为失败，但不自动采纳 LLM 建议的答案
                # 只有当确定性工具复核也提供了 repaired_answer 时才修复
                if not crosscheck.passed and crosscheck.name == "llm.crosscheck" and "模型判定错误" in crosscheck.detail:
                    checks.append(
                        VerificationCheck(
                            name="llm.suggestion_ignored",
                            passed=False,
                            detail="LLM 判定错误，但不会自动采纳其建议答案（需确定性工具复核支持）",
                            severity="warning",
                        )
                    )

        blocking_failures = [check for check in checks if check.severity == "blocking" and not check.passed]
        passed = not blocking_failures
        if passed:
            action = "accept"
            final_answer = solution.answer
        elif skill_action == "unverified":
            # R2c：独立复核未确认 → 受控弃权（不输出答案、不谎称验证纠错）
            action = "unverified"
            final_answer = None
        elif repaired is not None and (not question.options or repaired in question.option_labels()):
            action = "repair"
            final_answer = repaired
        else:
            action = "reject"
            final_answer = None
        return Verification(
            passed=passed,
            action=action,
            checks=checks,
            final_answer=final_answer,
            repaired_answer=repaired if action == "repair" else None,
            notes="结构校验 + 题型专属复核" + ("+ LLM 交叉校验" if self.settings.verifier_use_llm else ""),
            duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )

    # ------------------------------------------------------------------ 内部
    def _llm_crosscheck(self, question: Question, solution: Solution) -> Optional[VerificationCheck]:
        import json

        try:
            payload = self.llm.complete_json(  # type: ignore[union-attr]
                VERIFIER_SYSTEM,
                verifier_user_prompt(
                    question.render(),
                    json.dumps(solution.to_llm_payload(), ensure_ascii=False),
                ),
            )
        except LLMError as exc:
            return VerificationCheck(
                name="llm.crosscheck",
                passed=False,
                detail=f"交叉校验不可用：{exc}",
                severity="warning",
            )
        verdict = str(payload.get("verdict") or "uncertain").strip().lower()
        reason = str(payload.get("reason") or "")
        suggested = payload.get("suggested_answer")
        if verdict == "correct":
            return VerificationCheck(name="llm.crosscheck", passed=True, detail=f"模型判定正确：{reason}")
        if verdict == "incorrect":
            return VerificationCheck(
                name="llm.crosscheck",
                passed=False,
                detail=f"模型判定错误：{reason}；建议答案：{suggested}",
            )
        return VerificationCheck(
            name="llm.crosscheck",
            passed=True,
            detail=f"模型无法确定：{reason}",
            severity="warning",
        )


def _extract_suggested(detail: str) -> Optional[str]:
    marker = "建议答案："
    if marker not in detail:
        return None
    value = detail.split(marker, 1)[1].strip().strip("。.")
    return value or None


def _ctx_stub(verifier: Verifier):
    """题型复核只需要 settings / llm / toolbox 的空壳上下文。"""

    class _Ctx:
        settings = verifier.settings
        llm = verifier.llm
        knowledge = None
        artifacts = None
        toolbox = None

        def knowledge_note(self, skill_id: str, max_chars: int = 1200):
            return None

    return _Ctx()
