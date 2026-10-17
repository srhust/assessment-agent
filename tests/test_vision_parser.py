"""VisionParser 测试：区域协议校验、裁剪证据、兼容旧格式、结构化失败。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from PIL import Image, ImageDraw

from assessment_agent.vision_parser import VisionParseError, VisionParser
from tests.conftest import FakeChatModel


def _make_screenshot(path: Path) -> None:
    """400x300 灰度合成截图：2x2 矩阵 + 一行 4 个选项图形。"""
    image = Image.new("L", (400, 300), 255)
    draw = ImageDraw.Draw(image)
    # 题干图形矩阵：bbox [20, 40, 160, 120]，2x2 每格 80x60，格内画黑色方块
    for row in range(2):
        for col in range(2):
            x = 20 + col * 80 + 25
            y = 40 + row * 60 + 15
            draw.rectangle([x, y, x + 30, y + 30], outline=0, width=3)
    # 选项图形：一行 4 个，每个约 30x30，起点 y=200
    shapes = [
        lambda d, x, y: d.rectangle([x, y, x + 30, y + 30], outline=0, width=3),
        lambda d, x, y: d.ellipse([x, y, x + 30, y + 30], outline=0, width=3),
        lambda d, x, y: d.polygon([(x + 15, y), (x + 30, y + 30), (x, y + 30)], outline=0),
        lambda d, x, y: d.line([x, y, x + 30, y + 30], fill=0, width=3),
    ]
    for index, shape in enumerate(shapes):
        shape(draw, 210 + index * 45, 205)
    image.save(path)


PAYLOAD: Dict[str, Any] = {
    "stem_text": "下列图形中，符合规律的下一个图形是（ ）",
    "text_regions": [{"id": "t1", "bbox": [20, 10, 360, 25], "text": "下列图形中，符合规律的下一个图形是（ ）"}],
    "diagram_regions": [{"id": "d1", "role": "stem_matrix", "bbox": [20, 40, 160, 120], "grid": {"rows": 2, "cols": 2}}],
    "option_regions": [
        {"label": "A", "bbox": [205, 195, 40, 50], "text": "", "has_figure": True, "figure_bbox": [210, 205, 30, 30]},
        {"label": "B", "bbox": [250, 195, 40, 50], "text": "", "has_figure": True, "figure_bbox": [255, 205, 30, 30]},
        {"label": "C", "bbox": [295, 195, 40, 50], "text": "", "has_figure": True, "figure_bbox": [300, 205, 30, 30]},
        {"label": "D", "bbox": [340, 195, 40, 50], "text": "", "has_figure": True, "figure_bbox": [345, 205, 30, 30]},
    ],
    "table_regions": [],
    "reading_order": ["t1", "d1", "A", "B", "C", "D"],
    "question_type": "single_choice",
    "skill_hint": "figure_reasoning",
    "has_diagram": True,
    "confidence": 0.86,
}


@pytest.fixture
def screenshot(tmp_path: Path) -> Path:
    path = tmp_path / "question.png"
    _make_screenshot(path)
    return path


def test_parser_outputs_regions_and_crops(screenshot: Path, tmp_path: Path):
    client = FakeChatModel(payload=dict(PAYLOAD))
    parser = VisionParser(client, "vision-fake", crops_dir=tmp_path / "regions")
    result = parser.parse(screenshot)

    assert result.schema_version == 2
    assert result.parser == "vision_parser"
    assert result.stem == "下列图形中，符合规律的下一个图形是（ ）"
    assert [option.label for option in result.options] == ["A", "B", "C", "D"]
    assert result.image_width == 400 and result.image_height == 300
    assert result.confidence == pytest.approx(0.86)
    assert not [warning for warning in result.warnings if "已丢弃" in warning]

    # 像素证据：矩阵与 4 个选项都裁剪落盘
    matrix = result.diagram_regions[0]
    assert matrix.role == "stem_matrix" and matrix.grid is not None
    assert matrix.crop_path and Path(matrix.crop_path).is_file()
    for option in result.option_regions:
        assert option.crop_path and Path(option.crop_path).is_file()

    meta = result.vision_meta()
    assert meta["stem_matrix_grid"] == [2, 2]
    assert meta["bbox_format"] == "pixel_xywh"
    assert len(meta["option_regions"]) == 4


def test_parser_rejects_out_of_bounds_and_tiny_regions(screenshot: Path, tmp_path: Path):
    payload = dict(PAYLOAD)
    payload["text_regions"] = [
        {"id": "bad1", "bbox": [380, 10, 100, 20], "text": "越界"},  # x+w > 400
        {"id": "bad2", "bbox": [10, 10, 4, 4], "text": "过小"},
    ]
    client = FakeChatModel(payload=payload)
    result = VisionParser(client, "vision-fake", crops_dir=tmp_path / "regions").parse(screenshot)

    assert result.text_regions == []
    assert any("超出图像边界" in warning for warning in result.warnings)
    assert any("过小" in warning for warning in result.warnings)


def test_parser_drops_blank_diagram_region(screenshot: Path, tmp_path: Path):
    payload = dict(PAYLOAD)
    payload["diagram_regions"] = [
        {"id": "d1", "role": "stem_matrix", "bbox": [20, 40, 160, 120], "grid": {"rows": 2, "cols": 2}},
        {"id": "d2", "role": "other", "bbox": [20, 250, 100, 40]},  # 全白区域
    ]
    client = FakeChatModel(payload=payload)
    result = VisionParser(client, "vision-fake", crops_dir=tmp_path / "regions").parse(screenshot)

    assert len(result.diagram_regions) == 1
    assert any("d2" in warning and "无墨迹" in warning for warning in result.warnings)


def test_parser_deduplicates_option_labels(screenshot: Path, tmp_path: Path):
    payload = dict(PAYLOAD)
    payload["option_regions"] = list(PAYLOAD["option_regions"]) + [
        {"label": "A", "bbox": [100, 195, 40, 50], "text": "重复", "has_figure": False},
    ]
    client = FakeChatModel(payload=payload)
    result = VisionParser(client, "vision-fake", crops_dir=tmp_path / "regions").parse(screenshot)

    labels = [option.label for option in result.options]
    assert labels.count("A") == 1
    assert any("重复" in warning for warning in result.warnings)


def test_parser_keeps_payload_with_only_visible_text(screenshot: Path, tmp_path: Path):
    """只给文字不给区域也不虚构：stem_text 足够则成功，区域列表为空。"""
    payload = {"stem_text": "计算 15% × 240 的结果", "confidence": 0.9}
    client = FakeChatModel(payload=payload)
    result = VisionParser(client, "vision-fake", crops_dir=tmp_path / "regions").parse(screenshot)

    assert result.schema_version == 2
    assert result.stem == "计算 15% × 240 的结果"
    assert result.diagram_regions == [] and result.option_regions == []


def test_parser_structured_failure_without_content(screenshot: Path, tmp_path: Path):
    payload = {"stem_text": "", "text_regions": [], "diagram_regions": [], "option_regions": []}
    client = FakeChatModel(payload=payload)
    with pytest.raises(VisionParseError):
        VisionParser(client, "vision-fake", crops_dir=tmp_path / "regions").parse(screenshot)


def test_parser_legacy_payload_passthrough(screenshot: Path, tmp_path: Path):
    """模型返回旧版纯文本 schema → 兼容为 schema v1，不虚构区域。"""
    payload = {
        "stem": "数列 2, 4, 8, 16, ( ) 的下一项是？",
        "options": [{"label": "A", "text": "24"}, {"label": "C", "text": "32"}],
        "confidence": 0.9,
    }
    client = FakeChatModel(payload=payload)
    result = VisionParser(client, "vision-fake", crops_dir=tmp_path / "regions").parse(screenshot)

    assert result.schema_version == 1
    assert result.parser == "legacy_payload"
    assert result.stem.startswith("数列 2, 4, 8")
    assert result.vision_meta() == {}


def test_parser_image_only_requires_v2_flow(screenshot: Path, tmp_path: Path):
    """坐标换算规则：bbox 必须是像素 xywh，dict 形式 {x,y,w,h} 也接受。"""
    payload = dict(PAYLOAD)
    payload["diagram_regions"] = [
        {"id": "d1", "role": "stem_matrix", "bbox": {"x": 20, "y": 40, "w": 160, "h": 120}, "grid": {"rows": 2, "cols": 2}}
    ]
    client = FakeChatModel(payload=payload)
    result = VisionParser(client, "vision-fake", crops_dir=tmp_path / "regions").parse(screenshot)
    assert result.diagram_regions[0].bbox == [20, 40, 160, 120]
