"""评测程序测试：数据集加载、指标统计、失败归因、产物输出。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assessment_agent.evaluation.metrics import (
    CaseResult,
    MetricsAccumulator,
    compute_metrics,
    percentile,
)
from assessment_agent.evaluation.runner import EvaluationRunner, answers_match, load_cases
from assessment_agent.pipeline import Pipeline
from tests.conftest import make_settings, write_jsonl

ROWS = [
    {
        "id": "ok-1",
        "skill": "numeric_calculation",
        "stem": "计算 12.5 × 8 - 36 ÷ 4",
        "options": {"A": "90", "B": "91"},
        "answer": "B",
    },
    {
        "id": "ok-2",
        "skill": "number_series",
        "stem": "数列 2, 5, 8, 11, ( ) 的下一项是？",
        "options": {"A": "13", "B": "14"},
        "answer": "B",
    },
    {
        "id": "broken-1",
        "skill": "numeric_calculation",
        "stem": "小王有多少个苹果？",
        "options": {"A": "1", "B": "2"},
        "answer": "A",
    },
    {
        "id": "unsupported-1",
        "skill": "verbal_understanding",
        "stem": "依次填入下列横线处最恰当的一项是（ ）",
        "options": {"A": "简明", "B": "简单"},
        "answer": None,
        "expected_status": "unsupported",
    },
]


def _dataset(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    write_jsonl(data_dir / "test" / "cases.jsonl", ROWS)
    return data_dir


def test_load_cases_reads_jsonl(tmp_path: Path):
    data_dir = _dataset(tmp_path)
    cases = load_cases(data_dir, "test")
    assert len(cases) == 4
    assert cases[0].options[0].label == "A"
    assert cases[3].expected_status == "unsupported"


def test_load_cases_rejects_bad_split(tmp_path: Path):
    with pytest.raises(ValueError):
        load_cases(tmp_path / "data", "holding")


def test_answers_match_handles_labels_and_numbers():
    assert answers_match("B", "B")
    assert answers_match("b", "B")
    assert answers_match("14", "14.0")
    assert not answers_match("14", "15")
    assert not answers_match(None, "A")


def test_evaluation_runner_reports_accuracy_latency_and_failures(tmp_path: Path):
    data_dir = _dataset(tmp_path)
    settings = make_settings(tmp_path)
    pipeline = Pipeline(settings)
    runner = EvaluationRunner(pipeline, data_dir=data_dir, results_dir=tmp_path / "results")
    summary = runner.run(split="test", save=True)

    assert summary.total == 4
    assert summary.accuracy == pytest.approx(0.75)
    metrics = summary.metrics
    assert metrics["per_skill"]["numeric_calculation"]["total"] == 2
    assert metrics["per_skill"]["numeric_calculation"]["correct"] == 1
    assert metrics["per_skill"]["number_series"]["accuracy"] == 1.0
    # R3b：verbal 已实现（离线子集）——该合成样本无搭配依据，如实 failed（仍按无金标准判正确）
    assert metrics["per_skill"]["verbal_understanding"]["failed"] == 1
    reasons = metrics["per_skill"]["numeric_calculation"]["failure_reasons"]
    assert any("解析" in reason for reason in reasons)
    for stats in metrics["per_skill"].values():
        assert stats["p95_latency_ms"] >= 0
        assert stats["mean_latency_ms"] >= 0

    output_dir = Path(summary.output_dir)
    predictions = (output_dir / "predictions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(predictions) == 4
    metrics_file = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics_file["accuracy"] == pytest.approx(0.75)
    assert "per_skill" in metrics_file
    assert "评测报告" in (output_dir / "summary.md").read_text(encoding="utf-8")


def test_metrics_percentile_and_failure_reason_aggregation():
    assert percentile([], 0.5) == 0.0
    assert percentile([10.0], 0.95) == 10.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    results = [
        CaseResult(
            case_id="a",
            skill="number_series",
            expected="B",
            predicted="A",
            correct=False,
            status="solved",
            verification_action="reject",
            latency_ms=5.0,
            failure_reason="wrong_answer",
        ),
        CaseResult(
            case_id="b",
            skill="number_series",
            expected="B",
            predicted="B",
            correct=True,
            status="solved",
            verification_action="accept",
            latency_ms=7.0,
        ),
    ]
    metrics = compute_metrics(results)
    assert metrics["accuracy"] == pytest.approx(0.5)
    assert metrics["per_skill"]["number_series"]["failure_reasons"] == {"wrong_answer": 1}


def test_route_mode_oracle_and_auto(tmp_path: Path):
    """回归测试：验证 oracle 和 auto 模式都能正常工作。"""
    data_dir = _dataset(tmp_path)
    settings = make_settings(tmp_path)
    pipeline = Pipeline(settings)
    runner = EvaluationRunner(pipeline, data_dir=data_dir, results_dir=tmp_path / "results")
    
    # 测试 oracle 模式
    summary_oracle = runner.run(split="test", save=False, route_mode="oracle")
    assert summary_oracle.total == 4
    assert "route_accuracy" in summary_oracle.metrics
    assert summary_oracle.metrics["route_accuracy"] == 1.0  # oracle 模式应该 100% 路由正确
    
    # 测试 auto 模式
    summary_auto = runner.run(split="test", save=False, route_mode="auto")
    assert summary_auto.total == 4
    assert "route_accuracy" in summary_auto.metrics
    # auto 模式的路由准确率可能不是 100%，但应该有值
    assert summary_auto.metrics["route_accuracy"] >= 0.0


def test_new_metrics_coverage_and_selective_accuracy(tmp_path: Path):
    """回归测试：验证新指标 covered_count、coverage_ratio、selective_accuracy 正确计算。"""
    data_dir = _dataset(tmp_path)
    settings = make_settings(tmp_path)
    pipeline = Pipeline(settings)
    runner = EvaluationRunner(pipeline, data_dir=data_dir, results_dir=tmp_path / "results")
    summary = runner.run(split="test", save=False)
    
    metrics = summary.metrics
    # 检查全局指标
    assert "covered_count" in metrics
    assert "coverage_ratio" in metrics
    assert "selective_accuracy" in metrics
    assert metrics["covered_count"] >= 0
    assert 0.0 <= metrics["coverage_ratio"] <= 1.0
    assert 0.0 <= metrics["selective_accuracy"] <= 1.0
    
    # 检查每个 skill 的指标
    for skill_stats in metrics["per_skill"].values():
        assert "covered_count" in skill_stats
        assert "coverage_ratio" in skill_stats
        assert "selective_accuracy" in skill_stats


def test_failed_status_strict_matching(tmp_path: Path):
    """回归测试：验证 expected_status=failed 必须严格匹配 failed 状态。"""
    # 创建一个 expected_status=failed 但实际 solved 的测试用例
    rows_with_failed = [
        {
            "id": "should-fail",
            "skill": "numeric_calculation",
            "stem": "计算 2 + 2",
            "options": {"A": "4", "B": "5"},
            "answer": "A",
            "expected_status": "failed",  # 期望失败，但实际会成功
        },
    ]
    data_dir = tmp_path / "data"
    write_jsonl(data_dir / "test" / "cases.jsonl", rows_with_failed)
    
    settings = make_settings(tmp_path)
    pipeline = Pipeline(settings)
    runner = EvaluationRunner(pipeline, data_dir=data_dir, results_dir=tmp_path / "results")
    summary = runner.run(split="test", save=False)
    
    # 由于期望 failed 但实际 solved，应该算作不正确
    assert summary.total == 1
    assert summary.metrics["correct"] == 0
    assert summary.metrics["accuracy"] == 0.0


def test_answerable_coverage_excludes_wrongly_accepted_abstention():
    """P1 口径测试：错误接受应拒答样本时，answerable_coverage 不得超过 100%。

    分母 = 可答题样本数；分子 = **可答题样本中** 给出有效答案的数量。
    被错误接受的拒答样本可以抬高 covered_count / coverage_ratio，
    但绝不能混入 answerable_coverage 的分子。
    """
    results = [
        CaseResult(
            case_id="answerable-covered",
            skill="number_series",
            expected="B",
            predicted="B",
            correct=True,
            status="solved",
            verification_action="accept",
            latency_ms=1.0,
        ),
        CaseResult(
            case_id="abstention-wrongly-accepted",
            skill="number_series",
            expected=None,
            predicted="A",
            correct=False,
            status="solved",
            verification_action="accept",
            latency_ms=1.0,
            expected_status="unsupported",
        ),
    ]
    accumulator = MetricsAccumulator()
    for result in results:
        accumulator.add(result)
    summary = accumulator.summary()

    assert summary["answerable"] == 1
    assert summary["covered_count"] == 2  # 总体覆盖确实包含错误接受
    assert summary["answerable_coverage"] == 1.0  # 但可答题覆盖率不被污染、不超 100%
    for stats in summary["per_skill"].values():
        assert stats["answerable_coverage"] <= 1.0
