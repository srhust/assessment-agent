"""ProblemValidityVerifier 单测：四层各判且互不越权。"""
from fractions import Fraction as F

from assessment_agent.validity import DeclaredConditions, ProblemValidityVerifier

V = ProblemValidityVerifier()


def _da_cond(facts, rates, ask, delta=False):
    return DeclaredConditions(kind="data_analysis",
                              facts={k: F(v) for k, v in facts.items()},
                              rates={k: F(v) for k, v in rates.items()},
                              ask_year=ask, ask_delta=delta)


def test_da_underdetermined_rb09_shape():
    # rb-09 原型：2023 值+2023 率，问 2024 → 充分性失败，不得标 has_unique_answer
    cond = _da_cond({"2023": 1100}, {"2023": "1/5"}, "2024")
    rep = V.verify_item(gold_expr="1100*(1+1/5)", gold=F(1320),
                        options=[("A", "1320"), ("B", "1430")], cond=cond)
    assert rep.verdict == "invalid" and rep.marker == "underdetermined"
    assert rep.layers["sufficiency"][0] is False


def test_da_valid_rb11_shape():
    # rb-11 合法形：现期值与率同年 → 增量可导
    cond = _da_cond({"2024": 800}, {"2024": "1/4"}, "2024", delta=True)
    rep = V.verify_item(gold_expr="800-800/(1+1/4)", gold=F(160),
                        options=[("A", "640"), ("D", "160")], cond=cond)
    assert rep.verdict == "valid" and rep.layers["sufficiency"][0] is True


def test_da_forward_next_year_needs_next_year_rate():
    cond = _da_cond({"2023": 1000}, {"2023": "1/10", "2024": "1/8"}, "2024")
    rep = V.verify_item(gold_expr="1000*(1+1/8)", gold=F(1125),
                        options=[("A", "1125"), ("B", "1100")], cond=cond)
    assert rep.verdict == "valid"


def test_option_rounding_undeclared_flawed():
    # rb-05 原型：金标 1/3，选项只有 33.3%，题面无"约" → option_unique 失败
    cond = DeclaredConditions(kind="numeric", numbers=["25", "200"])
    rep = V.verify_item(gold_expr="(1/4)/(1-1/4)", gold=F(1, 3),
                        options=[("A", "12.5%"), ("D", "33.3%")], cond=cond)
    assert rep.verdict == "flawed" and rep.marker == "rounding_undeclared"
    # 显式声明近似后应通过
    rep2 = V.verify_item(gold_expr="(1/4)/(1-1/4)", gold=F(1, 3),
                         options=[("A", "12.5%"), ("D", "33.3%")], cond=cond,
                         approx_declared=True)
    assert rep2.verdict == "valid"


def test_ambiguous_options_in_tolerance():
    cond = DeclaredConditions(kind="numeric", numbers=["1/3"])
    rep = V.verify_item(gold_expr="1/3", gold=F(1, 3),
                        options=[("A", "33.3%"), ("B", "33.4%")], cond=cond,
                        approx_declared=True)
    assert rep.marker == "ambiguous_options"


def test_dependency_rejects_undeclared_number():
    # 表达式引用未声明的 1.3（材料率是 1/5→1.2）→ 依赖层失败
    cond = _da_cond({"2023": 1100}, {"2023": "1/5"}, "2024")
    rep = V.verify_item(gold_expr="1100*1.3", gold=F(1430),
                        options=[("A", "1430")], cond=cond)
    assert rep.layers["dependency"][0] is False and rep.verdict == "invalid"


def test_logic_rule_option_not_entailed_unsound_item():
    # 选项 A 写成规则"A1⇒A2"与前提规则同为全局方向 → 不必然；
    # 选项 C 重述前提规则 → 必然 → 两个 must → 题目不唯一
    cond = DeclaredConditions(kind="propositional",
                              premises=["rule:A2=>A1", "fact:A2"],
                              options_logic=[("A", "rule:A1=>A2"), ("B", "fact:A1"),
                                             ("C", "rule:A2=>A1"), ("D", "fact:!A1")])
    rep = V.verify_item(gold_expr="0", gold=F(0), options=[("B", "x")], cond=cond)
    assert rep.verdict == "invalid"


def test_logic_unique_under_rule_instance_semantics():
    # rb-13 原型："…都会…"是规则选项，不由实例事实推出；唯一必然=小李 A1
    cond = DeclaredConditions(kind="propositional",
                              premises=["rule:A2=>A1", "fact:A2"],
                              options_logic=[("A", "rule:A1=>A2"), ("B", "fact:A1"),
                                             ("C", "rule:A1=>A2"), ("D", "fact:!A1")])
    rep = V.verify_item(gold_expr="0", gold=F(0), options=[("B", "x")], cond=cond)
    assert rep.verdict == "valid", rep.layers


def test_da_gold_contradicts_direct_value():
    # 目标年材料直接给 1100，金标却算出 1584 → 充分性层矛盾拦截
    cond = _da_cond({"2023": 1100}, {"2023": "1/5"}, "2023")
    cond.gold_value = F(1584)
    rep = V.verify_item(gold_expr="1100*(1+1/5)*(1+1/5)", gold=F(1584),
                        options=[("A", "1584"), ("B", "1100")], cond=cond)
    assert rep.layers["sufficiency"][0] is False and "矛盾" in rep.layers["sufficiency"][1]


def test_arithmetic_mismatch():
    cond = DeclaredConditions(kind="numeric", numbers=["2"])
    rep = V.verify_item(gold_expr="2+2", gold=F(5), options=[("A", "5")], cond=cond)
    assert rep.layers["arithmetic"][0] is False and rep.marker == "gold_mismatch"


# ---------------- R9-A 独立检查①② ----------------

def test_material_share_conflict_rejected():
    # v2-39 原型：总额 8000×占比25%=2000 与显式分项 1500 矛盾 → 材料互斥不唯一
    cond = DeclaredConditions(
        kind="fact_match", semantic_gold=True,
        facts={"贸易额": F(8000), "服务额": F(1500)},
        material_formulas=[("total", "贸易额", 8000),
                           ("pct_of", "服务额", "贸易额", 1500, 25, 75)])
    ok, why = V.check_sufficiency(cond)
    assert ok is False and "矛盾" in why and "2000" in why


def test_material_consistent_passes():
    cond = DeclaredConditions(
        kind="numeric", numbers=["8000"],
        facts={"a": F(8000), "b": F(2000)},
        material_formulas=[("total", "a", 8000), ("pct_of", "b", "a", 2000, 25, 75),
                           ("growth", "b", "a", 20, 9600, 1)])
    ok, _ = V.check_sufficiency(cond)
    assert ok is True


def test_proof_option_alignment_catches_letter_drift():
    from assessment_agent.validity import check_proof_option_alignment
    opts = [("A", "增长 1584 亿元"), ("B", "增长 1100 亿元"), ("C", "1320"), ("D", "1100")]
    ok, why = check_proof_option_alignment("A：1100 为基期值", opts, "A")
    assert ok is False and "1100" in why and "B" in why
    ok2, _ = check_proof_option_alignment("A：1584 正确", opts, "A")
    assert ok2 is True


def test_proof_alignment_combinatorial_notation_not_flagged():
    # "C(5,2)=10" 类记号中的字母 C 不得被当作选项引用
    from assessment_agent.validity import check_proof_option_alignment
    opts = [("A", "10"), ("B", "20"), ("C", "30"), ("D", "40")]
    ok, _ = check_proof_option_alignment("C(5,2)=10 种，答案 B 20 错", opts, "A")
    assert ok is True
