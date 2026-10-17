"""图形推理工具：图像 → 规则网格 → 单元格特征（可独立测试）。

第一阶段能力（真实实现）：
- 二值化（Otsu 或固定阈值），返回墨迹掩码；
- 布局解析：行投影切“行带”、列投影切“单元格”，再由单元格中心的中位间距还原**统一网格**，
  从而让质心、面积占比等特征可以跨格比较，并能推断末格为空白；
- 单元格特征：连通域数量、多边形边数（approxPolyDP）、墨迹占比、包围盒、质心、
  水平/垂直镜像对称度（允许 ±2px 平移，避免细线条因栅格化错位而误判）、孔洞数。

仍在计划中的能力（显式抛 ``NotImplementedError``，不产生推测性结论）：
旋转/翻转匹配、曲线图形分类、非叠加类九宫格规律、3D/阴影类特征。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .base import Tool, ToolError

DEFAULT_CELL_GAP = 15
DEFAULT_BAND_GAP = 8
MIN_INK_PIXELS = 20
SYMMETRY_SHIFT = 2

_PLANNED_OPS = {
    "rotation_match": "旋转/翻转匹配（同一图形在角度上的连续变化）",
    "curved_shape_classification": "曲线图形与复杂图形的分类特征",
    "grid_rule_inference_non_overlay": "九宫格中非叠加类规律（位置轮换、样式交替）",
    "shadow_3d_features": "立体/阴影类图形特征",
}


def backend_status() -> Dict[str, bool]:
    status: Dict[str, bool] = {}
    for module in ("numpy", "PIL", "cv2"):
        try:
            __import__(module)
            status[module] = True
        except ImportError:
            status[module] = False
    return status


def _require_cv2():
    try:
        import cv2  # noqa: F401
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise ToolError("图形分析需要 OpenCV：pip install opencv-python-headless") from exc
    return cv2


# --------------------------------------------------------------------- 基础
def load_gray(path: str | Path) -> np.ndarray:
    """读取图像为二维 float 数组（0=白，1=黑）。"""
    cv2 = _require_cv2()
    image_path = Path(path)
    if not image_path.is_file():
        raise ToolError(f"图像不存在: {image_path}")
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ToolError(f"无法解码图像: {image_path}")
    return 1.0 - image.astype(np.float32) / 255.0


def load_mask(path: str | Path, threshold: Optional[float] = None) -> np.ndarray:
    """二值化：返回 bool 掩码，True 表示墨迹（图形）。``threshold=None`` 用 Otsu。"""
    cv2 = _require_cv2()
    image_path = Path(path)
    if not image_path.is_file():
        raise ToolError(f"图像不存在: {image_path}")
    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ToolError(f"无法解码图像: {image_path}")
    if threshold is None:
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    else:
        _, binary = cv2.threshold(gray, float(threshold), 255, cv2.THRESH_BINARY_INV)
    return binary > 0


def _segments(flags: np.ndarray, min_gap: int) -> List[Tuple[int, int]]:
    """把布尔序列切成若干连续段，间隔小于 ``min_gap`` 的段会被合并。"""
    indices = np.nonzero(flags)[0]
    if indices.size == 0:
        return []
    groups: List[List[int]] = [[int(indices[0]), int(indices[0])]]
    for position in indices[1:]:
        position = int(position)
        if position - groups[-1][1] - 1 < min_gap:
            groups[-1][1] = position
        else:
            groups.append([position, position])
    return [(start, end + 1) for start, end in groups]


def _iou(left: np.ndarray, right: np.ndarray) -> float:
    union = np.logical_or(left, right).sum()
    if union == 0:
        return 1.0
    return float(np.logical_and(left, right).sum() / union)


def _shift(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """平移掩码，空出的部分补 False（不使用 np.roll，避免环绕）。"""
    if dy == 0 and dx == 0:
        return mask
    height, width = mask.shape
    out = np.zeros_like(mask)
    src_y0, src_y1 = max(0, -dy), min(height, height - dy)
    src_x0, src_x1 = max(0, -dx), min(width, width - dx)
    dst_y0, dst_y1 = max(0, dy), min(height, height + dy)
    dst_x0, dst_x1 = max(0, dx), min(width, width + dx)
    if src_y1 > src_y0 and src_x1 > src_x0:
        out[dst_y0:dst_y1, dst_x0:dst_x1] = mask[src_y0:src_y1, src_x0:src_x1]
    return out


def _symmetry_score(crop: np.ndarray, axis: str, max_shift: int = SYMMETRY_SHIFT) -> float:
    """镜像对称度。

    细线条图形在栅格化后常有一两个像素的错位，直接算 IoU 会把对称图形误判成不对称
    （实测等腰三角形只有 0.83）。这里改为：翻转后在 ±max_shift 像素内搜索，
    并对两侧各膨胀 1px 后计算覆盖率，取两个方向的较小值。
    """
    cv2 = _require_cv2()
    if crop.size == 0:
        return 0.0
    flipped = np.fliplr(crop) if axis == "vertical" else np.flipud(crop)
    kernel = np.ones((3, 3), np.uint8)
    dilated_self = cv2.dilate(crop.astype(np.uint8), kernel) > 0
    best = 0.0
    for shift in range(-max_shift, max_shift + 1):
        shifted = np.roll(flipped, shift, axis=1 if axis == "vertical" else 0)
        if not shifted.any():
            continue
        dilated_flipped = cv2.dilate(shifted.astype(np.uint8), kernel) > 0
        forward = float((shifted & dilated_self).sum()) / max(1, int(shifted.sum()))
        backward = float((crop & dilated_flipped).sum()) / max(1, int(crop.sum()))
        best = max(best, min(forward, backward))
    return best


# --------------------------------------------------------------- 单元格特征
def cell_features(mask: np.ndarray, *, symmetry_min: float = 0.82) -> Dict[str, Any]:
    """单个单元格（统一网格切出的图块）的几何特征。"""
    cv2 = _require_cv2()
    height, width = mask.shape
    total = float(height * width) if height and width else 1.0
    ink = int(mask.sum())
    if ink < MIN_INK_PIXELS:
        return {
            "blank": True,
            "ink_pixels": ink,
            "shape_count": 0,
            "sides": 0,
            "sides_all": [],
            "area_ratio": 0.0,
            "bbox_ratio": 0.0,
            "centroid": [0.0, 0.0],
            "bbox": [0, 0, 0, 0],
            "symmetry": {"vertical": 0.0, "horizontal": 0.0},
            "vertical_symmetric": False,
            "horizontal_symmetric": False,
            "holes": 0,
        }

    u8 = mask.astype(np.uint8)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(u8, connectivity=8)
    min_area = max(MIN_INK_PIXELS, int(0.002 * total))
    component_ids = [index + 1 for index in range(count - 1) if stats[index + 1, cv2.CC_STAT_AREA] >= min_area]
    shape_count = len(component_ids)

    sides: List[int] = []
    holes = 0
    for component_id in component_ids:
        component = (labels == component_id).astype(np.uint8)
        contours, hierarchy = cv2.findContours(component, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        main = max(contours, key=cv2.contourArea)
        perimeter = cv2.arcLength(main, True)
        if perimeter <= 0:
            continue
        approx = cv2.approxPolyDP(main, 0.02 * perimeter, True)
        if len(approx) >= 3:
            sides.append(int(len(approx)))
        if hierarchy is not None:
            holes += sum(1 for entry in hierarchy[0] if entry[3] != -1)

    ys, xs = np.nonzero(mask)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    crop = mask[y0:y1, x0:x1]
    vertical = _symmetry_score(crop, "vertical")
    horizontal = _symmetry_score(crop, "horizontal")
    bbox_area = float(max(1, (y1 - y0) * (x1 - x0)))
    return {
        "blank": False,
        "ink_pixels": ink,
        "shape_count": shape_count,
        "sides": int(np.median(sides)) if sides else 0,
        "sides_all": sides,
        "area_ratio": round(ink / total, 6),
        "bbox_ratio": round(ink / bbox_area, 6),
        "centroid": [round(float(xs.mean()) / width, 6), round(float(ys.mean()) / height, 6)],
        "bbox": [x0, y0, x1, y1],
        "symmetry": {"vertical": round(vertical, 4), "horizontal": round(horizontal, 4)},
        "vertical_symmetric": bool(vertical >= symmetry_min),
        "horizontal_symmetric": bool(horizontal >= symmetry_min),
        "holes": holes,
    }


# ------------------------------------------------------------------ 布局解析
def _median(values: Sequence[float], default: float) -> float:
    return float(np.median(values)) if values else default


def _pitch_x(raw_bands: Sequence[Dict[str, Any]], widths: Sequence[float]) -> float:
    """列间距估计：优先选“格间距最一致”的行（通常是选项行）。

    叠加类图形可能出现两个连通块（如“去同存异”后的两枚月牙），按投影会被切成两段，
    因此不能直接用中位数间距；这里按间距变异系数挑选最整齐的一行。
    """
    candidates: List[Tuple[float, int, float]] = []
    for band in raw_bands:
        centers = [cell["x_center"] for cell in band["cells"]]
        if len(centers) < 2:
            continue
        diffs = [centers[index + 1] - centers[index] for index in range(len(centers) - 1)]
        mean = sum(diffs) / len(diffs)
        if mean <= 0:
            continue
        variance = sum((diff - mean) ** 2 for diff in diffs) / len(diffs)
        candidates.append(((variance ** 0.5) / mean, len(diffs), mean))
    if not candidates:
        return _median(widths, 0.0) * 1.35 or 1.0
    candidates.sort(key=lambda item: (round(item[0], 3), -item[1]))
    return candidates[0][2]


def analyze_layout(
    path: str | Path,
    *,
    threshold: Optional[float] = None,
    cell_gap: int = DEFAULT_CELL_GAP,
    band_gap: int = DEFAULT_BAND_GAP,
    grid: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """解析题目图片为“行带 × 列”的结构。

    - ``grid=(rows, cols)``：显式网格，按图片尺寸等分为 rows×cols 个单元（含空白格）；
    - 否则自动推断：由墨迹投影切行/切列，再由单元格中心的中位间距还原统一网格；
      若排列不是规则网格，则退回墨迹包围盒，并标记 ``grid_regular=False``
      （此时位置/平移类规律不可用，只使用与格内归一化无关的特征）。

    统一网格让质心、面积占比可以跨格比较；墨迹包围盒模式下这些特征只在格内归一化。
    """
    cv2 = _require_cv2()
    mask = load_mask(path, threshold)
    height, width = mask.shape

    # ------------------------------------------------------ 显式网格模式
    if grid is not None:
        rows, cols = (int(grid[0]), int(grid[1]))
        if rows < 1 or cols < 1 or rows * cols > 36:
            raise ToolError(f"grid 参数不合法: {tuple(grid)}")
        cell_w = width / cols
        cell_h = height / rows
        bands: List[Dict[str, Any]] = []
        for row in range(rows):
            y0 = int(round(row * cell_h))
            y1 = int(round((row + 1) * cell_h))
            cells: List[Dict[str, Any]] = []
            for column in range(cols):
                x0 = int(round(column * cell_w))
                x1 = int(round((column + 1) * cell_w))
                box = (x0, y0, x1, y1)
                features = cell_features(mask[y0:y1, x0:x1])
                cells.append(
                    {
                        "index": column,
                        "column": column,
                        "bbox": [x0, y0, x1, y1],
                        "features": features,
                    }
                )
            bands.append({"index": row, "row": row, "bbox": [0, y0, int(width), y1], "cells": cells})
        return {
            "path": str(path),
            "threshold": "otsu" if threshold is None else float(threshold),
            "size": [int(width), int(height)],
            "mode": "explicit",
            "grid": [rows, cols],
            "pitch": [round(cell_w, 3), round(cell_h, 3)],
            "grid_regular": True,
            "grid_deviation": 0.0,
            "bands": bands,
            "cell_gap": cell_gap,
            "band_gap": band_gap,
        }

    # ---------------------------------------------------------- 自动推断
    band_ranges = _segments(mask.any(axis=1), band_gap)
    kernel = np.ones((1, max(1, cell_gap)), np.uint8)

    raw_bands: List[Dict[str, Any]] = []
    for y0, y1 in band_ranges:
        band_mask = mask[y0:y1]
        closed = cv2.morphologyEx(band_mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
        cell_ranges = _segments(closed.any(axis=0), 2)
        raw_bands.append(
            {
                "y0": int(y0),
                "y1": int(y1),
                "y_center": (y0 + y1) / 2.0,
                "cells": [{"x0": int(x0), "x1": int(x1), "x_center": (x0 + x1) / 2.0} for x0, x1 in cell_ranges],
            }
        )

    x_diffs: List[float] = []
    widths: List[float] = []
    for band in raw_bands:
        centers = [cell["x_center"] for cell in band["cells"]]
        widths.extend(cell["x1"] - cell["x0"] for cell in band["cells"])
        x_diffs.extend(centers[index + 1] - centers[index] for index in range(len(centers) - 1))
    pitch_x = _pitch_x(raw_bands, widths)

    y_centers = [band["y_center"] for band in raw_bands]
    y_diffs = [y_centers[index + 1] - y_centers[index] for index in range(len(y_centers) - 1)]
    pitch_y = _median(y_diffs, pitch_x)

    anchor_x = min((cell["x_center"] for band in raw_bands for cell in band["cells"]), default=0.0)
    anchor_y = min(y_centers, default=0.0)

    # --- 归类到整数网格并检查规则性 -------------------------------------
    deviation = 0.0
    classified: List[Dict[str, Any]] = []
    for band_index, band in enumerate(raw_bands):
        row = int(round((band["y_center"] - anchor_y) / pitch_y)) if pitch_y else 0
        deviation = max(deviation, abs(band["y_center"] - (anchor_y + row * pitch_y)))
        # 同一列里出现多段墨迹（例如“去同存异”后的两枚月牙）→ 合并为一格
        grouped: Dict[int, Dict[str, float]] = {}
        for cell in sorted(band["cells"], key=lambda item: item["x_center"]):
            column = int(round((cell["x_center"] - anchor_x) / pitch_x)) if pitch_x else 0
            entry = grouped.setdefault(column, {"x0": cell["x0"], "x1": cell["x1"]})
            entry["x0"] = min(entry["x0"], cell["x0"])
            entry["x1"] = max(entry["x1"], cell["x1"])
        cells: List[Dict[str, Any]] = []
        for column in sorted(grouped):
            box = grouped[column]
            center = (box["x0"] + box["x1"]) / 2.0
            deviation = max(deviation, abs(center - (anchor_x + column * pitch_x)))
            cells.append({"column": column, "x0": box["x0"], "x1": box["x1"], "x_center": center})
        classified.append({"index": band_index, "row": row, "y0": band["y0"], "y1": band["y1"], "cells": cells})

    regular = bool(pitch_x > 1 and pitch_y > 1 and deviation <= 0.25 * max(pitch_x, pitch_y))

    # --- 生成单元格并计算特征 -------------------------------------------
    layout_bands: List[Dict[str, Any]] = []
    for band in classified:
        row = band["row"]
        y_center = anchor_y + row * pitch_y
        y0 = max(0, int(round(y_center - pitch_y / 2)))
        y1 = min(height, int(round(y_center + pitch_y / 2)))
        cells: List[Dict[str, Any]] = []
        for cell in band["cells"]:
            column = cell["column"]
            if regular:
                x_center = anchor_x + column * pitch_x
                x0 = max(0, int(round(x_center - pitch_x / 2)))
                x1 = min(width, int(round(x_center + pitch_x / 2)))
                box = (x0, y0, x1, y1)
            else:  # 兜底：退回墨迹包围盒（位置类特征不可用）
                box = (cell["x0"], max(0, band["y0"]), cell["x1"], min(height, band["y1"]))
            features = cell_features(mask[box[1] : box[3], box[0] : box[2]])
            cells.append(
                {
                    "index": column,
                    "column": column,
                    "bbox": [int(box[0]), int(box[1]), int(box[2]), int(box[3])],
                    "features": features,
                }
            )
        layout_bands.append(
            {
                "index": band["index"],
                "row": row,
                "bbox": [0, y0, int(width), y1],
                "cells": cells,
            }
        )

    return {
        "path": str(path),
        "threshold": "otsu" if threshold is None else float(threshold),
        "size": [int(width), int(height)],
        "mode": "auto",
        "pitch": [round(pitch_x, 3), round(pitch_y, 3)],
        "grid_regular": regular,
        "grid_deviation": round(deviation, 3),
        "bands": layout_bands,
        "cell_gap": cell_gap,
        "band_gap": band_gap,
    }


def layout_summary(layout: Dict[str, Any]) -> Dict[str, Any]:
    """用于日志/产物的小结（不含完整特征，避免产物膨胀）。"""
    return {
        "threshold": layout["threshold"],
        "size": layout["size"],
        "mode": layout.get("mode"),
        "grid": layout.get("grid"),
        "pitch": layout.get("pitch"),
        "grid_regular": layout.get("grid_regular"),
        "cells_per_band": [len(band["cells"]) for band in layout["bands"]],
        "columns_per_band": [[cell["column"] for cell in band["cells"]] for band in layout["bands"]],
        "blanks": sum(
            1 for band in layout["bands"] for cell in band["cells"] if cell["features"]["blank"]
        ),
    }


def normalized_cell_mask(
    mask: np.ndarray,
    bbox: Sequence[int],
    size: int = 96,
) -> np.ndarray:
    """把单元格裁切并缩放到统一尺寸，供叠加/匹配比较。"""
    cv2 = _require_cv2()
    x0, y0, x1, y1 = (int(value) for value in bbox[:4])
    crop = mask[y0:y1, x0:x1]
    if crop.size == 0:
        return np.zeros((size, size), dtype=bool)
    resized = cv2.resize(crop.astype(np.uint8), (size, size), interpolation=cv2.INTER_NEAREST)
    return resized > 0


def cell_mask_iou(left: np.ndarray, right: np.ndarray) -> float:
    return _iou(left, right)


# ------------------------------------------------------------------ 兼容接口
def image_summary(path: str | Path) -> Dict[str, Any]:
    cv2 = _require_cv2()
    image_path = Path(path)
    if not image_path.is_file():
        raise ToolError(f"图像不存在: {image_path}")
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ToolError(f"无法解码图像: {image_path}")
    mask = load_mask(image_path)
    height, width = image.shape
    return {
        "path": str(image_path),
        "width": int(width),
        "height": int(height),
        "ink_ratio": round(float(mask.mean()), 6),
    }


def grid_ink_density(path: str | Path, rows: int = 3, cols: int = 3) -> Dict[str, Any]:
    if rows <= 0 or cols <= 0:
        raise ToolError("rows / cols 必须为正整数")
    mask = load_mask(path)
    height, width = mask.shape
    density: List[List[float]] = []
    for row in range(rows):
        top = int(height * row / rows)
        bottom = int(height * (row + 1) / rows)
        row_values: List[float] = []
        for col in range(cols):
            left = int(width * col / cols)
            right = int(width * (col + 1) / cols)
            block = mask[top:bottom, left:right]
            row_values.append(round(float(block.mean()) if block.size else 0.0, 6))
        density.append(row_values)
    return {"rows": rows, "cols": cols, "density": density, "shape": [int(height), int(width)]}


def _not_implemented(operation: str) -> NotImplementedError:
    detail = _PLANNED_OPS.get(operation, operation)
    return NotImplementedError(
        f"图形能力「{detail}」尚未实现，本阶段不提供推测性结论"
    )


class FigureTools(Tool):
    """图形题目工具：布局解析、单元格特征、叠加比较（真实实现）。"""

    id = "figure_tools"
    description = "图像统一网格解析、单元格几何特征、叠加 IoU 比较"
    requires = ("numpy", "opencv-python-headless")
    stage = 1

    def available(self) -> bool:
        status = backend_status()
        return status["numpy"] and status["cv2"]

    def unavailable_reason(self) -> Optional[str]:
        status = backend_status()
        missing = [name for name in ("numpy", "cv2") if not status[name]]
        return f"缺少依赖: {', '.join(missing)}（pip install opencv-python-headless）" if missing else None

    def capabilities(self) -> Dict[str, Any]:
        return {
            "backends": backend_status(),
            "implemented": [
                "image_summary",
                "grid_ink_density",
                "analyze_layout",
                "cell_features",
                "overlay_compare",
            ],
            "planned": dict(_PLANNED_OPS),
        }

    def run(self, operation: str = "capabilities", **kwargs: Any) -> Any:
        if operation == "capabilities":
            return self.capabilities()
        if operation == "image_summary":
            return image_summary(kwargs.get("path", ""))
        if operation == "grid_ink_density":
            return grid_ink_density(
                kwargs.get("path", ""),
                rows=int(kwargs.get("rows", 3)),
                cols=int(kwargs.get("cols", 3)),
            )
        if operation == "analyze_layout":
            layout = analyze_layout(
                kwargs.get("path", ""),
                threshold=kwargs.get("threshold"),
                cell_gap=int(kwargs.get("cell_gap", DEFAULT_CELL_GAP)),
                band_gap=int(kwargs.get("band_gap", DEFAULT_BAND_GAP)),
                grid=kwargs.get("grid"),
            )
            layout["summary"] = layout_summary(layout)
            return layout
        if operation == "overlay_compare":
            left = np.asarray(kwargs.get("left"), dtype=bool)
            right = np.asarray(kwargs.get("right"), dtype=bool)
            return {"iou": _iou(left, right)}
        raise _not_implemented(operation)
