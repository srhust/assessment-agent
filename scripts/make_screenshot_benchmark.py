"""R2a 截图基准生成器：合成"招聘评估页面"截图 + 区域金标准 + 离线自校验。

产物（全部可复现，零网络、零外部素材）：
- ``data/screens/images/<id>.png``             原始截图（合成数据，非真实招聘平台）
- ``data/screens/regions/<id>/{stem,option_*}.png``  题干矩阵/选项图形的区域裁剪
- ``data/screens/screens.jsonl``               样本：题型/答案/区域坐标/布局来源

覆盖场景：纯文字题、数字序列、图形序列（含两行选项排布、九宫格叠加）、
缩放/低分辨率、干扰页眉页脚、诚实失败（无匹配规律）、弃权（未实现题型）。

每个图形/序列样本都会**先用生产 Solver/适配层自校验**再落盘：
若推断答案与标注不一致则整个生成失败退出——杜绝"意外构造的坏基准"。

运行：``python -X utf8 scripts/make_screenshot_benchmark.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import generate_figure_dataset as gfd  # noqa: E402
from assessment_agent.config import Settings  # noqa: E402
from assessment_agent.schemas import Question, QuestionOption  # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry  # noqa: E402
from assessment_agent.tools.figure_adapter import solve_figure_regions  # noqa: E402

DATA_DIR = ROOT / "data"
SCREEN_DIR = DATA_DIR / "screens"
IMAGE_DIR = SCREEN_DIR / "images"
REGION_DIR = SCREEN_DIR / "regions"
JSONL_PATH = SCREEN_DIR / "screens.jsonl"

PAGE_W = 880
PROVENANCE = "synthetic-generated (scripts/make_screenshot_benchmark.py, 非真实招聘平台截图)"

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _font_path() -> str:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise SystemExit("未找到可用中文字体（Windows 需在 C:\\Windows\\Fonts 下存在 msyh/simhei/simsun）")


FONT_PATH = _font_path()


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


# ------------------------------------------------------------------ 页面骨架
class Page:
    """一页评估截图：标题/干扰文本/题干/选项（文字或图形）分层绘制，坐标全部留档。"""

    def __init__(self, title: str, height: int, *, distractor: bool = False) -> None:
        self.image = Image.new("L", (PAGE_W, height), 255)
        self.draw = ImageDraw.Draw(self.image)
        self.width, self.height = PAGE_W, height
        self.warnings: List[str] = []
        self.cursor_y = 24
        self.text_regions: List[Dict[str, Any]] = []
        font_head, font_foot = _font(18), _font(15)
        header = f"2026 春季校园招聘统一测评 · 能力模块一 · 第 1 页（共 3 页）"
        box = self._line(40, self.cursor_y, header, font_head)
        if distractor:
            self.text_regions.append({"id": "hdr", "bbox": box, "text": header})
        self.cursor_y += 34
        footer = "机密 · 仅限本人作答 · 系统编号 SH2026-0001"
        fbox = self._line(40, height - 34, footer, font_foot)
        if distractor:
            self.text_regions.append({"id": "ftr", "bbox": fbox, "text": footer})

    def _line(self, x: int, y: int, text: str, font: ImageFont.FreeTypeFont) -> List[int]:
        self.draw.text((x, y), text, font=font, fill=0)
        left, top, right, bottom = self.draw.textbbox((x, y), text, font=font)
        return [left, top, max(1, right - left), max(1, bottom - top)]

    def stem(self, text: str, *, size: int = 22, x: int = 40, numbered: bool = True) -> List[int]:
        prefix = f"{self._number}. " if numbered else ""
        bbox = self._line(x, self.cursor_y, prefix + text, _font(size))
        if prefix:
            bbox = [x, bbox[1], bbox[2] + 0, bbox[3]]
        self.text_regions.append({"id": "stem", "bbox": bbox, "text": text})
        self.cursor_y += size + 26
        return bbox

    def text_options_row(self, texts: Sequence[str], *, per_line: int = 4, size: int = 20) -> List[Dict[str, Any]]:
        """文字选项：一行排 per_line 个（多行时自动换行），返回标号+区域。"""
        regions: List[Dict[str, Any]] = []
        labels = "ABCDEFGH"
        for start in range(0, len(texts), per_line):
            chunk = texts[start : start + per_line]
            x = 60
            y = self.cursor_y
            max_h = 0
            for offset, text in enumerate(chunk):
                label = labels[start + offset]
                item = f"{label}．{text}"
                bbox = self._line(x, y, item, _font(size))
                regions.append({"label": label, "bbox": bbox, "text": str(text), "has_figure": False})
                x = bbox[0] + bbox[2] + 46
                max_h = max(max_h, bbox[3])
            self.cursor_y += max_h + 24
        return regions

    def paste_block(self, block: Image.Image, *, ox: int, oy: int, labels: Optional[Sequence[str]] = None, label_size: int = 20) -> None:
        """把题图块贴进页面并记录标号位置（labels 提供时在每个单元左上角写标号）。"""
        self.image.paste(block, (ox, oy))
        self._block_ox, self._block_oy = ox, oy

    def block_label(self, index: int, total: int, block_w: int, block_h: int, ox: int, oy: int) -> None:
        pass

    _number = 1

    def set_number(self, value: int) -> None:
        self._number = value


# ------------------------------------------------------------------ 题图块
def _sequence_sheet(painters: Sequence[Callable[[ImageDraw.ImageDraw, Sequence[int]], None]], option_painters: Sequence[Callable]) -> gfd.Sheet:
    sheet = gfd.Sheet(bands=2)
    for index, painter in enumerate(painters):
        painter(sheet.draw, sheet.box(0, index))
    for index, painter in enumerate(option_painters):
        painter(sheet.draw, sheet.box(1, index))
    return sheet


def _two_row_options_sheet(painters: Sequence[Callable], option_painters: Sequence[Callable]) -> Image.Image:
    """题干一行（3 个图形） + 选项**两行×每行2个**的自定义题图块。"""
    cell, gap, margin = gfd.CELL, gfd.GAP, gfd.MARGIN
    band_h = cell + 40
    width = 2 * margin + 3 * cell + 2 * gap
    height = margin + cell + band_h + 2 * cell + gap + margin
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    for index, painter in enumerate(painters):
        box = (margin + index * (cell + gap), margin, margin + index * (cell + gap) + cell, margin + cell)
        painter(draw, box)
    for index, painter in enumerate(option_painters):
        row, col = divmod(index, 2)
        x = margin + col * (cell + gap)
        y = margin + cell + band_h + row * (cell + gap)
        painter(draw, (x, y, x + cell, y + cell))
    return image


def _cell_boxes(sheet_width: int) -> Dict[str, Any]:
    cell, gap, margin = gfd.CELL, gfd.GAP, gfd.MARGIN
    return {"cell": cell, "gap": gap, "margin": margin}


# ------------------------------------------------------------------ 案例构造
def _save_regions(case_id: str, stem_crop: Image.Image, option_crops: Sequence[Tuple[str, Image.Image]]) -> Dict[str, str]:
    directory = REGION_DIR / case_id
    directory.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, str] = {"stem": f"screens/regions/{case_id}/stem.png"}
    stem_crop.save(directory / "stem.png")
    for label, crop in option_crops:
        paths[label] = f"screens/regions/{case_id}/option_{label}.png"
        crop.save(directory / f"option_{label}.png")
    return paths


def _figure_case(
    page: Page,
    case_id: str,
    stem_text: str,
    sheet: Image.Image,
    *,
    stem_box: Tuple[int, int, int, int],
    option_boxes: Sequence[Tuple[str, Tuple[int, int, int, int]]],
    answer: str,
    grid: Optional[Tuple[int, int]],
    tags: List[str],
    expected_status: str = "solved",
    ox: int = 60,
    oy: Optional[int] = None,
    labels_font: int = 20,
) -> Dict[str, Any]:
    """把题图块贴进页面，区域坐标换算为页面坐标，裁剪留档并自校验。"""
    # 先画题干并推进游标，再把题图块贴在题干下方（否则文字会叠进题干图形区域）
    stem_bbox_record = _record_bbox(page, stem_text)
    paste_y = oy if oy is not None else page.cursor_y
    page.image.paste(sheet, (ox, paste_y))
    sheet_w, sheet_h = sheet.size
    stem_bbox = [ox + stem_box[0], paste_y + stem_box[1], stem_box[2] - stem_box[0], stem_box[3] - stem_box[1]]
    crop_dir = REGION_DIR / case_id
    crop_dir.mkdir(parents=True, exist_ok=True)
    stem_path = crop_dir / "stem.png"
    sheet.crop(stem_box).save(stem_path)

    option_regions: List[Dict[str, Any]] = []
    option_crops: List[Tuple[str, Image.Image]] = []
    labels_font_obj = _font(labels_font)
    for label, box in option_boxes:
        x0, y0, x1, y1 = box
        figure_bbox = [ox + x0, paste_y + y0, x1 - x0, y1 - y0]
        # 选项标号写在图形左侧（视觉布局的一部分，figure_bbox 不含标号）
        page.draw.text((ox + x0 - 34, y0 + paste_y + (y1 - y0) // 2 - 12), label, font=labels_font_obj, fill=0)
        crop = sheet.crop(box)
        crop.save(crop_dir / f"option_{label}.png")
        option_crops.append((label, crop))
        option_regions.append(
            {
                "label": label,
                "bbox": [figure_bbox[0] - 34, figure_bbox[1], figure_bbox[2] + 34, figure_bbox[3]],
                "text": "",
                "has_figure": True,
                "figure_bbox": figure_bbox,
                "crop_path": f"screens/regions/{case_id}/option_{label}.png",
            }
        )

    # ---------------- 自校验：必须用生产适配层推断出与标注一致的答案
    crops = [(label, str(crop_dir / f"option_{label}.png")) for label, _ in option_boxes]
    outcome = solve_figure_regions(str(stem_path), crops, grid=grid)
    if expected_status == "solved":
        assert outcome["status"] == "solved", f"{case_id} 自校验失败：{outcome.get('reason')}"
        assert outcome["answer"] == answer, f"{case_id} 自校验答案不一致：适配层 {outcome['answer']} vs 标注 {answer}"
    else:
        assert outcome["status"] == "failed", f"{case_id} 应为诚实失败，实际 {outcome}"

    page.cursor_y = paste_y + sheet.size[1] + 30
    return {
        "stem_bbox": stem_bbox_record,
        "regions": {
            "bbox_format": "pixel_xywh",
            "image_size": [page.width, page.height],
            "stem_matrix_bbox": stem_bbox,
            "stem_matrix_grid": list(grid) if grid else None,
            "stem_matrix_crop": f"screens/regions/{case_id}/stem.png",
            "option_regions": option_regions,
            "reading_order": ["stem", "d1"] + [label for label, _ in option_boxes],
        },
        "answer": answer,
        "tags": tags,
        "expected_status": expected_status,
        "option_crop_paths": [entry["crop_path"] for entry in option_regions],
    }


def _record_bbox(page: Page, text: str) -> List[int]:
    """把题干文本画出来并返回 bbox（页面坐标）。"""
    font = _font(22)
    x = 40
    y = page.cursor_y
    page.draw.text((x, y), text, font=font, fill=0)
    left, top, right, bottom = page.draw.textbbox((x, y), text, font=font)
    page.cursor_y += 22 + 26
    for region in page.text_regions:
        if region["id"] == "stem":
            region["bbox"] = [left, top, right - left, bottom - top]
            return region["bbox"]
    page.text_regions.append({"id": "stem", "bbox": [left, top, right - left, bottom - top], "text": text})
    return [left, top, right - left, bottom - top]


# ------------------------------------------------------------------ 具体样本
def build_cases() -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    cell, gap, margin = gfd.CELL, gfd.GAP, gfd.MARGIN

    def register(record: Dict[str, Any]) -> None:
        records.append(record)

    # ---------------- 1. 纯文字计算题（单行四选项 + 干扰页眉页脚）
    page = Page("", height=300, distractor=True)
    stem = "某部门面试 240 人，15% 进入终面。计算 240 的 15% 是多少？"
    page.set_number(1)
    page.text_regions.append({"id": "stem", "bbox": [0, 0, 1, 1], "text": stem})
    _record_bbox(page, f"1. {stem}")
    option_regions = page.text_options_row(["32", "36", "40", "48"])
    image_rel = "screens/images/sch-train-arith.png"
    page.image.save(IMAGE_DIR / "sch-train-arith.png")
    regions = {
        "bbox_format": "pixel_xywh",
        "image_size": [page.width, page.height],
        "stem_matrix_bbox": None,
        "stem_matrix_grid": None,
        "stem_matrix_crop": None,
        "option_regions": [{**entry, "crop_path": None} for entry in option_regions],
        "reading_order": ["stem"] + [entry["label"] for entry in option_regions],
    }
    register(
        {
            "id": "sch-train-arith",
            "skill": "numeric_calculation",
            "stem": stem,
            "options": [{"label": e["label"], "text": e["text"]} for e in option_regions],
            "answer": "B",
            "expected_status": "solved",
            "image": image_rel,
            "grid": None,
            "regions": regions,
            "text_regions": page.text_regions,
            "tags": ["synthetic", "text-only", "single-row-options", "distractor-page"],
        }
    )

    # ---------------- 2. 数字序列（几何倍增，双行文字选项）
    page = Page("", height=320, distractor=False)
    stem = "2, 4, 8, 16, ( ) 的下一项是？"
    page.set_number(2)
    page.text_regions.append({"id": "stem", "bbox": [0, 0, 1, 1], "text": stem})
    _record_bbox(page, f"2. {stem}")
    option_regions = page.text_options_row(["30", "31", "32", "34"], per_line=2)
    page.image.save(IMAGE_DIR / "sch-train-series-geo.png")
    register(
        {
            "id": "sch-train-series-geo",
            "skill": "number_series",
            "stem": stem,
            "options": [{"label": e["label"], "text": e["text"]} for e in option_regions],
            "answer": "C",
            "expected_status": "solved",
            "image": "screens/images/sch-train-series-geo.png",
            "grid": None,
            "regions": {
                "bbox_format": "pixel_xywh",
                "image_size": [page.width, page.height],
                "stem_matrix_bbox": None,
                "stem_matrix_grid": None,
                "stem_matrix_crop": None,
                "option_regions": [{**e, "crop_path": None} for e in option_regions],
                "reading_order": ["stem"] + [e["label"] for e in option_regions],
            },
            "text_regions": page.text_regions,
            "tags": ["synthetic", "text-only", "two-row-options"],
        }
    )

    # ---------------- 3. 数字序列（等差，单行选项，低分辨率）
    page = Page("", height=300, distractor=True)
    stem = "3, 10, 17, 24, ( ) 的下一项是？"
    page.set_number(3)
    page.text_regions.append({"id": "stem", "bbox": [0, 0, 1, 1], "text": stem})
    _record_bbox(page, f"3. {stem}")
    option_regions = page.text_options_row(["29", "30", "31", "33"])
    page.image.save(IMAGE_DIR / "sch-train-series-arith.png")
    register(
        {
            "id": "sch-train-series-arith",
            "skill": "number_series",
            "stem": stem,
            "options": [{"label": e["label"], "text": e["text"]} for e in option_regions],
            "answer": "C",
            "expected_status": "solved",
            "image": "screens/images/sch-train-series-arith.png",
            "grid": None,
            "regions": {
                "bbox_format": "pixel_xywh",
                "image_size": [page.width, page.height],
                "option_regions": [{**e, "crop_path": None} for e in option_regions],
                "reading_order": ["stem"] + [e["label"] for e in option_regions],
            },
            "text_regions": page.text_regions,
            "tags": ["synthetic", "text-only", "distractor-page"],
        }
    )

    # ---------------- 4. 图形序列（个数规律 1→2→3 → 4，一行四选项）
    page = Page("", height=560, distractor=False)
    stem = "下列图形中，符合规律的下一个图形是（ ）"
    page.set_number(4)
    painters = [lambda d, b, n=n: gfd.draw_circles(d, b, n) for n in (1, 2, 3)]
    option_painters = [
        lambda d, b: gfd.draw_circles(d, b, 2),
        lambda d, b: gfd.draw_circles(d, b, 3),
        lambda d, b: gfd.draw_circles(d, b, 5),
        lambda d, b: gfd.draw_circles(d, b, 4),
    ]
    sheet_obj = _sequence_sheet(painters, option_painters)
    sheet = sheet_obj.image
    boxes = [(chr(ord("A") + i), sheet_obj.box(1, i)) for i in range(4)]
    result = _figure_case(
        page,
        "sch-train-figure-count",
        stem,
        sheet,
        stem_box=(0, 0, sheet.size[0], margin + cell),
        option_boxes=boxes,
        answer="D",
        grid=None,
        tags=["synthetic", "figure-sequence", "count", "single-row-options"],
    )
    page.image.save(IMAGE_DIR / "sch-train-figure-count.png")
    register(
        {
            "id": "sch-train-figure-count",
            "skill": "figure_reasoning",
            "stem": stem,
            "options": [{"label": label, "text": "图"} for label, _ in boxes],
            "answer": result["answer"],
            "expected_status": "solved",
            "image": "screens/images/sch-train-figure-count.png",
            "grid": None,
            "regions": result["regions"],
            "text_regions": [{"id": "stem", "bbox": result["stem_bbox"], "text": stem}],
            "tags": result["tags"],
        }
    )

    # ---------------- 5. 图形序列（边数 3→4→5 → 6，两行选项布局）
    page = Page("", height=760, distractor=False)
    stem = "下列图形中，符合规律的下一个图形是（ ）"
    page.set_number(5)
    painters = [lambda d, b, n=n: gfd.draw_polygon(d, b, n) for n in (3, 4, 5)]
    option_painters = [
        lambda d, b: gfd.draw_polygon(d, b, 8),
        lambda d, b: gfd.draw_polygon(d, b, 6),
        lambda d, b: gfd.draw_polygon(d, b, 5),
        lambda d, b: gfd.draw_polygon(d, b, 7),
    ]
    block = _two_row_options_sheet(painters, option_painters)
    w0 = margin
    h_band = cell + 40
    boxes = []
    for index in range(4):
        row, col = divmod(index, 2)
        x = margin + col * (cell + gap)
        y = margin + cell + h_band + row * (cell + gap)
        boxes.append((chr(ord("A") + index), (x, y, x + cell, y + cell)))
    result = _figure_case(
        page,
        "sch-dev-figure-sides",
        stem,
        block,
        stem_box=(0, 0, block.width, margin + cell),
        option_boxes=boxes,
        answer="B",
        grid=None,
        tags=["synthetic", "figure-sequence", "sides", "two-row-options"],
    )
    page.image.save(IMAGE_DIR / "sch-dev-figure-sides.png")
    register(
        {
            "id": "sch-dev-figure-sides",
            "skill": "figure_reasoning",
            "stem": stem,
            "options": [{"label": label, "text": "图"} for label, _ in boxes],
            "answer": result["answer"],
            "expected_status": "solved",
            "image": "screens/images/sch-dev-figure-sides.png",
            "grid": None,
            "regions": result["regions"],
            "text_regions": [{"id": "stem", "bbox": result["stem_bbox"], "text": stem}],
            "tags": result["tags"],
        }
    )

    # ---------------- 6. 九宫格叠加（显式题干网格，选项带空白干扰）
    page = Page("", height=920, distractor=True)
    stem = "九宫格中问号处应填入哪个图形？"
    page.set_number(6)
    sheet = gfd.BareSheet(rows=4, cols=3, cell_w=150, cell_h=150)
    for row_index, (variant, operator) in enumerate([(0, "xor"), (1, "xor"), (2, None)]):
        gfd.paste_cell(sheet, row_index, 0, gfd._shape_mask("rect", variant))
        gfd.paste_cell(sheet, row_index, 1, gfd._shape_mask("circle", variant))
        if operator is not None:
            gfd.paste_cell(sheet, row_index, 2, gfd._combo_mask(variant, operator))
    option_variant = 2
    option_defs = [("rect", None), ("circle", None), ("rect", "xor"), ("rect", "or")]
    for cell_index, (kind, operator) in enumerate(option_defs):
        mask = gfd._shape_mask(kind, option_variant) if operator is None else gfd._combo_mask(option_variant, operator)
        gfd.paste_cell(sheet, 3, cell_index % 3, mask) if cell_index < 3 else None
    # 4 个选项排不进 3 列：改为选项排布在第四行的 3 列 + 单独补第 4 个（两行布局）
    option_blocks: List[Tuple[str, Tuple[int, int, int, int]]] = []
    for cell_index, (kind, operator) in enumerate(option_defs):
        mask = gfd._shape_mask(kind, option_variant) if operator is None else gfd._combo_mask(option_variant, operator)
        if cell_index < 3:
            x = cell_index * 150
            y = 3 * 150
        else:
            x = 0
            y = 4 * 150
        sheet.image.paste(Image.new("L", (150, 150), 255), (x, y))
        patch = mask.resize((150, 150), Image.NEAREST)
        white = Image.new("L", (150, 150), 255)
        white.paste(0, (0, 0), patch)
        sheet.image.paste(white, (x, y))
        option_blocks.append((chr(ord("A") + cell_index), (x, y, x + 150, y + 150)))
    full = sheet.image
    # 扩展画布容纳第 4 个选项行
    canvas = Image.new("L", (full.width, 5 * 150), 255)
    canvas.paste(full.crop((0, 0, full.width, 4 * 150)), (0, 0))
    for label, (x0, y0, x1, y1) in option_blocks:
        if y0 >= 4 * 150:
            src_index = ord(label) - ord("A")
            kind, operator = option_defs[src_index]
            mask = gfd._shape_mask(kind, option_variant) if operator is None else gfd._combo_mask(option_variant, operator)
            white = Image.new("L", (150, 150), 255)
            white.paste(0, (0, 0), mask.resize((150, 150), Image.NEAREST))
            canvas.paste(white, (x0, y0))
    result = _figure_case(
        page,
        "sch-test-figure-grid",
        stem,
        canvas,
        stem_box=(0, 0, 3 * 150, 3 * 150),
        option_boxes=option_blocks,
        answer="C",
        grid=(3, 3),
        tags=["synthetic", "figure-grid", "overlay", "explicit-stem-grid", "wrapped-options"],
    )
    page.image.save(IMAGE_DIR / "sch-test-figure-grid.png")
    register(
        {
            "id": "sch-test-figure-grid",
            "skill": "figure_reasoning",
            "stem": stem,
            "options": [{"label": label, "text": "图"} for label, _ in option_blocks],
            "answer": result["answer"],
            "expected_status": "solved",
            "image": "screens/images/sch-test-figure-grid.png",
            "grid": [3, 3],
            "regions": result["regions"],
            "text_regions": [{"id": "stem", "bbox": result["stem_bbox"], "text": stem}],
            "tags": result["tags"],
        }
    )

    # ---------------- 7. 缩放版图形序列（低分辨率 55%，最近邻保持硬边缘）
    source_path = IMAGE_DIR / "sch-train-figure-count.png"
    small_id = "sch-dev-figure-count-lowres"
    with Image.open(source_path) as raw:
        small = raw.resize((raw.width * 55 // 100, raw.height * 55 // 100), Image.NEAREST)
    small.save(IMAGE_DIR / f"{small_id}.png")
    scaled_regions = json.loads(json.dumps(records[3]["regions"]))
    scale = 55 / 100.0

    def _scale_bbox(bbox: Optional[List[int]]) -> Optional[List[int]]:
        if bbox is None:
            return None
        return [round(bbox[0] * scale), round(bbox[1] * scale), max(2, round(bbox[2] * scale)), max(2, round(bbox[3] * scale))]

    scaled_regions["image_size"] = [small.width, small.height]
    scaled_regions["stem_matrix_bbox"] = _scale_bbox(scaled_regions.get("stem_matrix_bbox"))
    directory = REGION_DIR / small_id
    directory.mkdir(parents=True, exist_ok=True)
    sb = scaled_regions["stem_matrix_bbox"]
    small.crop((sb[0], sb[1], sb[0] + sb[2], sb[1] + sb[3])).save(directory / "stem.png")
    scaled_regions["stem_matrix_crop"] = f"screens/regions/{small_id}/stem.png"
    for entry in scaled_regions["option_regions"]:
        fb = _scale_bbox(entry.get("figure_bbox"))
        entry["figure_bbox"] = fb
        entry["bbox"] = _scale_bbox(entry["bbox"])
        small.crop((fb[0], fb[1], fb[0] + fb[2], fb[1] + fb[3])).save(directory / f"option_{entry['label']}.png")
        entry["crop_path"] = f"screens/regions/{small_id}/option_{entry['label']}.png"
    # 自校验：低分辨率裁剪也必须能被适配层解出同一答案
    crops = [(entry["label"], str(ROOT / "data" / entry["crop_path"])) for entry in scaled_regions["option_regions"]]
    outcome = solve_figure_regions(str(ROOT / "data" / scaled_regions["stem_matrix_crop"]), crops)
    assert outcome["status"] == "solved" and outcome["answer"] == records[3]["answer"], (
        f"低分辨率自校验失败：{outcome.get('reason')}"
    )
    register(
        {
            "id": small_id,
            "skill": "figure_reasoning",
            "stem": records[3]["stem"],
            "options": records[3]["options"],
            "answer": records[3]["answer"],
            "expected_status": "solved",
            "image": f"screens/images/{small_id}.png",
            "grid": None,
            "regions": scaled_regions,
            "text_regions": [{"id": "stem", "bbox": _scale_bbox(records[3]["text_regions"][0]["bbox"]), "text": records[3]["stem"]}],
            "tags": ["synthetic", "scaled-55pct", "low-resolution", "same-source-as:sch-train-figure-count"],
        }
    )

    # ---------------- 8. 诚实失败：图形序列但无选项匹配任何规律
    page = Page("", height=560, distractor=False)
    stem = "下列图形中，符合规律的下一个图形是（ ）"
    page.set_number(8)
    painters = [lambda d, b, n=n: gfd.draw_circles(d, b, n) for n in (1, 2, 3)]
    option_painters = [
        lambda d, b: gfd.draw_circles(d, b, 1),
        lambda d, b: gfd.draw_circles(d, b, 2),
        lambda d, b: gfd.draw_circles(d, b, 5),
        lambda d, b: gfd.draw_polygon(d, b, 6),
    ]
    sheet_obj = _sequence_sheet(painters, option_painters)
    sheet = sheet_obj.image
    boxes = [(chr(ord("A") + i), sheet_obj.box(1, i)) for i in range(4)]
    result = _figure_case(
        page,
        "sch-test-figure-unmatched",
        stem,
        sheet,
        stem_box=(0, 0, sheet.size[0], margin + cell),
        option_boxes=boxes,
        answer=None,
        grid=None,
        tags=["synthetic", "figure-sequence", "honest-failure"],
        expected_status="failed",
    )
    page.image.save(IMAGE_DIR / "sch-test-figure-unmatched.png")
    register(
        {
            "id": "sch-test-figure-unmatched",
            "skill": "figure_reasoning",
            "stem": stem,
            "options": [{"label": label, "text": "图"} for label, _ in boxes],
            "answer": None,
            "expected_status": "failed",
            "image": "screens/images/sch-test-figure-unmatched.png",
            "grid": None,
            "regions": result["regions"],
            "text_regions": [{"id": "stem", "bbox": result["stem_bbox"], "text": stem}],
            "tags": result["tags"],
        }
    )

    # ---------------- 9. 弃权样本：数据分析（未实现 Skill）
    page = Page("", height=320, distractor=True)
    stem = "根据下方统计表，销售额增长最快的季度是哪个季度？"
    page.set_number(9)
    page.text_regions.append({"id": "stem", "bbox": [0, 0, 1, 1], "text": stem})
    _record_bbox(page, f"9. {stem}")
    option_regions = page.text_options_row(["第一季度", "第二季度", "第三季度", "第四季度"])
    page.image.save(IMAGE_DIR / "sch-dev-unsupported-chart.png")
    register(
        {
            "id": "sch-dev-unsupported-chart",
            "skill": "data_analysis",
            "stem": stem,
            "options": [{"label": e["label"], "text": e["text"]} for e in option_regions],
            "answer": None,
            "expected_status": "unsupported",
            "image": "screens/images/sch-dev-unsupported-chart.png",
            "grid": None,
            "regions": {
                "bbox_format": "pixel_xywh",
                "image_size": [page.width, page.height],
                "option_regions": [{**e, "crop_path": None} for e in option_regions],
                "reading_order": ["stem"] + [e["label"] for e in option_regions],
            },
            "text_regions": page.text_regions,
            "tags": ["synthetic", "abstention", "unsupported-skill", "distractor-page"],
        }
    )

    return records


# ------------------------------------------------------------------ 自校验（序列/计算）
def _selfcheck_text_cases() -> None:
    settings = Settings(offline=True)
    registry = SkillRegistry()
    ctx = SkillContext(settings=settings)
    checks = {
        "sch-train-arith": ("numeric_calculation", "B"),
        "sch-train-series-geo": ("number_series", "C"),
        "sch-train-series-arith": ("number_series", "C"),
    }
    for line in JSONL_PATH.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        case_id = record["id"]
        if case_id not in checks:
            continue
        skill_id, expected = checks[case_id]
        question = Question(
            stem=record["stem"],
            options=[QuestionOption(label=o["label"], text=o["text"]) for o in record["options"]],
            skill=skill_id,
        )
        solution = registry.require(skill_id).solve(question, ctx)
        assert solution.status == "solved", f"{case_id} 文本自校验失败: {solution.error}"
        assert solution.answer == expected, f"{case_id} 自校验答案不一致: {solution.answer} vs {expected}"


def main() -> int:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    REGION_DIR.mkdir(parents=True, exist_ok=True)
    records = build_cases()
    with JSONL_PATH.open("w", encoding="utf-8") as handle:
        for record in records:
            record["source"] = {"provenance": PROVENANCE, "generator": "scripts/make_screenshot_benchmark.py"}
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    _selfcheck_text_cases()
    print(f"[OK] 生成 {len(records)} 个截图样本 → {JSONL_PATH}")
    print("     全部图形样本已经过生产适配层自校验；文本/序列样本经过 Skill 自校验。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
