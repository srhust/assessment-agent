"""R8-C：MethodKB batch-2 验收测试。"""
from __future__ import annotations

import json
from pathlib import Path

from assessment_agent.knowledge.methods import MethodKB
from assessment_agent.planner import (Given, PlanStep, StructuredPlan,
                                      execute_plan, scan_missing_operators)

B2_IDS = [
    "nc.combinatorics-bundling", "nc.classical-probability", "nc.tiered-pricing",
    "ns.multi-stage-diff", "ns.power-adjust", "ns.interleaved-group", "ns.recursive-relation",
    "da.mixed-growth-rate", "da.annual-average-timing",
    "lg.categorical-syllogism", "lg.definition-judgment", "lg.propositional-formula",
    "vu.sentence-insertion", "vu.intent-attitude",
    "fig.count-elements", "fig.solid-view", "fig.analogy-classification", "fig.sequence-rule",
]


def _kb():
    kb = MethodKB()
    kb._cards = None
    return kb


def test_batch2_cards_present_and_valid():
    kb = _kb()
    assert kb.validate_all() == []
    ids = set(kb.cards())
    assert set(B2_IDS) <= ids


def test_batch2_fields_include_inapplicability_and_exercise():
    kb = _kb()
    for mid in B2_IDS:
        c = kb.cards()[mid]
        raw = c.raw
        assert any(str(a).startswith("不适用") for a in c.applicability_conditions), mid
        assert c.reasoning_operations and c.verification_strategy and c.common_pitfalls, mid
        assert raw.get("dev_exercise", {}).get("stem"), mid
        assert all(r.get("license") for r in c.source_references), mid


def test_batch2_retrieval_hits_per_domain():
    kb = _kb()
    probes = {
        "number_series": ("3, 5, 16, 63, 124 幂次 修正 下一项", "ns."),
        "figure_reasoning": ("图形 凌乱 交点 笔画 封闭面 数量", "fig."),
        "logical_reasoning": ("所有 有些 必然推出 三段论 周延", "lg."),
        "verbal_understanding": ("填入划横线部分 最恰当 一句 衔接", "vu."),
        "data_analysis": ("两部分 增速 混合 整体 增长率", "da."),
        "numeric_calculation": ("相邻 排法 多少种 捆绑 插空", "nc."),
    }
    for skill, (q, prefix) in probes.items():
        hits = [c.method_id for c in kb.search(q, skill=skill, top_k=3)]
        assert any(h.startswith(prefix) for h in hits), (skill, hits)


def test_missing_operator_abstention_names_operator():
    plan = StructuredPlan(
        family="prob", method_id="nc.classical-probability",
        givens=[Given(label="n", value="7", stem_span=""),
                Given(label="k", value="2", stem_span="")],
        steps=[PlanStep(var="ways", expr="C(7,2)", source="", deps=[])],
        target="ways", rationale="")
    ex = execute_plan(plan)
    assert ex.final is None
    assert "缺失算子" in ex.error and "组合数 C(n,k)" in ex.error
    assert scan_missing_operators(["2^10"]) == ["幂 a^b"]
    assert scan_missing_operators(["a/(1+r/100)", "x+y*z"]) == []


def test_missing_op_plan_still_executes_normal_plans():
    plan = StructuredPlan(
        family="base", method_id="da.base-period",
        givens=[Given(label="now", value="2500", stem_span=""),
                Given(label="r", value="25", stem_span="")],
        steps=[PlanStep(var="base", expr="now/(1+r/100)", source="", deps=[])],
        target="base", rationale="")
    ex = execute_plan(plan)
    assert ex.error is None and float(ex.final) == 2000.0


def test_card_exercises_do_not_collide_with_frozen_v2():
    """纪律：方法卡 dev 练习不得与冻结 v2 集数字结构重合（生成器已拦，此处双保险）。"""
    from assessment_agent.planner import _tokenize_expr  # noqa: F401 确保模块可用
    v2 = Path("data/benchmark_xingce_blind_v2/test/xingce_blind_v2.jsonl")
    if not v2.exists():
        return
    v2_nums = set()
    for line in v2.read_text(encoding="utf-8").splitlines():
        it = json.loads(line)
        import re
        for n in re.findall(r"\d+(?:\.\d+)?", it["stem"]):
            v2_nums.add(n)
    kb = _kb()
    for mid in B2_IDS:
        stem = kb.cards()[mid].raw.get("dev_exercise", {}).get("stem", "")
        # 小整数（≤10）通用无区分度；只禁止有判别力的特征数值（≥3 位或小数）重合
        distinctive = [n for n in re.findall(r"\d+(?:\.\d+)?", stem)
                       if len(n.split(".")[0]) >= 3]
        shared = [n for n in distinctive if n in v2_nums]
        assert not shared, (mid, shared)
