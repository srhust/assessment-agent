"""R2b 独立泛化基准生成器：data/benchmark_real/。

与 `make_screenshot_benchmark.py`（regression 集，生成时用生产求解器自校验）严格不同，
本脚本体现**独立选择**原则：

1. 样本内容与标准答案在内容表里**手工定义**（算术/规律由构造确定），生成过程
   **不调用任何求解器**决定样本去留——已知超出现有规则能力的样本（旋转、翻转、
   半径外推、缺口九宫格）与未实现题型（资料分析/言语/性格）同样保留，
   这些失败正是基准要测量的信号。
2. dev 可调参；holdout 冻结（manifest 记录 sha256、冻结日期与冻结政策）。
3. 防泄漏分组：同一题目/同一生成模板（含重排、缩放变体）共享 `group`，
   整组只进入一个 split（脚本断言检查）。
4. 全部为自编模拟测评页面（independent-synthetic），**不是真实招聘平台截图**；
   来源/授权/构造方法写进每条记录与 manifest。
5. 只做"标注 QA"：bbox 不越界、必需裁剪非空白、标号唯一、答案标号存在——
   全部是标注一致性检查，与系统解题能力无关。

运行：``python -X utf8 scripts/make_independent_benchmark.py``（重建全部产物）
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image, ImageDraw, ImageFont, ImageOps  # noqa: E402

import generate_figure_dataset as gfd  # noqa: E402

DATA_DIR = ROOT / "data"
BENCH_DIR = DATA_DIR / "benchmark_real"
IMAGE_DIR = BENCH_DIR / "images"
REGION_DIR = BENCH_DIR / "regions"

PAGE_W = 920
CELL, GAP, MARGIN = gfd.CELL, 24, 24
BAND_GAP = 46
LABEL_OFFSET = 30  # 选项标号写在图形左侧（figure_bbox 不含标号）

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
FONT_PATH = next((c for c in _FONT_CANDIDATES if Path(c).is_file()), None)
if FONT_PATH is None:  # pragma: no cover
    raise SystemExit("未找到可用中文字体")

SOURCE = {
    "type": "independent_synthetic",
    "provenance": "自编模拟测评页面（scripts/make_independent_benchmark.py 手工内容表生成）——非真实招聘平台截图，非在线题库抓取",
    "author": "assessment-agent 项目原创（题目文字、图形与答案均为原创构造）",
    "license": "内部评估使用（自创内容，无第三方版权）",
    "contains_real_exam": False,
    "contains_pii": False,
    "selection_method": "independent-content-table; NOT filtered by solver capability",
}


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def _ink_ratio(image: Image.Image) -> float:
    gray = image.convert("L")
    dark = sum(1 for pixel in gray.getdata() if pixel < 128)
    return dark / max(1, gray.width * gray.height)


def _draw_image(draw: ImageDraw.ImageDraw) -> Image.Image:
    """从 ImageDraw 拿回宿主图像（兼容不同 Pillow 版本）。"""
    getter = getattr(ImageDraw, "getimage", None)
    if getter is not None:
        return getter(draw)
    return draw._image  # type: ignore[attr-defined]


def _paste_mask(draw: ImageDraw.ImageDraw, box: Sequence[int], mask: Image.Image) -> None:
    """把白底墨迹 mask（255=墨）贴进白页区域（同 gfd.paste_cell 语义）。"""
    scaled = mask.resize((box[2] - box[0], box[3] - box[1]), Image.NEAREST)
    patch = Image.new("L", scaled.size, 255)
    patch.paste(0, (0, 0), scaled)
    _draw_image(draw).paste(patch, (box[0], box[1]))


# ------------------------------------------------------------------ 自定义图形
def paint_arrow(draw: ImageDraw.ImageDraw, box: Sequence[int], angle_deg: float) -> None:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    rad = math.radians(angle_deg)
    length = 38.0
    tip = (cx + length * math.cos(rad), cy + length * math.sin(rad))
    draw.line([cx, cy, tip[0], tip[1]], fill=0, width=5)
    draw.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], outline=0, width=3)
    for delta in (150, -150):
        barb = (tip[0] + 14 * math.cos(rad + math.radians(delta)), tip[1] + 14 * math.sin(rad + math.radians(delta)))
        draw.line([tip, barb], fill=0, width=5)


def paint_flag(draw: ImageDraw.ImageDraw, box: Sequence[int], *, mirror: bool = False) -> None:
    x0, y0, x1, y1 = box
    pole_x = x0 + (x1 - x0) * (0.65 if mirror else 0.35)
    draw.line([pole_x, y0 + 18, pole_x, y1 - 18], fill=0, width=5)
    if mirror:
        points = [(pole_x, y0 + 18), (pole_x - 44, y0 + 32), (pole_x, y0 + 48)]
    else:
        points = [(pole_x, y0 + 18), (pole_x + 44, y0 + 32), (pole_x, y0 + 48)]
    draw.polygon(points, fill=0)


def paint_box_dot(draw: ImageDraw.ImageDraw, box: Sequence[int], fx: float, fy: float) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle([x0 + 12, y0 + 12, x1 - 12, y1 - 12], outline=0, width=3)
    radius = 11
    px = x0 + 12 + fx * (x1 - x0 - 24)
    py = y0 + 12 + fy * (y1 - y0 - 24)
    draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=0)


def paint_squares(draw: ImageDraw.ImageDraw, box: Sequence[int], count: int) -> None:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    side = 20 if count <= 4 else 14
    gap = 8
    cols, rows = (count, 1) if count <= 4 else (3, math.ceil(count / 3))
    index = 0
    for row in range(rows):
        for col in range(cols):
            if index >= count:
                break
            px = cx + (col - (cols - 1) / 2) * (side + gap)
            py = cy + (row - (rows - 1) / 2) * (side + gap + 4)
            draw.rectangle([px - side / 2, py - side / 2, px + side / 2, py + side / 2], outline=0, width=3)
            index += 1


def paint_circle_r(draw: ImageDraw.ImageDraw, box: Sequence[int], radius: int) -> None:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=0, width=4)


def paint_grid_circles(draw: ImageDraw.ImageDraw, box: Sequence[int]) -> None:
    """4 个圆 2×2 排布（与横排 4 圆在个数规律下等价——undecidable 样本的构造核心）。"""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    radius, gap = 13, 6
    for row in range(2):
        for col in range(2):
            px = cx + (col - 0.5) * (radius * 2 + gap)
            py = cy + (row - 0.5) * (radius * 2 + gap)
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], outline=0, width=3)


def paint_notched(draw: ImageDraw.ImageDraw, box: Sequence[int], base: str, notches: int) -> None:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    if base == "circle":
        draw.ellipse([cx - 34, cy - 34, cx + 34, cy + 34], outline=0, width=3)
    elif base == "square":
        draw.rectangle([cx - 32, cy - 32, cx + 32, cy + 32], outline=0, width=3)
    else:
        gfd.draw_polygon(draw, box, 3)
    for index in range(notches):
        x = cx + (index - (notches - 1) / 2) * 18
        draw.rectangle([x - 6, box[1] + 16, x + 6, box[1] + 28], fill=0)


def mask_painter(kind: str, variant: int, operator: Optional[str] = None) -> Callable:
    def painter(draw: ImageDraw.ImageDraw, box: Sequence[int]) -> None:
        mask = gfd._combo_mask(variant, operator) if operator else gfd._shape_mask(kind, variant)
        _paste_mask(draw, box, mask)

    return painter


# ------------------------------------------------------------------ 页面骨架
class Paper:
    def __init__(
        self,
        height: int,
        *,
        header: str = "职业能力练习卷（自编样题） · 第 1 页",
        browser: bool = False,
        watermark: Optional[str] = None,
        footer: str = "自编题目 · 仅供系统评估使用",
    ) -> None:
        self.image = Image.new("L", (PAGE_W, height), 255)
        self.draw = ImageDraw.Draw(self.image)
        self.width, self.height = PAGE_W, height
        self.cursor_y = 18
        self.text_regions: List[Dict[str, Any]] = []
        if browser:
            self.draw.rectangle([0, 0, self.width, 34], outline=120, width=2)
            box = self._line(12, 8, "https://practice.invalid/module1  访问者：考生", _font(16))
            self.text_regions.append({"id": "chrome", "bbox": box, "text": "browser chrome (distractor)"})
            self.cursor_y = 46
        box = self._line(28, self.cursor_y, header, _font(18))
        self.text_regions.append({"id": "hdr", "bbox": box, "text": header})
        self.cursor_y += 36
        if watermark:
            wm_box = self._line(self.width - 240, self.cursor_y - 20, watermark, _font(15), fill=170)
            self.text_regions.append({"id": "wm", "bbox": wm_box, "text": watermark})
        self._line(28, height - 30, footer, _font(14))

    def _line(self, x: int, y: int, text: str, font, fill: int = 0) -> List[int]:
        self.draw.text((x, y), text, font=font, fill=fill)
        left, top, right, bottom = self.draw.textbbox((x, y), text, font=font)
        return [left, top, max(1, right - left), max(1, bottom - top)]

    def stem(self, text: str, *, size: int = 22, x: int = 36, region_id: str = "stem") -> List[int]:
        bbox = self._line(x, self.cursor_y, text, _font(size))
        self.text_regions.append({"id": region_id, "bbox": bbox, "text": text})
        self.cursor_y += size + 24
        return bbox

    def paragraph(self, lines: Sequence[str], *, size: int = 20) -> None:
        top = self.cursor_y
        for line in lines:
            self._line(36, self.cursor_y, line, _font(size))
            self.cursor_y += size + 10
        left, _, right, bottom = self.draw.textbbox((36, top), lines[0], font=_font(size))
        last = self.draw.textbbox((36, self.cursor_y - size - 10), lines[-1], font=_font(size))
        bbox = [36, top, max(right, last[2]) - 36, last[3] - top]
        self.text_regions.append({"id": "passage", "bbox": bbox, "text": "".join(lines)})
        self.cursor_y += 6

    def options_text(self, pairs: Sequence[Tuple[str, str]], *, layout: str = "row4", size: int = 20) -> List[Dict[str, Any]]:
        regions: List[Dict[str, Any]] = []
        if layout == "row4":
            for start in range(0, len(pairs), 4):
                x, heights = 60, []
                for label, text in pairs[start : start + 4]:
                    bbox = self._line(x, self.cursor_y, f"{label}．{text}", _font(size))
                    regions.append({"label": label, "bbox": bbox, "text": text, "has_figure": False})
                    x = bbox[0] + bbox[2] + 52
                    heights.append(bbox[3])
                self.cursor_y += max(heights) + 26
        elif layout == "twocol":
            half = (len(pairs) + 1) // 2
            columns = [pairs[:half], pairs[half:]]
            column_x = [60, self.width // 2 + 20]
            tallest = 0
            for col_index, column in enumerate(columns):
                y = self.cursor_y
                for label, text in column:
                    bbox = self._line(column_x[col_index], y, f"{label}．{text}", _font(size))
                    regions.append({"label": label, "bbox": bbox, "text": text, "has_figure": False})
                    y += bbox[3] + 22
                tallest = max(tallest, y)
            self.cursor_y = tallest + 10
        else:
            raise ValueError(f"未知文字选项布局 {layout!r}")
        return regions

    def figure_band(
        self,
        painters: Sequence[Callable],
        option_painters: Sequence[Callable],
        *,
        option_rows: int = 1,
        option_texts: Optional[Sequence[Tuple[str, str]]] = None,
    ) -> Dict[str, Any]:
        stem_count = len(painters)
        per_opt_row = math.ceil(len(option_painters) / option_rows)
        cols = max(stem_count, per_opt_row)
        block_w = 2 * MARGIN + cols * CELL + (cols - 1) * GAP
        rows_used = max(option_rows, 1) if option_painters else 0
        block_h = MARGIN + CELL + (BAND_GAP + rows_used * CELL + (rows_used - 1) * GAP if rows_used else 0) + MARGIN
        block = Image.new("L", (block_w, block_h), 255)
        bdraw = ImageDraw.Draw(block)
        for index, painter in enumerate(painters):
            box = (MARGIN + index * (CELL + GAP), MARGIN, MARGIN + index * (CELL + GAP) + CELL, MARGIN + CELL)
            painter(bdraw, box)
        option_boxes: List[Tuple[str, Tuple[int, int, int, int]]] = []
        labels = "ABCDEFGH"
        for index, painter in enumerate(option_painters):
            if option_rows == 1:
                row, col = 0, index
                y0 = MARGIN + CELL + BAND_GAP
            else:
                row, col = divmod(index, 2)
                y0 = MARGIN + CELL + BAND_GAP + row * (CELL + GAP)
            x0 = MARGIN + col * (CELL + GAP)
            painter(bdraw, (x0, y0, x0 + CELL, y0 + CELL))
            option_boxes.append((labels[index], (x0, y0, x0 + CELL, y0 + CELL)))
        ox = 48
        oy = self.cursor_y
        self.image.paste(block, (ox, oy))
        stem_bbox = [ox, oy, MARGIN * 2 + stem_count * CELL + (stem_count - 1) * GAP, MARGIN + CELL]
        option_regions: List[Dict[str, Any]] = []
        for label, (x0, y0, x1, y1) in option_boxes:
            figure_bbox = [ox + x0, oy + y0, x1 - x0, y1 - y0]
            self.draw.text((ox + x0 - LABEL_OFFSET, oy + y0 + (y1 - y0) // 2 - 12), label, font=_font(20), fill=0)
            option_regions.append(
                {
                    "label": label,
                    "bbox": [figure_bbox[0] - LABEL_OFFSET, figure_bbox[1], figure_bbox[2] + LABEL_OFFSET, figure_bbox[3]],
                    "text": "",
                    "has_figure": True,
                    "figure_bbox": figure_bbox,
                }
            )
        if option_texts:
            tx = min(ox + block.width + 30, self.width - 220)
            for label, text in option_texts:
                bbox = self._line(tx, oy + MARGIN + CELL + BAND_GAP + 40, f"{label}．{text}", _font(18))
                option_regions.append({"label": label, "bbox": bbox, "text": text, "has_figure": False})
        self.cursor_y = oy + block.height + 28
        return {"stem_bbox": stem_bbox, "option_regions": option_regions, "block": block, "option_boxes": option_boxes}

    def grid_band(
        self,
        cells: Sequence[Sequence[Optional[Callable]]],
        option_painters: Sequence[Callable],
    ) -> Dict[str, Any]:
        rows, cols = len(cells), len(cells[0])
        sheet = gfd.BareSheet(rows=rows, cols=cols, cell_w=140, cell_h=140)
        for r, row in enumerate(cells):
            for c, painter in enumerate(row):
                if painter is not None:
                    painter(sheet.draw, sheet.box(r, c))
                else:
                    box = sheet.box(r, c)
                    sheet.draw.text((box[0] + 58, box[1] + 52), "?", font=_font(40), fill=0)
        ox, oy = 60, self.cursor_y
        self.image.paste(sheet.image, (ox, oy))
        stem_bbox = [ox, oy, cols * 140, rows * 140]
        self.cursor_y = oy + sheet.image.height + 18
        labels = "ABCDEFGH"
        option_regions = []
        for index, painter in enumerate(option_painters):
            x0 = ox + index * (CELL + GAP)
            y0 = self.cursor_y
            block = Image.new("L", (CELL, CELL), 255)
            painter(ImageDraw.Draw(block), (0, 0, CELL, CELL))
            self.image.paste(block, (x0, y0))
            self.draw.text((x0 - LABEL_OFFSET, y0 + CELL // 2 - 12), labels[index], font=_font(20), fill=0)
            option_regions.append(
                {
                    "label": labels[index],
                    "bbox": [x0 - LABEL_OFFSET, y0, CELL + LABEL_OFFSET, CELL],
                    "text": "",
                    "has_figure": True,
                    "figure_bbox": [x0, y0, CELL, CELL],
                }
            )
        self.cursor_y += CELL + 26
        return {"stem_bbox": stem_bbox, "grid": [rows, cols], "option_regions": option_regions}

    def table_band(self, rows: Sequence[Sequence[str]], *, size: int = 20) -> Dict[str, Any]:
        x0, y0 = 60, self.cursor_y
        col_w, row_h = 150, 40
        width = col_w * len(rows[0])
        height = row_h * len(rows)
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self._line(x0 + c * col_w + 10, y0 + r * row_h + 9, text, _font(size))
        self.draw.rectangle([x0, y0, x0 + width, y0 + height], outline=0, width=2)
        for r in range(1, len(rows)):
            self.draw.line([x0, y0 + r * row_h, x0 + width, y0 + r * row_h], fill=0, width=1)
        for c in range(1, len(rows[0])):
            self.draw.line([x0 + c * col_w, y0, x0 + c * col_w, y0 + height], fill=0, width=1)
        self.cursor_y = y0 + height + 24
        return {"table_bbox": [x0, y0, width, height], "table_rows": [list(row) for row in rows]}


# ------------------------------------------------------------------ 内容表（手工定义；答案由构造确定）
def content_items() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    def add(**kwargs: Any) -> None:
        items.append(kwargs)

    # ================= DEV（16）=================
    add(id="bi-arith-pct-01", group="g-arith-pct", split="dev", skill="numeric_calculation",
        kind="text", layout="row4_text", stem="计算 480 的 35% 是多少？",
        options=[("A", "168"), ("B", "144"), ("C", "175"), ("D", "180")], answer="A",
        note="480×0.35=168（构造算术）")
    add(id="bi-arith-pct-02", group="g-arith-pct", split="dev", skill="numeric_calculation",
        kind="text", layout="row4_text", stem="原价 260 元的商品打八折，现价是多少元？",
        options=[("A", "212"), ("B", "198"), ("C", "208"), ("D", "224")], answer="C",
        note="260×0.8=208")
    add(id="bi-arith-speed-03", group="g-arith-speed", split="dev", skill="numeric_calculation",
        kind="text", layout="row4_text", chrome=True, stem="一列火车 1.5 小时行驶 90 千米。照这样计算，4 小时行驶多少千米？",
        options=[("A", "210"), ("B", "240"), ("C", "260"), ("D", "360")], answer="B",
        note="90/1.5=60；60×4=240（应用题句式，表达式抽取为诚实难点）")
    add(id="bi-series-lin-04", group="g-series-lin", split="dev", skill="number_series",
        kind="text", layout="row4_text", stem="数列 7, 15, 23, 31, ( ) 的下一项是？",
        options=[("A", "37"), ("B", "39"), ("C", "41"), ("D", "45")], answer="B", note="公差 8")
    add(id="bi-series-geo-05", group="g-series-geo", split="dev", skill="number_series",
        kind="text", layout="row4_text", stem="数列 3, 6, 12, 24, ( ) 的下一项是？",
        options=[("A", "36"), ("B", "42"), ("C", "48"), ("D", "60")], answer="C", note="公比 2")
    add(id="bi-series-sq-06", group="g-series-sq", split="dev", skill="number_series",
        kind="text", layout="row4_text", stem="数列 2, 5, 10, 17, ( ) 的下一项是？",
        options=[("A", "24"), ("B", "25"), ("C", "26"), ("D", "27")], answer="C", note="n²+1，第 5 项 26")
    add(id="bi-series-prime-07", group="g-series-prime", split="dev", skill="number_series",
        kind="text", layout="row4_text", watermark="内部练习", stem="数列 2, 3, 5, 7, 11, ( ) 的下一项是？",
        options=[("A", "12"), ("B", "13"), ("C", "14"), ("D", "15")], answer="B",
        note="质数列下一项 13（当前规则库不覆盖质数：允许诚实失败并被归因）")
    add(id="bi-fig-count-08", group="g-fig-count", split="dev", skill="figure_reasoning",
        kind="fig", fig="count", layout="single_row_options", stem="按规律，问号处应填入哪个图形？",
        stem_counts=[1, 2, 3], option_counts=[4, 5, 3, 6], answer="A", note="圆圈个数 1→2→3→4")
    add(id="bi-fig-sides-09", group="g-fig-sides", split="dev", skill="figure_reasoning",
        kind="fig", fig="sides", layout="single_row_options", stem="下列图形按边数递增规律排列，下一个图形是（ ）",
        stem_sides=[3, 4, 5], option_sides=[8, 6, 5, 7], answer="B", note="边数 3→4→5→6")
    add(id="bi-fig-rot-10", group="g-fig-rotation", split="dev", skill="figure_reasoning",
        kind="fig", fig="rotation", layout="single_row_options", stem="指针按顺时针规律旋转，下一个位置是（ ）",
        stem_angles=[0, 90, 180], option_angles=[270, 0, 90, 180], answer="A",
        note="顺时针 90° 步进 → 270°（六类规则不含旋转：允许诚实失败）")
    add(id="bi-fig-mixopt-11", group="g-fig-count", split="dev", skill="figure_reasoning",
        kind="fig", fig="count", layout="mixed_fig_text_options", stem="按规律，问号处应填入哪个选项？",
        stem_counts=[1, 2, 3], option_counts=[4, 2], text_options=[("C", "无法确定")], answer="A",
        note="图形选项 A(4个)/B(2个) + 文字选项 C；答案 A 由构造确定")
    add(id="bi-fig-ambig-12", group="g-fig-ambiguous", split="dev", skill="figure_reasoning",
        kind="fig", fig="count_ambig", layout="single_row_options", stem="问号处应填入哪个图形？",
        stem_counts=[1, 2, 3], answer=None, expected="undecidable",
        note="个数外推为 4，但 A（横排4圆）与 B（2×2四圆）都满足——按构造即不可唯一判定")
    add(id="bi-fig-count2-13", group="g-fig-count2", split="dev", skill="figure_reasoning",
        kind="fig", fig="squares", layout="two_row_options", stem="小正方形按规律排列，下一个图形是（ ）",
        stem_counts=[2, 4, 6], option_counts=[5, 6, 7, 8], answer="D",
        note="2→4→6→8（两行选项 + 方块形状，独立于圆圈组）")
    add(id="bi-grid-xor-14", group="g-grid-xor", split="dev", skill="figure_reasoning",
        kind="grid", fig="grid_xor", layout="grid_3x3", stem="九宫格中问号处应填入哪个图形？", answer="A",
        note="每行第三列 = 前两列叠加（异或）；答案按构造为第三行 xor 组合")
    add(id="bi-table-total-15", group="g-da-table", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["季度", "销售额(万元)"], ["一", "180"], ["二", "210"], ["三", "240"], ["四", "300"]],
        question="四个季度销售额合计是多少万元？",
        options=[("A", "930"), ("B", "870"), ("C", "900"), ("D", "960")], answer="A",
        expected="solved", note="R3a 起资料分析已实现：180+210+240+300=930（万元）")
    # ---- R3a 资料分析 dev 样本扩展（全部 split="dev"；冻结 Holdout 不增不改）----
    add(id="bi-table-yoy-17", group="g-da-yoy", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["年份", "销售额(万元)", "增长率"], ["2022年", "180", "12.5%"], ["2023年", "210", "16.7%"]],
        question="2023年销售额同比增长率约为多少？",
        options=[("A", "16.7%"), ("B", "12.5%"), ("C", "30.0%"), ("D", "16.3%")], answer="A",
        expected="solved", note="(210-180)/180=16.67%，四舍五入读数 16.7%；增长率列不得当操作数")
    add(id="bi-table-mom-18", group="g-da-mom", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["月份", "产量(件)", "备注"], ["5月", "120", "正常"], ["6月", "150", "加班追产"]],
        question="6月产量环比增长多少件？",
        options=[("A", "30"), ("B", "36"), ("C", "25"), ("D", "180")], answer="A",
        expected="solved", note="150-120=30 件；“备注”干扰列不参与计算")
    add(id="bi-table-share-19", group="g-da-share", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["类别", "数量(件)"], ["甲", "30"], ["乙", "45"], ["丙", "25"], ["合计", "100"]],
        question="丙的数量占合计数量的比重是多少？",
        options=[("A", "25%"), ("B", "75%"), ("C", "30%"), ("D", "20%")], answer="A",
        expected="solved", note="25/100=25%；含合计行（总量直接取合计单元格）")
    add(id="bi-table-mean-20", group="g-da-mean", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["季度", "投诉量(件)"], ["一", "80"], ["二", "120"], ["三", "100"], ["四", "140"]],
        question="平均每个季度的投诉量是多少件？",
        options=[("A", "110"), ("B", "111"), ("C", "100"), ("D", "120")], answer="A",
        expected="solved", note="(80+120+100+140)/4=110")
    add(id="bi-table-rank-21", group="g-da-rank", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["季度", "投诉量(件)"], ["一", "80"], ["二", "120"], ["三", "100"], ["四", "140"]],
        question="投诉量最多的季度是哪个季度？",
        options=[("A", "第一季度"), ("B", "第二季度"), ("C", "第三季度"), ("D", "第四季度")], answer="D",
        expected="solved", note="标签类答案：最大值 140 对应行“四”，须唯一匹配到“第四季度”")
    add(id="bi-table-diff-22", group="g-da-diff2", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["季度", "销售额(万元)"], ["一季度", "180"], ["二季度", "210"], ["三季度", "240"], ["四季度", "300"]],
        question="二季度销售额比一季度多多少万元？",
        options=[("A", "30"), ("B", "40"), ("C", "60"), ("D", "90")], answer="A",
        expected="solved", note="两实体差值 210-180=30，不是全体最大最小差 120")
    add(id="bi-table-base-23", group="g-da-base", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["年份", "销售额(万元)", "同比增速"], ["2022年", "180", ""], ["2023年", "210", "16.7%"]],
        question="2023年销售额同比增长16.7%，上年销售额约为多少万元？",
        options=[("A", "180"), ("B", "178.6"), ("C", "190"), ("D", "210")], answer="A",
        expected="solved", note="基期=现期/(1+增长率)=210/1.167≈179.95，四舍五入选项 180")
    add(id="bi-table-multi-24", group="g-da-multi", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["组别", "完成件数"], ["甲组", "60"], ["乙组", "20"], ["丙组", "30"]],
        question="甲组完成件数是乙组的多少倍？",
        options=[("A", "3倍"), ("B", "2倍"), ("C", "4倍"), ("D", "1.5倍")], answer="A",
        expected="solved", note="60/20=3；“是多少倍”取商，“多几倍”才取商-1")
    add(id="bi-table-ppt-25", group="g-da-ppt", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["年份", "销售额(万元)", "增长率"], ["2022年", "180", "12.5%"], ["2023年", "210", "20%"]],
        question="2023年增长率比2022年高约多少个百分点？",
        options=[("A", "7.5个百分点"), ("B", "12.5个百分点"), ("C", "20个百分点"), ("D", "8个百分点")], answer="A",
        expected="solved", note="百分点差=两期百分比读数直接相减 20-12.5=7.5")
    add(id="bi-table-unit-26", group="g-da-unit", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["年份", "利润(亿元)"], ["2022年", "3.5"], ["2023年", "4.2"]],
        question="2023年利润换算成万元约为多少？",
        options=[("A", "42000"), ("B", "4200"), ("C", "420000"), ("D", "35000")], answer="A",
        expected="solved", note="4.2亿元=42000万元；单位换算必须显式乘除倍率")
    add(id="bi-table-hdr2-27", group="g-da-hdr2", split="dev", skill="data_analysis",
        kind="table", layout="table2hdr", stem="根据下表回答问题。",
        table=[["类别", "2022年", "2023年"], ["", "销售额(万元)", "销售额(万元)"],
               ["产品A", "100", "130"], ["产品B", "50", "70"]],
        question="2023年产品A销售额同比增长率是多少？",
        options=[("A", "30%"), ("B", "13%"), ("C", "26%"), ("D", "40%")], answer="A",
        expected="solved", note="双层表头不得丢失年份或统计对象：(130-100)/100=30%")
    add(id="bi-table-totalrow-28", group="g-da-total", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["季度", "销售额(万元)"], ["一季度", "180"], ["二季度", "210"], ["三季度", "240"],
               ["四季度", "300"], ["合计", "930"]],
        question="四个季度合计销售额比三季度多多少万元？",
        options=[("A", "690"), ("B", "750"), ("C", "660"), ("D", "720")], answer="A",
        expected="solved", note="点名两实体（合计 vs 三季度）：930-240=690，禁止全体最大最小差")
    add(id="bi-table-avg-29", group="g-da-avg", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["年份", "营收(亿元)"], ["2021年", "150"], ["2022年", "180"], ["2023年", "216"]],
        question="2021至2023年营收年均增长率约为多少？",
        options=[("A", "20%"), ("B", "22%"), ("C", "18%"), ("D", "44%")], answer="A",
        expected="solved", note="(216/150)^(1/2)-1=20%（精确有理开方，无需近似）")
    add(id="bi-chart-diff-30", group="g-da-chart-diff", split="dev", skill="data_analysis",
        kind="chart", layout="chart_bar", values=[17, 42, 30],
        question="从统计图可以看出，完成最多的组比最少的组多多少件？",
        options=[("A", "25"), ("B", "12"), ("C", "30"), ("D", "72")], answer="A",
        expected="solved", note="柱状图读数只取标注文本；验证层用柱高比例做像素一致性复核")
    add(id="bi-chart-sum-31", group="g-da-chart-sum", split="dev", skill="data_analysis",
        kind="chart", layout="chart_bar", values=[25, 40, 15],
        question="从统计图可以看出，三个组一共完成多少件？",
        options=[("A", "80"), ("B", "85"), ("C", "75"), ("D", "65")], answer="A",
        expected="solved", note="25+40+15=80")
    add(id="bi-chart-nolabel-32", group="g-da-chart-bare", split="dev", skill="data_analysis",
        kind="chart", layout="chart_bar", values=[17, 42, 30], value_text=False,
        question="从统计图可以看出，乙组比甲组约多完成多少件？",
        options=[("A", "25"), ("B", "22"), ("C", "27"), ("D", "无法确定")], answer=None,
        expected="failed", note="无标注数值文本：不允许仅凭柱长猜精确数值 → 必须如实失败")
    add(id="bi-table-ocr-33", group="g-da-ocr", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["季度", "销售额(万元)"], ["一季度", "l80"], ["二季度", "210"]],
        question="一季度与二季度销售额合计是多少万元？",
        options=[("A", "390"), ("B", "300"), ("C", "290"), ("D", "420")], answer=None,
        expected="failed", note="OCR 歧义数字 l80：digit_ambiguous → 引用即失败，绝不按 180 猜")
    add(id="bi-table-gap-34", group="g-da-missing", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["年份", "出口额(万美元)", "同比增速"], ["2022年", "2400", "20%"], ["2023年", "", "25%"]],
        question="2023年出口额比2022年多多少万美元？",
        options=[("A", "600"), ("B", "480"), ("C", "540"), ("D", "3000")], answer=None,
        expected="failed", note="2023 出口额缺失：cell_missing → 如实失败，不得用增速推算")
    add(id="bi-table-ambigcol-35", group="g-da-ambigcol", split="dev", skill="data_analysis",
        kind="table", layout="table", stem="根据下表回答问题。",
        table=[["品类", "销售额(万元)", "销售额(元)"], ["甲", "100", "12000000"], ["乙", "50", "6500000"]],
        question="甲的销售额合计是多少万元？",
        options=[("A", "100"), ("B", "1200"), ("C", "150"), ("D", "2200")], answer=None,
        expected="failed", note="同名指标列两种单位并存、题面无法区分 → ambiguous_reference 失败")
    add(id="bi-verbal-blank-16", group="g-vu-blank", split="dev", skill="verbal_understanding",
        kind="text", layout="row4_text", stem="他的发言获得了与会者一致的（ ），会场气氛由此变得热烈起来。",
        options=[("A", "反应"), ("B", "反映"), ("C", "批示"), ("D", "注视")], answer="A",
        expected="unsupported", note="选词填空（正确答案：反应）；言语理解未实现→弃权")

    # ================= HOLDOUT（16，冻结）=================
    add(id="bi-arith-frac-17", group="g-arith-frac", split="holdout", skill="numeric_calculation",
        kind="text", layout="row4_text", stem="计算 3/7 × 14 的结果是？",
        options=[("A", "5"), ("B", "6"), ("C", "7"), ("D", "9")], answer="B", note="3/7×14=6")
    add(id="bi-arith-pctdiff-18", group="g-arith-pctdiff", split="holdout", skill="numeric_calculation",
        kind="text", layout="row4_text", stem="甲商品 500 元，比乙商品便宜 20%。乙商品价格是多少元？",
        options=[("A", "600"), ("B", "650"), ("C", "625"), ("D", "750")], answer="C", note="500/0.8=625")
    add(id="bi-series-cube-19", group="g-series-pow", split="holdout", skill="number_series",
        kind="text", layout="row4_text", watermark="内部练习", stem="数列 1, 8, 27, 64, ( ) 的下一项是？",
        options=[("A", "100"), ("B", "121"), ("C", "125"), ("D", "144")], answer="C", note="立方数列 5³=125")
    add(id="bi-series-fib-20", group="g-series-fib", split="holdout", skill="number_series",
        kind="text", layout="row4_text", stem="数列 1, 1, 2, 3, 5, 8, ( ) 的下一项是？",
        options=[("A", "11"), ("B", "12"), ("C", "13"), ("D", "14")], answer="C", note="斐波那契 5+8=13")
    add(id="bi-series-alter-21", group="g-series-alter", split="holdout", skill="number_series",
        kind="text", layout="twocol_text", lowres=0.6, stem="数列 100, 5, 90, 10, 80, ( ) 的下一项是？",
        options=[("A", "15"), ("B", "20"), ("C", "75"), ("D", "70")], answer="A",
        note="奇偶分组 → 15（竖读双列 + 60% 低分辨率，同组同 split）")
    add(id="bi-series-secdiff-22", group="g-series-secdiff", split="holdout", skill="number_series",
        kind="text", layout="row4_text", stem="数列 1, 2, 4, 7, 11, ( ) 的下一项是？",
        options=[("A", "15"), ("B", "16"), ("C", "17"), ("D", "18")], answer="B", note="差分 1,2,3,4 → +5=16")
    add(id="bi-series-ambig-23", group="g-series-ambiguous", split="holdout", skill="number_series",
        kind="text", layout="row4_text", stem="数列 1, 2, 4, ( ) 的下一项是？",
        options=[("A", "7"), ("B", "8"), ("C", "9"), ("D", "6")], answer=None, expected="undecidable",
        note="仅三项：+1,+2,+3→7 与 ×2 等比→8 都成立——构造上不可唯一判定")
    add(id="bi-fig-trans-24", group="g-fig-trans", split="holdout", skill="figure_reasoning",
        kind="fig", fig="translation", layout="single_row_options", stem="圆点沿方框按规律移动，下一个位置是（ ）",
        stem_pos=[(0.12, 0.12), (0.88, 0.12), (0.88, 0.88)],
        option_pos=[(0.12, 0.88), (0.88, 0.12), (0.5, 0.5), (0.12, 0.12)], answer="A",
        note="圆点顺时针绕角：左上→右上→右下→左下")
    add(id="bi-fig-trans-25", group="g-fig-trans", split="holdout", skill="figure_reasoning",
        kind="fig", fig="translation", layout="two_row_options", stem="圆点沿方框按规律移动，下一个位置是（ ）",
        stem_pos=[(0.5, 0.1), (0.9, 0.5), (0.5, 0.9)],
        option_pos=[(0.1, 0.5), (0.5, 0.1), (0.9, 0.5), (0.1, 0.1)], answer="A",
        note="菱形绕圈：上→右→下→左（两行选项变体，同组）")
    add(id="bi-fig-flip-26", group="g-fig-flip", split="holdout", skill="figure_reasoning",
        kind="fig", fig="flag_flip", layout="single_row_options", stem="小旗按“正-反-正-反”规律排列，下一个是（ ）",
        stem_mirrors=[False, True, False], answer="B",
        note="镜像交替 → 反向旗（翻转匹配不在六类规则内：允许诚实失败）")
    add(id="bi-fig-scale-27", group="g-fig-scale", split="holdout", skill="figure_reasoning",
        kind="fig", fig="scaling", layout="single_row_options", stem="圆的半径按规律增大，下一个圆是（ ）",
        stem_radii=[12, 18, 24], option_radii=[28, 30, 34, 36], answer="B",
        note="半径 +6 递增 → 30（现有确定性规则不含半径外推）")
    add(id="bi-grid-notch-28", group="g-grid-notch", split="holdout", skill="figure_reasoning",
        kind="grid", fig="grid_notch", layout="grid_3x3", stem="九宫格中问号处应填入哪个图形？", answer="C",
        note="行=基础形状（圆/方/三角），列=顶边缺口数 0,1,2 → 答案：三角形带 2 缺口（按构造）")
    add(id="bi-chart-bar-29", group="g-da-chart", split="holdout", skill="data_analysis",
        kind="chart", layout="chart", values=[17, 42, 30],
        question="完成最多的组比最少的组多多少件？",
        options=[("A", "25"), ("B", "12"), ("C", "13"), ("D", "24")], answer="A",
        expected="unsupported", note="42-17=25；柱状图读数（资料分析未实现→弃权）")
    add(id="bi-verbal-read-30", group="g-vu-read", split="holdout", skill="verbal_understanding",
        kind="text", layout="row4_text", dark=True,
        paragraph_lines=[
            "城市绿地不仅美化环境，还能缓解热岛效应、降低噪声、吸附",
            "扬尘。因此，有人主张在老城区尽可能多种树。",
        ],
        stem="以下哪项如果为真，最能削弱上述主张？",
        options=[("A", "种树需要长期维护成本"), ("B", "树木能吸附扬尘"), ("C", "绿地可降低噪声"), ("D", "热岛效应危害健康")],
        answer="A", expected="unsupported", note="削弱型阅读题（答案 A）；未实现→弃权；深色反色页面变体")
    add(id="bi-pers-scale-31", group="g-pers", split="holdout", skill="personality",
        kind="personality", layout="row4_text", stem="我觉得自己在截止期限前会感到明显紧张。",
        options=[("A", "完全符合"), ("B", "比较符合"), ("C", "不太符合"), ("D", "完全不符合")], answer=None,
        expected="unsupported", note="性格测评无标准答案（不可判定）；系统必须弃权，不代答")
    add(id="bi-log-judge-32", group="g-log-judge", split="holdout", skill="logical_reasoning",
        kind="text", layout="row4_text", chrome=True,
        stem="已知“所有获奖者都参加了培训”“有些参加培训的人是实习生”。能否必然推出“有些获奖者是实习生”？",
        options=[("A", "能"), ("B", "不能"), ("C", "条件不足"), ("D", "以上都不对")], answer="B",
        note="三段论：不能必然推出（培训与实习生交集不含获奖者必然性）")
    return items


def _last_stem_bbox(page: Paper) -> List[int]:
    for region in reversed(page.text_regions):
        if region["id"] == "stem":
            return region["bbox"]
    return [0, 0, 10, 10]


# ------------------------------------------------------------------ 渲染分派
def _fig_painters(item: Dict[str, Any]) -> Tuple[List[Callable], List[Callable]]:
    kind = item["fig"]
    if kind == "count":
        stem = [lambda d, b, n=n: gfd.draw_circles(d, b, n) for n in item["stem_counts"]]
        opts = [lambda d, b, n=n: gfd.draw_circles(d, b, n) for n in item["option_counts"]]
    elif kind == "count_ambig":
        stem = [lambda d, b, n=n: gfd.draw_circles(d, b, n) for n in item["stem_counts"]]
        opts = [
            lambda d, b: gfd.draw_circles(d, b, 4),
            lambda d, b: paint_grid_circles(d, b),
            lambda d, b: gfd.draw_circles(d, b, 5),
            lambda d, b: gfd.draw_circles(d, b, 2),
        ]
    elif kind == "squares":
        stem = [lambda d, b, n=n: paint_squares(d, b, n) for n in item["stem_counts"]]
        opts = [lambda d, b, n=n: paint_squares(d, b, n) for n in item["option_counts"]]
    elif kind == "sides":
        stem = [lambda d, b, n=n: gfd.draw_polygon(d, b, n) for n in item["stem_sides"]]
        opts = [lambda d, b, n=n: gfd.draw_polygon(d, b, n) for n in item["option_sides"]]
    elif kind == "rotation":
        stem = [lambda d, b, a=a: paint_arrow(d, b, a) for a in item["stem_angles"]]
        opts = [lambda d, b, a=a: paint_arrow(d, b, a) for a in item["option_angles"]]
    elif kind == "translation":
        stem = [lambda d, b, p=p: paint_box_dot(d, b, *p) for p in item["stem_pos"]]
        opts = [lambda d, b, p=p: paint_box_dot(d, b, *p) for p in item["option_pos"]]
    elif kind == "flag_flip":
        stem = [lambda d, b, m=m: paint_flag(d, b, mirror=m) for m in item["stem_mirrors"]]
        opts = [
            lambda d, b: paint_flag(d, b, mirror=False),
            lambda d, b: paint_flag(d, b, mirror=True),
            lambda d, b: gfd.draw_circles(d, b, 1),
            lambda d, b: paint_box_dot(d, b, 0.5, 0.5),
        ]
    elif kind == "scaling":
        stem = [lambda d, b, r=r: paint_circle_r(d, b, r) for r in item["stem_radii"]]
        opts = [lambda d, b, r=r: paint_circle_r(d, b, r) for r in item["option_radii"]]
    else:
        raise ValueError(kind)
    return stem, opts


def render_item(page: Paper, item: Dict[str, Any]) -> Dict[str, Any]:
    kind = item["kind"]
    result: Dict[str, Any] = {}
    if "paragraph_lines" in item:
        page.paragraph(item["paragraph_lines"])
    if kind in {"text", "personality"}:
        if kind == "personality":
            page.stem(item["stem"])
            page.stem("请选择最符合你近期真实感受的一项（本题没有对错之分）：", size=18)
        else:
            page.stem(item["stem"])
        stem_bbox = _last_stem_bbox(page)
        option_regions = page.options_text(item["options"], layout="twocol" if item["layout"] == "twocol_text" else "row4")
        result.update(stem_bbox=stem_bbox, option_regions=option_regions,
                      reading_order=[entry["label"] for entry in option_regions])
    elif kind == "fig":
        page.stem(item["stem"])
        stem_painters, option_painters = _fig_painters(item)
        band = page.figure_band(
            stem_painters, option_painters,
            option_rows=2 if item["layout"] == "two_row_options" else 1,
            option_texts=item.get("text_options"),
        )
        labels = [entry["label"] for entry in band["option_regions"]]
        result.update(stem_bbox=_last_stem_bbox(page),
                      stem_matrix_bbox=band["stem_bbox"], option_regions=band["option_regions"],
                      reading_order=["stem", "d1"] + labels)
    elif kind == "grid":
        page.stem(item["stem"])
        grid_kind = item["fig"]
        if grid_kind == "grid_xor":
            cells = [
                [mask_painter("rect", 0), mask_painter("circle", 0), mask_painter("xor", 0, "xor")],
                [mask_painter("rect", 1), mask_painter("circle", 1), mask_painter("xor", 1, "xor")],
                [mask_painter("rect", 2), mask_painter("circle", 2), None],
            ]
            options = [
                mask_painter("xor", 2, "xor"),   # A：构造答案
                mask_painter("rect", 2),
                mask_painter("or", 2, "or"),
                mask_painter("circle", 2),
            ]
        else:  # grid_notch
            def notch(base: str, count: int) -> Callable:
                return lambda d, b, k=base, n=count: paint_notched(d, b, k, n)

            cells = [
                [notch("circle", 0), notch("circle", 1), notch("circle", 2)],
                [notch("square", 0), notch("square", 1), notch("square", 2)],
                [notch("triangle", 0), notch("triangle", 1), None],
            ]
            options = [notch("triangle", 0), notch("triangle", 1), notch("triangle", 2), notch("circle", 2)]
        band = page.grid_band(cells, options)
        result.update(stem_bbox=_last_stem_bbox(page),
                      stem_matrix_bbox=band["stem_bbox"], stem_matrix_grid=band["grid"],
                      option_regions=band["option_regions"],
                      reading_order=["stem", "d1"] + [e["label"] for e in band["option_regions"]])
    elif kind == "table":
        page.stem(item["stem"])
        band = page.table_band(item["table"])
        page.stem(item["question"])
        option_regions = page.options_text(item["options"], layout="row4")
        result.update(stem_bbox=band["table_bbox"], table={"bbox": band["table_bbox"], "cells": band["table_rows"]},
                      option_regions=option_regions,
                      reading_order=["stem", "table"] + [e["label"] for e in option_regions])
    elif kind == "chart":
        labels, values = ["甲", "乙", "丙"], item["values"]
        show_text = bool(item.get("value_text", True))
        top, max_h = page.cursor_y, 150
        base_y = top + max_h + 10
        x = 80
        for name, value in zip(labels, values):
            bar_h = int(max_h * value / max(values))
            page.draw.rectangle([x, base_y - bar_h, x + 46, base_y], outline=0, width=3)
            if show_text:
                page._line(x + 12, base_y - bar_h - 26, str(value), _font(18))
            page._line(x + 14, base_y + 4, name, _font(18))
            x += 110
        page.draw.line([60, base_y, x - 60, base_y], fill=0, width=2)
        chart_bbox = [60, top, x - 120, base_y - top + 30]
        chart_text = " ".join(f"{n}组{v}件" for n, v in zip(labels, values)) if show_text else ""
        page.cursor_y = base_y + 42
        page.stem(item["question"])
        option_regions = page.options_text(item["options"], layout="row4")
        result.update(stem_bbox=_last_stem_bbox(page),
                      option_regions=option_regions,
                      chart_bbox=chart_bbox, chart_text=chart_text,
                      extra_text_regions=([{"id": "chart", "bbox": chart_bbox, "text": chart_text}]
                                          if chart_text else []),
                      reading_order=["chart", "stem"] + [e["label"] for e in option_regions])
    else:
        raise ValueError(kind)
    return result


HEIGHTS = {
    "text": 300,
    "personality": 280,
    "fig": 480,
    "grid": 810,
    "table": 520,
    "chart": 480,
}


def build_records() -> List[Dict[str, Any]]:
    items = content_items()

    # 防泄漏断言：同一 group 只进一个 split
    group_split: Dict[str, str] = {}
    for item in items:
        if item["group"] in group_split and group_split[item["group"]] != item["split"]:
            raise SystemExit(f"防泄漏断言失败：组 {item['group']} 跨 split")
        group_split[item["group"]] = item["split"]

    records: List[Dict[str, Any]] = []
    for item in items:
        height = HEIGHTS[item["kind"]]
        if item["kind"] == "fig" and item["layout"] == "two_row_options":
            height = 630
        if item.get("paragraph_lines"):
            height = 380
        page = Paper(height, browser=bool(item.get("chrome")), watermark=item.get("watermark"))
        result = render_item(page, item)

        scale = item.get("lowres", 1.0)
        if scale != 1.0:
            page.image = page.image.resize((int(page.width * scale), int(page.height * scale)), Image.NEAREST)
        if item.get("dark"):
            page.image = ImageOps.invert(page.image.convert("L"))
        final_w, final_h = page.image.size

        def scale_bbox(bbox: List[int]) -> List[int]:
            x = min(int(round(bbox[0] * scale)), final_w - 1)
            y = min(int(round(bbox[1] * scale)), final_h - 1)
            w = max(1, min(int(round(bbox[2] * scale)), final_w - x))
            h = max(1, min(int(round(bbox[3] * scale)), final_h - y))
            return [x, y, w, h]

        def to_pil_box(xywh: List[int]) -> Tuple[int, int, int, int]:
            x, y, w, h = xywh
            return (x, y, x + w, y + h)

        option_regions = result.get("option_regions") or []
        for entry in option_regions:
            entry["bbox"] = scale_bbox(entry["bbox"])
            if entry.get("figure_bbox"):
                entry["figure_bbox"] = scale_bbox(entry["figure_bbox"])

        stem_matrix_bbox = scale_bbox(result["stem_matrix_bbox"]) if result.get("stem_matrix_bbox") else None

        # ---- 标注 QA（与解题能力无关） ----
        problems: List[str] = []

        def in_bounds(bbox: List[int]) -> bool:
            return 0 <= bbox[0] and 0 <= bbox[1] and bbox[0] + bbox[2] <= final_w and bbox[1] + bbox[3] <= final_h

        for entry in option_regions:
            if not in_bounds(entry["bbox"]):
                problems.append(f"选项 {entry['label']} 越界")
        labels = [e["label"] for e in option_regions]
        if len(labels) != len(set(labels)):
            problems.append("选项标号重复")
        if item.get("answer") and labels and item["answer"] not in labels:
            problems.append(f"答案标号 {item['answer']} 不在选项 {labels} 中")

        crop_dir = REGION_DIR / item["id"]
        crop_dir.mkdir(parents=True, exist_ok=True)
        stem_rel: Optional[str] = None
        if stem_matrix_bbox:
            crop = page.image.crop(to_pil_box(stem_matrix_bbox))
            crop.save(crop_dir / "stem.png")
            stem_rel = f"regions/{item['id']}/stem.png"
            if _ink_ratio(crop) < 0.001:
                problems.append("题干矩阵裁剪为空白")
        for entry in option_regions:
            if entry.get("has_figure") and entry.get("figure_bbox"):
                crop = page.image.crop(to_pil_box(entry["figure_bbox"]))
                crop.save(crop_dir / f"option_{entry['label']}.png")
                entry["crop_path"] = f"regions/{item['id']}/option_{entry['label']}.png"
                if _ink_ratio(crop) < 0.001:
                    problems.append(f"选项 {entry['label']} 图形裁剪空白")
        chart_crop_rel = None
        if (item["split"] == "dev" and item.get("kind") == "chart" and result.get("chart_bbox")):
            cb = result["chart_bbox"]
            crop = page.image.crop(to_pil_box(cb))
            crop.save(crop_dir / "chart.png")
            if _ink_ratio(crop) < 0.001:
                problems.append("统计图裁剪为空白")
            chart_crop_rel = f"regions/{item['id']}/chart.png"
        if problems:
            raise SystemExit(f"{item['id']} 标注 QA 失败: {problems}")

        image_dir = IMAGE_DIR / item["split"]
        image_dir.mkdir(parents=True, exist_ok=True)
        page.image.save(image_dir / f"{item['id']}.png")
        digest = hashlib.sha256((image_dir / f"{item['id']}.png").read_bytes()).hexdigest()

        # 选项 payload：图形选项文字以"图"占位（与 screens 约定一致），文字选项保持原文
        options_payload = [
            {"label": e["label"], "text": e["text"] or ("图" if e.get("has_figure") else "")}
            for e in option_regions
        ] or [{"label": label, "text": text} for label, text in item.get("options", [])]

        stem_text = item.get("stem") or item.get("question", "")
        if item.get("paragraph_lines"):
            stem_text = "".join(item["paragraph_lines"]) + item["stem"]
        # R3a（仅 Dev）：表格题把小问并入记录题干——图片本就画了这两行文字，
        # 合并后题干文本与图像严格一致，且计算计划必须能看到小问语义。Holdout 不动。
        if (item["split"] == "dev" and item.get("kind") == "table"
                and item.get("stem") and item.get("question")):
            stem_text = item["stem"] + item["question"]

        text_regions = [{"id": r["id"], "bbox": scale_bbox(r["bbox"]), "text": r["text"]} for r in page.text_regions if r["id"] in {"stem", "passage"}] + [
            {"id": r["id"], "bbox": scale_bbox(r["bbox"]), "text": r["text"]} for r in result.get("extra_text_regions", [])
        ]

        regions: Dict[str, Any] = {
            "bbox_format": "pixel_xywh",
            "image_size": [final_w, final_h],
            "stem_matrix_bbox": stem_matrix_bbox,
            "stem_matrix_grid": result.get("stem_matrix_grid"),
            "stem_matrix_crop": stem_rel,
            "option_regions": option_regions,
            "reading_order": result.get("reading_order", []),
        }
        if result.get("table"):
            regions["table"] = {"bbox": scale_bbox(result["table"]["bbox"]), "cells": result["table"]["cells"]}
        # R3a（仅 Dev）：oracle/auto 模式同样提供文本/统计图金标证据，与 image_only 对齐
        if item["split"] == "dev":
            if text_regions:
                regions["text_regions"] = text_regions
            if item.get("kind") == "chart" and result.get("chart_bbox"):
                regions["chart"] = {"bbox": scale_bbox(result["chart_bbox"]),
                                     "text": result.get("chart_text", ""),
                                     "crop_path": chart_crop_rel}

        records.append(
            {
                "id": item["id"],
                "skill": item["skill"],
                "stem": stem_text,
                "options": options_payload,
                "answer": item.get("answer"),
                "expected_status": item.get("expected", "solved"),
                "tags": ["benchmark_real", "independent-synthetic", item["layout"]],
                "group": item["group"],
                "layout": item["layout"],
                "image": f"benchmark_real/images/{item['split']}/{item['id']}.png",
                "grid": result.get("stem_matrix_grid"),
                "regions": regions,
                "text_regions": text_regions,
                # created 必须确定性：冻结 Holdout 保持 2026-10-17 字节不变（重生成不得漂移日期）
                "source": {**SOURCE,
                           "created": "2026-10-17" if item["split"] == "holdout" else "2026-10-18",
                           "note": item["note"]},
                "image_sha256": digest,
            }
        )
    return records


def write_outputs(records: List[Dict[str, Any]]) -> None:
    for split in ("dev", "holdout"):
        directory = BENCH_DIR / split
        directory.mkdir(parents=True, exist_ok=True)
        split_records = [r for r in records if f"/{split}/" in r["image"].replace("\\", "/")]
        lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in split_records) + "\n"
        (directory / "independent.jsonl").write_text(lines, encoding="utf-8")

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    by_group: Dict[str, List[str]] = {}
    for record in records:
        by_group.setdefault(record["group"], []).append(record["id"])
    dev_path, hold_path = BENCH_DIR / "dev" / "independent.jsonl", BENCH_DIR / "holdout" / "independent.jsonl"
    dev_ids = [json.loads(line)["id"] for line in dev_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    hold_ids = [json.loads(line)["id"] for line in hold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = {
        "name": "benchmark_real",
        "data_class": "independent-synthetic（独立自编模拟测评页面；不是真实招聘平台截图）",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "authorship": SOURCE["author"],
        "license": SOURCE["license"],
        "contains_real_exam": False,
        "contains_pii": False,
        "independence_declaration": (
            "样本按内容表独立选择，未按当前 Solver 是否解对进行筛选；"
            "包含已知超出现有规则能力的题型（旋转/翻转/半径外推/缺口九宫格/质数）"
            "与未实现题型（资料分析/言语理解/性格），这些样本的失败是基准要测量的信号。"
        ),
        "splits": {
            "dev": {"count": len(dev_ids), "file": "dev/independent.jsonl", "sha256": sha(dev_path)},
            "holdout": {
                "count": len(hold_ids),
                "file": "holdout/independent.jsonl",
                "sha256": sha(hold_path),
                "frozen": True,
                "frozen_date": date.today().isoformat(),
                "freeze_policy": "冻结后不得根据求解结果增删样本；扩充只能新建版本目录",
            },
        },
        "leakage_groups": by_group,
        "skill_coverage": sorted({r["skill"] for r in records}),
        "layout_coverage": sorted({r["layout"] for r in records}),
        "expected_status_coverage": sorted({r["expected_status"] for r in records}),
    }
    (BENCH_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 独立基准：{len(records)} 样本 → {BENCH_DIR}")
    print(f"     dev={len(dev_ids)}  holdout={len(hold_ids)}（冻结）")


def main() -> int:
    for stale in (IMAGE_DIR, REGION_DIR, BENCH_DIR / "dev", BENCH_DIR / "holdout"):
        if stale.exists():
            shutil.rmtree(stale)
    records = build_records()
    write_outputs(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
