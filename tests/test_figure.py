"""图形推理测试：布局解析、特征提取、六类规律、Skill 端到端与诚实失败。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assessment_agent.schemas import Question, QuestionOption
from assessment_agent.skills import SkillContext, SkillRegistry
from assessment_agent.tools.figure_rules import FigureRuleTool, solve_figure
from assessment_agent.tools.figure_tools import FigureTools, analyze_layout, load_mask
from assessment_agent.verifier import Verifier

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
IMAGES = DATA_DIR / "figure" / "images"


def _case_index() -> dict:
    index = {}
    for split in ("train", "dev", "test"):
        for line in (DATA_DIR / split / "figure.jsonl").read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            index[record["id"]] = record
    return index


CASES = _case_index()


# ------------------------------------------------------------------ 布局与特征
def test_layout_splits_bands_and_cells():
    layout = analyze_layout(IMAGES / "count_train.png")
    assert [len(band["cells"]) for band in layout["bands"]] == [3, 4]
    assert layout["grid_regular"] is True
    assert layout["mode"] == "auto"


def test_explicit_grid_includes_blank_target():
    layout = analyze_layout(IMAGES / "overlay_train.png", grid=(4, 3))
    assert layout["mode"] == "explicit"
    assert [len(band["cells"]) for band in layout["bands"]] == [3, 3, 3, 3]
    blanks = [
        (band["index"], cell["column"])
        for band in layout["bands"]
        for cell in band["cells"]
        if cell["features"]["blank"]
    ]
    assert blanks == [(2, 2)]


def test_cell_features_count_sides_and_area():
    layout = analyze_layout(IMAGES / "sides_train.png")
    question = [cell["features"] for cell in layout["bands"][0]["cells"]]
    assert [feature["sides"] for feature in question] == [3, 4, 5]
    assert all(feature["shape_count"] == 1 for feature in question)

    layout = analyze_layout(IMAGES / "count_train.png")
    counts = [cell["features"]["shape_count"] for cell in layout["bands"][0]["cells"]]
    assert counts == [1, 2, 3]


def test_symmetry_metric_distinguishes_orientation():
    layout = analyze_layout(IMAGES / "symmetry_train.png")
    question = [cell["features"] for cell in layout["bands"][0]["cells"]]
    # 正立等腰三角形：垂直对称、不水平对称
    assert all(feature["vertical_symmetric"] for feature in question)
    assert not any(feature["horizontal_symmetric"] for feature in question)
    assert question[0]["symmetry"]["vertical"] > 0.9


def test_overlay_cells_with_two_blobs_are_merged_not_split():
    layout = analyze_layout(IMAGES / "overlay_train.png", grid=(4, 3))
    xor_cell = layout["bands"][0]["cells"][2]["features"]
    assert xor_cell["shape_count"] == 2  # 去同存异后是两个连通块
    # 但仍被归到同一列，没有被切成两格
    assert len(layout["bands"][0]["cells"]) == 3


# ---------------------------------------------------------------------- 规律
@pytest.mark.parametrize("case_id", [key for key in CASES if CASES[key]["expected_status"] == "solved"])
def test_dataset_cases_are_solved(case_id):
    record = CASES[case_id]
    result = solve_figure(str(DATA_DIR / record["image"]), grid=record.get("grid"))
    assert result["status"] == "solved", result["reason"]
    assert result["answer"] == record["answer"]
    assert result["rule"] in {"count", "sides", "fill", "centroid", "symmetry", "overlay"}


def test_each_rule_is_exercised_by_dataset():
    rules = set()
    for case_id, record in CASES.items():
        if record["expected_status"] != "solved":
            continue
        result = solve_figure(str(DATA_DIR / record["image"]), grid=record.get("grid"))
        rules.add(result["rule"])
    assert rules == {"count", "sides", "fill", "centroid", "symmetry", "overlay"}


def test_ambiguous_case_fails_without_guessing():
    record = CASES["fig-test-unresolved"]
    result = solve_figure(str(DATA_DIR / record["image"]), grid=record.get("grid"))
    assert result["status"] == "failed"
    assert result["answer"] is None
    assert result["candidates"], "失败时必须给出每条规律的具体原因"


def test_position_rule_requires_grid():
    record = CASES["fig-train-position"]
    strict = solve_figure(str(DATA_DIR / record["image"]), grid=record["grid"])
    loose = solve_figure(str(DATA_DIR / record["image"]))
    assert strict["status"] == "solved" and strict["rule"] == "centroid"
    assert loose["answer"] is None or loose["answer"] != strict["answer"]


def test_solver_is_stable_across_thresholds():
    for case_id, record in CASES.items():
        if record["expected_status"] != "solved":
            continue
        image = str(DATA_DIR / record["image"])
        otsu = solve_figure(image, grid=record.get("grid"))
        fixed = solve_figure(image, grid=record.get("grid"), threshold=127)
        assert otsu["answer"] == fixed["answer"], case_id


# ---------------------------------------------------------------- Skill 端到端
def _question(record: dict) -> Question:
    return Question(
        stem=record["stem"],
        options=[],
        skill="figure_reasoning",
        image_path=str(DATA_DIR / record["image"]),
        source="image",
        meta={"grid": record.get("grid")} if record.get("grid") else {},
    )


def test_skill_solves_and_verifier_accepts(settings):
    registry = SkillRegistry()
    ctx = SkillContext(settings=settings)
    record = CASES["fig-test-overlay"]
    question = _question(record)
    solution = registry.require("figure_reasoning").solve(question, ctx)
    assert solution.status == "solved"
    assert solution.answer == record["answer"]
    assert solution.meta["rule"] == "overlay"
    assert solution.steps and "网格解析" in solution.steps[0]

    verification = Verifier(settings, registry=registry).verify(question, solution, ctx)
    assert verification.passed is True
    assert verification.action == "accept"
    assert any(check.name == "figure.rerun_consistent" and check.passed for check in verification.checks)


def test_skill_fails_without_screenshot(settings):
    registry = SkillRegistry()
    ctx = SkillContext(settings=settings)
    question = Question(stem="下列图形序列中，符合规律的下一个图形是（ ）", skill="figure_reasoning")
    solution = registry.require("figure_reasoning").solve(question, ctx)
    assert solution.status == "failed"
    assert solution.answer is None
    assert "截图" in (solution.error or "")


def test_skill_reports_ambiguous_case_as_failed(settings):
    registry = SkillRegistry()
    ctx = SkillContext(settings=settings)
    question = _question(CASES["fig-test-unresolved"])
    solution = registry.require("figure_reasoning").solve(question, ctx)
    assert solution.status == "failed"
    assert solution.answer is None
    assert solution.meta["candidates"]


def test_skill_verifier_rejects_when_solution_status_is_failed(settings):
    registry = SkillRegistry()
    ctx = SkillContext(settings=settings)
    question = _question(CASES["fig-test-unresolved"])
    solution = registry.require("figure_reasoning").solve(question, ctx)
    verification = Verifier(settings, registry=registry).verify(question, solution, ctx)
    assert verification.passed is False
    assert verification.action == "reject"


# -------------------------------------------------------------------- 工具壳
def test_figure_rule_tool_validates_input():
    tool = FigureRuleTool()
    assert tool.available() is True
    with pytest.raises(Exception):
        tool.run(path="")
    payload = tool.run(path=str(IMAGES / "count_train.png"))
    assert payload["status"] == "solved"


def test_figure_tools_expose_real_and_planned_capabilities():
    tool = FigureTools()
    capabilities = tool.run(operation="capabilities")
    assert "analyze_layout" in capabilities["implemented"]
    assert "rotation_match" in capabilities["planned"]
    with pytest.raises(NotImplementedError):
        tool.run(operation="rotation_match")
