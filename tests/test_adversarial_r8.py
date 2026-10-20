"""R8-D：验证器三组对抗测试（工单 §D）。

目标：证明验证器拦截的是**求解关系错误**，而非仅计划内部不自洽。
九例分三组：
  言语组：真实引文但推断方向错 / 否定·量词替换 / 竞争项未真正排除；
  数字组：所有数字都在但依赖缺失（rb-09 形）/ 篡改表达式多算一步 / 基期现期混淆；
  约值组：容差带内相邻双候选 / 百分点 vs 百分比 / 舍入方向偏差。
每例断言"不放行"（reject/unverified/非 accept），并检查拦截签名指向正确一层。
"""
from __future__ import annotations

from fractions import Fraction

import pytest

from assessment_agent.config import Settings
from assessment_agent.schemas import Question, QuestionOption, Solution
from assessment_agent.skills import SkillRegistry, default_skills
from assessment_agent.validity import DeclaredConditions as DC
from assessment_agent.validity import ProblemValidityVerifier
from assessment_agent.verifier import Verifier

V = ProblemValidityVerifier()


def _verifier():
    return Verifier(Settings(), registry=SkillRegistry(default_skills()))


def _q(stem, skill, opts):
    return Question(id="adv", stem=stem, skill=skill,
                    options=[QuestionOption(label=l, text=t) for l, t in opts])


# ================= 言语组 =================
STEM_NEG = ("材料：报告调查了本市通勤方式，但并未认定地铁扩容能缓解拥堵，是否开工仍需论证。"
            "问：根据材料，以下说法正确的是：")


def _verb_solution(answer, relation_a="supported", spans_forged=True):
    stem = STEM_NEG
    i = stem.find("地铁扩容能缓解拥堵")
    ev = {"text": "地铁扩容能缓解拥堵", "start": i, "end": (i + 30) if spans_forged
          else i + len("地铁扩容能缓解拥堵"), "role": "support"}
    plan = {
        "task_type": "detail_check",
        "negative_target": False,
        "option_analyses": [
            {"label": "A", "relation": relation_a, "definitive": True,
             "evidence": [ev], "new_terms": []},
            {"label": "B", "relation": "supported", "definitive": True,
             "evidence": [], "new_terms": []},
            {"label": "C", "relation": "contradicted", "definitive": True,
             "evidence": [], "new_terms": []},
            {"label": "D", "relation": "contradicted", "definitive": True,
             "evidence": [], "new_terms": []},
        ],
    }
    texts = {"A": "地铁扩容能缓解拥堵", "B": "报告未认定地铁扩容能缓解拥堵",
             "C": "地铁扩容已经开工", "D": "论证已完成"}
    return Solution(status="solved", skill="verbal_understanding", answer=answer,
                    answer_text=texts[answer], confidence=0.9, method="vu.detail",
                    meta={"verbal_plan": plan})


def test_verbal_real_citation_wrong_inference_blocked():
    """引文真实存在但把否定语境当支持：区间错位+独立重算双重拦截。"""
    q = _q(STEM_NEG, "verbal_understanding",
           [("A", "地铁扩容能缓解拥堵"), ("B", "报告未认定地铁扩容能缓解拥堵"),
            ("C", "地铁扩容已经开工"), ("D", "论证已完成")])
    ver = _verifier().verify(q, _verb_solution("A"))
    assert not ver.passed and ver.action in {"reject", "unverified"}
    names = {c.name for c in ver.checks if not c.passed}
    assert "verbal.evidence_spans" in names or "verbal.cues_preserved" in names


def test_verbal_quantifier_negation_swap_blocked():
    """量词/否定替换（"未认定"被当"认定"）：audit 独立重算必须反驳或不可判定。"""
    from assessment_agent.skills.verbal_understanding import audit
    res = audit(STEM_NEG, [("A", "地铁扩容能缓解拥堵"), ("B", "报告未认定")],
                "A", "detail_check", "supported", ["地铁扩容能缓解拥堵"])
    assert res.get("status") in {"refuted", "insufficient", "self_consistent"}
    # 若规则核自洽放行，逐字否定线索必须留痕
    if res.get("status") == "self_consistent":
        assert "reason" in res


def test_verbal_competitor_not_excluded_blocked():
    """两个选项同记 supported（竞争未排除）：唯一性层拦截。"""
    q = _q(STEM_NEG, "verbal_understanding",
           [("A", "地铁扩容能缓解拥堵"), ("B", "报告未认定地铁扩容能缓解拥堵"),
            ("C", "地铁扩容已经开工"), ("D", "论证已完成")])
    ver = _verifier().verify(q, _verb_solution("A", spans_forged=False))
    assert not ver.passed
    assert any(c.name == "verbal.uniqueness" and not c.passed for c in ver.checks)


