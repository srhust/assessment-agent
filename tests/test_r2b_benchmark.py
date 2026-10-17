"""R2b 回归测试：失败归因、视觉指标口径、独立基准结构、区域降级拦截、批次隔离。

覆盖验收条款里的"新回归测试"六项：
1. 不完整区域 → 结构化 region_incomplete，绝不静默回退整图；
2. 错标选项 → option_mapping_error 归因；
3. 错误坐标/漏检 → IoU/Recall 分母保留漏检样本，不从评分中剔除；
4. 重复运行 → eval_batch 批次目录隔离，历史 runs 证据不被覆盖；
5. 漏检计分 → 缺失 extraction 全部按 0/失败计入；
6. 错误归因 → classify_* 的映射与"最上游失效优先"次序；
外加：undecidable 金标准的判分与作答归因、benchmark_real 的防泄漏/哈希/结构完整性。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from assessment_agent.config import LLMSettings, Settings
from assessment_agent.evaluation.diagnosis import classify_case_failure, classify_exception
from assessment_agent.evaluation.mock_vision import MockVisionClient, payload_from_record
from assessment_agent.evaluation.runner import Case, EvaluationRunner, load_cases
from assessment_agent.evaluation.vision_metrics import (
    aggregate_vision,
    bbox_iou,
    cer,
    char_edit_distance,
    compare_extraction_detail,
)
from assessment_agent.llm import OpenAICompatibleClient
from assessment_agent.pipeline import Pipeline
from assessment_agent.schemas import Question, QuestionOption
from assessment_agent.skills import SkillContext, SkillRegistry

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
BENCH = DATA_DIR / "benchmark_real"


def _bench_records(split: str) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in (BENCH / split / "independent.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------------------ 1. region_incomplete 拦截静默回退
def test_figure_skill_region_incomplete_blocks_full_image_fallback(tmp_path: Path):
    """vision meta 存在但选项裁剪不足 → 结构化失败，不回退整图旧求解器。"""
    registry = SkillRegistry()
    ctx = SkillContext(settings=Settings(offline=True, data_dir=DATA_DIR, results_dir=tmp_path / "results"))
    image = DATA_DIR / "screens" / "images" / "sch-train-figure-count.png"
    assert image.is_file()
    question = Question(
        stem="按规律，问号处应填入哪个图形？",
        skill="figure_reasoning",
        image_path=str(image),  # 有整图：旧逻辑会拿整图猜——现在必须被拦截
        source="image",
        meta={
            "vision": {
                "stem_matrix_crop": str(image),
                "option_regions": [  # 只有 1 个带图选项（<2）→ 证据不完整
                    {"label": "A", "has_figure": True, "crop_path": str(image)},
                ],
            }
        },
    )
    solution = registry.require("figure_reasoning").solve(question, ctx)
    assert solution.status == "failed"
    assert solution.answer is None
    assert solution.meta.get("reason") == "region_incomplete"
    assert "不回退整图" in (solution.error or "")


def test_figure_skill_legacy_no_vision_meta_still_allowed(tmp_path: Path):
    """无 vision meta 的旧输入（文本/grid 题图）保持兼容，不被 region 检查误伤。"""
    registry = SkillRegistry()
    ctx = SkillContext(settings=Settings(offline=True, data_dir=DATA_DIR, results_dir=tmp_path / "results"))
    image = DATA_DIR / "figure" / "images"
    candidates = sorted(image.glob("*.png"))
    assert candidates
    question = Question(stem="图形序列题", skill="figure_reasoning", image_path=str(candidates[0]), source="image")
    solution = registry.require("figure_reasoning").solve(question, ctx)
    assert not (solution.meta or {}).get("reason") == "region_incomplete"


# ------------------------------------------------------------------ 2/6. 归因映射
def _case(answer: Optional[str] = "A", skill: str = "figure_reasoning", expected: str = "solved") -> Case:
    return Case(id="x1", skill=skill, stem="s", answer=answer, expected_status=expected)


def test_classify_region_marker_first():
    result = classify_case_failure(
        case=_case(), mode="oracle_solver", correct=False, status="failed", action="reject",
        predicted=None, solution_answer=None,
        solution_error="图形区域证据不完整：缺少题干矩阵裁剪（不回退整图求解）",
        solution_meta={"reason": "region_incomplete"}, routed_skill="figure_reasoning",
    )
    assert result == "region_missing_or_shifted"


def test_classify_extraction_and_option_error_order():
    text_err = classify_case_failure(
        case=_case(), mode="image_only", correct=False, status="failed", action="reject",
        predicted=None, solution_answer=None, solution_error="", solution_meta={},
        routed_skill="figure_reasoning", extraction_present=True, extraction_stem_ok=False, option_map_ok=False,
    )
    assert text_err == "extraction_text_error"  # 文本错误比选项映射更上游
    option_err = classify_case_failure(
        case=_case(), mode="image_only", correct=False, status="failed", action="reject",
        predicted=None, solution_answer=None, solution_error="无解", solution_meta={},
        routed_skill="figure_reasoning", extraction_present=True, extraction_stem_ok=True, option_map_ok=False,
    )
    assert option_err == "option_mapping_error"


def test_classify_routing_and_unsupported():
    unsupported_answerable = classify_case_failure(
        case=_case(), mode="auto_router", correct=False, status="unsupported", action="reject",
        predicted=None, solution_answer=None, solution_error="", solution_meta={}, routed_skill="figure_reasoning",
    )
    assert unsupported_answerable == "unsupported_skill"
    wrong_route = classify_case_failure(
        case=_case(skill="number_series"), mode="auto_router", correct=False, status="failed", action="reject",
        predicted=None, solution_answer=None, solution_error="解析失败", solution_meta={}, routed_skill="figure_reasoning",
    )
    assert wrong_route == "routing_error"


def test_classify_verifier_false_reject_and_accept():
    false_reject = classify_case_failure(
        case=_case(answer="A"), mode="oracle_solver", correct=False, status="solved", action="reject",
        predicted=None, solution_answer="A", solution_error="", solution_meta={}, routed_skill="figure_reasoning",
    )
    assert false_reject == "verifier_false_reject"  # 答案与金标准一致却被拒
    false_accept = classify_case_failure(
        case=_case(answer="A"), mode="oracle_solver", correct=False, status="solved", action="accept",
        predicted="D", solution_answer="D", solution_error="", solution_meta={}, routed_skill="figure_reasoning",
    )
    assert false_accept == "verifier_false_accept"


def test_classify_undecidable_gold_answered_is_solver_error():
    """不可判定题被强制作答：最上游是 Solver 本应弃权 → solver_error（非误放）。"""
    result = classify_case_failure(
        case=_case(answer=None, expected="undecidable"), mode="oracle_solver", correct=False,
        status="solved", action="accept", predicted="B", solution_answer="B",
        solution_error="", solution_meta={}, routed_skill="figure_reasoning",
    )
    assert result == "solver_error"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("视觉解析调用失败: timeout", "model_api_error"),
        ("需要视觉模型但未配置 LLM_VISION_MODEL", "model_api_error"),
        ("视觉结构化解析失败: JSON 不合法", "vision_parse_error"),
        ("截图抽取未得到题干文本", "vision_parse_error"),
        ("截图不存在", "model_api_error"),
        ("其它未知异常", "pipeline_error"),
    ],
)
def test_classify_exception_mapping(message: str, expected: str):
    assert classify_exception(message) == expected


# ------------------------------------------------------------------ 3. CER / IoU / 漏检计分
@pytest.mark.parametrize(
    ("gold", "pred", "expected_distance"),
    [("123", "123", 0), ("123", "12", 1), ("123", "1234", 1), ("abc", "abd", 1), ("", "ab", 2)],
)
def test_cer_units(gold: str, pred: str, expected_distance: int):
    assert char_edit_distance(gold, pred) == expected_distance
    value = cer(gold, pred)
    if gold:
        assert value == pytest.approx(min(1.0, expected_distance / len(gold)))
    else:
        assert value == (1.0 if pred else 0.0)


def test_cer_ignores_whitespace():
    assert cer("计算 480 的 35%", "计算480的35%") == 0.0


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [([0, 0, 10, 10], [0, 0, 10, 10], 1.0), ([0, 0, 10, 10], [5, 0, 10, 10], 1 / 3), ([0, 0, 10, 10], [100, 100, 5, 5], 0.0)],
)
def test_bbox_iou(left: List[float], right: List[float], expected: float):
    assert bbox_iou(left, right) == pytest.approx(expected)


def test_missed_regions_stay_in_denominators():
    """预测漏掉题干矩阵与部分选项：IoU 记 0、Recall 掉、漏检标记置真——不得剔除。"""
    gold = {
        "stem_text": "按规律",
        "stem_matrix_bbox": [10, 10, 100, 100],
        "option_regions": [
            {"label": "A", "bbox": [0, 0, 20, 20], "text": "图"},
            {"label": "B", "bbox": [30, 0, 20, 20], "text": "图"},
        ],
    }

    class _FakeExtraction:
        stem = "按规律"
        options: List[Any] = []
        option_regions = [type("O", (), {"label": "A", "bbox": [0, 0, 20, 20], "text": ""})()]
        diagram_regions: List[Any] = []  # 漏检矩阵
        table_regions: List[Any] = []

    detail = compare_extraction_detail(gold, _FakeExtraction())
    assert detail["diagram_iou_mean"] == 0.0
    assert detail["diagram_iou_scored"] == 1
    assert detail["diagram_recall"] == 0.0
    assert detail["region_detection_failure"] is True
    assert detail["option_label_recall"] == 0.5
    assert detail["option_iou_scored"] == 2
    assert 0.0 < detail["option_iou_mean"] < 0.6  # 漏掉的 B 以 0 计入分母

    # extraction 为 None：全部按漏检计入
    miss = compare_extraction_detail(gold, None)
    assert miss["stem_exact"] is False and miss["stem_cer"] == 1.0
    assert miss["option_iou_mean"] == 0.0 and miss["option_iou_scored"] == 2
    assert miss["diagram_iou_mean"] == 0.0 and miss["diagram_iou_scored"] == 1
    assert miss["region_detection_failure"] is True

    agg = aggregate_vision([detail])
    assert agg["diagram_region_iou_mean"]["value"] == 0.0
    assert agg["diagram_region_iou_mean"]["denominator"] == 1
    assert agg["region_detection_failure_rate"]["value"] == 1.0


# ------------------------------------------------------------------ 4. 批次 run_id 隔离
def test_image_only_eval_batch_isolates_run_artifacts(tmp_path: Path):
    """同一题跑两个批次：runs 目录互不覆盖，历史证据保留。"""
    records = _bench_records("dev")
    fake = MockVisionClient({Path(r["image"]).stem: payload_from_record(r) for r in records})
    settings = Settings(
        offline=True,
        llm=LLMSettings(api_key="sk-offline-mock", vision_model="mock-vlm"),
        data_dir=DATA_DIR,
        results_dir=tmp_path / "results",
        runs_dir=tmp_path / "runs",
    )
    runner = EvaluationRunner(Pipeline(settings, llm=fake), data_dir=DATA_DIR, results_dir=tmp_path / "results")
    first = runner.run("bench_dev", eval_mode="image_only", save=True, run_name="b1", eval_batch="batch-one")
    second = runner.run("bench_dev", eval_mode="image_only", save=True, run_name="b2", eval_batch="batch-two")
    assert first.eval_batch == "batch-one" and second.eval_batch == "batch-two"
    preds1 = [json.loads(line) for line in (Path(first.output_dir) / "predictions.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    preds2 = [json.loads(line) for line in (Path(second.output_dir) / "predictions.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    refs1 = {row["run_ref"] for row in preds1 if row.get("run_ref")}
    refs2 = {row["run_ref"] for row in preds2 if row.get("run_ref")}
    assert refs1 and refs2 and not (refs1 & refs2), "不同批次必须落在不同的 run 目录"
    assert all(ref.startswith("e2e-batch-one-") for ref in refs1)
    for ref in refs1 | refs2:
        assert (tmp_path / "runs" / ref).is_dir()


# ------------------------------------------------------------------ 真实用量捕获接口
def test_usage_snapshot_and_delta():
    client = OpenAICompatibleClient(
        LLMSettings(api_key="sk-test", base_url="https://example.invalid/v1", vision_model="vision-x")
    )
    snapshot = client.usage_snapshot()
    assert snapshot["calls"] == 0
    assert snapshot["prompt_tokens"] == 0
    assert snapshot["model"] is None  # 未发起任何调用前不臆造模型名
    delta = EvaluationRunner._usage_delta(
        {"calls": 1, "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "model": ""},
        {"calls": 2, "prompt_tokens": 25, "completion_tokens": 7, "total_tokens": 32, "model": "vision-x"},
    )
    assert delta == {"calls": 1, "prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20, "model": "vision-x"}
    assert EvaluationRunner._usage_delta({}, {"calls": 3}) == {}


# ------------------------------------------------------------------ 8. benchmark_real 结构与防泄漏
def test_benchmark_structure_and_leakage_groups():
    dev, holdout = _bench_records("dev"), _bench_records("holdout")
    assert len(dev) + len(holdout) >= 30
    manifest = json.loads((BENCH / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["splits"]["holdout"]["frozen"] is True
    assert manifest["splits"]["dev"]["count"] == len(dev)
    assert manifest["splits"]["holdout"]["count"] == len(holdout)
    # 文件级 sha256 与 manifest 一致（冻结证据）
    for split in ("dev", "holdout"):
        path = BENCH / split / "independent.jsonl"
        assert manifest["splits"][split]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

    ids = [r["id"] for r in dev + holdout]
    assert len(set(ids)) == len(ids), "样本 id 必须唯一"
    groups: Dict[str, set] = {}
    for record in dev + holdout:
        groups.setdefault(record["group"], set()).add(record["image"].split("/")[2])
        for leak_id, leak_split in groups.items():
            assert len(leak_split) == 1, f"组 {leak_id} 跨 split → 泄漏"
    # 同组样本同 split 的机械校验（防泄漏分组声明与数据一致）
    for group, ids_in_group in manifest["leakage_groups"].items():
        splits = {("dev" if rid in [r["id"] for r in dev] else "holdout") for rid in ids_in_group}
        assert len(splits) == 1, f"manifest 组 {group} 跨 split"

    for record in dev + holdout:
        # 独立选择声明与来源/授权字段必须齐备
        assert record["source"]["type"] == "independent_synthetic"
        assert record["source"]["license"]
        assert "not filtered by solver" in record["source"]["selection_method"].lower()
        assert record["image_sha256"] == hashlib.sha256((DATA_DIR / record["image"]).read_bytes()).hexdigest()
        expected = record["expected_status"]
        assert expected in {"solved", "unsupported", "undecidable", "failed"}
        if expected in {"undecidable"}:
            assert record["answer"] is None, "undecidable 样本不得携带标准答案"
        elif expected == "solved" and record["answer"]:
            labels = [o["label"] for o in record["options"]]
            assert record["answer"] in labels
        regions = record["regions"]
        # crop_path 与 runner._load_regions 一致，按 jsonl 的 parent.parent 解析（= BENCH）
        for option in regions.get("option_regions") or []:
            if option.get("crop_path"):
                crop = BENCH / option["crop_path"]
                assert crop.is_file(), f"金标准裁剪缺失: {option['crop_path']}"
        if regions.get("stem_matrix_crop"):
            assert (BENCH / regions["stem_matrix_crop"]).is_file()

    # 未实现/超纲题型必须保留在基准里（证明"不按求解器结果筛选"）
    skills = {r["skill"] for r in dev + holdout}
    assert {"data_analysis", "verbal_understanding", "personality"} <= skills
    assert any(r["expected_status"] == "undecidable" for r in dev + holdout)


# ------------------------------------------------------------------ undecidable 判分链路（runner 级）
def test_undecidable_case_correct_when_system_abstains(tmp_path: Path):
    """金标准 undecidable：系统 failed/unsupported 记为正确；答案不计入可答分母。"""
    records = _bench_records("dev")
    fake = MockVisionClient({Path(r["image"]).stem: payload_from_record(r) for r in records})
    settings = Settings(
        offline=True,
        llm=LLMSettings(api_key="sk-offline-mock", vision_model="mock-vlm"),
        data_dir=DATA_DIR,
        results_dir=tmp_path / "results",
        runs_dir=tmp_path / "runs",
    )
    runner = EvaluationRunner(Pipeline(settings, llm=fake), data_dir=DATA_DIR, results_dir=tmp_path / "results")
    summary = runner.run("bench_dev", eval_mode="image_only", save=True, eval_batch="unit")
    ambig = next(r for r in summary.results if r.case_id == "bi-fig-ambig-12")
    assert ambig.expected_status == "undecidable"
    assert ambig.status in {"failed", "unsupported"} and ambig.correct is True
    e2e = summary.metrics["e2e"]
    assert e2e["answerable_coverage"] < 1.0  # 不可判定/未实现样本拉低可答率，符合真实
    assert e2e["answer_scored"] == sum(
        1 for r in summary.results if r.expected_status == "solved" and r.expected
    ), "可答题分母只含带标准答案的样本"
    assert "end_to_end_accuracy_mixed" in e2e and e2e.get("mixed_note")
    assert summary.metrics["failure_classes"], "失败必须逐类归因，不允许统一 pipeline_error"
    # 归因分布中不得出现大量未分类兜底
    assert summary.metrics["failure_classes"].get("pipeline_error") is None
