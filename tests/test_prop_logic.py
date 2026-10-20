"""R7-C3 prop_logic：公式语言、中文方向规则、Z3 判定、LLM payload 绑定与验证重放。"""

import copy

import pytest

from assessment_agent.schemas import Question, QuestionOption
from assessment_agent.skills.base import SkillContext
from assessment_agent.skills.logical_reasoning import LogicalReasoningSkill
from assessment_agent.tools import default_toolbox
from assessment_agent.tools.prop_logic import (
    FormulaError, entail, parse_chinese_sentence, parse_formula,
    parse_statements, possible, validate_payload)

pytest.importorskip("z3")


def _q(stem, options):
    return Question(stem=stem, options=[QuestionOption(label=l, text=t)
                                        for l, t in options])


def _ctx():
    from assessment_agent.config import Settings
    return SkillContext(settings=Settings.from_env(), llm=None, knowledge=None,
                        toolbox=default_toolbox())


# ------------------------------------------------------------------ 公式语言

def test_formula_parse_and_precedence():
    f = parse_formula("!P & Q => R")
    assert f.kind == "imp"
    g = parse_formula("P => Q => R")
    assert g.kind == "imp" and g.children[1].kind == "imp"  # 右结合
    assert parse_formula("P <=> Q").kind == "iff"
    with pytest.raises(FormulaError):
        parse_formula("P &")
    with pytest.raises(FormulaError):
        parse_formula("蕴含 P")


def test_entail_contradirection():
    p = [parse_formula("P => Q")]
    assert entail(p, parse_formula("!Q => !P")) is True     # 逆否成立
    assert entail(p, parse_formula("Q => P")) is False      # 肯后无效
    assert possible(p, parse_formula("Q & !P")) is True     # 肯前否后可能
    # 前提自相矛盾 → 如实 None（不可判定），不得借爆炸原理输出答案
    assert entail([parse_formula("P"), parse_formula("!P")], parse_formula("Q")) is None


# ------------------------------------------------------------------ 中文规则

def test_only_if_direction():
    atoms = {}
    f = parse_chinese_sentence("只有提前预约才能进馆", atoms)
    assert repr(f) == "(A2 => A1)"  # Q→P：进馆→预约
    assert atoms["A1"].startswith("提前预约")


def test_all_are_general_pattern():
    prems, atoms = parse_statements("所有通过笔试的员工都参加了培训。张明没有参加培训。")
    assert any(p.kind == "imp" for p in prems)
    assert "!A2" in [repr(x) for x in prems]  # 否定事实归并


def test_meta_option_not_decided():
    assert parse_chinese_sentence("无法确定张明是否通过笔试", {}) is None


# ------------------------------------------------------------------ payload 绑定

def test_validate_payload_verbatim_discipline():
    stem = "如果明天下雨，比赛就取消。比赛没有取消。"
    good = {"premises": [
                {"text": "如果明天下雨，比赛就取消", "formula": "P=>Q"},
                {"text": "比赛没有取消", "formula": "!Q"}],
            "options": [{"label": "A", "formula": "!P"},
                        {"label": "B", "formula": "P"}],
            "question": "must"}
    bound, err = validate_payload(good, stem)
    assert bound is not None and err is None
    bad = copy.deepcopy(good)
    bad["premises"][0]["text"] = "假设明天下雨，比赛就取消"  # 改词 → 非逐字
    assert validate_payload(bad, stem)[0] is None
    bad2 = copy.deepcopy(good)
    bad2["question"] = "guess"
    assert validate_payload(bad2, stem)[0] is None


# ------------------------------------------------------------------ skill 端到端

def test_skill_translation_rule_channel_accepts():
    sk = LogicalReasoningSkill()
    q = _q("只有提前预约的人才能进入展馆。李华进入了展馆，那么以下哪项必然为真？",
           [("A", "李华提前预约了"), ("B", "李华没有提前预约"),
            ("C", "提前预约的人都能进馆"), ("D", "进馆不一定需要预约")])
    sol = sk.solve(q, _ctx())
    assert sol.status == "solved" and sol.answer == "A"
    assert sol.meta["solver_source"] == "prop_rules"
    ver = sk.verify(q, sol, _ctx())
    assert ver.action == "accept" and ver.final_answer == "A"


def test_skill_vacuous_implication_not_answer():
    sk = LogicalReasoningSkill()
    q = _q("所有通过笔试的员工都参加了培训。张明没有参加培训。由此可以推出：",
           [("A", "张明通过了笔试"), ("B", "张明没有通过笔试"),
            ("C", "无法确定张明是否通过笔试"), ("D", "参加培训的都是通过笔试的")])
    sol = sk.solve(q, _ctx())
    assert sol.status == "solved" and sol.answer == "B"
    # D 是空真蕴含，不计入命中
    assert {d["label"]: d["verdict"] for d in sol.meta["decision"]}["D"] == "None"


def test_skill_rejects_fabricated_source_on_verify():
    sk = LogicalReasoningSkill()
    q = _q("所有通过笔试的员工都参加了培训。张明没有参加培训。由此可以推出：",
           [("A", "张明通过了笔试"), ("B", "张明没有通过笔试"),
            ("C", "无法确定张明是否通过笔试"), ("D", "参加培训的都是通过笔试的")])
    sol = sk.solve(q, _ctx())
    tampered = copy.deepcopy(sol)
    tampered.meta = dict(sol.meta)
    prem = [copy.deepcopy(p) for p in sol.meta["premises"]]
    prem[0]["text"] = "所有通过了面试的员工都参加了培训"  # 编造来源
    tampered.meta["premises"] = prem
    ver = sk.verify(q, tampered, _ctx())
    assert ver.action == "reject" and ver.final_answer is None
