"""R3b 言语理解 Skill：原文证据 → 问题目标 → 逐选项分析 → 冲突检查 → 答案或弃权。

可靠性纪律：
- 答案只能来自"证据引用 + 确定性规则核/审计"，模型不得只交字母，也不得凭自我确认通过；
- 每条证据引用必须对应原文有效字符区间：不存在/越界/截断改义 → 拒绝；
- 无 LLM 时只运行可形式化子集（搭配表/否定极性/量词情态强度/结论句覆盖/条件链 Z3），
  覆盖不到一律 failed 或 unverified——不伪造离线语义能力；
- 验证层与求解共享文本输入但使用**独立实现的重算**（audit：包含度+奇偶扫描）；
  cloze/argument 的审计只能自洽复核，其局限如实写入 notes。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..schemas import (Question, Solution, VerbalEvidence, VerbalOptionAnalysis,
                        VerbalReasoningPlan, Verification, VerificationCheck)
from ..tools.verbal_tool import NEGATIVE_Q, audit, build_verbal_plan, detect_task, spans_valid
from .base import BaseSkill, SkillContext

_ALLOWED_RELATIONS = {"supported", "contradicted", "overreach", "absent", "insufficient",
                      "covers", "misses", "collocation_hit", "collocation_conflict",
                      "strengthens", "weakens", "neutral", "not_formalizable", "unanalyzable",
                      "assumption_candidate"}
_ALLOWED_TASKS = {"cloze", "main_idea", "inference", "argument"}


def _fail_solution(skill_id: str, failure: Dict[str, Any],
                   extra: Optional[Dict[str, Any]] = None) -> Solution:
    meta: Dict[str, Any] = {"failure": failure}
    if extra:
        meta.update(extra)
    return Solution(
        status="failed", skill=skill_id, answer=None, confidence=0.0,
        method="verbal_understanding:plan",
        error=(f"言语理解规则核无法可靠判定: {failure.get('code')} "
               f"{json.dumps(failure.get('detail', {}), ensure_ascii=False)[:200]}"),
        meta=meta,
    )


class VerbalUnderstandingSkill(BaseSkill):
    id = "verbal_understanding"
    name_zh = "言语理解"
    description = "选词填空（搭配子集）、主旨概括（结论覆盖）、阅读推断（否定/量词/情态核对）、加强削弱（条件链 Z3）"
    stage = 3
    supported = True
    question_types = ("single_choice",)

    # ------------------------------------------------------------------ solve
    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        if getattr(ctx.settings, "r3b_verbal", True) is False:
            return self.unsupported(question, "言语理解能力已由开关关闭（消融基线）")
        options = [(o.label, o.text) for o in question.options]
        if not options or not any(norm_light(t) for _, t in options):
            # 无选项的填空题不在离线子集：如实声明不支持，而不是硬猜
            return self.unsupported(question, "言语理解当前离线子集只覆盖有选项题（无选项填空未实现）")
        if detect_task(question.stem) is None:
            return self.unsupported(question, "题干不属于言语理解离线子集（选词/主旨/推断/论证之外）")

        result = build_verbal_plan(question.stem, options)
        plan_source = "rules"
        llm_rejection: Optional[Dict[str, Any]] = None
        if not result.get("ok") and ctx.llm is not None:
            llm_result, llm_rejection = self._llm_plan(question, ctx, options)
            if llm_result is not None:
                result = llm_result
                plan_source = "llm_audited"
        if not result.get("ok"):
            failure = result.get("failure", {"code": "plan_unavailable"})
            return _fail_solution(self.id, failure,
                                  {"llm_rejection": llm_rejection} if llm_rejection else None)

        plan: VerbalReasoningPlan = result["plan"]
        if plan.proposed_answer not in {l for l, _ in options}:
            return _fail_solution(self.id, {"code": "answer_binding", "detail": {"label": plan.proposed_answer}})
        meta = {
            "verbal_plan": plan.model_dump(),
            "task_type": plan.task_type,
            "plan_source": plan_source,
            "evidence_count": sum(len(a.evidence) for a in plan.option_analyses) + len(plan.passage_spans),
            "unsupported_claims": plan.unsupported_claims,
            "uncertainty": plan.uncertainty_reasons,
            "confidence": plan.confidence,
        }
        answer_ana = next((a for a in plan.option_analyses if a.label == plan.proposed_answer), None)
        steps = [
            f"任务类型: {plan.task_type}（提问锚点: {plan.question_target[:40]}）",
            f"逐选项关系: " + ", ".join(f"{a.label}={a.relation}" for a in plan.option_analyses),
        ]
        if answer_ana and answer_ana.evidence:
            ev = answer_ana.evidence[0]
            steps.append(f"关键证据 [{ev.start}:{ev.end}] {ev.text[:40]}")
        steps.append(f"答案: {plan.proposed_answer}（{plan.resolution}，conf {plan.confidence}）")
        if plan_source != "rules":
            steps.append("计划来源: LLM 提交，证据区间与审计重算通过后才保留")
        return Solution(
            status="solved", skill=self.id, answer=plan.proposed_answer,
            confidence=plan.confidence, method=f"verbal_understanding:{plan.task_type}",
            reasoning_steps=steps, meta=meta,
        )

    # ------------------------------------------------------------------ LLM 候选计划（必须过同一审计）
    def _llm_plan(self, question: Question, ctx: SkillContext,
                  options: Sequence[Tuple[str, str]]
                  ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        payload = None
        try:
            payload = ctx.llm.complete_json(  # type: ignore[union-attr]
                system="你是言语理解分析器。只输出 JSON：{task_type, question_target, premises, conclusion, "
                       "option_analyses:[{label, relation, evidence:[{start,end,text}], notes}], "
                       "unsupported_claims, proposed_answer, confidence, uncertainty_reasons}。"
                       "必须逐选项分析；evidence 的 text 必须是题干原文逐字片段并给出其字符区间（start,end）；"
                       "不得引用训练记忆或外部事实；不得只给字母答案。",
                user=json.dumps({"stem": question.stem,
                                  "options": [{"label": l, "text": t} for l, t in options],
                                  "relations_allowed": sorted(_ALLOWED_RELATIONS - {"unanalyzable"}),
                                  "tasks_allowed": sorted(_ALLOWED_TASKS)}, ensure_ascii=False)[:6000],
            )
        except Exception:
            return None, {"code": "llm_call_failed"}
        return self._bind_llm_plan(question, payload, options)

    def _bind_llm_plan(self, question: Question, payload: Any,
                       options: Sequence[Tuple[str, str]]) -> Tuple[Optional[Dict[str, Any]],
                                                                      Optional[Dict[str, Any]]]:
        """把 LLM 计划绑定到原文证据 + 独立审计；任何不合格项直接拒绝（不回退给模型自证）。"""
        stem = question.stem
        if not isinstance(payload, dict):
            return None, {"code": "payload_shape"}
        task = str(payload.get("task_type") or "")
        if task not in _ALLOWED_TASKS:
            return None, {"code": "task_not_allowed", "detail": {"task_type": task[:30]}}
        labels = {l for l, _ in options}
        answer = str(payload.get("proposed_answer") or "")
        if answer not in labels:
            return None, {"code": "answer_label_invalid", "detail": {"label": answer[:10]}}
        raw_analyses = payload.get("option_analyses")
        if not isinstance(raw_analyses, list):
            return None, {"code": "analyses_missing"}
        by_label = {l: t for l, t in options}
        analyses: List[VerbalOptionAnalysis] = []
        seen: set = set()
        for item in raw_analyses:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "")
            if label not in labels or label in seen:
                continue
            seen.add(label)
            rel = str(item.get("relation") or "unanalyzable")
            if rel not in _ALLOWED_RELATIONS:
                rel = "unanalyzable"
            evs = []
            for ev in item.get("evidence") or []:
                if not isinstance(ev, dict):
                    continue
                try:
                    start, end = int(ev.get("start", -1)), int(ev.get("end", -1))
                except (TypeError, ValueError):
                    continue
                text = str(ev.get("text") or "")
                role = str(ev.get("role") or "support")
                # 证据必须逐字对应原文区间；截断改义在验证层另有奇偶复核
                if 0 <= start < end <= len(stem) and stem[start:end] == text:
                    evs.append(VerbalEvidence(option=label, start=start, end=end, text=text, role=role))
            analyses.append(VerbalOptionAnalysis(label=label, option_text=by_label[label],
                                                 relation=rel, definitive=False, evidence=evs,
                                                 notes=str(item.get("notes") or "")[:200]))
        missing = labels - seen
        answer_ana = next((a for a in analyses if a.label == answer), None)
        if answer_ana is None or missing:
            return None, {"code": "analysis_incomplete",
                          "detail": {"missing": sorted(missing) or [answer]}}
        if not answer_ana.evidence and answer_ana.relation != "absent":
            # 无证据引用的"模型直觉"不允许被接受（absent 类反向候选可无区间引用）
            return None, {"code": "answer_without_evidence", "detail": {"relation": answer_ana.relation}}
        # claims 必须能在原文中定位，否则记入 unsupported_claims
        claims = []
        for c in payload.get("unsupported_claims") or []:
            s = str(c)
            if s and s not in stem:
                claims.append(s[:60])
        plan = VerbalReasoningPlan(
            task_type=task, question_target=str(payload.get("question_target") or "")[:200],
            negative_target=bool(NEGATIVE_Q.match(str(payload.get("question_target") or ""))),
            premises=[str(x)[:120] for x in (payload.get("premises") or [])][:8],
            conclusion=str(payload.get("conclusion") or "")[:200],
            option_analyses=analyses, unsupported_claims=claims,
            proposed_answer=answer, confidence=float(payload.get("confidence") or 0.0),
            uncertainty_reasons=[str(x)[:120] for x in (payload.get("uncertainty_reasons") or [])][:6],
            resolution="llm_audited",
            assumptions=["LLM 候选计划；接受条件=证据区间有效+独立审计 confirmed（自洽复核不足以接受）"],
        )
        audit_res = audit(stem, options, answer, task, answer_ana.relation,
                          [e.text for e in answer_ana.evidence if e.role != "against"])
        if audit_res.get("status") == "refuted":
            return None, {"code": "audit_refuted", "detail": audit_res}
        if audit_res.get("status") == "insufficient":
            return None, {"code": "audit_insufficient", "detail": audit_res}
        # self_consistent：cloze/argument 的审计与求解共享实现——
        # LLM 通道不因此获得接受资格，验证层会把 self_consistent+llm_audited 记为 unverified
        return {"ok": True, "plan": plan}, None

    # ------------------------------------------------------------------ verify
    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        if solution.status != "solved" or "verbal_plan" not in (solution.meta or {}):
            return None
        try:
            plan = VerbalReasoningPlan.model_validate(solution.meta["verbal_plan"])
        except Exception:
            return self._verdict(False, "reject", [VerificationCheck(
                name="verbal.plan_shape", passed=False, detail="计划结构无法解析", severity="blocking")],
                solution.answer)
        stem = question.stem
        options = [(o.label, o.text) for o in question.options]
        checks: List[VerificationCheck] = []
        contradicted = unverifiable = False
        llm_channel = solution.meta.get("plan_source") == "llm_audited"

        # ① 证据跨度真实存在（逐字符区间核对）
        problems = spans_valid(plan, stem)
        if problems:
            checks.append(VerificationCheck(name="verbal.evidence_spans", passed=False,
                                             detail="；".join(problems[:3]), severity="blocking"))
            contradicted = True
        else:
            checks.append(VerificationCheck(name="verbal.evidence_spans", passed=True,
                                             detail="全部引用与原文区间逐字一致"))

        # ② 引用必须来自材料区（提问句之后不属于证据来源；against/absence 角色除外）
        m_q = re.search(r"[?？]", stem)
        question_zone = m_q.start() if m_q else len(stem)
        bad_src = [e for a in plan.option_analyses for e in a.evidence
                   if e.start >= 0 and e.start >= question_zone and e.role == "support"]
        if bad_src:
            checks.append(VerificationCheck(name="verbal.evidence_source", passed=False,
                                             detail=f"{len(bad_src)} 条 support 引用落在提问句之后",
                                             severity="blocking"))
            contradicted = True
        else:
            checks.append(VerificationCheck(name="verbal.evidence_source", passed=True,
                                             detail="support 引用均位于材料区"))

        # ③ 否定/条件/量词遗漏：对答案选项做独立包含度+奇偶重算
        answer_ana = next((a for a in plan.option_analyses if a.label == solution.answer), None)
        if answer_ana is None:
            return self._verdict(False, "reject", [VerificationCheck(
                name="verbal.option_coverage", passed=False, detail="答案选项缺少分析记录",
                severity="blocking")], solution.answer)
        audit_res = audit(stem, options, str(solution.answer), plan.task_type,
                          answer_ana.relation,
                          [e.text for e in answer_ana.evidence if e.role != "against"])
        status = audit_res.get("status")
        if status == "refuted":
            checks.append(VerificationCheck(name="verbal.cues_preserved", passed=False,
                                             detail=f"独立重算反驳计划结论: {audit_res}", severity="blocking"))
            contradicted = True
        elif status in {"insufficient"}:
            checks.append(VerificationCheck(name="verbal.cues_preserved", passed=False,
                                             detail=f"独立重算不可判定: {audit_res.get('reason', '')}",
                                             severity="blocking"))
            unverifiable = True
        elif status == "self_consistent":
            note = "审计与求解共享实现（自洽复核，非独立证明）"
            if llm_channel:
                checks.append(VerificationCheck(name="verbal.cues_preserved", passed=False,
                                                 detail=note + "；LLM 通道不允许仅凭自洽接受", severity="blocking"))
                unverifiable = True
            else:
                checks.append(VerificationCheck(name="verbal.cues_preserved", passed=True, detail=note))
        else:
            checks.append(VerificationCheck(name="verbal.cues_preserved", passed=True,
                                             detail=f"独立重算确认: {audit_res.get('reason', '')}"))

        # ④ 无依据外部事实：答案选项的内容词必须能在原文定位（absent/overreach 反向题除外）
        new_terms = [t for t in answer_ana.new_terms if t and len(t) >= 2]
        allow_new = plan.negative_target and answer_ana.relation in {"absent", "overreach", "contradicted"}
        if not allow_new and len(new_terms) > 2 and answer_ana.relation == "supported":
            checks.append(VerificationCheck(name="verbal.unsupported_claims", passed=False,
                                             detail=f"答案引入未定位内容词 {new_terms[:4]}", severity="blocking"))
            contradicted = True
        else:
            checks.append(VerificationCheck(name="verbal.unsupported_claims", passed=True,
                                             detail=f"未定位内容词 {len(new_terms)}（已核对/反向题允许）"))

        # ⑤ 竞争选项排除：每个非答案选项必须有分析记录；规则通道允许"不可判定"但降置信
        others = [a for a in plan.option_analyses if a.label != solution.answer]
        if len(others) + 1 != len(plan.option_analyses):
            checks.append(VerificationCheck(name="verbal.competitors_excluded", passed=False,
                                             detail="选项覆盖不完整", severity="blocking"))
            unverifiable = True
        else:
            undef = [a.label for a in others if a.relation in {"unanalyzable", "insufficient",
                                                               "not_formalizable"}]
            if undef and llm_channel:
                checks.append(VerificationCheck(name="verbal.competitors_excluded", passed=False,
                                                 detail=f"LLM 计划存在未排除竞争项 {undef}", severity="blocking"))
                unverifiable = True
            else:
                checks.append(VerificationCheck(name="verbal.competitors_excluded", passed=True,
                                                 detail=("全部竞争选项有确定性判定" if not undef
                                                          else f"部分竞争项规则不可判定 {undef}（已记录不确定性）")))

        # ⑥ 多合理答案：重算候选数（不同实现按 audit 度量），>1 不允许接受
        rival = [a.label for a in others if a.definitive and a.relation == answer_ana.relation
                 and a.relation in {"supported", "covers", "collocation_hit", "strengthens", "weakens",
                                     "contradicted", "overreach", "absent"}]
        if rival:
            checks.append(VerificationCheck(name="verbal.uniqueness", passed=False,
                                             detail=f"同关系竞争项 {rival}，答案不唯一", severity="blocking"))
            unverifiable = True
        else:
            checks.append(VerificationCheck(name="verbal.uniqueness", passed=True, detail="无同关系确定竞争项"))

        return self._verdict(not contradicted and not unverifiable,
                             "reject" if contradicted else ("unverified" if unverifiable else "accept"),
                             checks, solution.answer,
                             notes=("言语验证：区间证据+独立重算+排除竞争；"
                                    "cloze/argument 自洽复核局限已声明，LLM 通道需 confirmed 才接受"))

    @staticmethod
    def _verdict(passed: bool, action: str, checks: List[VerificationCheck],
                 answer: Optional[str], notes: str = "") -> Verification:
        return Verification(passed=passed, action=action, checks=checks,
                            final_answer=answer if action == "accept" else None,
                            notes=notes)


def norm_light(text: str) -> str:
    return re.sub(r"\s+", "", text or "")
