"""R2c 可靠性测试：口径拆分、归因拆分、图形独立复核、应用题层、路由结构与消融开关。

原则：测**失败机制**（误放、越权、伪装高置信、污染数据目录），不只看成功路径；
所有断言只依赖 Dev/screens 集与合成掩码，不依赖 Holdout 表现。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

from assessment_agent.config import Settings
from assessment_agent.evaluation.diagnosis import (
    derive_root_cause,
    derive_verification_outcome,
)
from assessment_agent.evaluation.metrics import CaseResult, compute_capability_metrics
from assessment_agent.evaluation.runner import (
    derive_answerability,
    derive_capability_status,
    load_cases,
)
from assessment_agent.router import Router
from assessment_agent.schemas import Question, QuestionOption, RouteDecision, Solution, Verification
from assessment_agent.tools.figure_verify import substitution_check
from assessment_agent.tools.word_problem import evaluate_plan, extract_word_problem

DATA = Path(__file__).resolve().parents[1] / "data"


def _case_result(case_id: str, *, answerability: str, capability: str = "supported",
                 outcome: str, root: str = "none", action: str = "accept",
                 expected: str | None = None, predicted: str | None = None) -> CaseResult:
    return CaseResult(
        case_id=case_id, skill="figure_reasoning", expected=expected, predicted=predicted,
        correct=predicted is not None and predicted == expected, status="solved",
        verification_action=action, latency_ms=1.0,
        answerability=answerability, capability_status=capability,
        root_cause=root, verification_outcome=outcome,
    )


# --------------------------------------------------------------------------- 1. 标注派生


def test_label_derivation_rules():
    assert derive_answerability("A", "solved", "figure_reasoning") == "has_unique_answer"
    assert derive_answerability(None, "undecidable", "number_series") == "undecidable"
    assert derive_answerability(None, "failed", "number_series") == "undecidable"
    assert derive_answerability(None, "unsupported", "personality") == "subjective"
    assert derive_answerability(None, "unsupported", "data_analysis") == "objective_unlabeled"
    assert derive_capability_status("figure_reasoning") == "supported"
    assert derive_capability_status("data_analysis") == "unsupported"


def test_bench_labels_derived_not_written_back():
    # JSONL 未被修改：原始记录不含显式标注字段，全部由 loader 派生
    for rel in ("benchmark_real/dev", "benchmark_real/holdout"):
        for file in (DATA / rel).glob("*.jsonl"):
            for line in file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                assert "answerability" not in payload and "capability_status" not in payload, rel
    dev = load_cases(DATA, "bench_dev")
    hold = load_cases(DATA, "bench_holdout")
    objective_dev = [c for c in dev if c.answerability == "has_unique_answer"]
    objective_hold = [c for c in hold if c.answerability == "has_unique_answer"]
    # 与用户核对口径一致：Dev 分母 15、Holdout 分母 14（含未实现但有答案的题型）
    assert len(objective_dev) == 15
    assert len(objective_hold) == 14
    table = [c for c in dev if c.id == "bi-table-total-15"][0]
    assert table.answerability == "has_unique_answer"
    assert table.capability_status == "unsupported"


# --------------------------------------------------------------------------- 2. 口径指标


def test_capability_metrics_separate_honest_and_gap():
    results = [
        # 8 客观答对（已支持）
        *[_case_result(f"ok{i}", answerability="has_unique_answer", outcome="correct_accept",
                       expected="A", predicted="A") for i in range(8)],
        # 1 误放
        _case_result("fa", answerability="has_unique_answer", outcome="false_accept",
                     expected="A", predicted="B"),
        # 2 客观有答案但未实现题型（安全拒答=能力缺口，不是诚实弃权）
        _case_result("gap1", answerability="has_unique_answer", capability="unsupported",
                     outcome="no_answer", root="capability_gap", action="reject"),
        _case_result("gap2", answerability="has_unique_answer", capability="unsupported",
                     outcome="no_answer", root="capability_gap", action="reject"),
        # 不可判定/主观：各 1 题诚实弃权
        _case_result("und", answerability="undecidable", outcome="correct_abstention", action="reject"),
        _case_result("sub", answerability="subjective", outcome="correct_abstention", action="reject"),
        # unverified 受控弃权（可答题）
        _case_result("unv", answerability="has_unique_answer", outcome="unverified_answerable",
                     root="solver_rule_selection", action="unverified"),
    ]
    cap = compute_capability_metrics(results)
    assert cap["objective_total"] == 12  # 8+1+2+1
    assert cap["overall_answer_accuracy"]["hit"] == 8
    assert cap["overall_answer_accuracy"]["denominator"] == 12
    assert abs(cap["overall_answer_accuracy"]["value"] - 8 / 12) < 1e-4
    assert cap["supported_answer_accuracy"]["denominator"] == 10
    assert cap["capability_coverage"]["value"] == round(10 / 12, 4)
    # 诚实弃权只含 undecidable/subjective，不含能力缺口拒答
    assert cap["honest_abstention"]["denominator"] == 2
    assert cap["honest_abstention"]["value"] == 1.0
    assert cap["unsupported_answerable_refusals"] == 2
    assert cap["false_accept_total"] == 1
    assert cap["verification_outcomes"]["unverified_answerable"] == 1


def test_outcome_derivation_branches():
    assert derive_verification_outcome(
        answerability="has_unique_answer", action="reject", predicted=None,
        solution_answer="B", gold="B") == "false_reject"
    assert derive_verification_outcome(
        answerability="has_unique_answer", action="reject", predicted=None,
        solution_answer="B", gold="A") == "correct_reject"
    assert derive_verification_outcome(
        answerability="has_unique_answer", action="unverified", predicted=None,
        solution_answer="B", gold="A") == "unverified_answerable"
    assert derive_verification_outcome(
        answerability="undecidable", action="unverified", predicted=None,
        solution_answer="B", gold=None) == "unverified_abstention"
    assert derive_verification_outcome(
        answerability="undecidable", action="accept", predicted="B",
        solution_answer="B", gold=None) == "over_answer"
    # false_accept 的 root_cause 归求解侧，不归 verifier
    assert derive_root_cause(outcome="false_accept", failure_class="verifier_false_accept",
                             skill="figure_reasoning", solver_rule="fill") == "solver_rule_selection"
    assert derive_root_cause(outcome="false_reject", failure_class="verifier_false_reject") == "verifier"
    assert derive_root_cause(outcome="no_answer", failure_class=None,
                             capability_status="unsupported", skill="data_analysis") == "capability_gap"


# --------------------------------------------------------------------------- 3. 图形独立复核


_R = 12


def _draw_cluster(canvas, x_center: int, y_center: int, count: int, radius: int = _R) -> None:
    """把 count 个圆画成一行、彼此**不接触**（每个圆是独立连通域）。"""
    import cv2

    for k in range(count):
        cx = x_center + (k - (count - 1) / 2) * (2 * radius + 5)
        cv2.circle(canvas, (int(cx), y_center), radius, 0, -1)


def _draw_strip(path: Path, counts: List[int], cell=140, gap=60) -> None:
    import cv2

    height = cell + 2 * gap
    width = len(counts) * cell + (len(counts) - 1) * gap + 2 * gap
    canvas = np.full((height, width), 255, np.uint8)
    for index, count in enumerate(counts):
        x0 = gap + index * (cell + gap)
        _draw_cluster(canvas, x0 + cell // 2, height // 2, count)
    cv2.imwrite(str(path), canvas)


def _draw_option_png(path: Path, count: int) -> None:
    import cv2

    canvas = np.full((160, 160), 255, np.uint8)
    _draw_cluster(canvas, 80, 80, count)
    cv2.imwrite(str(path), canvas)


def test_substitution_check_confirms_unique_count(tmp_path):
    stem = tmp_path / "stem.png"
    _draw_strip(stem, [1, 2, 3])
    options = []
    for label, count in (("A", 1), ("B", 2), ("C", 3), ("D", 4)):
        p = tmp_path / f"opt_{label}.png"
        _draw_option_png(p, count)
        options.append((label, str(p)))
    verdict = substitution_check(str(stem), options, "D", "count")
    assert verdict["verdict"] == "confirmed", verdict


def test_substitution_check_contradicts_wrong_candidate(tmp_path):
    stem = tmp_path / "stem.png"
    _draw_strip(stem, [1, 2, 3])
    options = []
    for label, count in (("A", 1), ("B", 2), ("C", 3), ("D", 4)):
        p = tmp_path / f"opt_{label}.png"
        _draw_option_png(p, count)
        options.append((label, str(p)))
    verdict = substitution_check(str(stem), options, "C", "count")
    assert verdict["verdict"] == "contradicted"
    assert any("个数" in reason for reason in verdict["reasons"])


def test_weak_constant_constraint_never_confirms(tmp_path):
    # 常数个数序列不构成判别性约束——不能借此"确认"任何选项（scale-27 类误放机制）
    stem = tmp_path / "stem.png"
    _draw_strip(stem, [1, 1, 1])
    options = []
    for label, count in (("A", 1), ("B", 1), ("C", 2), ("D", 1)):
        p = tmp_path / f"opt_{label}.png"
        _draw_option_png(p, count)
        options.append((label, str(p)))
    verdict = substitution_check(str(stem), options, "A", "count")
    assert verdict["verdict"] == "unverified"


def test_missing_candidate_crop_is_unverified(tmp_path):
    stem = tmp_path / "stem.png"
    _draw_strip(stem, [1, 2, 3])
    verdict = substitution_check(str(stem), [("A", str(tmp_path / "nope.png"))], "A", "count")
    assert verdict["verdict"] == "unverified"
    missing_stem = substitution_check(str(tmp_path / "missing_stem.png"), [], "A", "count")
    assert missing_stem["verdict"] == "unverified"


def test_bench_figure_false_accepts_are_blocked():
    # R2b 的 Dev 图形误放回归锚：错误候选必须被判 contradicted/unverified，绝不再 confirmed
    dev = {c.id: c for c in load_cases(DATA, "bench_dev")}
    expect = {
        "bi-fig-count-08": ("D", "contradicted"),
        "bi-fig-sides-09": ("D", "contradicted"),
        "bi-fig-count2-13": ("B", "contradicted"),
        "bi-fig-rot-10": ("D", "unverified"),
    }
    for case_id, (chosen, expected_verdict) in expect.items():
        case = dev[case_id]
        regions = case.regions or {}
        options = [(o["label"], o["crop_path"]) for o in regions.get("option_regions") or []
                   if o.get("crop_path")]
        result = substitution_check(
            regions["stem_matrix_crop"], options, chosen, None, grid=case.grid)
        assert result["verdict"] == expected_verdict, (case_id, result)


# --------------------------------------------------------------------------- 4. 应用题层


@pytest.mark.parametrize("stem,kind,value", [
    ("480 的 35% 是多少", "pct_of", 168.0),
    ("原价 260 元打八折出售，实际支付多少元", "discount", 208.0),
    ("1.5 小时行驶 90 千米，照这样计算，4 小时行驶多少千米", "speed_distance", 240.0),
    ("每千克苹果 8.5 元，买 6 千克需要多少元", "unit_price", 51.0),
    ("甲乙按 3:5 分配 1200 元，最多的一方得多少元", "ratio_share", 750.0),
    ("甲商品 500 元，比乙商品便宜 20%。乙商品价格是多少元？", "pct_reverse", 625.0),
])
def test_word_problem_plans(stem, kind, value):
    plan = extract_word_problem(stem)
    assert plan is not None and plan["kind"] == kind
    env, error = evaluate_plan(plan)
    assert error is None and env is not None
    assert abs(float(env[plan["final_var"]]) - value) < 1e-9


def test_word_problem_honest_refusal():
    # 支持范围外/信息不足：返回 None，上层保持诚实失败——不允许猜答案
    assert extract_word_problem("一个数既是 12 的倍数又是 18 的约数，这个数是多少") is None
    assert extract_word_problem("他为什么迟到了？") is None
    # 距离单位不在支持表（米）：不强行换算，也不猜答案 → None
    assert extract_word_problem("1.5 小时行驶 90 米，照这样 4 分钟走多少米") is None


def test_numeric_skill_word_fallback_offline():
    from assessment_agent.pipeline import Pipeline

    pipeline = Pipeline(Settings(offline=True))
    run = pipeline.run(
        text="甲商品 500 元，比乙商品便宜 20%。乙商品价格是多少元？",
        options=[QuestionOption(label="A", text="600"), QuestionOption(label="B", text="650"),
                 QuestionOption(label="C", text="625"), QuestionOption(label="D", text="750")],
        skill="numeric_calculation",
        save=False,
    )
    assert run.solution.status == "solved"
    assert run.solution.answer == "C"
    assert run.solution.method.startswith("word_problem:")
    assert run.verification.action == "accept"


# --------------------------------------------------------------------------- 5. 路由结构优先


def _router(structure: bool = True) -> Router:
    settings = Settings(offline=True, r2c_router_structure=structure)
    return Router(settings, llm=None)


def test_structure_beats_pattern_keywords():
    question = Question(
        stem="从所给的四个选项中，选择最合适的一个填入问号处，使之呈现一定的规律性",
        options=[QuestionOption(label="A", text=""), QuestionOption(label="B", text="")],
        meta={"vision": {
            "stem_matrix_crop": "x.png",
            "option_regions": [
                {"label": "A", "has_figure": True, "crop_path": "a.png"},
                {"label": "B", "has_figure": True, "crop_path": "b.png"},
                {"label": "C", "has_figure": True, "crop_path": "c.png"},
                {"label": "D", "has_figure": True, "crop_path": "d.png"},
            ],
        }},
    )
    decision = _router().route(question)
    assert decision.skill == "figure_reasoning"
    assert decision.source == "structure"
    # "规律"关键词与结构证据冲突 → 不允许伪装高置信
    assert decision.ambiguous is True
    assert decision.confidence <= 0.6


def test_table_structure_routes_data_analysis():
    question = Question(
        stem="根据下表回答问题。",
        options=[QuestionOption(label="A", text="930")],
        meta={"vision": {"table": {"bbox": [1, 2, 3, 4], "cells": [["a", "b"], ["1", "2"]]}}},
    )
    decision = _router().route(question)
    assert decision.skill == "data_analysis" and decision.source == "structure"


def test_blank_paren_with_wordy_options_not_number_series():
    question = Question(
        stem="他的发言获得了与会者一致的（ ），会场气氛由此变得热烈起来。",
        options=[QuestionOption(label="A", text="好评"), QuestionOption(label="B", text="赞赏"),
                 QuestionOption(label="C", text="批评"), QuestionOption(label="D", text="无视")],
    )
    decision = _router().route(question)
    assert decision.skill == "verbal_understanding"
    assert decision.confidence <= 0.6


def test_unknown_is_not_unsupported():
    question = Question(stem="hello world foo bar", options=[])
    decision = _router().route(question)
    assert decision.skill == "unknown"
    assert decision.classified is False
    assert decision.confidence == 0.0


def test_router_flag_disables_structure():
    settings = Settings(offline=True, r2c_router_structure=False)
    router = Router(settings, llm=None)
    question = Question(
        stem="按照呈现规律，选择下一个图形",
        options=[QuestionOption(label="A", text=""), QuestionOption(label="B", text="")],
        meta={"vision": {"stem_matrix_crop": "x.png", "option_regions": [
            {"label": "A", "has_figure": True, "crop_path": "a.png"},
            {"label": "B", "has_figure": True, "crop_path": "b.png"},
        ]}},
    )
    decision = router.route(question)
    assert decision.source != "structure"


# --------------------------------------------------------------------------- 6. unverified 传播


class _StubSkill:
    id = "figure_reasoning"

    def verify(self, question, solution, ctx):
        return Verification(passed=False, action="unverified",
                            checks=[], notes="独立复核无法确认")


class _StubRegistry:
    def get(self, skill_id):
        return _StubSkill() if skill_id == "figure_reasoning" else None


def test_main_verifier_propagates_unverified():
    from assessment_agent.verifier import Verifier

    settings = Settings(offline=True)
    verifier = Verifier(settings, registry=_StubRegistry())
    question = Question(stem="s", options=[QuestionOption(label="A", text="x")])
    solution = Solution(status="solved", skill="figure_reasoning", answer="A", confidence=0.9)
    result = verifier.verify(question, solution)
    assert result.action == "unverified"
    assert result.final_answer is None
    assert any(check.name == "verify.unverified_action" for check in result.checks)


# --------------------------------------------------------------------------- 7. 开关与卫生


def test_r2c_disable_env(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_R2C_DISABLE", "word_problem,figure_verify")
    settings = Settings.from_env()
    assert settings.r2c_word_problem is False
    assert settings.r2c_figure_verify is False
    assert settings.r2c_router_structure is True
    assert settings.describe()["r2c_flags"]["word_problem"] is False


def test_no_adapter_canvas_artifacts_in_data_dir():
    # adapter 合成画布只允许写系统临时目录，不得污染冻结数据集目录
    assert list(DATA.rglob("*_adapter_canvas.png")) == []


def test_figure_skill_flag_off_skips_independent_check():
    from assessment_agent.pipeline import Pipeline

    dev = {c.id: c for c in load_cases(DATA, "bench_dev")}
    case = dev["bi-fig-count-08"]
    settings = Settings(offline=True, r2c_figure_verify=False)
    pipeline = Pipeline(settings)
    run = pipeline.run(
        text=case.stem, image_path=case.image, options=case.options,
        skill="figure_reasoning", save=False,
        meta={"vision": case.regions, "grid": case.grid},
    )
    # 关掉独立复核 = R2b 行为：错误候选（fill→D）会被接受（误放机制复现，证明开关有效）
    assert run.solution.status == "solved"
    assert run.solution.answer == "D"
    assert run.verification.action == "accept"


def test_figure_skill_unverified_blocks_accept():
    from assessment_agent.pipeline import Pipeline

    dev = {c.id: c for c in load_cases(DATA, "bench_dev")}
    case = dev["bi-fig-count-08"]
    pipeline = Pipeline(Settings(offline=True))
    run = pipeline.run(
        text=case.stem, image_path=case.image, options=case.options,
        skill="figure_reasoning", save=False,
        meta={"vision": case.regions, "grid": case.grid},
    )
    assert run.verification.action == "reject"
    assert run.verification.final_answer is None
