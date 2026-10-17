"""截图基准离线/真实模型测试：三种评测输入模式与全链路 image_only。

- oracle_solver / auto_router：离线（无 API Key 也可跑），注入金标准区域，
  分别验证 Solver 本身与 Router+Solver；
- image_only：仅原始截图 → VisionParser → Router → Skill → Verifier。
  离线用 FakeChatModel（payload 由金标准区域构造，测试解析/适配层而非模型识别力）；
- real_vision_e2e：只有配置了 LLM_API_KEY + LLM_VISION_MODEL 才运行，
  未配置时明确 skip（不得把 Fake 结果冒充真实模型结果）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from assessment_agent.config import LLMSettings, Settings
from assessment_agent.evaluation.runner import EVAL_MODES, EvaluationRunner
from assessment_agent.llm import LLMError
from assessment_agent.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def _vision_configured() -> bool:
    if os.getenv("ASSESSMENT_OFFLINE") == "1":
        return False
    key = os.getenv("LLM_API_KEY") or ""
    vision = os.getenv("LLM_VISION_MODEL") or ""
    if not (key and vision):
        env_file = ROOT / ".env"
        if env_file.is_file():
            values = {}
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    values[k.strip()] = v.strip()
            key = key or values.get("LLM_API_KEY", "")
            vision = vision or values.get("LLM_VISION_MODEL", "")
    return bool(key and vision)


def _payload_from_case(case: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """金标准区域 → 视觉模型应输出的 v2 payload（测解析/适配层，不测 OCR 识别力）。"""
    regions = case.get("regions") or {}
    option_regions = [
        {
            "label": entry["label"],
            "bbox": entry["bbox"],
            "text": entry.get("text", ""),
            "has_figure": bool(entry.get("has_figure")),
            **({"figure_bbox": entry["figure_bbox"]} if entry.get("figure_bbox") else {}),
        }
        for entry in regions.get("option_regions") or []
    ]
    payload: Dict[str, Any] = {
        "stem_text": case["stem"],
        "text_regions": case.get("text_regions") or [],
        "option_regions": option_regions,
        "diagram_regions": [],
        "reading_order": regions.get("reading_order") or [],
        "question_type": "single_choice",
        "confidence": 0.9,
    }
    if regions.get("stem_matrix_bbox"):
        payload["diagram_regions"] = [
            {
                "id": "d1",
                "role": "stem_matrix",
                "bbox": regions["stem_matrix_bbox"],
                **({"grid": {"rows": regions["stem_matrix_grid"][0], "cols": regions["stem_matrix_grid"][1]}} if regions.get("stem_matrix_grid") else {}),
            }
        ]
    return payload


class _MultiImageFake:
    """按截图文件名返回各自的视觉 payload（模拟逐题识别，离线）。"""

    def __init__(self, payloads: Dict[str, Dict[str, Any]]):
        self.payloads = payloads
        self.calls: List[str] = []

    def chat(self, messages, **kwargs):
        raise AssertionError("Router/LLM 聊天不应在离线截图评测中被调用")

    def complete_json(self, system, user, image_path=None, model=None):
        name = Path(str(image_path)).stem
        self.calls.append(name)
        if name not in self.payloads:
            raise LLMError(f"离线 Fake 模型没有为 {name} 准备 payload")
        return self.payloads[name]


def _cases() -> List[Dict[str, Any]]:
    return [json.loads(line) for line in (DATA_DIR / "screens" / "screens.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------------------ 离线两模式
def test_screens_oracle_solver(tmp_path: Path):
    settings = Settings(offline=True, data_dir=DATA_DIR, results_dir=tmp_path / "results")
    runner = EvaluationRunner(Pipeline(settings), data_dir=DATA_DIR, results_dir=tmp_path / "results")
    summary = runner.run("screens", eval_mode="oracle_solver", save=True, run_name="oracle")
    assert "oracle_solver" in EVAL_MODES
    assert summary.metrics["answerable"] >= 7
    assert summary.metrics["answer_accuracy"] == 1.0, summary.metrics["failure_reasons"]
    assert summary.metrics["answerable_coverage"] <= 1.0
    assert summary.metrics["abstention_accuracy"] == 1.0
    summary_md = (Path(summary.output_dir) / "summary.md").read_text(encoding="utf-8")
    assert "N/A" in summary_md  # oracle 模式路由准确率必须显示 N/A


def test_screens_auto_router(tmp_path: Path):
    settings = Settings(offline=True, data_dir=DATA_DIR, results_dir=tmp_path / "results")
    runner = EvaluationRunner(Pipeline(settings), data_dir=DATA_DIR, results_dir=tmp_path / "results")
    summary = runner.run("screens", eval_mode="auto_router", save=True, run_name="auto")
    assert summary.metrics["answer_accuracy"] == 1.0, summary.metrics["failure_reasons"]
    # 8 道可路由样本全部路由正确；弃权样本由 Router 判为 unknown 也算正确处置
    assert summary.metrics["route_accuracy"] >= 8 / 9
    assert summary.metrics["failure_reasons"] == {}


def test_screens_image_only_with_fake_vision(tmp_path: Path):
    """image_only：只把截图交给 pipeline；Fake 模型提供区域 payload。"""
    cases = _cases()
    payloads = {}
    for case in cases:
        image_name = Path(case["image"]).stem
        payloads[image_name] = _payload_from_case(case)
    fake = _MultiImageFake(payloads)

    # 离线 Settings + 注入 Fake 视觉模型：vision_configured 打开解析路径，但不产生任何真实网络调用
    settings = Settings(
        offline=True,
        llm=LLMSettings(api_key="sk-offline-fake", vision_model="fake-vision"),
        data_dir=DATA_DIR,
        results_dir=tmp_path / "results",
        runs_dir=tmp_path / "runs",
    )
    pipeline = Pipeline(settings, llm=fake)
    runner = EvaluationRunner(pipeline, data_dir=DATA_DIR, results_dir=tmp_path / "results")
    summary = runner.run("screens", eval_mode="image_only", save=True, run_name="image_only")

    metrics = summary.metrics
    assert metrics["e2e"]["cases"] == len(cases)
    assert metrics["e2e"]["extraction_accuracy"] == 1.0, "Fake 场景题干解析必须与金标准一致"
    assert metrics["e2e"]["region_iou_mean"] == 1.0, "Fake 场景区域坐标必须与标注完全重合"
    assert metrics["e2e"]["option_mapping_accuracy"] == 1.0
    assert metrics["e2e"]["end_to_end_accuracy"] == 1.0, metrics["failure_reasons"]
    assert all(call in payloads for call in fake.calls) and len(fake.calls) == len(cases)

    # 裁剪证据 + 原图 + 抽取 JSON 均落盘可追溯（以图形样本为准）
    predictions = [json.loads(line) for line in (Path(summary.output_dir) / "predictions.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    assert len(predictions) == len(cases)
    figure_row = next(row for row in predictions if row["case_id"] == "sch-train-figure-count")
    assert figure_row.get("run_ref"), "image_only 必须记录 runs/ 产物目录"
    run_dir = tmp_path / "runs" / figure_row["run_ref"]
    crops = list((run_dir / "regions").glob("option_*.png"))
    assert len(crops) == 4, "图形样本应保存 4 份选项裁剪图"
    assert list((run_dir / "regions").glob("diagram_*.png")), "应保存题干矩阵裁剪"
    assert (run_dir / "extraction.json").is_file()
    assert (run_dir / "image" / "original.png").is_file(), "应保存原始截图副本"


# ------------------------------------------------------------------ 真实模型
@pytest.mark.skipif(not _vision_configured(), reason="未配置 LLM_API_KEY/LLM_VISION_MODEL，真实模型端到端测试未执行")
def test_real_vision_e2e_single_figure_case(tmp_path: Path):
    """真实视觉模型：单张图形矩阵截图走全链路（人工核对，不自动判分）。"""
    settings = Settings(data_dir=DATA_DIR, results_dir=tmp_path / "results")
    if not settings.llm.vision_configured:
        settings = settings.model_copy(update={"llm": settings.llm.model_copy(update={"vision_model": os.getenv("LLM_VISION_MODEL", settings.llm.vision_model or "")})})
    assert settings.llm.vision_configured
    pipeline = Pipeline(settings)
    image = DATA_DIR / "screens" / "images" / "sch-train-figure-count.png"
    run = pipeline.run(image_path=str(image), save=True, run_id="real-vision-e2e")
    # 真实模型识别能力不在断言中强求 100%：只断言链路结构完整
    assert run.extraction is not None
    assert run.extraction.parser in {"vision_parser", "legacy_payload"}
    assert run.solution is not None
    assert run.verification is not None
