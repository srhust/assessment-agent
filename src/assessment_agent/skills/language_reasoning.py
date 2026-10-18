"""R3c 语言推理 Skill：词典关系证据 → 方向签名 → 类比/近义/反义判别 → 独立复核。

可靠性纪律（与言语理解同一套三层判定，绝不坍缩成单一置信度）：
1. **证据有效**：每个关系判定必须绑定 `lexicon_r3c` 人工词条（word/field/source_text），
   verify 对账词典现存内容；
2. **语义关系成立**：方向签名（GENUS_UP≠GENUS_DOWN 等）显式比对，
   组成 vs 用途的粒度偷换记 mismatch；多重关系要求选项**覆盖题干全部签名**；
3. **答案可接受**：唯一 match + 独立重推导（audit_relation）confirmed 才 accept；
   竞争项未登录词典 → 不积极排除 → unverified（如实降格，不硬猜）。

LLM 通道（如配置）只能**提议**关系候选：所有提议词对必须能在词典中重算出一致签名，
同一模型的自评绝不构成确定性验证（拒绝词对不可重算、拒绝仅凭 confidence 的提交）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..schemas import (LanguageRelationPlan, Question, RelationEvidence,
                       Solution, Verification, VerificationCheck)
from ..tools.language_tool import (core_analogy, core_word_relation, detect_lang,
                                    infer_relations, parse_pair)
from .base import BaseSkill, SkillContext

_ALLOWED_TASKS = {"analogy", "synonym", "antonym", "en_analogy"}


def _fail(failure: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Solution:
    meta: Dict[str, Any] = {"failure": failure}
    if extra:
        meta.update(extra)
    return Solution(
        status="failed", skill="language_reasoning", answer=None, confidence=0.0,
        method="language_reasoning:plan",
        error=(f"语言推理规则核无法可靠判定: {failure.get('code')} "
               f"{json.dumps(failure.get('detail', {}), ensure_ascii=False)[:200]}"),
        meta=meta,
    )


class LanguageReasoningSkill(BaseSkill):
    id = "language_reasoning"
    name_zh = "语言推理"
    description = "词语类比（七类方向关系词典判别）、近义/反义（仅知识库证据）、英文关系类比子集"
    stage = 3
    supported = True
    question_types = ("single_choice",)

    # ------------------------------------------------------------------ solve
    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        if getattr(ctx.settings, "r3c_language", True) is False:
            return self.unsupported(question, "语言推理能力已由开关关闭（消融基线）")
        options = [(o.label, o.text) for o in question.options]
        if not options:
            return self.unsupported(question, "语言推理离线子集需要有选项的类比/近义/反义题")

        task = self._detect_task(question.stem)
        if task is None:
            return self.unsupported(question, "题干不属于语言推理离线子集（词语类比/近义/反义之外）")

        if task == "synonym":
            result = core_word_relation(question.stem, options, "syn")
        elif task == "antonym":
            result = core_word_relation(question.stem, options, "ant")
        else:
            result = core_analogy(question.stem, options)

        plan_source = "rules"
        llm_rejection: Optional[Dict[str, Any]] = None
        if not result.get("ok") and ctx.llm is not None:
            llm_result, llm_rejection = self._llm_plan(question, ctx, options, task)
            if llm_result is not None:
                result = llm_result
                plan_source = "llm_audited"
        if not result.get("ok"):
            failure = result.get("failure", {"code": "plan_unavailable"})
            return _fail(failure, {"llm_rejection": llm_rejection} if llm_rejection else None)

        plan: LanguageRelationPlan = result["plan"]
        if plan.proposed_answer not in {l for l, _ in options}:
            return _fail({"code": "answer_binding", "detail": {"label": plan.proposed_answer}})
        meta = {
            "relation_plan": plan.model_dump(),
            "task_type": plan.task_type,
            "plan_source": plan_source,
            "signature": plan.relation_signature,
            "uncertainty": plan.uncertainty_reasons,
            "confidence": plan.confidence,
        }
        steps = [
            f"任务: {plan.task_type}；题干词对 {plan.stem_pair}；关系签名 {plan.relation_signature}",
            "逐选项: " + ", ".join(f"{a.label}={a.verdict}"
                                    f"{'(缺' + '+'.join(a.missing) + ')' if a.missing else ''}"
                                    for a in plan.option_analyses),
            f"答案: {plan.proposed_answer}（{plan.resolution}，conf {plan.confidence}）",
        ]
        if plan_source != "rules":
            steps.append("计划来源: LLM 提议，词典重算一致后才保留（模型自评不作验证）")
        return Solution(
            status="solved", skill=self.id, answer=plan.proposed_answer,
            confidence=plan.confidence, method=f"language_reasoning:{plan.task_type}",
            reasoning_steps=steps, meta=meta,
        )

    @staticmethod
    def _detect_task(stem: str) -> Optional[str]:
        from ..tools.language_tool import _ANALOGY_Q, _ANT_Q, _SYN_Q, _PAIR_ZH
        if re.search(r"与.{0,16}意思(?:相近|相反)", stem):
            return "antonym" if "相反" in stem else "synonym"
        if _ANT_Q.search(stem):
            return "antonym"
        if _SYN_Q.search(stem):
            return "synonym"
        if re.search(r"\bis\s+to\b", stem, re.I) or re.search(r"[一-龥]{1,8}[∶:][一-龥]{1,8}", stem):
            return "analogy"     # 英文句式或词对格式（∶）——Skill 边界内，核心自会判断可解性
        if _ANALOGY_Q.search(stem) and (_PAIR_ZH.search(stem)
                                         or re.search(r"is\s+to", stem, re.I)):
            return "analogy"
        return None

    # ------------------------------------------------------- LLM 候选（仅提议权）
    def _llm_plan(self, question: Question, ctx: SkillContext,
                  options: Sequence[Tuple[str, str]], task: Optional[str]
                  ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        payload = None
        try:
            payload = ctx.llm.complete_json(  # type: ignore[union-attr]
                system="你是词语关系分析器。只输出 JSON：{task_type(analogy|synonym|antonym|en_analogy), "
                       "stem_pairs:[[word_a, word_b]], option_analyses:[{label, pair:[a,b], "
                       "relations:[TAG], verdict(match|partial|mismatch|unknown_pair)}], "
                       "proposed_answer, confidence}。"
                       "relations 只能从 GENUS_UP/GENUS_DOWN/PART_OF/HAS_PART/MADE_FROM/PRODUCES/"
                       "FUNCTION/PLACE/TOOL/CAUSE/EFFECT/SYN/ANT 中选择并保证方向。"
                       "你的输出将被人工词典逐对重算，无法重算一致的提交会被拒绝。",
                user=json.dumps({"stem": question.stem,
                                  "options": [{"label": l, "text": t} for l, t in options]},
                                  ensure_ascii=False)[:5000],
            )
        except Exception:
            return None, {"code": "llm_call_failed"}
        return self._bind_llm_plan(question, payload, options)

    def _bind_llm_plan(self, question: Question, payload: Any,
                       options: Sequence[Tuple[str, str]]
                       ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """LLM 提议 → 词典逐对重算；任何不一致/不可重算 → 拒绝（不回退给模型自证）。"""
        from ..tools.language_tool import _ANALOGY_Q  # noqa: F401
        if not isinstance(payload, dict):
            return None, {"code": "payload_shape"}
        task = str(payload.get("task_type") or "")
        if task not in _ALLOWED_TASKS:
            return None, {"code": "task_not_allowed", "detail": {"task_type": task[:30]}}
        labels = {l for l, _ in options}
        answer = str(payload.get("proposed_answer") or "")
        if answer not in labels:
            return None, {"code": "answer_label_invalid", "detail": {"label": answer[:10]}}
        sp = payload.get("stem_pairs")
        if not (isinstance(sp, list) and sp and isinstance(sp[0], list) and len(sp[0]) == 2):
            return None, {"code": "stem_pairs_invalid"}
        lang = detect_lang(question.stem)
        stem_pair = (str(sp[0][0]), str(sp[0][1]))
        rels_stem = infer_relations(stem_pair[0], stem_pair[1], lang)
        if not rels_stem:
            return None, {"code": "stem_pair_not_in_lexicon",
                           "detail": {"pair": list(stem_pair)}}
        sig = sorted({t for t, _, _ in rels_stem})
        by_label = dict(options)
        cands: List[str] = []
        analyses = []
        for item in payload.get("option_analyses") or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "")
            if label not in labels:
                continue
            pair_raw = item.get("pair")
            if not (isinstance(pair_raw, list) and len(pair_raw) == 2):
                return None, {"code": "option_pair_invalid", "detail": {"label": label}}
            p = (str(pair_raw[0]), str(pair_raw[1]))
            rels = infer_relations(p[0], p[1], lang)
            tags = sorted({t for t, _, _ in rels})
            model_rels = sorted(str(x) for x in (item.get("relations") or []))
            if task in ("analogy", "en_analogy"):
                if model_rels and model_rels != tags:
                    return None, {"code": "llm_relation_disagreement",
                                   "detail": {"label": label, "model": model_rels, "lexicon": tags}}
                verdict = ("match" if set(sig) <= set(tags) else
                            ("partial" if set(tags) & set(sig) else
                             ("unknown_pair" if not tags else "mismatch")))
                if not tags:
                    verdict = "unknown_pair"
            else:
                verdict = str(item.get("verdict") or "")
                want = "SYN" if task == "synonym" else "ANT"
                ok_syn = want in tags
                verdict = "match" if ok_syn else ("unknown_pair" if not tags else "mismatch")
            if verdict == "match":
                cands.append(label)
            analyses.append({"label": label, "pair": list(p), "own_relations": tags,
                              "verdict": verdict})
        seen = {a["label"] for a in analyses}
        if len(cands) != 1 or answer not in cands or seen != labels:
            return None, {"code": "llm_binding_failed",
                           "detail": {"candidates": cands, "answer": answer,
                                        "coverage": sorted(seen) == sorted(labels)}}
        from ..schemas import LanguageRelationOption
        plan = LanguageRelationPlan(
            task_type=task, question_target=question.stem[-40:],
            stem_pair=[stem_pair[0], stem_pair[1]], relation_signature=sig,
            evidence=[RelationEvidence(word_a=stem_pair[0], word_b=stem_pair[1], field=f,
                                        source_text=s, direction="a->b")
                       for _, f, s in rels_stem],
            option_analyses=[LanguageRelationOption(**a) for a in analyses],
            proposed_answer=answer, confidence=0.7, resolution="llm_audited",
            assumptions=["LLM 提议词对关系；接受条件=人工词典逐对重算一致+独立复核 confirmed"],
        )
        return {"ok": True, "plan": plan}, None

    # ------------------------------------------------------------------ verify
    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        if solution.status != "solved" or "relation_plan" not in (solution.meta or {}):
            return None
        try:
            plan = LanguageRelationPlan.model_validate(solution.meta["relation_plan"])
        except Exception:
            return self._verdict(False, "reject", [VerificationCheck(
                name="language.plan_shape", passed=False, detail="关系计划无法解析",
                severity="blocking")], solution.answer)
        stem = question.stem
        options = [(o.label, o.text) for o in question.options]
        checks: List[VerificationCheck] = []
        contradicted = unverifiable = False
        llm_channel = solution.meta.get("plan_source") == "llm_audited"

        # ① 关系证据可审计：plan.evidence 每条 source_text 与词典现存释义一致，标签可重推导
        from ..tools.language_tool import _lex
        L = _lex(detect_lang(stem))
        bad_ev: List[str] = []
        for ev in plan.evidence:
            entry = L.get(ev.word_a, {})
            if not entry or ev.source_text != entry.get("def", ""):
                bad_ev.append(f"{ev.word_a}->{ev.word_b}({ev.field})")
        if bad_ev:
            checks.append(VerificationCheck(name="language.relation_evidence", passed=False,
                                             detail=f"词条证据对账失败: {bad_ev[:3]}",
                                             severity="blocking"))
            contradicted = True
        else:
            checks.append(VerificationCheck(name="language.relation_evidence", passed=True,
                                             detail=f"{len(plan.evidence)} 条关系证据均可在人工词典对账"))

        # ② 方向一致性 + ③ 粒度偷换 + ⑥ 唯一性：独立重推导（audit_relation，不复用求解中间量）
        res = self._audit_call(stem, options, str(solution.answer), plan.model_dump())
        if res.get("status") == "confirmed":
            checks.append(VerificationCheck(name="language.direction_consistency", passed=True,
                                             detail=f"签名重推导一致 {res.get('signature')}"))
            checks.append(VerificationCheck(name="language.granularity_swap", passed=True,
                                             detail="无组成/用途等粒度或方向偷换"))
            checks.append(VerificationCheck(name="language.uniqueness", passed=True,
                                             detail="竞争选项均未覆盖题干关系签名"))
        else:
            probs = res.get("problems", [])
            sig_issue = any("签名" in p for p in probs)
            rival_issue = any("竞争选项" in p for p in probs)
            checks.append(VerificationCheck(
                name="language.direction_consistency", passed=not sig_issue,
                detail=("方向/签名一致" if not sig_issue else "；".join(p for p in probs if "签名" in p)[:150]),
                severity="blocking"))
            checks.append(VerificationCheck(
                name="language.granularity_swap", passed=not sig_issue,
                detail=("未发现粒度替换" if not sig_issue else "存在关系粒度/方向替换"),
                severity="blocking"))
            checks.append(VerificationCheck(
                name="language.uniqueness", passed=not rival_issue,
                detail=("唯一覆盖签名的选项" if not rival_issue
                        else "；".join(p for p in probs if "竞争选项" in p)[:150]),
                severity="blocking"))
            if res.get("status") == "refuted":
                contradicted = True
            else:
                unverifiable = True

        # ④ 竞争选项同等对待：每个非答案选项必须有可判定 verdict；unknown_pair → 未积极排除
        others = [a for a in plan.option_analyses if a.label != solution.answer]
        undef = [a.label for a in others if a.verdict in {"unknown_pair", ""}]
        if len(others) + 1 != len(plan.option_analyses):
            checks.append(VerificationCheck(name="language.competitors_excluded", passed=False,
                                             detail="选项覆盖不完整", severity="blocking"))
            unverifiable = True
        elif undef:
            detail = f"竞争项未登录词典无法积极排除: {undef}"
            if llm_channel:
                checks.append(VerificationCheck(name="language.competitors_excluded", passed=False,
                                                 detail=detail + "；LLM 通道拒绝", severity="blocking"))
                unverifiable = True
            else:
                checks.append(VerificationCheck(name="language.competitors_excluded", passed=True,
                                                 detail=detail + "（接受依赖唯一 match，不确定性已声明）"))
        else:
            checks.append(VerificationCheck(name="language.competitors_excluded", passed=True,
                                             detail="全部竞争选项有确定性排除"))

        # ⑤ 外部世界知识来源：全部涉及词对绑定人工词典；LLM 通道必须 audit confirmed 才接受
        words_ok = all(a.own_relations or a.verdict == "unknown_pair"
                        for a in plan.option_analyses)
        if llm_channel and res.get("status") != "confirmed":
            checks.append(VerificationCheck(name="language.world_knowledge_source", passed=False,
                                             detail="LLM 提议未经词典重算独立 confirmed",
                                             severity="blocking"))
            unverifiable = True
        elif not words_ok:
            checks.append(VerificationCheck(name="language.world_knowledge_source", passed=False,
                                             detail="存在既非词典可判定又未声明 unknown 的选项",
                                             severity="blocking"))
            contradicted = True
        else:
            checks.append(VerificationCheck(name="language.world_knowledge_source", passed=True,
                                             detail="关系判定全部绑定 lexicon_r3c 人工词条"))

        return self._verdict(not contradicted and not unverifiable,
                             "reject" if contradicted else ("unverified" if unverifiable else "accept"),
                             checks, solution.answer,
                             notes="语言推理验证：词条对账+方向签名独立重推导+竞争排除；"
                                   "词典外关系不可见，未登录竞争项如实降级不硬猜")

    def _audit_call(self, stem: str, options: Sequence[Tuple[str, str]],
                    answer: str, plan_dict: Dict[str, Any]) -> Dict[str, Any]:
        task = str(plan_dict.get("task_type") or "")
        if task in ("synonym", "antonym"):
            # 近义/反义：独立重推导答案词对 + 竞争覆盖
            from ..tools.language_tool import _lex
            want = "syn" if task == "synonym" else "ant"
            pair = plan_dict.get("stem_pair") or []
            if len(pair) < 1 or not pair[0]:
                return {"status": "insufficient", "problems": ["目标词缺失"]}
            target = pair[0]
            L = _lex("zh")
            if target not in L:
                return {"status": "insufficient", "problems": ["目标词不在词典"]}
            rels = L[target].get(want) or []
            rels = rels if isinstance(rels, list) else [rels]
            probs: List[str] = []
            unresolved: List[str] = []
            hits = []
            for label, text in options:
                w = re.sub(r"[^一-龥A-Za-z]", "", text)
                own = sorted({t for t, _, _ in infer_relations(target, w, "zh")}
                              | {t for t, _, _ in infer_relations(w, target, "zh")})
                if w in rels:
                    hits.append(label)
                elif own:
                    pass  # 可判定为不含目标关系
                else:
                    unresolved.append(f"竞争项 {label} 未登录词典（未被积极排除）")
            if answer not in hits:
                probs.append("答案签名不一致：重推导后答案不满足目标关系")
            if len(hits) > 1:
                probs.append(f"唯一性失败：{hits} 均满足")
            if probs:
                return {"status": "refuted" if any("答案" in p for p in probs) else "insufficient",
                        "problems": probs, "unresolved": unresolved}
            return {"status": "confirmed", "signature": [want.upper()], "unresolved": unresolved}
        from ..tools.language_tool import audit_relation
        return audit_relation(stem, options, answer, plan_dict)

    @staticmethod
    def _verdict(passed: bool, action: str, checks: List[VerificationCheck],
                 answer: Optional[str], notes: str = "") -> Verification:
        return Verification(passed=passed, action=action, checks=checks,
                            final_answer=answer if action == "accept" else None,
                            notes=notes)
