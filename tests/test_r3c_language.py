"""R3c 测试：命题归属消融开关 + 语言推理核（词典关系/方向/判别性/审计/LLM 提议绑定）。"""
from __future__ import annotations

import pytest

from assessment_agent.config import Settings
from assessment_agent.router import score_rules
from assessment_agent.schemas import Question, QuestionOption
from assessment_agent.skills.base import SkillContext
from assessment_agent.skills.language_reasoning import LanguageReasoningSkill
from assessment_agent.skills.verbal_understanding import VerbalUnderstandingSkill
from assessment_agent.tools.language_tool import (audit_relation, core_analogy,
                                                   core_word_relation, infer_relations)


def _question(stem, pairs):
    return Question(stem=stem,
                    options=[QuestionOption(label=l, text=t) for l, t in pairs],
                    question_type="single_choice")


def _settings(**kw):
    base = Settings.from_env(env={"ASSESSMENT_OFFLINE": "1"})
    return base.model_copy(update=kw)


# ------------------------------------------------------------------ 消融开关
def test_epistemic_flag_off_restores_r3b_behaviour():
    """r3c_epistemic=False：传闻证据下的裸断言回到 R3b 的重叠度判定（复现误放，供消融对照）。"""
    stem = "有报道称，新型电池的续航时间更长。根据这段文字，以下哪项可以推出？"
    opts = [("A", "新型电池的续航时间更长"), ("C", "新型电池不存在任何续航问题")]
    skill = VerbalUnderstandingSkill()
    ctx_on = SkillContext(settings=_settings(), llm=None)
    r_on = skill.solve(_question(stem, opts), ctx_on)
    ctx_off = SkillContext(settings=_settings(r3c_epistemic=False), llm=None)
    r_off = skill.solve(_question(stem, opts), ctx_off)
    # 开：裸断言 A 被归属规则阻断 → 无候选诚实 failed；关：A 按重叠度直接命中（R3b 误放复现）
    assert r_on.status == "failed" and (r_on.meta.get("failure") or {}).get("code") == "insufficient_overlap"
    assert r_off.status == "solved" and r_off.answer == "A"
    ver_off = skill.verify(_question(stem, opts), r_off, ctx_off)
    assert ver_off.action == "accept"  # 旧验证层同样放行——这正是 P0 要修的洞


def test_language_flag_off_declares_unsupported():
    skill = LanguageReasoningSkill()
    ctx = SkillContext(settings=_settings(r3c_language=False), llm=None)
    sol = skill.solve(_question("汽车∶交通工具 与下列关系最为相似的是",
                                [("A", "香蕉∶水果"), ("B", "医生∶医院")]), ctx)
    assert sol.status == "unsupported" and sol.answer is None


def test_language_honest_on_unrelated_stem():
    """已实现不等于全知：非类比题干必须 unsupported，绝不伪造答案（原 declared 纪律延续）。"""
    skill = LanguageReasoningSkill()
    ctx = SkillContext(settings=_settings(), llm=None)
    sol = skill.solve(_question("任意题干", [("A", "甲"), ("B", "乙")]), ctx)
    assert sol.status == "unsupported" and sol.answer is None


# ------------------------------------------------------------------ 关系核
def test_infer_relations_direction_explicit():
    tags = {t for t, _, _ in infer_relations("汽车", "交通工具")}
    assert tags == {"GENUS_UP"}
    assert {t for t, _, _ in infer_relations("交通工具", "汽车")} == {"GENUS_DOWN"}
    assert {t for t, _, _ in infer_relations("轮胎", "汽车")} == {"PART_OF"}
    assert {t for t, _, _ in infer_relations("汽车", "轮胎")} == {"HAS_PART"}


def test_analogy_unique_and_audit():
    stem = "汽车∶交通工具 与下列哪组词语逻辑关系最为相似？"
    opts = [("A", "轮胎∶汽车"), ("B", "香蕉∶水果"), ("C", "医生∶医院")]
    r = core_analogy(stem, opts)
    assert r["ok"] and r["plan"].proposed_answer == "B"
    assert r["plan"].relation_signature == ["GENUS_UP"]
    aud = audit_relation(stem, opts, "B", r["plan"].model_dump())
    assert aud["status"] == "confirmed"