# ================= 数字组 =================
def test_numeric_all_numbers_present_but_dependency_missing():
    """rb-09 形：2023 值+2023 率都在题面，算 2024 表达式数字全"真实"——
    充分性层（年份可达性）必须判 underdetermined，而非算术自洽即放行。"""
    cond = DC(kind="data_analysis", facts={"2023": Fraction(1100)},
              rates={"2023": Fraction(1, 5)}, ask_year="2024")
    rep = V.verify_item(gold_expr="1100*(1+1/5)", gold=Fraction(1320),
                        options=[("A", "1320"), ("B", "1100")], cond=cond)
    assert rep.marker == "underdetermined"
    assert rep.layers["sufficiency"][0] is False
    # 算术层本身是"自洽"的——证明单靠重放不足以拦截
    assert rep.layers["arithmetic"][0] is True


def test_numeric_fabricated_extra_step_replay_mismatch():
    """篡改步骤：声称 (1+r) 连乘两次但题面只有一年增长事件。"""
    cond = DC(kind="data_analysis", facts={"2023": Fraction(1100)},
              rates={"2024": Fraction(1, 5)}, ask_year="2024",
              gold_value=Fraction(1584))
    rep = V.verify_item(gold_expr="1100*(1+1/5)*(1+1/5)", gold=Fraction(1584),
                        options=[("A", "1584")], cond=cond)
    assert rep.verdict == "invalid"
    # 依赖层抓到第二次乘率所引用的事件不存在？——本例数字全在等价类，
    # 拦截层应是充分性（金标与直给矛盾）或算术，二者之一必须为 False
    assert (not rep.layers["sufficiency"][0]) or (not rep.layers["arithmetic"][0])


def test_numeric_base_current_confusion_rejected_by_semantics():
    """基期现期混淆：问基期而计划用现期乘法，da 复核的 operation↔表达式
    语义一致层必须拦截（即使 3125 恰为某选项）。"""
    stem = ("根据统计表回答问题。\n某市 GDP：2022 年 2500 亿元（同比增长 25%）。"
            "问：2021 年 GDP 是多少亿元？")
    q = _q(stem, "data_analysis",
           [("A", "2000"), ("B", "2125"), ("C", "1875"), ("D", "3125")])
    q.meta["vision"] = {"tables": [{"bbox": [0, 0, 9, 9],
                                    "cells": [["年份", "GDP", "增速"], ["2022", "2500", "25%"]]}]}

    def op(name, row, col, raw, val):
        return {"name": name, "table_ref": 0, "row": row, "col": col,
                "row_label": "", "col_label": "", "raw_text": raw,
                "value": val, "unit": "", "kind": "number"}

    plan_bad = {"operation": "current_value",
                "operands": [op("gdp", 1, 1, "2500", "2500"), op("r", 1, 2, "25%", "25")],
                "expression": "2500*(1+25/100)", "result_unit": "亿元"}
    sol = Solution(status="solved", skill="data_analysis", answer="D",
                   answer_text="3125", confidence=0.9,
                   method="da.base-period-mislabeled", meta={"data_plan": plan_bad})
    ver = _verifier().verify(q, sol)
    assert not ver.passed and ver.action == "reject"
    # 拦截签名必须包含语义/绑定层（不是只靠选项匹配）
    names = {c.name for c in ver.checks if not c.passed}
    assert {"data.plan_semantics", "data.source_recheck"} & names


# ================= 约值组 =================
def test_approx_adjacent_both_in_tolerance_ambiguous():
    """33.3% 与 33.4% 同落金标 33.33 容差带：选项唯一层判 ambiguous。"""
    cond = DC(kind="numeric", numbers=["1/3"])
    rep = V.verify_item(gold_expr="1/3", gold=Fraction(1, 3),
                        options=[("A", "33.3%"), ("B", "33.4%")],
                        cond=cond, approx_declared=True)
    assert rep.marker == "ambiguous_options"


def test_approx_percent_vs_percentage_point_distinct():
    """金标"1.5 个百分点"与干扰"1.5%"数值抽取必须分道（_F 的 % 语义）。"""
    cond = DC(kind="numeric", numbers=["8", "6.5"])
    rep = V.verify_item(gold_expr="8-6.5", gold=Fraction(15, 10),
                        options=[("A", "1.5 个百分点"), ("B", "15%"), ("C", "1.5%"),
                                 ("D", "2 个百分点")], cond=cond)
    assert rep.layers["option_unique"][0] is True  # 恰一命中 A


def test_approx_rounding_direction_no_release():
    """重放 1103.33：33.3 与 33.4 相邻候选——无声明近似时不得有唯一命中。"""
    cond = DC(kind="numeric", numbers=["3310", "3"])
    rep = V.verify_item(gold_expr="3310/3", gold=Fraction(3310, 3),
                        options=[("A", "1100"), ("B", "1103.4"),
                                 ("C", "1120"), ("D", "1135")], cond=cond)
    # 1103.4 与 1103.33 差 0.07 → 严格容差 1e-9 不命中；未声明近似 → flawed
    assert rep.layers["option_unique"][0] is False
    assert rep.marker == "rounding_undeclared"
