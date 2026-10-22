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
    from .option_evidence import strip_label_from_crop
    for label, path in option_images:
        mask = strip_label_from_crop(load_mask(path, threshold))
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
    if canvas_path is None:
        # 叠加规律需要按 bbox 从同一张掩码画布重读选项区域——画布必须存在，
        # 但**绝不默认写入数据/裁剪目录**（R2c：数据集必须保持生成器产物纯净，
        # 画布是每次调用的中间证据，落到可追溯的临时目录；调用方可显式指定 runs/ 路径）
        import hashlib as _hl
        import tempfile as _tf

        signature = _hl.sha1(
            "|".join([str(stem), str(grid), str(threshold)] + [str(p) for _, p in option_images]).encode("utf-8")
        ).hexdigest()[:16]
        tmp_root = Path(_tf.gettempdir()) / "assessment-agent-figure"
        tmp_root.mkdir(parents=True, exist_ok=True)
        canvas_path = tmp_root / f"{Path(stem).stem}_{signature}_adapter_canvas.png"
    canvas_file = _save_canvas(canvas, Path(canvas_path))
    if canvas_file is None:
        raise ToolError("合成证据画布落盘失败，无法执行叠加规律复核")

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


# ------------------------------------------------------------------ R4a 变换通道
#
# 独立于六类规律引擎的新入口：把题干裁剪按网格切成**每格二值 mask**，空格（低墨迹）
# 视为待预测位置；选项裁剪各自成 mask。全部推理交给 figure_transform 的候选/仲裁，
# 验证用独立帧尺寸/容差扫描（不采信本通道的归一化前提）。不修改上面任何既有语义。


