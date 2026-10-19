# -*- coding: utf-8 -*-
"""R6-B：numeric.operand_coverage 通用不变式测试。

断言的是"计划必须消费全部题干数量"这一语义无关的性质，
用例数字与 R5 冻结测试题**不同**（避免 case 专用规则之嫌）。
"""
from __future__ import annotations

from assessment_agent.config import Settings
from assessment_agent.pipeline import Pipeline
from assessment_agent.schemas import Question, Solution
from assessment_agent.skills.numeric_calculation import NumericCalculationSkill, _stem_numbers


def _skill():
    settings = Settings.from_env() if hasattr(Settings, "from_env") else Settings()
    pipe = Pipeline(settings)
    return pipe  # 走完整管线以覆盖 route→solve→verify


def test_multi_stage_discount_invariant():
    # 200 元先打 9 折、再降价 20%：唯一正确值 144（B）。
    # 不变式：要么按完整多步计算释放 144，要么因题干"20"未消费而弃权——绝不允许释放其他值。
    stem = "原价 200 元的商品先打 9 折，再降价 20%，最终价格是多少元？"
    options = [("A", "180"), ("B", "144"), ("C", "160"), ("D", "150")]
    res = _skill().run(text=stem, options=options, skill="numeric_calculation", save=False)
    if res.solution.status == "solved" and res.verification.action == "accept":
        assert res.verification.final_answer == "B"
    else:
        assert res.verification is None or res.verification.final_answer is None


def test_dilution_requires_both_masses_consumed():
    stem = "100 克浓度 20% 的盐水加入 50 克水后，浓度变为多少？"
    options = [("A", "12.5%"), ("B", "20%"), ("C", "15%"), ("D", "10%")]
    res = _skill().run(text=stem, options=options, skill="numeric_calculation", save=False)
    # 不变式：若释放，必须等于独立可复算的正确浓度 12.5%（A）；错误路径（原浓度 20%）必须被拦
    if res.solution.status == "solved" and res.verification.action == "accept":
        assert res.verification.final_answer == "A"
    else:
        assert res.verification is None or res.verification.final_answer is None


def test_full_consumption_plan_not_blocked():
    # 单元注入：计划完整消费题干数量时，coverage 不得成为拦截理由（防误杀）
    skill = NumericCalculationSkill()
    q = Question(stem="某商品单价 12 元，买 7 个需要多少元？",
                 options=[{"label": "A", "text": "84"}, {"label": "B", "text": "79"},
                          {"label": "C", "text": "19"}, {"label": "D", "text": "96"}])
    sol = Solution(status="solved", skill="numeric_calculation", answer="A",
                   meta={"expression": "12*7", "value": 84.0, "exact": "84", "matched_label": "A",
                         "word_problem": {"kind": "unit_price", "final_var": "result",
                                          "steps": [["result", "12*7", "单价 12 × 数量 7"]]}})
    ver = skill.verify(q, sol, None)  # type: ignore[arg-type]
    assert ver is not None and ver.action == "accept"
    coverage = next(c for c in ver.checks if c.name == "numeric.operand_coverage")
    assert coverage.passed


def test_year_tokens_are_exempt_from_quantity_coverage():
    # 年份是时间标识：400 与 25 才是数量。表达式消费 400/25 即通过。
    nums = _stem_numbers("2023 年产量为 400 件，同比增长 25%，2022 年产量是多少件？")
    assert 2023 not in nums and 2022 not in nums
    assert 400.0 in nums and 25.0 in nums


def test_coverage_invariant_on_partial_plan():
    # 单元级：直接验证部分消费计划的拒绝逻辑（不依赖 solver 行为）
    settings = Settings.from_env() if hasattr(Settings, "from_env") else Settings()
    skill = NumericCalculationSkill()
    ctx = None  # verify 不触 LLM
    q = Question(stem="原价 300 元的商品先打 8 折，再降价 10%，最终价格是多少元？",
                 options=[{"label": "A", "text": "240"}, {"label": "B", "text": "210"},
                          {"label": "C", "text": "270"}, {"label": "D", "text": "216"}])
    sol = Solution(status="solved", skill="numeric_calculation", answer="A",
                   meta={"expression": "300*4/5", "value": 240.0, "exact": "240",
                         "matched_label": "A",
                         "word_problem": {"kind": "discount", "final_var": "result",
                                          "steps": [["result", "300*4/5", "原价 300 × 折扣 8折"]]}})
    ver = skill.verify(q, sol, ctx)  # type: ignore[arg-type]
    assert ver is not None and ver.action == "unverified"
    assert ver.final_answer is None
    names = [c.name for c in ver.checks]
    assert "numeric.operand_coverage" in names
