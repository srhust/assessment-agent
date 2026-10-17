"""图形布局适配层（R2a）：题干矩阵 + 独立选项裁剪 → 现有 figure_rules 六类规律。

设计约束：
- **不重写规律引擎**：复用 ``figure_rules._solve_sequence`` / ``_solve_grid`` 与全部规则函数；
- 解决旧链路两大限制：不再假定"整张截图最后一行是选项"，也不再要求外部提供覆盖
  整图的 ``grid``（grid 只作用于题干矩阵裁剪区域）；
- 选项标号 A/B/C/D/E 由区域解析给出并**原样保留**（按阅读顺序传入），不依赖位置推断；
- 为了在不改动叠加规律（需要同一张掩码画布）的前提下工作，把题干与选项裁剪合成为
  一张证据画布（composite canvas）并落盘，全部推断只在画布内进行；
- 任何区域缺失 / 空白 / 无法成网格 → 返回结构化失败并给出原因，绝不猜测。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .base import ToolError
from .figure_rules import _failed, _solve_grid, _solve_sequence  # noqa: PLC2701 - 同包内复用引擎入口
from .figure_tools import analyze_layout, cell_features, layout_summary, load_mask

MAX_OPTIONS = 6
MIN_OPTIONS = 2


def _save_canvas(mask: np.ndarray, target: Path) -> Optional[str]:
    """把 bool 掩码写成黑字白底的 PNG 证据。"""
    image = np.where(mask, 0, 255).astype(np.uint8)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cv2

        if cv2.imwrite(str(target), image):
            return str(target)
    except ImportError:
        pass
    try:
        from PIL import Image

        Image.fromarray(image).save(target)
        return str(target)
    except Exception:  # noqa: BLE001
        return None


def solve_figure_regions(
    stem_image: str | Path,
    option_images: Sequence[Tuple[str, str]],
    *,
    grid: Optional[Sequence[int]] = None,
    threshold: Optional[float] = None,
    canvas_path: Optional[str | Path] = None,
    pad: int = 24,
) -> Dict[str, Any]:
    """用区域裁剪求解图形题，返回与 ``solve_figure`` 同构的 payload。

    ``option_images``：``[(label, crop_path), ...]``，按题目阅读顺序，标号原样保留。
    ``grid``：仅作用于题干矩阵裁剪（行、列），选项不再被网格覆盖。
    """
    stem = Path(stem_image)
    if not stem.is_file():
        raise ToolError(f"题干矩阵裁剪不存在: {stem}")

    # ------------------------------------------------ 输入合法性（不猜测）
    labels = [label for label, _ in option_images]
    if len(labels) != len(set(labels)):
        raise ToolError(f"选项标号重复: {labels}")
    if len(labels) < MIN_OPTIONS:
        raise ToolError(f"选项裁剪只有 {len(labels)} 个，至少需要 {MIN_OPTIONS} 个")
    if len(labels) > MAX_OPTIONS:
        raise ToolError(f"选项裁剪 {len(labels)} 个超过上限 {MAX_OPTIONS}，区域解析可能失败")
    missing = [label for label, path in option_images if not Path(path).is_file()]
    if missing:
        raise ToolError(f"选项裁剪缺失: {missing}")

    # ------------------------------------------------ 题干网格（仅题干裁剪）
    stem_layout = analyze_layout(stem, threshold=threshold, grid=grid)
    question_bands = stem_layout["bands"]
    if not question_bands:
        return {
            "status": "failed",
            "mode": "regions",
            "answer": None,
            "rule": None,
            "agreement": [],
            "confidence": 0.0,
            "reason": "题干矩阵未检测到任何图形（裁剪可能为空或布局无法解析）",
            "steps": ["题干矩阵未检测到任何图形"],
            "candidates": [],
            "layout": {"mode": "regions", "size": stem_layout["size"], "cells_per_band": []},
            "options": [],
            "adapter": {"stem_crop": str(stem), "option_labels": labels},
        }

    # ------------------------------------------------ 选项特征
    option_masks: List[np.ndarray] = []
    option_features: List[Dict[str, Any]] = []
    for label, path in option_images:
        mask = load_mask(path, threshold)
        features = cell_features(mask)
        if features["blank"]:
            return _blank_option_failure(stem_layout, option_images, label, str(stem))
        option_masks.append(mask)
        option_features.append(features)

    # ------------------------------------------------ 合成证据画布
    stem_mask = load_mask(stem, threshold)
    stem_h, stem_w = stem_mask.shape
    cell_h = max(mask.shape[0] for mask in option_masks)
    cell_w = max(mask.shape[1] for mask in option_masks)
    row_width = len(option_masks) * cell_w + (len(option_masks) + 1) * pad
    canvas_w = max(stem_w, row_width)
    canvas_h = stem_h + cell_h + 3 * pad
    canvas = np.zeros((canvas_h, canvas_w), dtype=bool)
    canvas[:stem_h, :stem_w] = stem_mask

    option_band_cells: List[Dict[str, Any]] = []
    for index, ((label, path), mask) in enumerate(zip(option_images, option_masks)):
        x0 = pad + index * (cell_w + pad) + (canvas_w - row_width) // 2
        y0 = stem_h + 2 * pad
        h, w = mask.shape
        canvas[y0 : y0 + h, x0 : x0 + w] = mask
        option_band_cells.append(
            {
                "index": index,
                "column": index,
                "bbox": [int(x0), int(y0), int(x0 + w), int(y0 + h)],
                "features": cell_features(canvas[y0 : y0 + h, x0 : x0 + w]),
                "label": label,
                "source": str(path),
            }
        )

    canvas_file: Optional[str] = None
    if canvas_path is not None:
        canvas_file = _save_canvas(canvas, Path(canvas_path))

    layout = {
        "path": canvas_file or str(stem),
        "threshold": stem_layout["threshold"],
        "size": [int(canvas_w), int(canvas_h)],
        "mode": "regions",
        "grid": stem_layout.get("grid"),
        "pitch": stem_layout.get("pitch"),
        "grid_regular": stem_layout.get("grid_regular", False),
        "bands": list(question_bands) + [{"index": len(question_bands), "row": len(question_bands), "bbox": [0, stem_h + pad, canvas_w, canvas_h], "cells": option_band_cells}],
        "canvas_saved": canvas_file,
    }

    options = [(label, option_features[index]) for index, (label, _) in enumerate(option_images)]
    adapter_meta = {
        "source": "vision_regions",
        "stem_crop": str(stem),
        "stem_grid": list(grid) if grid else stem_layout.get("grid"),
        "stem_layout_mode": stem_layout.get("mode"),
        "option_labels": labels,
        "option_crops": [str(path) for _, path in option_images],
        "canvas": canvas_file,
    }

    # ------------------------------------------------ 复用原引擎推断
    if len(question_bands) == 1:
        row = _aligned_row(question_bands[0], stem_layout)
        result = _solve_sequence(row, options, layout)
    else:
        rows = [_aligned_row(band, stem_layout) for band in question_bands]
        result = _solve_grid(rows, option_band_cells, options, layout, canvas_file or str(stem), threshold)
    result["adapter"] = adapter_meta
    result["steps"] = [
        f"[区域适配] 题干裁剪 {stem.name}（{'显式网格 ' + str(tuple(grid)) if grid else '自动布局'}）+ "
        f"{len(labels)} 个选项裁剪按标号 {labels} 映射，合成证据画布后按原规律引擎推断"
    ] + list(result.get("steps") or [])
    return result


def _aligned_row(band: Dict[str, Any], stem_layout: Dict[str, Any]) -> List[Optional[Dict[str, Any]]]:
    """把题干行按列号对齐（含空白格），语义与 figure_rules._aligned 一致。"""
    columns = max((cell["column"] for cell in band["cells"]), default=-1) + 1
    if stem_layout.get("mode") == "explicit" and stem_layout.get("grid"):
        columns = int(stem_layout["grid"][1])
    mapping = {cell["column"]: cell for cell in band["cells"]}
    return [mapping.get(column) for column in range(columns)]


def _blank_option_failure(
    stem_layout: Dict[str, Any],
    option_images: Sequence[Tuple[str, str]],
    blank_label: str,
    stem: str,
) -> Dict[str, Any]:
    labels = [label for label, _ in option_images]
    return {
        "status": "failed",
        "mode": "regions",
        "answer": None,
        "rule": None,
        "agreement": [],
        "confidence": 0.0,
        "reason": f"选项 {blank_label} 的裁剪区域几乎空白，无法参与规律匹配（不猜测）",
        "steps": [f"选项 {blank_label} 裁剪为空"],
        "candidates": [],
        "layout": {"mode": "regions", "size": stem_layout["size"], "cells_per_band": []},
        "options": [],
        "adapter": {"source": "vision_regions", "stem_crop": stem, "option_labels": labels},
    }
