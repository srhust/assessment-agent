"""R3b Part 1：资料分析可靠性修复回归。

- LLM Plan Fallback 必须**真正执行**模型提交的计划（白名单/模板/证据绑定后走同一确定性路径），
  而不是忽略模型计划再重跑刚失败的规则解析器；
- 每个 DataOperand 必须命中且只命中一种有效证据来源，越界/缺失/无法绑定一律阻断接受；
- 操作↔表达式一致性成为独立的结构语义检查；intent 只声明表层线索检查。
"""

from __future__ import annotations

import copy
from pathlib import Path

from assessment_agent.config import Settings
from assessment_agent.evaluation.runner import load_cases
from assessment_agent.schemas import DataCalculationPlan, Question, QuestionOption
from assessment_agent.skills.base import SkillContext
from assessment_agent.skills.data_analysis import DataAnalysisSkill
from assessment_agent.tools.data_plan import (OPERATION_EXPRESSION_TEMPLATES,
                                               plan_from_llm_payload)
from assessment_agent.tools.table_tool import normalize_table

DATA = Path(__file__).resolve().parents[1] / "data"


def _ctx(llm=None) -> SkillContext:
    return SkillContext(settings=Settings(offline=True, data_dir=DATA), llm=llm)


def _table_question(cells, stem, options):
    return Question(stem=stem,
                    options=[QuestionOption(label=l, text=t) for l, t in options],
                    meta={"vision": {"table": {"bbox": [60, 100, 300, 200], "cells": cells}}})


TOTAL15_CELLS = [["季度", "销售额(万元)"], ["一", "180"], ["二", "210"], ["三", "240"], ["四", "300"]]

ADDHOCK_STEM = "把一季度与二季度的销售额相加得到多少万元？"  # 规则解析器不认识"相加"
ADDHOCK_OPTIONS = [("A", "390"), ("B", "300"), ("C", "420"), ("D", "210")]


class ScriptedLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete_json(self, system, user, **kwargs):
        self.calls += 1
        return copy.deepcopy(self.payload)


def _valid_sum_payload():
    return {"operation": "sum",
            "operands": [{"name": "O1", "source": "table", "table_ref": 0, "row": 1, "col": 1, "raw": "180"},
                          {"name": "O2", "source": "table", "table_ref": 0, "row": 2, "col": 1, "raw": "210"}],
            "expression": "O1 + O2", "result_unit": "万元", "question_focus": "相加"}


# --------------------------------------------------------- 1. LLM 计划被真正执行

def test_llm_fallback_executes_valid_evidence_bound_plan():
    """规则失败 → 合法模型计划必须**被执行**（不是重跑同一个失败的规则解析器）。"""
    skill = DataAnalysisSkill()
    llm = ScriptedLLM(_valid_sum_payload())
    solution = skill.solve(_table_question(TOTAL15_CELLS, ADDHOCK_STEM, ADDHOCK_OPTIONS), _ctx(llm))
    assert llm.calls == 1
    assert solution.status == "solved", solution.error
    assert solution.answer == "A"
    assert solution.meta["plan_source"] == "llm_evidence_bound"
    verification = skill.verify(_table_question(TOTAL15_CELLS, ADDHOCK_STEM, ADDHOCK_OPTIONS),
                                 solution, _ctx(llm))
    assert verification.action == "accept"  # 模型计划同样必须过六步独立验证才能接受
    check_names = {c.name for c in verification.checks}
    assert {"data.source_recheck", "data.plan_semantics", "data.recompute"} <= check_names
    assert all(c.passed for c in verification.checks)


def test_llm_plan_bypasses_rule_failure_proof():
    """证明执行的确实是模型计划：把 raw 改成规则题面（合计）下也合法的引用无关字段无效——
    直接把表达式换成均值模板会被拒；换成正确的 sum 表达式才可执行。"""
    skill = DataAnalysisSkill()
    payload = _valid_sum_payload()
    payload["expression"] = "(O1 + O2) / 2"  # 操作说 sum、表达式是均值形式 → 语义不一致
    solution = skill.solve(_table_question(TOTAL15_CELLS, ADDHOCK_STEM, ADDHOCK_OPTIONS),
                            _ctx(ScriptedLLM(payload)))
    assert solution.status == "failed"
    assert solution.meta["llm_rejection"]["code"] == "expression_operation_mismatch"


