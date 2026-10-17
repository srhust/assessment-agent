"""生成图形推理的合成数据集（可复现，不依赖任何外部素材）。

布局约定（与 ``tools/figure_tools.py`` 的解析一致）：
- 白色背景、黑色图形；
- 题干区在上：序列题一行 3 个已知图形 + 1 个空白目标格；九宫格题 3 行 × 3 格；
- 选项区紧接题干区下方、独立一行，从左到右对应 A、B、C…；
- 单元格 120×120、列间距 40、行间距 50、页边距 30（``Sheet``）；
- 位置类题目使用无页边距的等分网格（``BareSheet``），并在 JSONL 里显式给出 ``grid``，
  因为单个图形居中时无法从墨迹反推网格。

运行：``python scripts/generate_figure_dataset.py``
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
IMAGE_DIR = DATA_DIR / "figure" / "images"
CELL = 120
GAP = 40
BAND_GAP = 50
MARGIN = 30
CELLS_PER_ROW = 4


# ------------------------------------------------------------------ 画布
class Sheet:
    """带页边距的题图（单元格 120×120，列间距 40，行间距 50）。"""

    def __init__(self, bands: int, cells: int = CELLS_PER_ROW):
        width = 2 * MARGIN + cells * CELL + (cells - 1) * GAP
        height = 2 * MARGIN + bands * CELL + (bands - 1) * BAND_GAP
        self.image = Image.new("L", (width, height), color=255)
        self.draw = ImageDraw.Draw(self.image)

    def box(self, band: int, cell: int) -> Tuple[int, int, int, int]:
        x0 = MARGIN + cell * (CELL + GAP)
        y0 = MARGIN + band * (CELL + BAND_GAP)
        return (x0, y0, x0 + CELL, y0 + CELL)

    def save(self, name: str) -> str:
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.image.save(IMAGE_DIR / name)
        return f"figure/images/{name}"


class BareSheet:
    """无页边距的等分网格题图（用于显式 grid 的位置类题目）。"""

    def __init__(self, rows: int, cols: int, cell_w: int = 180, cell_h: int = 175):
        self.cell_w = cell_w
        self.cell_h = cell_h
        self.image = Image.new("L", (cols * cell_w, rows * cell_h), color=255)
        self.draw = ImageDraw.Draw(self.image)

    def box(self, band: int, cell: int) -> Tuple[int, int, int, int]:
        x0 = cell * self.cell_w
        y0 = band * self.cell_h
        return (x0, y0, x0 + self.cell_w, y0 + self.cell_h)

    def save(self, name: str) -> str:
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.image.save(IMAGE_DIR / name)
        return f"figure/images/{name}"


# ------------------------------------------------------------------ 基础图形
def draw_circles(draw: ImageDraw.ImageDraw, box: Sequence[int], count: int) -> None:
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if count <= 4:
        rows, cols = 1, max(1, count)
    else:
        cols = 3
        rows = math.ceil(count / cols)
    radius = 13 if count <= 4 else 9
    step_x = 2 * radius + 8
    step_y = 2 * radius + 10
    index = 0
    for row in range(rows):
        for col in range(cols):
            if index >= count:
                break
            px = cx + (col - (cols - 1) / 2) * step_x
            py = cy + (row - (rows - 1) / 2) * step_y
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], outline=0, width=3)
            index += 1


def _polygon_points(cx: float, cy: float, sides: int, radius: float, rotation: float = -90.0) -> List[Tuple[float, float]]:
    return [
        (
            cx + radius * math.cos(math.radians(rotation + 360.0 * index / sides)),
            cy + radius * math.sin(math.radians(rotation + 360.0 * index / sides)),
        )
        for index in range(sides)
    ]


def draw_polygon(draw: ImageDraw.ImageDraw, box: Sequence[int], sides: int) -> None:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    draw.polygon(_polygon_points(cx, cy, sides, 42.0), outline=0, width=3)


def draw_triangle(draw: ImageDraw.ImageDraw, box: Sequence[int], direction: str = "up") -> None:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    rotation = {"up": -90.0, "right": 0.0, "down": 90.0, "left": 180.0}[direction]
    draw.polygon(_polygon_points(cx, cy, 3, 42.0, rotation), outline=0, width=3)


def draw_bar(draw: ImageDraw.ImageDraw, box: Sequence[int], width: int, height: int) -> None:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    draw.rectangle([cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2], fill=0)


def draw_dot(draw: ImageDraw.ImageDraw, box: Sequence[int], fx: float, fy: float, radius: int = 16) -> None:
    x0, y0, x1, y1 = box
    px = x0 + fx * (x1 - x0)
    py = y0 + fy * (y1 - y0)
    draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=0)


def draw_slash(draw: ImageDraw.ImageDraw, box: Sequence[int]) -> None:
    x0, y0, x1, y1 = box
    draw.line([x0 + 28, y1 - 28, x1 - 28, y0 + 28], fill=0, width=4)


# ------------------------------------------------------------------ 叠加图形
def _shape_mask(kind: str, variant: int = 0) -> Image.Image:
    """单元格内的基础图形（L 模式，255=墨迹）。"""
    image = Image.new("L", (CELL, CELL), 0)
    painter = ImageDraw.Draw(image)
    if kind == "rect":
        if variant == 0:
            painter.rectangle([30, 28, 92, 92], fill=255)
        else:
            painter.rectangle([36, 34, 76, 96], fill=255)
    elif kind == "circle":
        if variant == 0:
            painter.ellipse([46, 36, 104, 94], fill=255)
        else:
            painter.ellipse([30, 44, 88, 102], fill=255)
    else:
        raise ValueError(f"未知基础图形: {kind}")
    return image


def _combo_mask(variant: int, operator: str) -> Image.Image:
    left = _shape_mask("rect", variant)
    right = _shape_mask("circle", variant)
    if operator == "xor":
        return ImageChops.logical_xor(left.convert("1"), right.convert("1")).convert("L")
    if operator == "or":
        return ImageChops.lighter(left, right)
    if operator == "and":
        return ImageChops.multiply(left, right)
    return left


def paste_cell(sheet: object, band: int, cell: int, mask_image: Image.Image) -> None:
    """把 CELL×CELL 的墨迹掩码贴进指定单元格（自动缩放到该格尺寸）。"""
    x0, y0, x1, y1 = sheet.box(band, cell)  # type: ignore[attr-defined]
    width, height = x1 - x0, y1 - y0
    scaled = mask_image.resize((width, height), Image.NEAREST)
    patch = Image.new("L", (width, height), 255)
    patch.paste(0, (0, 0), scaled)
    sheet.image.paste(patch, (x0, y0))  # type: ignore[attr-defined]


# ------------------------------------------------------------------ 题目构造
Case = Dict[str, object]


def sequence_case(
    stem: str,
    painters: Sequence[Callable[[ImageDraw.ImageDraw, Sequence[int]], None]],
    option_painters: Sequence[Callable[[ImageDraw.ImageDraw, Sequence[int]], None]],
    *,
    answer: str,
) -> Tuple[Sheet, Case]:
    sheet = Sheet(bands=2)
    for index, painter in enumerate(painters):
        painter(sheet.draw, sheet.box(0, index))
    for index, painter in enumerate(option_painters):
        painter(sheet.draw, sheet.box(1, index))
    return sheet, {"answer": answer, "stem": stem}


def grid_case(
    stem: str,
    rows: Sequence[Tuple[int, Optional[str]]],
    options: Sequence[Tuple[str, Optional[str]]],
    *,
    answer: str,
) -> Tuple[BareSheet, Case]:
    """九宫格：每行 (基础图形变体, 叠加算子)，算子为 None 表示目标格留空。

    使用无页边距的等分网格（3 列），整图可被 rows×3 精确等分，
    因此可以用显式 ``grid`` 让单元格边界与图形对齐；
    选项沿用目标行的图形变体，保证叠加结果的几何与目标一致。
    """
    sheet = BareSheet(rows=len(rows) + 1, cols=3, cell_w=150, cell_h=150)
    for row_index, (variant, operator) in enumerate(rows):
        paste_cell(sheet, row_index, 0, _shape_mask("rect", variant))
        paste_cell(sheet, row_index, 1, _shape_mask("circle", variant))
        if operator is not None:
            paste_cell(sheet, row_index, 2, _combo_mask(variant, operator))
    option_variant = rows[-1][0]
    option_band = len(rows)
    for cell_index, (kind, operator) in enumerate(options):
        mask = (
            _shape_mask(kind, option_variant)
            if operator is None
            else _combo_mask(option_variant, operator)
        )
        paste_cell(sheet, option_band, cell_index, mask)
    return sheet, {"answer": answer, "stem": stem}


def position_case(
    stem: str,
    known: Sequence[Tuple[float, float]],
    options: Sequence[Tuple[float, float]],
    *,
    answer: str,
) -> Tuple[BareSheet, Case]:
    """位置类题目：等分网格，题干 3 格 + 1 空，选项 4 格。"""
    sheet = BareSheet(rows=2, cols=4)
    for index, (fx, fy) in enumerate(known):
        draw_dot(sheet.draw, sheet.box(0, index), fx, fy)
    for index, (fx, fy) in enumerate(options):
        draw_dot(sheet.draw, sheet.box(1, index), fx, fy)
    return sheet, {"answer": answer, "stem": stem}


# ------------------------------------------------------------------ 数据集
def build_cases() -> Dict[str, List[Dict[str, object]]]:
    cases: Dict[str, List[Dict[str, object]]] = {"train": [], "dev": [], "test": []}
    stem_sequence = "观察图形的变化规律，选出括号处应填入的图形。"

    def add(
        split: str,
        case_id: str,
        sheet: object,
        name: str,
        meta: Case,
        *,
        status: str = "solved",
        grid: Optional[Sequence[int]] = None,
    ) -> None:
        image = sheet.save(name)  # type: ignore[attr-defined]
        record: Dict[str, object] = {
            "id": case_id,
            "skill": "figure_reasoning",
            "stem": meta["stem"],
            "image": image,
            "answer": meta["answer"],
            "expected_status": status,
        }
        if grid is not None:
            record["grid"] = list(grid)
        cases[split].append(record)

    # ---- 图形个数（等差） ----------------------------------------------
    sheet, meta = sequence_case(
        stem_sequence,
        [lambda d, b: draw_circles(d, b, 1), lambda d, b: draw_circles(d, b, 2), lambda d, b: draw_circles(d, b, 3)],
        [
            lambda d, b: draw_circles(d, b, 4),
            lambda d, b: draw_circles(d, b, 3),
            lambda d, b: draw_circles(d, b, 5),
            lambda d, b: draw_circles(d, b, 2),
        ],
        answer="A",
    )
    add("train", "fig-train-count", sheet, "count_train.png", meta)

    sheet, meta = sequence_case(
        stem_sequence,
        [lambda d, b: draw_circles(d, b, 2), lambda d, b: draw_circles(d, b, 4), lambda d, b: draw_circles(d, b, 6)],
        [
            lambda d, b: draw_circles(d, b, 6),
            lambda d, b: draw_circles(d, b, 8),
            lambda d, b: draw_circles(d, b, 10),
            lambda d, b: draw_circles(d, b, 3),
        ],
        answer="B",
    )
    add("test", "fig-test-count", sheet, "count_test.png", meta)

    # ---- 边数（等差） --------------------------------------------------
    sheet, meta = sequence_case(
        stem_sequence,
        [lambda d, b: draw_polygon(d, b, 3), lambda d, b: draw_polygon(d, b, 4), lambda d, b: draw_polygon(d, b, 5)],
        [
            lambda d, b: draw_polygon(d, b, 5),
            lambda d, b: draw_polygon(d, b, 7),
            lambda d, b: draw_polygon(d, b, 6),
            lambda d, b: draw_polygon(d, b, 4),
        ],
        answer="C",
    )
    add("train", "fig-train-sides", sheet, "sides_train.png", meta)

    sheet, meta = sequence_case(
        stem_sequence,
        [lambda d, b: draw_polygon(d, b, 4), lambda d, b: draw_polygon(d, b, 5), lambda d, b: draw_polygon(d, b, 6)],
        [
            lambda d, b: draw_polygon(d, b, 8),
            lambda d, b: draw_polygon(d, b, 7),
            lambda d, b: draw_polygon(d, b, 6),
            lambda d, b: draw_polygon(d, b, 5),
        ],
        answer="B",
    )
    add("test", "fig-test-sides", sheet, "sides_test.png", meta)

    # ---- 面积（等差） --------------------------------------------------
    sheet, meta = sequence_case(
        stem_sequence,
        [
            lambda d, b: draw_bar(d, b, 60, 40),
            lambda d, b: draw_bar(d, b, 60, 60),
            lambda d, b: draw_bar(d, b, 60, 80),
        ],
        [
            lambda d, b: draw_bar(d, b, 60, 100),
            lambda d, b: draw_bar(d, b, 60, 70),
            lambda d, b: draw_bar(d, b, 60, 115),
            lambda d, b: draw_bar(d, b, 60, 45),
        ],
        answer="A",
    )
    add("train", "fig-train-fill", sheet, "fill_train.png", meta)

    sheet, meta = sequence_case(
        stem_sequence,
        [
            lambda d, b: draw_bar(d, b, 40, 50),
            lambda d, b: draw_bar(d, b, 40, 70),
            lambda d, b: draw_bar(d, b, 40, 90),
        ],
        [
            lambda d, b: draw_bar(d, b, 40, 110),
            lambda d, b: draw_bar(d, b, 40, 60),
            lambda d, b: draw_bar(d, b, 40, 100),
            lambda d, b: draw_bar(d, b, 40, 45),
        ],
        answer="A",
    )
    add("dev", "fig-dev-fill", sheet, "fill_dev.png", meta)

    # ---- 位置平移（显式网格） ------------------------------------------
    sheet, meta = position_case(
        stem_sequence,
        [(0.2, 0.5), (0.4, 0.5), (0.6, 0.5)],
        [(0.8, 0.5), (0.5, 0.5), (0.95, 0.5), (0.3, 0.5)],
        answer="A",
    )
    add("train", "fig-train-position", sheet, "position_train.png", meta, grid=[2, 4])

    sheet, meta = position_case(
        stem_sequence,
        [(0.5, 0.2), (0.5, 0.4), (0.5, 0.6)],
        [(0.5, 0.8), (0.5, 0.5), (0.8, 0.5), (0.5, 0.3)],
        answer="A",
    )
    add("test", "fig-test-position", sheet, "position_test.png", meta, grid=[2, 4])

    # ---- 对称性（恒定 / 交替） -----------------------------------------
    sheet, meta = sequence_case(
        stem_sequence,
        [
            lambda d, b: draw_triangle(d, b, "up"),
            lambda d, b: draw_triangle(d, b, "up"),
            lambda d, b: draw_triangle(d, b, "up"),
        ],
        [
            lambda d, b: draw_triangle(d, b, "up"),
            lambda d, b: draw_circles(d, b, 1),
            lambda d, b: draw_triangle(d, b, "right"),
            lambda d, b: draw_slash(d, b),
        ],
        answer="A",
    )
    add("train", "fig-train-symmetry", sheet, "symmetry_train.png", meta)

    sheet, meta = sequence_case(
        stem_sequence,
        [
            lambda d, b: draw_circles(d, b, 1),
            lambda d, b: draw_slash(d, b),
            lambda d, b: draw_circles(d, b, 1),
        ],
        [
            lambda d, b: draw_circles(d, b, 1),
            lambda d, b: draw_triangle(d, b, "up"),
            lambda d, b: draw_triangle(d, b, "right"),
            lambda d, b: draw_slash(d, b),
        ],
        answer="D",
    )
    add("dev", "fig-dev-symmetry", sheet, "symmetry_dev.png", meta)

    # ---- 九宫格叠加：去同存异 / 取交集（显式网格） ---------------------
    sheet, meta = grid_case(
        stem_sequence,
        [(0, "xor"), (0, "xor"), (0, None)],
        [("rect", "xor"), ("rect", "or"), ("circle", "and")],
        answer="A",
    )
    add("train", "fig-train-overlay", sheet, "overlay_train.png", meta, grid=[4, 3])

    sheet, meta = grid_case(
        stem_sequence,
        [(1, "and"), (1, "and"), (1, None)],
        [("rect", "or"), ("rect", "xor"), ("circle", "and")],
        answer="C",
    )
    add("test", "fig-test-overlay", sheet, "overlay_test.png", meta, grid=[4, 3])

    # ---- 无法唯一确定（应明确失败，不给答案） --------------------------
    sheet, meta = sequence_case(
        stem_sequence,
        [
            lambda d, b: draw_circles(d, b, 1),
            lambda d, b: draw_circles(d, b, 3),
            lambda d, b: draw_circles(d, b, 2),
        ],
        [
            lambda d, b: draw_circles(d, b, 4),
            lambda d, b: draw_circles(d, b, 5),
            lambda d, b: draw_circles(d, b, 1),
            lambda d, b: draw_circles(d, b, 2),
        ],
        answer="",
    )
    add("test", "fig-test-unresolved", sheet, "unresolved_test.png", meta, status="failed")

    return cases


def main() -> None:
    cases = build_cases()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for split, records in cases.items():
        path = DATA_DIR / split / "figure.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"{split}: {len(records)} 条 → {path.relative_to(DATA_DIR)}")
    print(f"图片目录: {IMAGE_DIR.relative_to(DATA_DIR)}")


if __name__ == "__main__":
    main()