def test_analogy_direction_swap_is_mismatch_not_match():
    """PART_OF 题干 vs HAS_PART 选项：粒度/方向偷换不得算满足。"""
    stem = "页码∶书本 关系最为相似的一组是"
    opts = [("A", "书本∶页码"), ("B", "键盘∶电脑"), ("C", "司机∶驾驶")]
    r = core_analogy(stem, opts)
    assert r["ok"] and r["plan"].proposed_answer == "B"
    a_opt = next(a for a in r["plan"].option_analyses if a.label == "A")
    assert a_opt.verdict == "partial"  # 只有方向相反标签，缺 PART_OF


def test_analogy_multiple_match_refuses():
    stem = "木头∶桌子 关系最为相似的是"
    opts = [("A", "面粉∶面包"), ("B", "钢材∶桥梁"), ("C", "医生∶医院")]
    r = core_analogy(stem, opts)
    assert not r["ok"] and r["failure"]["code"] == "ambiguous_relation"


def test_analogy_unknown_stem_pair_fails():
    stem = "收集∶整理 关系最为相似的是"
    r = core_analogy(stem, [("A", "苹果∶水果"), ("B", "医生∶医院")])
    assert not r["ok"] and r["failure"]["code"] == "no_lexicon_evidence"


def test_analogy_unregistered_competitor_degrades_not_blocks():
    """规则通道：竞争项未登录词典 → 不积极排除但不硬拦；不确定性必须显式声明。"""
    stem = "汽车∶交通工具 与下列关系最为相似的是"
    opts = [("A", "香蕉∶水果"), ("B", "医生∶医院"), ("C", "夸克∶轻子")]
    r = core_analogy(stem, opts)
    assert r["ok"] and r["plan"].proposed_answer == "A"
    assert any("未登录" in u for u in r["plan"].uncertainty_reasons)
    aud = audit_relation(stem, opts, "A", r["plan"].model_dump())
    assert aud["status"] == "confirmed" and aud["unresolved"] == ["C"]


def test_synonym_and_antonym_only_lexicon_evidence():
    r = core_word_relation("找出下列词语的近义词：吝啬",
                           [("A", "节俭"), ("B", "小气"), ("C", "大方"), ("D", "慷慨")], "syn")
    assert r["ok"] and r["plan"].proposed_answer == "B"
    r2 = core_word_relation("下列词语与“勤奋”意思相反的是",
                            [("A", "勤勉"), ("B", "懒惰"), ("C", "刻苦"), ("D", "努力")], "ant")
    assert r2["ok"] and r2["plan"].proposed_answer == "B"
    # 词典外目标词 → 如实不可判
    r3 = core_word_relation("找出下列词语的近义词：饕餮", [("A", "贪吃"), ("B", "饥饿")], "syn")
    assert not r3["ok"] and r3["failure"]["code"] == "target_not_in_lexicon"


def test_english_analogy_subset():
    stem = "Fork is to eating as knife is to"
    r = core_analogy(stem, [("A", "cooking"), ("B", "cutting"), ("C", "spoon")])
    assert r["ok"] and r["plan"].proposed_answer == "B"
    aud = audit_relation(stem, [("A", "cooking"), ("B", "cutting"), ("C", "spoon")], "B",
                         r["plan"].model_dump())
    assert aud["status"] == "confirmed"


# ------------------------------------------------------------------ Skill 端到端
def test_language_skill_accept_and_checks():
    skill = LanguageReasoningSkill()
    ctx = SkillContext(settings=_settings(), llm=None)
    q = _question("努力∶成功 与下列哪组词语逻辑关系最为相似？",
                  [("A", "懈怠∶失败"), ("B", "医生∶医院"), ("C", "键盘∶电脑")])
    sol = skill.solve(q, ctx)
    assert sol.status == "solved" and sol.answer == "A"
    ver = skill.verify(q, sol, ctx)
    assert ver.action == "accept", ver.model_dump()
    names = {c.name for c in ver.checks}
    assert {"language.relation_evidence", "language.direction_consistency",
            "language.granularity_swap", "language.competitors_excluded",
            "language.world_knowledge_source", "language.uniqueness"} <= names