# --------------------------------------------------------- 2. 伪造/越界计划必须被拒

def test_llm_fabricated_evidence_rejected():
    skill = DataAnalysisSkill()
    payload = _valid_sum_payload()
    payload["operands"][1]["raw"] = "190"  # 声称的原文与真实单元格不符
    solution = skill.solve(_table_question(TOTAL15_CELLS, ADDHOCK_STEM, ADDHOCK_OPTIONS),
                            _ctx(ScriptedLLM(payload)))
    assert solution.status == "failed" and solution.answer is None
    assert solution.meta["llm_rejection"]["code"] == "evidence_mismatch"

    payload = _valid_sum_payload()
    payload["operands"][1]["row"] = 99
    solution = skill.solve(_table_question(TOTAL15_CELLS, ADDHOCK_STEM, ADDHOCK_OPTIONS),
                            _ctx(ScriptedLLM(payload)))
    assert solution.meta["llm_rejection"]["code"] == "cell_out_of_bounds"

    payload = _valid_sum_payload()
    payload["operation"] = "predict_market"
    solution = skill.solve(_table_question(TOTAL15_CELLS, ADDHOCK_STEM, ADDHOCK_OPTIONS),
                            _ctx(ScriptedLLM(payload)))
    assert solution.meta["llm_rejection"]["code"] == "operation_not_whitelisted"

    payload = _valid_sum_payload()
    payload["expression"] = "O1 + O2 + float(3)"
    solution = skill.solve(_table_question(TOTAL15_CELLS, ADDHOCK_STEM, ADDHOCK_OPTIONS),
                            _ctx(ScriptedLLM(payload)))
    assert solution.meta["llm_rejection"]["code"] in {"expression_charset", "expression_references"}


def test_llm_plan_never_returns_answer_field():
    """模型 payload 里塞 final_answer 也不允许影响结果：执行仍由求值+选项唯一匹配决定。"""
    skill = DataAnalysisSkill()
    payload = _valid_sum_payload()
    payload["final_answer"] = "D"
    solution = skill.solve(_table_question(TOTAL15_CELLS, ADDHOCK_STEM, ADDHOCK_OPTIONS),
                            _ctx(ScriptedLLM(payload)))
    assert solution.status == "solved" and solution.answer == "A"  # 390，不是模型自称的 D


def test_plan_from_llm_payload_unit_inline_and_chart_sources():
    table = normalize_table(TOTAL15_CELLS)["table"]
    # inline：数字必须逐字在题干
    payload = {"operation": "difference",
               "operands": [{"name": "O1", "source": "inline", "raw": "180"},
                             {"name": "O2", "source": "table", "table_ref": 0, "row": 2, "col": 1, "raw": "210"}],
               "expression": "O2 - O1"}
    built = plan_from_llm_payload("差额是多少？（180 出现在这里）", payload, [table], None)
    assert built["ok"]
    ghost = dict(payload, operands=[dict(payload["operands"][0], raw="181"), payload["operands"][1]])
    assert plan_from_llm_payload("差额是多少？（180 出现在这里）", ghost, [table], None
                                  )["failure"]["code"] == "inline_evidence_missing"
    # chart：必须命中图上标注读数
    chart = {"ok": True, "series": {"甲": 17, "乙": 42}, "unit": "件",
             "evidence": ["甲组17件", "乙组42件"], "order": ["甲", "乙"]}
    payload = {"operation": "difference",
               "operands": [{"name": "O1", "source": "chart", "label": "乙", "raw": "乙组42件"},
                             {"name": "O2", "source": "chart", "label": "甲", "raw": "甲组17件"}],
               "expression": "O1 - O2"}
    assert plan_from_llm_payload("乙组比甲组多多少件？", payload, [], chart)["ok"]
    bad = dict(payload, operands=[dict(payload["operands"][0], raw="乙组43件"), payload["operands"][1]])
    assert plan_from_llm_payload("乙组比甲组多多少件？", bad, [], chart)["failure"]["code"] \
        == "chart_evidence_mismatch"


# --------------------------------------------------------- 3. 引用篡改/越界必须阻断接受

