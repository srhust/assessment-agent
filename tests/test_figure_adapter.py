"""图形布局适配层测试：区域裁剪 → 原规律引擎、标号映射、诚实失败、Skill 端到端。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import pytest

from assessment_agent.config import Settings
from assessment_agent.schemas import Question, QuestionOption
from assessment_agent.skills import SkillContext, SkillRegistry
from assessment_agent.tools.base import ToolError
from assessment_agent.tools.figure_adapter import solve_figure_regions
from assessment_agent.tools.figure_rules import solve_figure
from assessment_agent.tools.figure_tools import analyze_layout
from assessment_agent.verifier import Verifier
from tests.conftest import make_settings

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
IMAGES = DATA_DIR / "figure" / "images"


def _crop_gray(source: Path, box: Tuple[int, int, int, int], target: Path) -> Path:
    from PIL import Image

    with Image.open(source) as raw:
        raw.convert("L").crop(box).save(target)
    return target


def _split_question_and_options(image_path: Path, out_dir: Path) -> Tuple[str, List[Tuple[str, str]], List[int]]:
    """把一张旧版整图按布局切成"题干裁剪 + 选项裁剪"，模拟 VisionParser 的产物。"""
    layout = analyze_layout(str(image_path))
    bands = layout["bands"]
    width, height = layout["size"]
    question_bands, option_band = bands[:-1], bands[-1]
    y0 = min(int(band["bbox"][1]) for band in question_bands)
    y1 = max(int(band["bbox"][3]) for band in question_bands)
    stem_path = _crop_gray(image_path, (0, y0, width, y1), out_dir / "stem.png")
    crops: List[Tuple[str, str]] = []
    for cell in option_band["cells"]:
        x0, cy0, cx1, cy1 = (int(value) for value in cell["bbox"])
        label = chr(ord("A") + cell["column"])
        crop = _crop_gray(image_path, (x0, cy0, cx1, cy1), out_dir / f"option_{label}.png")
        crops.append((label, str(crop)))
    return str(stem_path), crops, [int(grid) for grid in (layout.get("grid") or [])]


def _split_explicit_grid(image_path: Path, out_dir: Path, grid: Tuple[int, int]) -> Tuple[str, List[Tuple[str, str]], Tuple[int, int]]:
    """显式整图网格 → 拆成"题干子网格 + 选项格裁剪"，验证 grid 不再覆盖选项。"""
    rows, cols = grid
    layout = analyze_layout(str(image_path), grid=grid)
    width, height = layout["size"]
    question_band_count = len(layout["bands"]) - 1
    cell_h = height / rows
    stem_path = _crop_gray(image_path, (0, 0, width, int(round(question_band_count * cell_h))), out_dir / "stem.png")
    option_band = layout["bands"][-1]
    crops: List[Tuple[str, str]] = []
    for cell in option_band["cells"]:
        x0, y0, x1, y1 = (int(value) for value in cell["bbox"])
        label = chr(ord("A") + cell["column"])
        crop = _crop_gray(image_path, (x0, y0, x1, y1), out_dir / f"option_{label}.png")
        crops.append((label, str(crop)))
    return str(stem_path), crops, (question_band_count, cols)


# ---------------------------------------------------------------------- 适配层
def test_adapter_reproduces_answer_for_sequence_case(tmp_path: Path):
    image = IMAGES / "count_train.png"
    legacy = solve_figure(str(image))
    assert legacy["status"] == "solved"

    stem, crops, _ = _split_question_and_options(image, tmp_path)
    result = solve_figure_regions(stem, crops, canvas_path=tmp_path / "canvas.png")
    assert result["status"] == "solved", result.get("reason")
    assert result["answer"] == legacy["answer"]
    assert result["rule"] == legacy["rule"] == "count"
    assert Path(result["adapter"]["canvas"]).is_file()  # 证据画布落盘
    assert result["adapter"]["option_labels"] == [label for label, _ in crops]


def test_adapter_keeps_option_label_mapping(tmp_path: Path):
    """选项按乱序/换标号传入时，答案必须返回**给定的标号**，而不是位置字母。"""
    image = IMAGES / "count_train.png"
    stem, crops, _ = _split_question_and_options(image, tmp_path)
    # 物理顺序不变，但标号整体偏移（B/C/D/E）
    relabeled = [(chr(ord(label) + 1), path) for label, path in crops]
    result = solve_figure_regions(stem, relabeled)
    assert result["status"] == "solved", result.get("reason")
    legacy = solve_figure(str(image))
    same_index = ord(legacy["answer"]) - ord("A")
    assert result["answer"] == relabeled[same_index][0]


def test_adapter_supports_explicit_stem_grid_without_full_image_grid(tmp_path: Path):
    """九宫格：grid 只作用于题干裁剪（3x3），选项不再被整图网格覆盖。"""
    image = IMAGES / "overlay_train.png"
    legacy = solve_figure(str(image), grid=(4, 3))
    assert legacy["status"] == "solved"

    stem, crops, stem_grid = _split_explicit_grid(image, tmp_path, (4, 3))
    assert stem_grid == (3, 3)
    result = solve_figure_regions(stem, crops, grid=stem_grid, canvas_path=tmp_path / "canvas.png")
    assert result["status"] == "solved", result.get("reason")
    assert result["answer"] == legacy["answer"]
    assert result["rule"] == "overlay"


def test_adapter_fails_honestly_on_blank_option(tmp_path: Path):
    image = IMAGES / "count_train.png"
    stem, crops, _ = _split_question_and_options(image, tmp_path)
    blank = _crop_gray(image, (0, 0, 40, 40), tmp_path / "blank.png")  # 全白区域
    crops[-1] = ("D", str(blank))
    result = solve_figure_regions(stem, crops)
    assert result["status"] == "failed"
    assert result["answer"] is None
    assert "空白" in result["reason"]


def test_adapter_rejects_bad_inputs(tmp_path: Path):
    image = IMAGES / "count_train.png"
    stem, crops, _ = _split_question_and_options(image, tmp_path)
    with pytest.raises(ToolError):
        solve_figure_regions(stem, crops[:1])  # 选项不足
    with pytest.raises(ToolError):
        solve_figure_regions(stem, [("A", crops[0][1]), ("A", crops[1][1])])  # 标号重复
    with pytest.raises(ToolError):
        solve_figure_regions(stem, [("A", str(tmp_path / "missing.png")), crops[1]])  # 裁剪缺失


# ------------------------------------------------------------------ Skill 端到端
def test_skill_region_end_to_end_with_verification(tmp_path: Path):
    """完整闭环：题干/选项裁剪 → Skill → 区域模式 Verifier（独立复算 + 标号校验）。"""
    image = IMAGES / "count_train.png"
    stem, crops, _ = _split_question_and_options(image, tmp_path)

    settings = make_settings(tmp_path)
    registry = SkillRegistry()
    ctx = SkillContext(settings=settings)
    question = Question(
        stem="下列图形中，符合规律的下一个图形是（ ）",
        options=[QuestionOption(label=label, text="图") for label, _ in crops],
        image_path=str(image),
        source="image",
        meta={
            "vision": {
                "bbox_format": "pixel_xywh",
                "image_size": [400, 300],
                "stem_matrix_bbox": [0, 0, 400, 150],
                "stem_matrix_grid": None,
                "stem_matrix_crop": stem,
                "option_regions": [
                    {"label": label, "has_figure": True, "crop_path": path, "bbox": [0, 0, 40, 50]}
                    for label, path in crops
                ],
            }
        },
    )

    solution = registry.require("figure_reasoning").solve(question, ctx)
    assert solution.status == "solved", solution.error
    assert solution.method == "figure_rules_adapter"
    assert solution.meta["regions"]["stem_crop"] == stem

    verifier = Verifier(settings, registry=registry)
    verification = verifier.verify(question, solution)
    names = [check.name for check in verification.checks]
    assert "figure.rerun_consistent" in names
    assert "figure.labels_from_regions" in names
    assert verification.passed is True, [c.detail for c in verification.checks if not c.passed]
    assert verification.action == "accept"
    assert verification.final_answer == solution.answer


def test_skill_falls_back_to_legacy_without_regions(tmp_path: Path):
    """没有区域信息 → 旧整图链路保持原行为（兼容性验收）。"""
    image = IMAGES / "count_train.png"
    settings = make_settings(tmp_path)
    registry = SkillRegistry()
    ctx = SkillContext(settings=settings)
    question = Question(
        stem="下列图形中，符合规律的下一个图形是（ ）",
        options=[QuestionOption(label=label, text="图") for label in "ABCD"],
        image_path=str(image),
        source="image",
        meta={},
    )
    solution = registry.require("figure_reasoning").solve(question, ctx)
    assert solution.status == "solved"
    assert solution.method == "figure_rules"  # 旧接口未破坏
    assert "regions" not in solution.meta
