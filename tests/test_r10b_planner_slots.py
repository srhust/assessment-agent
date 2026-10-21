# -*- coding: utf-8 -*-
"""R10-B：结构化计划的类型化槽位与 REL 相对速度通道。

工单：givens 不再强迫所有信息都是数值；重点支持 相向/追及、先行时间、
相对方向等非数值关系。执行层把 REL 重写为纯算术（和/abs 差），复核通道
不引入不可验证的新语法。
"""
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assessment_agent.planner import (Given, PlanStep, StructuredPlan,
                                      execute_plan, rewrite_rel_expr,
                                      validate_plan)

STEM_MEET = "甲乙两人从相距 240 千米的两地同时相向出发，甲每小时 60 千米，乙每小时 40 千米，几小时后相遇？"


def _meet_plan() -> StructuredPlan:
    return StructuredPlan(
        family="travel-meeting", method_id="nc.travel-meeting-pursuit",
        givens=[
            Given("d", "240", "相距 240 千米", kind="quantity"),
            Given("vA", "60", "甲每小时 60 千米", kind="rate"),
            Given("vB", "40", "乙每小时 40 千米", kind="rate"),
            Given("rel", "", "同时相向出发", kind="categorical_relation", mode="相向"),
        ],
        steps=[PlanStep("t", "d / REL(vA, vB, rel)", "几小时后相遇", ["d", "vA", "vB", "rel"])],
        target="t")


def test_meeting_via_categorical_slot():
    p = _meet_plan()
    assert validate_plan(p, STEM_MEET) == []
    ex = execute_plan(p)
    assert ex.final == Fraction(240, 100) and ex.error is None
    # 展开式为纯算术（无 REL 残留，可被 fraction_eval 复核）
    assert "REL" not in (ex.expression or "")


def test_pursuit_with_lead_minutes():
    stem = ("乙车 8:00 出发每小时行 40 千米，甲车 8:15 出发每小时行 60 千米同向追及，"
            "甲几小时后追上乙？")
    p = StructuredPlan(
        family="travel-pursuit", method_id="nc.travel-meeting-pursuit",
        givens=[
            Given("v_slow", "40", "每小时行 40 千米", kind="rate"),
            Given("v_fast", "60", "每小时行 60 千米", kind="rate"),
            Given("lead_min", "15", "8:15 出发", kind="time"),
            Given("mode", "", "同向追及", kind="categorical_relation", mode="追及"),
        ],
        steps=[
            PlanStep("lead_h", "lead_min / 60", "8:15 出发", ["lead_min"]),
            PlanStep("head", "v_slow * lead_h", "每小时行 40 千米", ["v_slow", "lead_h"]),
            PlanStep("t", "head / REL(v_fast, v_slow, mode)", "同向追及",
                     ["head", "v_fast", "v_slow", "mode"]),
        ],
        target="t")
    assert validate_plan(p, stem) == []
    ex = execute_plan(p)
    assert ex.final == Fraction(10, 20)  # 先行 10 千米 / 差 20


def test_nonnumeric_value_without_kind_is_rejected():
    """xd-nc-05 原失效形态：中文词塞进数值 given——验证器点名拒绝并指导改用 kind。"""
    p = StructuredPlan(
        family="x", method_id="m",
        givens=[Given("mode", "相向", "相向出发")],
        steps=[PlanStep("t", "mode * 2", "相向出发", ["mode"])], target="t")
    probs = validate_plan(p, STEM_MEET)
    assert any("值非法" in x and "kind" in x for x in probs)


def test_unknown_direction_word_rejected():
    p = _meet_plan()
    p.givens[3].mode = "斜向"
    probs = validate_plan(p, STEM_MEET)
    assert any("方向词不在词表" in x for x in probs)


def test_direction_word_must_be_in_span():
    p = _meet_plan()
    p.givens[3].stem_span = "两地同时出发"  # 片段里没有"相向"
    probs = validate_plan(p, STEM_MEET)
    assert any("未出现在其来源片段" in x for x in probs)


def test_rel_literal_and_rewrite():
    assert rewrite_rel_expr("REL(vA, vB, 相向)", {}) == "((vA) + (vB))"
    assert rewrite_rel_expr("REL(vF, vS, 追及)", {}) == "abs((vF) - (vS))"
    bad = None
    try:
        rewrite_rel_expr("REL(vA, vB, 乱向)", {})
    except ValueError as exc:
        bad = str(exc)
    assert bad and "不在词表" in bad


def test_equal_speed_pursuit_div_zero_controlled():
    p = _meet_plan()
    p.givens[2].value = "60"           # vB=60=vA
    p.givens[3].mode = "同向"
    p.steps[0].expr = "d / REL(vA, vB, rel)"
    ex = execute_plan(p)
    assert ex.final is None and ex.error  # 除零被受控捕获，不误出值


def test_mixed_script_varname_not_falsely_unbound():
    """pv-travel-04 根因回归：中英混排变量名 v和 曾被 ASCII 截断误报未绑定。"""
    stem = "甲乙两人从相距 240 千米的两地同时相向出发，甲每小时 60 千米，乙每小时 40 千米，几小时后相遇？"
    p = StructuredPlan(
        family="travel-meeting", method_id="nc.travel-meeting-pursuit",
        givens=[Given("距离", "240", "相距 240 千米", kind="quantity"),
               Given("v1", "60", "甲每小时 60 千米", kind="rate"),
               Given("v2", "40", "乙每小时 40 千米", kind="rate")],
        steps=[PlanStep("v和", "REL(v1, v2, 相向)", "同时相向出发", ["v1", "v2"]),
               PlanStep("t小时", "距离 / v和", "几小时后相遇", ["距离", "v和"])],
        target="t小时")
    assert validate_plan(p, stem) == []
    assert execute_plan(p).final == Fraction(240, 100)


def test_dict_roundtrip_keeps_slots():
    p = _meet_plan()
    q = StructuredPlan.from_dict(p.to_dict())
    assert q.givens[3].kind == "categorical_relation" and q.givens[3].mode == "相向"
    ex = execute_plan(q)
    assert ex.final == Fraction(240, 100)