def _solved_total15():
    skill = DataAnalysisSkill()
    question = _table_question(TOTAL15_CELLS, "四个季度销售额合计是多少万元？",
                                [("A", "930"), ("B", "870"), ("C", "900"), ("D", "960")])
    solution = skill.solve(question, _ctx())
    assert solution.status == "solved"
    return skill, question, solution


def _tampered_verify(mutate):
    skill, question, solution = _solved_total15()
    plan_dict = copy.deepcopy(solution.meta["data_plan"])
    mutate(plan_dict)
    tampered = copy.deepcopy(solution)
    tampered.meta["data_plan"] = plan_dict
    return skill, question, tampered


def test_out_of_range_table_ref_blocks_accept():
    def mutate(plan):
        plan["operands"][0]["table_ref"] = 5  # 引用不存在的表格
    skill, question, tampered = _tampered_verify(mutate)
    verification = skill.verify(question, tampered, _ctx())
    assert verification.action == "reject" and not verification.passed
    assert any(c.name == "data.source_recheck" and not c.passed for c in verification.checks)


def test_missing_row_col_reference_blocks_accept():
    def mutate(plan):
        plan["operands"][0]["row"] = None
        plan["operands"][0]["col"] = None
    skill, question, tampered = _tampered_verify(mutate)
    verification = skill.verify(question, tampered, _ctx())
    assert verification.action != "accept"


def test_operand_row_col_swap_blocks_accept():
    """把 O1 的引用指向另一真实单元格但保留原 raw：原文不一致必须被阻断。"""
    def mutate(plan):
        plan["operands"][0]["row"] = 3  # 实际是 "300"，raw 声称 "180"
    skill, question, tampered = _tampered_verify(mutate)
    verification = skill.verify(question, tampered, _ctx())
    assert verification.action == "reject"


def test_expression_semantic_mismatch_blocks_accept():
    def mutate(plan):
        plan["expression"] = "O1 + O2 + O3"  # 篡改后不再含 O4，独立复算与求值都失配
    skill, question, tampered = _tampered_verify(mutate)
    verification = skill.verify(question, tampered, _ctx())
    assert verification.action != "accept"
    # 更直接：表达式换成均值模板 → 结构语义检查阻断
    def mutate2(plan):
        plan["expression"] = "(O1 + O2 + O3 + O4) / 2"
    skill, question, tampered = _tampered_verify(mutate2)
    verification = skill.verify(question, tampered, _ctx())
    assert any(c.name == "data.plan_semantics" and not c.passed for c in verification.checks)
    assert verification.action == "reject"


def test_intent_check_is_honestly_scoped():
    """data.intent 通过时必须自我声明只是表层线索检查（不能冒充题意正确性证明）。"""
    skill, question, solution = _solved_total15()
    verification = skill.verify(question, solution, _ctx())
    intent = next(c for c in verification.checks if c.name == "data.intent")
    assert intent.passed and "表层" in intent.detail


def test_templates_cover_all_rule_operations():
    """操作白名单必须覆盖规则生成器全部操作，且模板能被规则产物满足（bench 全量复核）。"""
    cases = [c for c in load_cases(DATA, "bench_dev") if c.skill == "data_analysis"]
    covered_ops = set()
    for case in cases:
        question = Question(stem=case.stem,
                            options=[QuestionOption(label=o.label, text=o.text) for o in case.options],
                            meta={"vision": case.regions or {}})
        solution = DataAnalysisSkill().solve(question, _ctx())
        plan_dict = solution.meta.get("data_plan")
        if not plan_dict:
            continue
        covered_ops.add(plan_dict["operation"])
        verification = DataAnalysisSkill().verify(question, solution, _ctx())
        if verification and verification.action == "accept":
            semantics = [c for c in verification.checks if c.name == "data.plan_semantics"]
            assert semantics and semantics[0].passed, (case.id, semantics)
    assert covered_ops <= set(OPERATION_EXPRESSION_TEMPLATES)
    # 至少覆盖到这些关键操作，防止模板与规则实现脱节
    assert {"sum", "mean", "difference", "rank_extreme", "growth_yoy", "growth_amount",
            "share", "multiple", "avg_annual_growth", "base_value", "pct_point_diff",
            "unit_convert"} <= covered_ops
