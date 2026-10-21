# -*- coding: utf-8 -*-
"""R9-C：word_problem 新增确定性模板五类样本验收（浓度/行程/复合百分/恢复/工程两步/比例尺/单位数量）。"""
from __future__ import annotations

from fractions import Fraction

import pytest

from assessment_agent.tools.math_tool import match_option
from assessment_agent.tools.word_problem import evaluate_plan, extract_word_problem


def _solve(stem, options):
    plan = extract_word_problem(stem)
    assert plan is not None, f"未抽取: {stem}"
    env, err = evaluate_plan(plan)
    assert err is None, err
    final = env[plan["final_var"]]
    lab = match_option(float(final), options, rel_tol=2e-2 if "约" in stem else 1e-6)
    return plan["kind"], final, lab


# ---------------------------------------------------------------- 类1 正常
def test_compound_multi_discount():
    kind, f, lab = _solve("原价 100 元，先降价 10%，又降价 5%，最终多少元？",
                          [("A", "85.5"), ("B", "85"), ("C", "90.5")])
    assert kind == "compound_pct" and f == Fraction("85.5") and lab == "A"


def test_markup_then_discount():
    kind, f, lab = _solve("某商品按成本 200 元加价 25% 定价，之后按定价打九折出售，实际售价多少元？",
                          [("A", "220"), ("B", "225"), ("C", "198")])
    assert kind == "compound_pct" and f == Fraction(225) and lab == "B"


def test_concentration_add_water():
    kind, f, lab = _solve("300 克浓度 12% 的盐水中加入 60 克水，浓度变为多少？",
                          [("A", "10%"), ("B", "9%"), ("C", "12%")])
    assert kind == "concentration" and f == Fraction(1, 10) and lab == "A"


def test_concentration_add_salt():
    kind, f, lab = _solve("200 克浓度 15% 的盐水中加入 10 克盐，浓度约为多少？",
                          [("A", "17.1%"), ("B", "19.0%"), ("C", "20%")])
    assert kind == "concentration" and f == Fraction(40, 210) and lab == "B"


def test_travel_meeting():
    kind, f, lab = _solve("A、B 两地相距 240 千米，甲 60 千米/小时、乙 40 千米/小时同时相向出发，几小时后相遇？",
                          [("A", "2"), ("B", "2.4"), ("C", "3")])
    assert kind == "travel_meeting" and f == Fraction("2.4") and lab == "B"


def test_travel_pursuit_lead():
    kind, f, lab = _solve("弟弟步行 40 米/分先出发 10 分钟，哥哥骑车 120 米/分沿同路追及，多少分钟追上？",
                          [("A", "5"), ("B", "6"), ("C", "8")])
    assert kind == "travel_pursuit_lead" and f == Fraction(5) and lab == "A"


def test_net_compound_growth():
    kind, f, lab = _solve("2022 年比 2021 年增长 15%，2023 年比 2022 年增长 10%。2023 年比 2021 年增长约多少？",
                          [("A", "26.5%"), ("B", "24%"), ("C", "25%")])
    assert kind == "net_compound" and f == Fraction(265, 1000) and lab == "A"


def test_pct_restore():
    kind, f, lab = _solve("某商品现价比原价降低了 20%。要恢复原价，需要提价多少？",
                          [("A", "25%"), ("B", "20%"), ("C", "30%")])
    assert kind == "pct_restore" and f == Fraction(1, 4) and lab == "A"


def test_ratio_middle():
    kind, f, lab = _solve("把 360 本图书按 2∶3∶4 分给三个班级，中间那个班分得多少本？",
                          [("A", "120"), ("B", "160"), ("C", "80")])
    assert kind == "ratio_share" and f == Fraction(120) and lab == "A"


def test_map_scale():
    kind, f, lab = _solve("在 1∶5000000 的地图上，两城图上距离 6 厘米，实际距离是多少千米？",
                          [("A", "120"), ("B", "300"), ("C", "500")])
    assert kind == "map_scale" and f == Fraction(300) and lab == "B"


