"""R4a 阶段1：SYN/ANT 对称闭包、开放世界三态判定、词性歧义与 unverified 降级。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.config import Settings  # noqa: E402
from assessment_agent.schemas import Question, QuestionOption  # noqa: E402
from assessment_agent.skills.base import SkillContext  # noqa: E402
from assessment_agent.skills.language_reasoning import LanguageReasoningSkill  # noqa: E402
from assessment_agent.tools.language_tool import (audit_relation, core_analogy,  # noqa: E402
                                                   core_word_relation, infer_relations)


def _ctx():
    return SkillContext(settings=Settings.from_env(
        env={"ASSESSMENT_OFFLINE": "1"}), llm=None)


def _ask(stem, opts):
    return Question(stem=stem, options=[QuestionOption(label=l, text=t) for l, t in opts],
                    question_type="single_choice")


# ---------------------------------------------------------------- 对称闭包

def test_syn_ant_symmetric_closure_keeps_source():
    # 小气.ant=大方（单向填写）→ 大方∶小气 也必须可判 ANT（闭包），来源=小气词条
    rels = infer_relations("大方", "小气")
    tags = {t for t, _, _ in rels}
    assert "ANT" in tags
    src = [(f, s) for t, f, s in rels if t == "ANT"]
    assert src and src[0][0] == "ant@b"          # 来源字段标明取词自 b 侧词条
    assert src[0][1]                            # source_text=来源词条 def 非空
    # SYN 同理：勤勉.syn=[勤奋,…] → (勤奋,勤勉) 闭包可判
    assert any(t == "SYN" for t, _, _ in infer_relations("勤奋", "勤勉"))


def test_closure_antonym_question_uses_both_sides():
    # 目标"勤奋"的相反：A 勤勉(SYN) 排除，B 懒惰(勤奋.ant@a) 匹配，D 努力(SYN@b 闭包) 排除
    r = core_word_relation("下列词语与“勤奋”意思相反的是",
                           [("A", "勤勉"), ("B", "懒惰"), ("C", "刻苦"), ("D", "努力")], "ant")
    assert r["ok"] and r["plan"].proposed_answer == "B"
    assert not r["plan"].unresolved_competitors      # 全部可判定，开放世界无缺口
    verdicts = {a.label: a.verdict for a in r["plan"].option_analyses}
    assert verdicts == {"A": "mismatch", "B": "match", "C": "mismatch", "D": "mismatch"}


# ---------------------------------------------------------------- 三态与 unverified

def test_unknown_competitor_downgrades_to_unverified():
    """唯一已知匹配但存在不可排除竞争项 → 不对外作答（unverified）。"""
    stem = "汽车∶交通工具 与下列关系最为相似的是"
    opts = [("A", "苹果∶水果"), ("B", "医生∶医院"), ("C", "夸克∶轻子")]
    sk = LanguageReasoningSkill()
    sol = sk.solve(_ask(stem, opts), _ctx())
    assert sol.status == "solved" and sol.answer == "A"
    ver = sk.verify(_ask(stem, opts), sol, _ctx())
    assert ver.action == "unverified" and ver.final_answer is None
    assert any(c.name == "language.competitors_excluded" and not c.passed for c in ver.checks)


def test_fully_adjudicable_set_still_accepted():
    """全部选项可判定且唯一 match → 照常 accept（unverified 不是普遍拒答）。"""
    stem = "演员∶剧院 与下列关系最为相似的是"
    opts = [("A", "老师∶学校"), ("B", "厨师∶炒锅"), ("C", "医生∶听诊器"), ("D", "木头∶家具")]
    sk = LanguageReasoningSkill()
    sol = sk.solve(_ask(stem, opts), _ctx())
    ver = sk.verify(_ask(stem, opts), sol, _ctx())
    assert ver.action == "accept" and ver.final_answer == "A"


def test_contradicted_is_active_exclusion_not_unknown():
    """方向偷换=有证据的排除（contradicted），不得与 unknown 混同。"""
    r = core_analogy("页码∶书本 关系最为相似的一组是",
                     [("A", "书本∶页码"), ("B", "键盘∶电脑"), ("C", "医生∶医院")])
    a_opt = next(a for a in r["plan"].option_analyses if a.label == "A")
    assert a_opt.verdict == "contradicted" and "方向偷换" in a_opt.notes
    assert "A" not in r["plan"].unresolved_competitors


def test_multiple_matching_candidates_stay_ambiguous():
    r = core_analogy("木头∶桌子 关系最为相似的是",
                     [("A", "面粉∶面包"), ("B", "钢材∶桥梁"), ("C", "医生∶医院")])
    assert not r["ok"] and r["failure"]["code"] == "ambiguous_relation"


def test_duplicate_relation_signature_requires_coverage():
    """词对可同时携带 GENUS_UP+PRODUCES 等多关系时，签名是全集——只覆盖其一不得 match。"""
    rels = infer_relations("水果", "苹果") + infer_relations("苹果", "水果")
    tags = {t for t, _, _ in rels}
    assert "GENUS_UP" in tags or "GENUS_DOWN" in tags   # 种属可判
    # 构造双签名场景：题干词对若带 {A,B}，仅带 A 的选项必须 partial/contradicted/mismatch
    r = core_analogy("苹果∶水果 关系最为相似的一组是",
                     [("A", "西瓜∶水果"), ("B", "钢笔∶文具"), ("C", "轮胎∶汽车")])
    assert r["ok"] or r["failure"]["code"] == "ambiguous_relation"
    if r["ok"]:
        a_opts = {a.label: a.verdict for a in r["plan"].option_analyses}
        assert a_opts["C"] != "match"                     # PART_OF 不得混入 GENUS 签名
        assert sum(1 for v in a_opts.values() if v == "match") == 1


def test_missing_entry_side_yields_unknown_not_contradicted():
    # 任一侧词不在词典：词对返回空关系集（unknown），而不是"关系不成立"
    assert infer_relations("夸克", "轻子") == []
    r = core_analogy("演员∶剧院 与下列关系最为相似的一组是",
                     [("A", "老师∶学校"), ("B", "夸克∶轻子")])
    assert r["ok"] and r["plan"].proposed_answer == "A"
    assert r["plan"].unresolved_competitors == ["B"]


def test_polysemy_disambiguation_by_pair():
    """“大方”多义（慷慨/专家方某）：以词对可判定关系为准，不被同形词误导。"""
    # 吝啬∶大方 ANT；干扰 慷慨∶大方 是 SYN（词对不同）→ 不得算 match
    r = core_analogy("吝啬∶大方 关系最为相似的一组是",
                     [("A", "慷慨∶大方"), ("B", "勤奋∶懒惰")])
    if r["ok"]:
        verdicts = {a.label: a.verdict for a in r["plan"].option_analyses}
        assert verdicts.get("A") != "match"
