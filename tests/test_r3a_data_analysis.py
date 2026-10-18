"""R3a 资料分析 Skill 测试：真实表格/统计图 → 计算计划 → 确定性求值 → 独立验证。

纪律与实现同源：
- 每个数字必须能追溯到单元格/图上标注文本；
- OCR 歧义、缺数、单位冲突、引用歧义 → 结构化失败，绝不猜；
- 验证层用独立算术路径复算 + 源数据重读，不能确认时 unverified/reject，不默认 accept；
- 统计图数值只取文本证据，柱高像素仅做比例一致性复核（禁止按柱长猜数）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from assessment_agent.config import Settings
from assessment_agent.evaluation.runner import EvaluationRunner, load_cases
from assessment_agent.pipeline import Pipeline
from assessment_agent.schemas import Question, QuestionOption
from assessment_agent.skills.base import SkillContext
from assessment_agent.skills.data_analysis import DataAnalysisSkill
from assessment_agent.tools.chart_tool import chart_values_from_texts, measure_bars
from assessment_agent.tools.data_plan import build_data_plan, evaluate_plan
from assessment_agent.tools.figure_tools import load_mask, normalized_cell_mask
from assessment_agent.tools.table_tool import normalize_table

DATA = Path(__file__).resolve().parents[1] / "data"

DEV_SOLVED = [
    "bi-table-total-15", "bi-table-yoy-17", "bi-table-mom-18", "bi-table-share-19",
    "bi-table-mean-20", "bi-table-rank-21", "bi-table-diff-22", "bi-table-base-23",
    "bi-table-multi-24", "bi-table-ppt-25", "bi-table-unit-26", "bi-table-hdr2-27",
    "bi-table-totalrow-28", "bi-table-avg-29", "bi-chart-diff-30", "bi-chart-sum-31",
]
DEV_FAILED = {
    "bi-chart-nolabel-32": "no_data_regions",
    "bi-table-ocr-33": "digit_ambiguous",
    "bi-table-gap-34": "cell_missing",
    "bi-table-ambigcol-35": "ambiguous_reference",
}


def _dev_cases():
    return {case.id: case for case in load_cases(DATA, "bench_dev") if case.skill == "data_analysis"}


def _question(case) -> Question:
    return Question(stem=case.stem,
                    options=[QuestionOption(label=o.label, text=o.text) for o in case.options],
                    meta={"vision": case.regions or {}})


def _ctx(settings: Settings | None = None) -> SkillContext:
    return SkillContext(settings=settings or Settings(offline=True, data_dir=DATA))


# ------------------------------------------------------------------ 1. 全部 Dev DA 样本：可答→真答案，应败→真失败

@pytest.mark.parametrize("case_id", DEV_SOLVED)
def test_dev_data_analysis_solves_with_traceable_plan(case_id):
    case = _dev_cases()[case_id]
    skill = DataAnalysisSkill()
    question = _question(case)
    solution = skill.solve(question, _ctx())
    assert solution.status == "solved", solution.error
    assert solution.answer == case.answer
    # 答案可追溯：源表格 → 计算计划 → 最终数值（spec 验收条件）
    plan_meta = solution.meta["data_plan"]
    assert plan_meta["operands"], "计划必须引用源数据"
    for operand in plan_meta["operands"]:
        assert operand["raw_text"], f"操作数必须携带原文证据: {operand}"
    assert solution.meta["evaluation"]["value"] is not None
    assert solution.meta["matched_label"] == case.answer
    verification = skill.verify(question, solution, _ctx())
    assert verification is not None and verification.action == "accept" and verification.passed
    names = {check.name for check in verification.checks}
    assert {"data.source_recheck", "data.intent", "data.units",
            "data.recompute", "data.option_match"} <= names
    if case_id in {"bi-chart-diff-30", "bi-chart-sum-31"}:
        assert "data.chart_pixels" in names  # 有裁剪时必须做像素复核（通过或降级 warning）


@pytest.mark.parametrize("case_id,code", sorted(DEV_FAILED.items()))
def test_dev_data_analysis_honest_failures(case_id, code):
    case = _dev_cases()[case_id]
    skill = DataAnalysisSkill()
    solution = skill.solve(_question(case), _ctx())
    assert solution.status == "failed" and solution.answer is None
    assert solution.meta["failure"]["code"] == code
    verification = skill.verify(_question(case), solution, _ctx())
    assert verification is None or verification.action != "accept"


def test_unit_scale_and_result_kinds():
    """万元/亿元按显示单位匹配；增长率=百分比，百分点差=百分点；增长量是差额不是比率。"""
    cases = _dev_cases()
    skill = DataAnalysisSkill()
    unit26 = skill.solve(_question(cases["bi-table-unit-26"]), _ctx())
    assert unit26.meta["evaluation"]["value"] == "42000"  # 4.2 亿元 → 显示单位万元
    ppt = skill.solve(_question(cases["bi-table-ppt-25"]), _ctx())
    assert ppt.meta["data_plan"]["result_kind"] == "ppt"
    yoy = skill.solve(_question(cases["bi-table-yoy-17"]), _ctx())
    assert yoy.meta["data_plan"]["result_kind"] == "percent"
    mom = skill.solve(_question(cases["bi-table-mom-18"]), _ctx())
    assert mom.meta["data_plan"]["operation"] in {"growth_mom", "growth_amount"}
    assert mom.meta["data_plan"]["expression"] == "O2 - O1"  # 增长多少件：差额而非比率


def test_avg_annual_growth_exact_root_span():
    case = _dev_cases()["bi-table-avg-29"]
    solution = DataAnalysisSkill().solve(_question(case), _ctx())
    plan = solution.meta["data_plan"]
    assert "个增长期" in plan["assumptions"][0]
    assert "2021" in plan["assumptions"][0] and "2023" in plan["assumptions"][0]
    assert solution.meta["evaluation"]["approx"] is False  # 150→216 是精确平方率
    assert solution.meta["evaluation"]["value"].startswith("20")


def test_multilevel_header_keeps_time_and_object():
    case = _dev_cases()["bi-table-hdr2-27"]
    solution = DataAnalysisSkill().solve(_question(case), _ctx())
    col_labels = [o["col_label"] for o in solution.meta["data_plan"]["operands"]]
    assert all("2022" in label or "2023" in label for label in col_labels)
    assert all("销售额" in label for label in col_labels)


def test_rank_extreme_answers_by_label():
    cases = _dev_cases()
    solution = DataAnalysisSkill().solve(_question(cases["bi-table-rank-21"]), _ctx())
    assert solution.meta["data_plan"]["answer_kind"] == "label"
    assert solution.answer == "D"  # 第四季度
    # 并列最多 → 必须歧义失败而不是随便挑一个
    table = {"table": {"bbox": [0, 0, 10, 10],
                        "cells": [["组", "件数"], ["甲", "40"], ["乙", "40"], ["丙", "10"]]}}
    q = Question(stem="哪一组的件数最多？",
                 options=[QuestionOption(label="A", text="甲组"), QuestionOption(label="B", text="乙组")],
                 meta={"vision": table})
    tie = DataAnalysisSkill().solve(q, _ctx())
    assert tie.status == "failed"
    assert tie.meta["failure"]["code"] == "ambiguous_reference"


# ------------------------------------------------------------------ 2. 独立验证：篡改必须被阻断

def test_tampered_cell_rejected_by_source_recheck():
    case = _dev_cases()["bi-table-yoy-17"]
    skill = DataAnalysisSkill()
    question = _question(case)
    solution = skill.solve(question, _ctx())
    assert solution.status == "solved"
    tampered = _question(case)
    cells = [list(row) for row in tampered.meta["vision"]["table"]["cells"]]
    for row in cells:
        if "210" in row:
            row[row.index("210")] = "250"
    tampered.meta["vision"]["table"]["cells"] = cells
    verification = skill.verify(tampered, solution, _ctx())
    assert verification.action == "reject" and not verification.passed
    failed = [c for c in verification.checks if c.name == "data.source_recheck" and not c.passed]
    assert failed, "源单元格被篡改后验证层必须报矛盾"


def test_tampered_chart_text_rejected():
    case = _dev_cases()["bi-chart-diff-30"]
    skill = DataAnalysisSkill()
    question = _question(case)
    solution = skill.solve(question, _ctx())
    assert solution.status == "solved"
    tampered = _question(case)
    tampered.meta["vision"]["chart"]["text"] = "甲组17件 乙组37件 丙组30件"
    verification = skill.verify(tampered, solution, _ctx())
    assert verification.action in {"reject", "unverified"}
    assert not verification.passed


def test_verifier_abstains_when_options_become_ambiguous():
    case = _dev_cases()["bi-table-diff-22"]
    skill = DataAnalysisSkill()
    question = _question(case)
    solution = skill.solve(question, _ctx())
    drift = _question(case)
    drift.options = [QuestionOption(label="A", text="30"), QuestionOption(label="B", text="30"),
                     QuestionOption(label="C", text="40"), QuestionOption(label="D", text="90")]
    verification = skill.verify(drift, solution, _ctx())
    assert verification.action != "accept"  # 两个选项同值 → 不许唯一命中


def test_incomplete_or_invalid_data_structured_failures():
    """缺单元格/OCR 歧义/单位冲突在计划层就返回结构化失败码。"""
    skill = DataAnalysisSkill()

    def run(cells, stem):
        q = Question(stem=stem,
                     options=[QuestionOption(label="A", text="1")],
                     meta={"vision": {"table": {"bbox": [0, 0, 9, 9], "cells": cells}}})
        return skill.solve(q, _ctx())

    assert run([["季度", "销售额(万元)"], ["一", "l80"], ["二", "210"]],
               "一季度与二季度销售额合计是多少万元？").meta["failure"]["code"] == "digit_ambiguous"
    assert run([["季度", "销售额(万元)"], ["一", ""], ["二", "210"]],
               "两个季度销售额合计是多少万元？").meta["failure"]["code"] == "cell_missing"
    conflict = run([["季度", "销售额(万元)"], ["一", "180 亿元"], ["二", "210"]],
                   "两个季度销售额合计是多少万元？")
    assert conflict.status == "failed"
    assert conflict.meta["failure"]["code"] == "unit_conflict"
    # 行"一/二"是单字标签且题面无类别上下文：不得被误认成统计对象行
    plan = build_data_plan("两个季度销售额合计是多少万元？",
                           [normalize_table([["季度", "销售额(万元)"], ["一", "180"],
                                              ["二", "210"], ["三", "240"]])["table"]], None)
    assert plan["ok"] and plan["plan"].expression.count("+") == 2  # O1+O2+O3+O4 全列求和


# ------------------------------------------------------------------ 3. 统计图纪律

def test_chart_values_only_from_text_evidence():
    parsed = chart_values_from_texts([{"id": "chart", "text": "甲组17件 乙组42件 丙组30件 完成数量分布"}])
    assert parsed["ok"] and parsed["series"]["甲"] == 17 and parsed["unit"] == "件"
    # 无标注数值 → 不返回可用读数（绝不按柱长猜数）
    junk = chart_values_from_texts([{"id": "chart", "text": "各组合计完成情况 SH2026 统计图"}])
    assert not junk["ok"] and junk["failure"]["code"] == "chart_values_insufficient"
    # 同一组两个不同读数 → 冲突失败
    clash = chart_values_from_texts([{"id": "chart", "text": "甲组17件 甲组25件"}])
    assert not clash["ok"] and clash["failure"]["code"] == "chart_value_conflict"


def test_bar_pixel_ratio_consistency_on_generated_chart(tmp_path):
    """真实生成的柱状图裁剪：measure_bars 应测出与读数成比例的柱高（复核而非猜数）。"""
    case = _dev_cases()["bi-chart-diff-30"]
    crop = (case.regions.get("chart") or {}).get("crop_path")
    assert crop, "dev 统计图金标应带裁剪路径"
    measured = measure_bars(crop)
    assert measured["ok"], measured
    heights = measured["heights"]
    values = [17, 42, 30]
    # 允许轮廓量化误差：比例偏差 ≤ 20%
    norm_h = [h / max(heights) for h in heights]
    norm_v = [v / max(values) for v in values]
    assert all(abs(a - b) <= 0.2 for a, b in zip(norm_h, norm_v))


def test_chart_crop_without_text_never_yields_values():
    case = _dev_cases()["bi-chart-nolabel-32"]
    solution = DataAnalysisSkill().solve(_question(case), _ctx())
    assert solution.status == "failed"
    assert solution.meta["failure"]["code"] == "no_data_regions"


# ------------------------------------------------------------------ 4. LLM 入口只能供计划，不能定答案

def test_llm_cannot_invent_answer_or_fabricate_evidence():
    class JunkLLM:
        def __init__(self):
            self.calls = 0

        def complete_json(self, system, user, **kwargs):
            self.calls += 1
            return {"operation": "sum", "final_answer": "A",
                    "expression": "O1 + O2",
                    "operands": [{"row": 99, "col": 99, "raw": "666"}]}

    case = _dev_cases()["bi-table-gap-34"]  # 规则层本就失败（cell_missing）
    llm = JunkLLM()
    ctx = SkillContext(settings=Settings(offline=True, data_dir=DATA), llm=llm)
    solution = DataAnalysisSkill().solve(_question(case), ctx)
    assert llm.calls == 1  # 计划入口被调用
    assert solution.status == "failed" and solution.answer is None  # 伪造证据被拒绝，答案不被 LLM 决定
    # 即便规则能成功，LLM 也只被允许复述规则计划（solve 里先规则后 LLM）
    ok_case = _dev_cases()["bi-table-total-15"]
    solution2 = DataAnalysisSkill().solve(_question(ok_case), ctx)
    assert solution2.status == "solved" and solution2.meta["matched_label"] == "A"
    assert llm.calls == 1  # 规则成功时根本不再请求 LLM


# ------------------------------------------------------------------ 5. 开关 / 注册 / 口径（R3a 残留修复）

def test_ablation_flag_disables_data_analysis():
    settings = Settings.from_env(env={"ASSESSMENT_R2C_DISABLE": "data_analysis"})
    assert settings.r3a_data_analysis is False
    case = _dev_cases()["bi-table-total-15"]
    solution = DataAnalysisSkill().solve(_question(case), _ctx(settings))
    assert solution.status == "unsupported"  # 消融基线：回到未实现声明，且不猜


def test_capability_status_is_registry_derived():
    pipeline = Pipeline(Settings(offline=True, data_dir=DATA))
    supported = {s.id for s in pipeline.registry.supported()}
    assert "data_analysis" in supported  # 由实际注册能力派生，而不是固定名单
    assert "verbal_understanding" not in supported
    cases = load_cases(DATA, "bench_dev")
    da = [c for c in cases if c.skill == "data_analysis"]
    assert da and all(c.capability_status == "supported" for c in da)
    verbal = [c for c in cases if c.skill == "verbal_understanding"]
    assert verbal and all(c.capability_status == "unsupported" for c in verbal)


def test_router_prefers_data_analysis_on_table_structure():
    case = _dev_cases()["bi-table-yoy-17"]
    pipeline = Pipeline(Settings(offline=True, data_dir=DATA))
    run = pipeline.run(text=case.stem,
                       options=[{"label": o.label, "text": o.text} for o in case.options],
                       meta={"vision": case.regions}, save=False)
    assert run.route.skill == "data_analysis"
    assert run.solution.status == "solved" and run.solution.answer == case.answer


def test_primary_vs_legacy_metric_fields():
    runner = EvaluationRunner(Pipeline(Settings(offline=True, data_dir=DATA)),
                              data_dir=DATA, results_dir=DATA.parent / "results")
    summary = runner.run("screens", eval_mode="image_only", save=False)
    metrics = summary.metrics
    e2e = metrics["e2e"]
    assert "primary_answer_accuracy" in e2e and "legacy_fields" in e2e and "legacy_note" in e2e
    assert metrics["capability"]["capability_source"] == "skill_registry"
    assert isinstance(e2e["primary_false_accept"], int)


# ------------------------------------------------------------------ 6. R3a 修复 1：非方形网格裁切 bbox 序

def test_grid_substitution_bbox_order_for_rectangular_cells(tmp_path):
    """修复锚：shape 是 (高, 宽)，bbox 约定是 (x1, y1)。旧代码把 [0,0,h,w] 传给
    normalized_cell_mask，非方形裁剪会被截半；正确顺序 [0,0,w,h] 必须保留全部墨迹。"""
    import cv2

    height, width = 10, 25
    canvas = np.full((height, width), 255, dtype=np.uint8)
    canvas[:, 15:25] = 0  # 墨迹（暗）只在右半（宽方向）
    png = tmp_path / "wide.png"
    cv2.imwrite(str(png), canvas)
    mask = load_mask(png)
    fixed = normalized_cell_mask(mask, [0, 0, width, height])
    legacy = normalized_cell_mask(mask, [0, 0, height, width])
    assert fixed.sum() > 0.3 * fixed.size   # 正确顺序：一半以上是墨迹
    assert legacy.sum() == 0               # 旧顺序把 x1 当高度：右半墨迹被裁掉


# ------------------------------------------------------------------ 7. R2c 误放回归锚：新 DA 全量跑不得出现误放

def test_holdout_auto_router_still_has_zero_false_accept():
    runner = EvaluationRunner(Pipeline(Settings(offline=True, data_dir=DATA)),
                              data_dir=DATA, results_dir=DATA.parent / "results")
    summary = runner.run("bench_holdout", eval_mode="auto_router", save=False)
    cap = summary.metrics["capability"]
    assert cap["false_accept_total"] == 0  # R2c 原有误放继续被阻断
    assert cap["over_answer_total"] <= 1
    assert cap["objective_total"] == 14    # 冻结分母不变


def test_dev_auto_router_selective_integrity():
    runner = EvaluationRunner(Pipeline(Settings(offline=True, data_dir=DATA)),
                              data_dir=DATA, results_dir=DATA.parent / "results")
    summary = runner.run("bench_dev", eval_mode="auto_router", save=False)
    cap = summary.metrics["capability"]
    assert cap["false_accept_total"] == 0
    assert cap["objective_total"] == 30
    # 接受的答案里错率=0（选择性准确率 100%），且资料分析确实答对了多数样本
    outcomes = cap["verification_outcomes"]
    wrong_accepts = sum(v for k, v in outcomes.items() if "false_accept" in k)
    assert wrong_accepts == 0
    da_results = [r for r in summary.results if r.skill == "data_analysis" and r.expected is not None]
    assert sum(1 for r in da_results if r.correct) >= 14
    # 每条正确答案可追溯：solved+accept 的 DA 样本都带计划与求值 meta
    for r in da_results:
        if r.correct and r.verification_action == "accept":
            assert r.verification_outcome == "correct_accept"