def test_plan_actual_two_step():
    kind, f, lab = _solve("车间原计划 8 天生产 960 个零件，实际每天比原计划多生产 20 个，实际每天生产多少个？",
                          [("A", "130"), ("B", "140"), ("C", "160")])
    assert kind == "plan_actual_two_step" and f == Fraction(140) and lab == "B"


def test_unit_count():
    kind, f, lab = _solve("每箱零件 25 个，14 箱共有多少个零件？",
                          [("A", "345"), ("B", "350"), ("C", "360")])
    assert kind == "unit_count" and f == Fraction(350) and lab == "B"


def test_work_together():
    kind, f, lab = _solve("一项工程，甲单独做 10 天完成，乙单独做 15 天完成，两队合作多少天完成？",
                          [("A", "6"), ("B", "12.5"), ("C", "5")])
    assert kind == "work_together" and f == Fraction(6) and lab == "A"


# ---------------------------------------------------------------- 类2 反向边界
def test_evaporation_boundary():
    # 蒸发水：溶质不变、总量减少
    kind, f, lab = _solve("100 克浓度 20% 的糖水蒸发掉 20 克水，浓度变为多少？",
                          [("A", "25%"), ("B", "20%"), ("C", "16%")])
    assert kind == "concentration" and f == Fraction(1, 4) and lab == "A"


def test_pursuit_equal_speed_abstains():
    # 速度相等无法追及 → 不抽取（回退到其它/失败），不得除零
    plan = extract_word_problem("甲 60 千米/小时，乙 60 千米/小时同向出发，相距 100 千米，多久追上？")
    assert plan is None or plan["kind"] not in ("travel_pursuit", "travel_pursuit_lead")


# ---------------------------------------------------------------- 类3 典型错误对照
def test_compound_not_additive():
    # 典型错答是 100×(1-15%)=85；确定性复合必须给 85.5，绝不落到 85
    _kind, f, lab = _solve("原价 100 元，先降价 10%，又降价 5%，最终多少元？",
                           [("A", "85.5"), ("B", "85"), ("C", "90.5"), ("D", "94.5")])
    assert f != 85 and lab == "A"


def test_net_growth_not_additive():
    # 15%+10%=25% 是典型错答，正确净增 26.5%
    _k, f, lab = _solve("前年比去年增长 15%，去年比前一年增长 10%，两年净增长约多少？",
                        [("A", "26.5%"), ("C", "25%")])
    assert f != Fraction(1, 4)


# ---------------------------------------------------------------- 类4 不足条件→None（绝不猜）
@pytest.mark.parametrize("stem", [
    "某商品打八折后多少元？",                      # 无原价
    "甲乙两人相向而行，多久相遇？",                 # 无距离无速度
    "浓度约为多少？",                               # 无质量无浓度
    "把一批书按若干比例分给班级，中间班分多少？",    # 无具体数字
])
def test_insufficient_conditions_none(stem):
    assert extract_word_problem(stem) is None


# ---------------------------------------------------------------- 类5 独立复核
def test_plans_independent_recompute():
    """每个计划都可用 Fraction 独立重算，evaluate_plan 结果与手算一致。"""
    cases = {
        "原价 100 元，先降价 10%，又降价 5%，最终多少元？": Fraction("85.5"),
        "300 克浓度 12% 的盐水中加入 60 克水，浓度变为多少？": Fraction(1, 10),
        "A、B 两地相距 240 千米，甲 60 千米/小时、乙 40 千米/小时同时相向出发，几小时后相遇？": Fraction("2.4"),
    }
    for stem, want in cases.items():
        plan = extract_word_problem(stem)
        env, err = evaluate_plan(plan)
        assert err is None and env[plan["final_var"]] == want, (stem, env)
