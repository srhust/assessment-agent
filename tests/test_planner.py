"""R7 阶段B：ReasoningPlanner 单元测试（FakeLLM 驱动，零 API）。"""

from __future__ import annotations

import json
from fractions import Fraction

from assessment_agent.knowledge.methods import MethodCard
from assessment_agent.planner import (
    Given, PlanStep, ReasoningPlanner, StructuredPlan, execute_plan, validate_plan)

STEM = "某商品原价 300 元，先打 8 折，再降价 10%，最终价格是多少元？"
OPTIONS = [{"label": "A", "text": "240"}, {"label": "B", "text": "216"},
           {"label": "C", "text": "270"}, {"label": "D", "text": "210"}]


def _card() -> MethodCard:
    return MethodCard(
        method_id="test.successive-percent", skill="numeric_calculation",
        problem_family="successive-change", concepts=["乘法复合"],
        applicability_conditions=["多次百分比/折扣变化"],
        required_quantities=["初值", "各次变化率"],
        reasoning_operations=["逐步乘 (1-r)"],
        solution_procedure=["绑定原价", "第一段折扣", "第二段降价", "复合"],
        verification_strategy=["每步基准量核对"],
        common_pitfalls=["百分比相加"],
        source_references=[{"title": "test", "url": "https://example.org/t"}],
        version="1", raw={})


class FakeLLM:
    """按工单：无 API 时用 Fake 模型测试接口。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete_json(self, system, user, *, image_path=None, model=None):
        self.calls += 1
        assert "金标" not in user and "正确答案" not in system  # 输入无答案泄漏
        return self.payload if isinstance(self.payload, dict) else {"__raise__": self.payload}


def _good_payload() -> dict:
    return {
        "family": "successive-discount", "method_id": "test.successive-percent",
        "rationale": "两段变化逐步复合",
        "givens": [
            {"label": "price0", "value": "300", "stem_span": "原价 300 元"},
            {"label": "f1", "value": "4/5", "stem_span": "先打 8 折"},
            {"label": "r2", "value": "9/10", "stem_span": "再降价 10%"}],
        "steps": [
            {"var": "after1", "expr": "price0*f1", "source": "先打 8 折", "deps": ["price0", "f1"]},
            {"var": "final", "expr": "after1*r2", "source": "再降价 10%", "deps": ["after1", "r2"]}],
        "target": "final", "unknowns": [], "question_hypotheses": ["数量关系-折扣"]}


# ------------------------------------------------------------------ 执行器


def test_execute_plan_multi_stage_exact():
    p = ReasoningPlanner._bind_payload(_good_payload(), [_card()])
    assert p is not None
    ex = execute_plan(p)
    assert ex.error is None and ex.final == Fraction(216)
    assert ex.expression and "300" in ex.expression


def test_execute_plan_undefined_var_fails_closed():
    pay = _good_payload()
    pay["steps"][1]["expr"] = "after1*ghost"
    p = ReasoningPlanner._bind_payload(pay, [_card()])
    assert p is not None
    ex = execute_plan(p)
    assert ex.final is None and "ghost" in (ex.error or "")


# ------------------------------------------------------------------ 校验器（B1）


def test_validate_rejects_fabricated_span():
    pay = _good_payload()
    pay["givens"][0]["stem_span"] = "原价 999 元"   # 非题干子串
    p = ReasoningPlanner._bind_payload(pay, [_card()])
    assert p is not None
    problems = validate_plan(p, STEM)
    assert any("stem_span" in s for s in problems)


def test_validate_rejects_value_not_in_span():
    pay = _good_payload()
    pay["givens"][1] = {"label": "f1", "value": "7/10", "stem_span": "先打 8 折"}
    p = ReasoningPlanner._bind_payload(pay, [_card()])
    assert p is not None
    problems = validate_plan(p, STEM)
    assert any("来源片段数值不符" in s for s in problems)


def test_validate_rejects_unbound_reference_and_bad_target():
    pay = _good_payload()
    pay["target"] = "nonexistent"
    p = ReasoningPlanner._bind_payload(pay, [_card()])
    assert p is not None
    problems = validate_plan(p, STEM)
    assert any("目标量" in s for s in problems)


def test_validate_accepts_good_plan():
    p = ReasoningPlanner._bind_payload(_good_payload(), [_card()])
    assert p is not None
    assert validate_plan(p, STEM) == []


# ------------------------------------------------------------------ 规划通道（FakeLLM）


def test_plan_llm_end_to_end_with_fake():
    kb_cards = [_card()]

    class KB:  # 注入已检索卡（不触磁盘）
        def search(self, query, skill=None, top_k=3):
            return kb_cards

    planner = ReasoningPlanner(kb=KB(), llm=FakeLLM(_good_payload()))  # type: ignore[arg-type]
    plan, used, why = planner.plan_llm(STEM, OPTIONS, "numeric_calculation")
    assert why is None and plan is not None
    ex = execute_plan(plan)
    assert ex.final == Fraction(216)
    assert plan.solver_source == "llm_planner"


def test_plan_llm_rejects_unknown_method_card():
    pay = _good_payload()
    pay["method_id"] = "hallucinated.card"

    class KB:
        def search(self, query, skill=None, top_k=3):
            return [_card()]

    planner = ReasoningPlanner(kb=KB(), llm=FakeLLM(pay))  # type: ignore[arg-type]
    plan, _used, why = planner.plan_llm(STEM, OPTIONS, "numeric_calculation")
    assert plan is None and why == "plan_unbound"


def test_plan_multi_no_double_count_same_spans():
    """B2：第二个计划若 givens 来源完全相同则不加成（防同一错误前提共票）。"""
    class KB:
        def search(self, query, skill=None, top_k=3):
            return [_card()]

    planner = ReasoningPlanner(kb=KB(), llm=FakeLLM(_good_payload()))  # type: ignore[arg-type]
    results = planner.plan_multi(STEM, OPTIONS, "numeric_calculation", max_plans=2)
    assert len(results) == 1
