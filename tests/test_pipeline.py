"""端到端流水线与运行产物测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assessment_agent.artifacts import RunArtifacts, default_run_id
from assessment_agent.pipeline import Pipeline, PipelineError
from tests.conftest import FakeChatModel, make_settings


def test_text_pipeline_end_to_end_writes_artifacts(tmp_path: Path):
    settings = make_settings(tmp_path)
    pipeline = Pipeline(settings)
    result = pipeline.run(text="计算 (3.5 + 2.4) × 4 的结果是多少？", options={"A": "23.6", "B": "22.6"})
    assert result.solution.status == "solved"
    assert result.verification.passed is True
    assert result.verification.final_answer == "A"
    run_dir = Path(result.run_dir)
    for name in ("run.json", "question.json", "route.json", "solution.json", "verification.json", "trace.jsonl", "report.md"):
        assert (run_dir / name).is_file(), name
    trace_lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(trace_lines) >= 3
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "答案" in report and "校验明细" in report
    run_meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_meta["skill"] == "numeric_calculation"
    assert run_meta["verification"]["passed"] is True


def test_pipeline_parses_inline_options_and_series(tmp_path: Path):
    settings = make_settings(tmp_path)
    pipeline = Pipeline(settings)
    result = pipeline.run(text="数列 2, 5, 8, 11, ( ) 的下一项是？\nA. 13\nB. 14\nC. 15\nD. 16")
    assert [option.label for option in result.question.options] == ["A", "B", "C", "D"]
    assert result.route.skill == "number_series"
    assert result.solution.answer == "B"
    assert result.solution.confidence == pytest.approx(0.9)


def test_screenshot_flow_with_fake_vision_model(tmp_path: Path, sample_png: Path, screenshot_llm: FakeChatModel):
    settings = make_settings(
        tmp_path,
        {"LLM_API_KEY": "sk-test", "LLM_VISION_MODEL": "vision-test", "ASSESSMENT_OFFLINE": "1"},
    )
    pipeline = Pipeline(settings, llm=screenshot_llm)
    result = pipeline.run(image_path=str(sample_png), skill=None)
    assert result.extraction is not None
    assert result.extraction.stem.startswith("数列 2, 4, 8, 16")
    assert [option.label for option in result.question.options] == ["A", "B", "C", "D"]
    assert result.solution.status == "solved"
    assert result.solution.answer == "C"
    assert result.solution.answer_text == "32"
    run_dir = Path(result.run_dir)
    assert (run_dir / "extraction.json").is_file()
    assert (run_dir / "image" / "original.png").is_file()
    assert result.artifacts["image"].startswith("image/")
    extraction = json.loads((run_dir / "extraction.json").read_text(encoding="utf-8"))
    assert extraction["confidence"] == pytest.approx(0.92)
    assert screenshot_llm.calls and screenshot_llm.calls[0]["image_path"] == str(sample_png)


def test_screenshot_requires_vision_model(tmp_path: Path, sample_png: Path):
    settings = make_settings(tmp_path)
    pipeline = Pipeline(settings)
    with pytest.raises(PipelineError) as excinfo:
        pipeline.run(image_path=str(sample_png))
    assert "视觉模型" in str(excinfo.value)


def test_pipeline_rejects_unknown_skill_and_empty_input(tmp_path: Path):
    pipeline = Pipeline(make_settings(tmp_path))
    with pytest.raises(PipelineError):
        pipeline.run(text="随便", skill="not_a_skill")
    with pytest.raises(PipelineError):
        pipeline.run()


def test_unsupported_skill_returns_honest_result(tmp_path: Path):
    pipeline = Pipeline(make_settings(tmp_path))
    result = pipeline.run(text="依次填入下列横线处最恰当的一项是（ ）", skill="verbal_understanding")
    assert result.solution.status == "unsupported"
    assert result.solution.answer is None
    assert result.verification.passed is False
    assert result.verification.action == "reject"


def test_run_artifacts_helpers(tmp_path: Path):
    artifacts = RunArtifacts(tmp_path / "runs", run_id="fixed-id")
    saved = artifacts.save_image(b"\x89PNG\r\n", suffix="png")
    assert saved == artifacts.dir / "image" / "original.png"
    payload = artifacts.save_json("extra", {"a": 1})
    assert json.loads(payload.read_text(encoding="utf-8")) == {"a": 1}
    trace = artifacts.append_trace("hello", value=1)
    assert json.loads(trace.read_text(encoding="utf-8").splitlines()[0])["event"] == "hello"
    assert artifacts.relative(saved) == "image/original.png"


def test_default_run_id_shape():
    run_id = default_run_id()
    assert "_" in run_id
    assert len(run_id) > 15