def solve_figure_transform_regions(
    stem_image: str | Path,
    option_images: Sequence[Tuple[str, str]],
    *,
    grid: Optional[Sequence[int]] = None,
    threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """变换通道求解：返回 analyze_transform 的 payload + 格 mask 审计（供验证器复用）。"""
    from .figure_transform import analyze_transform
    from .figure_transform_tools import verify_transformation

    stem = Path(stem_image)
    if not stem.is_file():
        raise ToolError(f"题干矩阵裁剪不存在: {stem}")
    stem_layout = analyze_layout(stem, threshold=threshold, grid=grid)
    bands = stem_layout["bands"]
    stem_mask = load_mask(stem, threshold)

    # 空格识别：对每列对齐后的 None 或 blank 特征
    # R4e P0-1 坐标事实收集：每格 bbox 来自同一 stem_mask 画布的网格切片（可审计）；
    # 空格（待预测位）bbox 由同 band 行区间 + 显式网格取整公式推导，供叠加对齐用。
    cell_boxes: List[Optional[Tuple[int, int, int, int]]] = []

    def row_masks(band: Dict[str, Any]) -> Tuple[List[Optional[np.ndarray]], Optional[int]]:
        cols = max((c["column"] for c in band["cells"]), default=-1) + 1
        if stem_layout.get("mode") == "explicit" and stem_layout.get("grid"):
            cols = int(stem_layout["grid"][1])
        mapping = {c["column"]: c for c in band["cells"]}
        out: List[Optional[np.ndarray]] = []
        blank_at: Optional[int] = None
        for col in range(cols):
            cell = mapping.get(col)
            if cell is None:
                # 缺失格：y 区间与 band 同源（analyze_layout 对同 band 所有格用同一
                # y0/y1），x 由**同 band 已见格 + 网格 pitch** 推导。推导跨度异常或
                # 无 pitch 证据时如实给 None → 对齐层判 coordinate_frame_unverified。
                y0, y1 = band["bbox"][1], band["bbox"][3]
                pitch = (stem_layout.get("pitch") or [None, None])[0]
                seen = [c for c in band["cells"] if c.get("bbox")]
                x0 = x1 = None
                if seen and pitch:
                    ref = seen[0]
                    x0 = int(round(ref["bbox"][0] + (col - ref["column"]) * pitch))
                    x1 = int(round(ref["bbox"][1] + (col - ref["column"]) * pitch))
                    if x1 - x0 < 2:
                        x0 = x1 = None
                if x0 is not None:
                    cell_boxes.append((int(x0), int(y0), int(x1), int(y1)))
                else:
                    cell_boxes.append(None)
                out.append(None)
                if blank_at is None:
                    blank_at = col
                continue
            bx0, by0, bx1, by1 = cell["bbox"]
            cell_boxes.append((int(bx0), int(by0), int(bx1), int(by1)))
            m = stem_mask[by0:by1, bx0:bx1]
            if cell["features"].get("blank"):
                # 变换通道只把"几乎无墨"视为空格；问号形状启发式（target_marker）
                # 会误伤小尺寸合法图形，不适用于本通道
                out.append(None)
                if blank_at is None:
                    blank_at = col
            else:
                out.append(m.astype(np.uint8))
        return out, blank_at

    option_masks: Dict[str, np.ndarray] = {}
    for label, path in option_images:
        option_masks[label] = load_mask(path, threshold).astype(np.uint8)

    if len(bands) == 1:
        cells, blank_at = row_masks(bands[0])
        known = [c for c in cells if c is not None]
        known_boxes = [b for c, b in zip(cells, cell_boxes) if c is not None]
        if blank_at is None or blank_at != len(cells) - 1 or len(known) < 2:
            return {"status": "not_applicable",
                    "reason": "空格不在行末或已知格不足，变换通道不适用（序列）",
                    "candidates": [], "verdict": {"verdict": "no_rule", "answer": None,
                                                  "support": {}}}
        mode = "sequence"
        stem_list = known
    elif len(bands) == 3 and all(len(b["cells"]) >= 2 for b in bands):
        rows = [row_masks(b) for b in bands]
        flat: List[Optional[np.ndarray]] = [c for row, _ in rows for c in row]
        flat_boxes = list(cell_boxes)  # 与 flat 逐位对齐（row_masks 按位 append）
        blanks = [i for i, c in enumerate(flat) if c is None]
        if blanks != [8]:
            return {"status": "not_applicable",
                    "reason": f"九宫格需恰好第9格为空格，实际空格位置 {blanks}",
                    "candidates": [], "verdict": {"verdict": "no_rule", "answer": None,
                                                  "support": {}}}
        mode = "grid3"
        stem_list = [c for c in flat if c is not None]
        known_boxes = [b for c, b in zip(flat, flat_boxes) if c is not None]
    else:
        return {"status": "not_applicable", "reason": "布局非单行/三行九宫格",
                "candidates": [], "verdict": {"verdict": "no_rule", "answer": None,
                                              "support": {}}}

    stem_canvas_size = (int(stem_mask.shape[0]), int(stem_mask.shape[1]))
    res = analyze_transform(stem_list, option_masks, mode=mode,
                            cell_geometry=known_boxes,
                            stem_canvas=stem_canvas_size)
    verdict = res["verdict"]
    answer = verdict.get("answer") if verdict.get("verdict") == "unique" else None
    ver = verify_transformation({**res, "mode": mode}, stem_list, option_masks, answer)
    # R4b P0-3：两个布尔**必须分开**透出——parameter_stable=单因子归一化参数组
    # 全部唯一复算同一答案（扫描口径）；independently_verified=验证器自有帧下
    # 几何候选替代验证六项全过（代入口径）。二者不可共用一个字段。
    vchecks = {c.get("name"): bool(c.get("passed"))
               for c in (ver.model_dump().get("checks") or [])}
    parameter_stable = bool(vchecks.get("figure_transform.parameter_stable"))
    independently_verified = bool(vchecks.get("geometry.independently_verified"))
    return {
        "status": "solved" if (answer and ver.action == "accept") else
                  ("unverified" if answer and ver.action != "accept" else "failed"),
        "mode": mode,
        "answer": answer if ver.action == "accept" else None,
        "parameter_stable": parameter_stable,
        "independently_verified": independently_verified,
        "verdict": verdict,
        "candidates": res["candidates"],
        "symmetry_unresolvable": res["symmetry_unresolvable"],
        "blockers": res.get("blockers", []),
        "overlay_eval": res.get("overlay_eval", {}),
        "coord_contract": res.get("coord_contract"),
        "verification": ver.model_dump(),
        "reason": (f"变换律唯一：{verdict.get('support')}" if answer and ver.action == "accept"
                   else verdict.get("detail") or res.get("reason") or "变换规律不可唯一判定"),
        "steps": [f"[变换通道] {mode} 模式，候选 {len(res['candidates'])} 条，"
                  f"仲裁 {verdict.get('verdict')}，独立验证 {ver.action}"],
        "stem_fingerprint": {  # 审计指纹（验证器可据原始裁剪重算，不回传全量掩码）
            "cell_count": len(stem_list),
            "ink": [int((m > 0).sum()) for m in stem_list],
            "canvas": [list(map(int, m.shape)) for m in stem_list],
        },
    }