def test_language_skill_rejects_tampered_evidence():
    """source_text 与词典对账失败（证据被篡改）→ reject，不得凭字母接受。"""
    skill = LanguageReasoningSkill()
    ctx = SkillContext(settings=_settings(), llm=None)
    q = _question("汽车∶交通工具 与下列关系最为相似的是",
                  [("A", "香蕉∶水果"), ("B", "医生∶医院")])
    sol = skill.solve(q, ctx)
    assert sol.status == "solved"
    ev = sol.meta["relation_plan"]["evidence"][0]
    ev["source_text"] = ev["source_text"] + "（被篡改）"
    ver = skill.verify(q, sol, ctx)
    assert ver.action == "reject"


def test_llm_proposal_requires_lexicon_agreement():
    """LLM 只有提议权：与词典重算不一致的提交必须被拒绝（同模型自评不算验证）。"""
    skill = LanguageReasoningSkill()
    ctx = SkillContext(settings=_settings(), llm=None)
    q = _question("汽车∶交通工具 与下列关系最为相似的是",
                  [("A", "香蕉∶水果"), ("B", "医生∶医院")])
    opts = [("A", "香蕉∶水果"), ("B", "医生∶医院")]
    # 一致提议：重算可复现 → 绑定成功（resolution=llm_audited）
    payload = {"task_type": "analogy", "stem_pairs": [["汽车", "交通工具"]],
               "option_analyses": [
                   {"label": "A", "pair": ["香蕉", "水果"], "relations": ["GENUS_UP"], "verdict": "match"},
                   {"label": "B", "pair": ["医生", "医院"], "relations": ["PLACE"], "verdict": "mismatch"}],
               "proposed_answer": "A", "confidence": 0.99}
    bound, rej = skill._bind_llm_plan(q, payload, opts)
    assert bound is not None and rej is None
    assert bound["plan"].resolution == "llm_audited"
    # 篡改关系：词典重算是 GENUS_UP，模型声称 PART_OF → 拒绝
    payload2 = {"task_type": "analogy", "stem_pairs": [["汽车", "交通工具"]],
                "option_analyses": [
                    {"label": "A", "pair": ["香蕉", "水果"], "relations": ["PART_OF"], "verdict": "match"},
                    {"label": "B", "pair": ["医生", "医院"], "relations": ["PLACE"], "verdict": "mismatch"}],
                "proposed_answer": "A", "confidence": 0.99}
    bound2, rej2 = skill._bind_llm_plan(q, payload2, opts)
    assert bound2 is None and rej2["code"] == "llm_relation_disagreement"
    # 词典外词对：模型说什么都不接受
    payload3 = {"task_type": "analogy", "stem_pairs": [["夸克", "轻子"]],
                "option_analyses": [
                    {"label": "A", "pair": ["香蕉", "水果"], "relations": ["GENUS_UP"], "verdict": "match"},
                    {"label": "B", "pair": ["医生", "医院"], "relations": ["PLACE"], "verdict": "mismatch"}],
                "proposed_answer": "A", "confidence": 0.9}
    bound3, rej3 = skill._bind_llm_plan(q, payload3, opts)
    assert bound3 is None and rej3["code"] == "stem_pair_not_in_lexicon"


def test_router_prefers_language_for_analogy():
    scores = score_rules(_question("汽车∶交通工具 与下列哪组词语逻辑关系最为相似？",
                                   [("A", "香蕉∶水果"), ("B", "医生∶医院")]))
    assert "language_reasoning" in scores
    assert max(scores, key=scores.get) == "language_reasoning"
    scores2 = score_rules(_question("找出下列词语的反义词：吝啬",
                                    [("A", "节俭"), ("B", "小气")]))
    assert scores2.get("language_reasoning", 0) >= scores2.get("verbal_understanding", 0)
