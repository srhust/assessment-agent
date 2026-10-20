"""逻辑推理 Skill：排序比较走 Z3 排序模型；命题/蕴含/翻译类走 prop_logic（R7-C3）。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ..schemas import (Question, Solution, Verification, VerificationCheck)
from ..tools.logic_tool import Statement, _RankingModel, verify_answer
from ..tools.prop_logic import (F, FormulaError, entail, parse_chinese_sentence,
                                parse_formula, parse_statements, possible,
                                validate_payload)
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
            prop = self._solve_proposition(question, ctx)  # R7-C3：命题/蕴含通道
            if prop is not None:
                prop.tool_calls = [invocation]
                return prop
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
        if meta.get("solver_source") in {"prop_rules", "prop_llm"}:
            return self._verify_proposition(question, solution)
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

    # ------------------------------------------------------------ R7-C3 命题通道

    def _verify_proposition(self, question: Question,
                            solution: Solution) -> Verification:
        """独立复核：从 meta 的公式文本重建 → 逐字来源回查 → 本地 Z3 重判。
        求解与验证共享公式语言但**不共享求解期的判定结果**，判定完全重放。"""
        checks: List[VerificationCheck] = []
        meta = solution.meta or {}
        stem = question.stem
        from ..tools.prop_logic import _canon
        # 回查域：题干 + 选项原文（选项解析可能新增原子，仍须可溯源、禁止编造）
        domain = _canon(stem) + "".join(_canon(o.text) for o in question.options)
        try:
            prem: List[F] = []
            for item in meta.get("premises") or []:
                prem.append(parse_formula(str(item.get("formula") or "")))
                text = str(item.get("text") or "")
                # prop_rules 通道 text 是"；"拼接的原子表——分段核对，每段须能溯源
                for frag in re.split(r"；", text):
                    frag_n = _canon(frag)
                    if frag_n and frag_n not in domain and len(frag_n) >= 2:
                        checks.append(VerificationCheck(
                            name="prop.premise_source", passed=False,
                            detail=f"前提子句无法逐字回查题干: {frag[:30]}", severity="blocking"))
                        return Verification(passed=False, action="reject", checks=checks,
                                            final_answer=None, notes="命题前提来源绑定失效")
            checks.append(VerificationCheck(name="prop.premise_source", passed=True,
                                             detail="前提子句全部逐字回查命中"))
            opts: List[F] = []
            for label, f_repr in (meta.get("options_formula") or {}).items():
                opts.append(parse_formula(str(f_repr)))
            labels = list((meta.get("options_formula") or {}).keys())
            hits, detail = self._decide(str(meta.get("question_kind") or "must"), prem,
                                        list(zip(labels, opts)))
            checks.append(VerificationCheck(name="prop.recompute", passed=True,
                                             detail=f"独立重放判定: {detail}"))
            if len(hits) != 1:
                checks.append(VerificationCheck(name="prop.uniqueness", passed=False,
                                                 detail=f"重放后命中 {len(hits)} 项，不唯一",
                                                 severity="blocking"))
                return Verification(passed=False, action="unverified", checks=checks,
                                    final_answer=None, notes="命题判定不唯一")
            checks.append(VerificationCheck(name="prop.uniqueness", passed=True,
                                             detail=f"唯一命中 {hits[0]}"))
            if hits[0] != solution.answer:
                checks.append(VerificationCheck(name="prop.agreement", passed=False,
                                                 detail=f"独立重放 {hits[0]} ≠ 求解 {solution.answer}",
                                                 severity="blocking"))
                return Verification(passed=False, action="repair", checks=checks,
                                    final_answer=None, repaired_answer=hits[0],
                                    notes="独立重放修正答案")
            checks.append(VerificationCheck(name="prop.agreement", passed=True,
                                             detail="独立重放与求解一致"))
            return Verification(passed=True, action="accept", checks=checks,
                                final_answer=solution.answer,
                                notes="命题公式本地 Z3 独立重放通过")
        except (FormulaError, KeyError, TypeError) as exc:
            checks.append(VerificationCheck(name="prop.verify_error", passed=False,
                                             detail=f"{type(exc).__name__}: {exc}",
                                             severity="blocking"))
            return Verification(passed=False, action="reject", checks=checks,
                                 final_answer=None, notes="命题复核重建失败")

    @staticmethod
    def _question_kind(stem: str) -> str:
        tail = stem[-80:] if stem else ""
        if re.search(r"不能推出|无法推出|不必然|一定为假|不可能", tail):
            return "impossible"
        if re.search(r"可能|可以推出(?:哪项)?为真|最?未必", tail):
            return "possible"
        return "must"

    def _decide(self, kind: str, prem: List[F], opts: List[F]):
        """返回 (答案label列表, 判定明细)。
        实质蕴含防线：结论是 P=>Q 而前提已否定 P（Σ∧P 不可满足）时，
        经典逻辑的空真不算"可推出"（如"参加培训的都通过笔试"在 ¬培训 下 vacuous）。"""
        hits, detail = [], []
        for label, f in opts:
            if kind == "must":
                res = entail(prem, f)
                if res is True and f.kind == "imp":
                    # 实质蕴含的空真防线（相关性）：前件须与前提相容，
                    # 且后件不得已是前提自身的定理（"学生→培训"在后件本已
                    # 为事实时 vacuous True，不是"推出"）。
                    if possible(prem, f.children[0]) is False or \
                            entail(prem, f.children[1]) is True:
                        res = None  # 空真：如实标记，不计入命中
            elif kind == "impossible":
                res = entail(prem, F("not", children=(f,)))
            else:
                res = possible(prem, f)
            detail.append({"label": label, "verdict": str(res)})
            if res is True:
                hits.append(label)
        return hits, detail

    def _solve_proposition(self, question: Question, ctx: SkillContext) -> Optional[Solution]:
        stem = question.stem
        kind = self._question_kind(stem)
        options = [(o.label, o.text) for o in question.options]

        # ---- 通道 1：规则分解（条件句模式；选项与前提**共享原子符号表**）----
        prems, atoms = parse_statements(stem)
        if prems and any(f.kind == "imp" for f in prems):
            opt_fs: List[Optional[F]] = []
            for _, text in options:
                t = re.sub(r"由此(?:可以|可)?推出.*|根据上述.*", "", text or "").strip("。；;，, ")
                opt_fs.append(parse_chinese_sentence(t, atoms) if t else None)
            # "无法确定"类元选项规则层不判定（None）；其余选项须有 ≥2 可形式化
            pairs = [(l, f) for (l, _), f in zip(options, opt_fs) if f is not None]
            if len(pairs) >= 2:
                hits, detail = self._decide(kind, prems, pairs)
                if len(hits) == 1:
                    label = hits[0]
                    meta_opts = [l for (l, _), f in zip(options, opt_fs) if f is None]
                    return Solution(
                        status="solved", skill=self.id, answer=label,
                        answer_text=question.option_text(label) or "",
                        confidence=0.9, method="prop_logic:rules",
                        steps=[f"前提公式: {[repr(p) for p in prems]}",
                               f"问题判定: {kind}",
                               f"选项判定: {detail}",
                               f"元选项未形式化（存在必然真结论即不成立）: {meta_opts}"],
                        meta={"solver_source": "prop_rules", "question_kind": kind,
                              "premises": [{"text": "；".join(a for a in atoms.values()),
                                            "formula": repr(p)} for p in prems],
                              "atoms": dict(atoms),
                              "options_formula": {l: repr(f) for l, f in pairs},
                              "decision": detail},
                    )
        # ---- 通道 2：LLM 子句抽取（模型只给公式骨架，求值在本地 Z3）----
        if ctx.llm is None or not options:
            return None
        payload = self._extract_propositions(ctx, stem, options)
        if payload is None:
            return None
        bound, err = validate_payload(payload, stem)
        if bound is None:
            return Solution(
                status="failed", skill=self.id, method="prop_logic:llm",
                error=f"命题抽取未通过绑定校验: {err}",
                meta={"solver_source": "prop_llm", "rejection": {"code": err or "unknown"}},
            )
        prem = [f for _, f in bound["premises"]]
        opts_f = [(l, f) for l, f in bound["options"]]
        hits, detail = self._decide(bound["question"], prem, opts_f)
        if len(hits) != 1:
            return Solution(
                status="failed", skill=self.id, method="prop_logic:llm",
                error=f"命题判定不唯一（{len(hits)} 命中）: {detail}",
                meta={"solver_source": "prop_llm", "decision": detail,
                      "premises": [{"text": t, "formula": repr(f)} for t, f in bound["premises"]],
                      "options_formula": {l: repr(f) for l, f in opts_f},
                      "question_kind": bound["question"]},
            )
        label = hits[0]
        return Solution(
            status="solved", skill=self.id, answer=label,
            answer_text=question.option_text(label) or "",
            confidence=0.88, method="prop_logic:llm",
            steps=[f"子句抽取（逐字绑定）: {[(t[:30], repr(f)) for t, f in bound['premises']]}",
                   f"本地 Z3 判定: {detail}"],
            meta={"solver_source": "prop_llm",
                  "premises": [{"text": t, "formula": repr(f)} for t, f in bound["premises"]],
                  "options_formula": {l: repr(f) for l, f in opts_f},
                  "question_kind": bound["question"], "decision": detail},
        )

    def _extract_propositions(self, ctx: SkillContext, stem: str,
                              options) -> Optional[dict]:
        try:
            return ctx.llm.complete_json(  # type: ignore[union-attr]
                system="你是命题逻辑抽取器。把题干拆成原子子句并符号化，只输出 JSON："
                       '{"premises":[{"text":"题干逐字片段","formula":"如 P=>Q"}],'
                       '"options":[{"label":"A","formula":"如 P"}],'
                       '"question":"must|possible|impossible"}。'
                       "公式语言：大写字母原子、! 非、& 且、| 或、=> 蕴含、<=> 充要、括号。"
                       "方向硬规则：只有P才Q ⇒ Q=>P；如果P就Q ⇒ P=>Q；除非P否则Q ⇒ !P=>Q。"
                       "text 必须能在题干逐字找到；不要给答案，只做结构化抽取。",
                user=json.dumps({"stem": stem[:2500],
                                  "options": [{"label": l, "text": t} for l, t in options]},
                                  ensure_ascii=False),
            )
        except Exception:
            return None
