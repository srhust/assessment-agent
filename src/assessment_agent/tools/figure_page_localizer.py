# -*- coding: utf-8 -*-
"""R10-D：页面级图形区域定位器（确定性像素通道）。

工单：图形突破 = 区域定位与内容完整性，不再扩图形 Solver 规则。
- 图形区/选项区**分别**定位（行带分离 → 列投影分格）
- 多区域组合（矩阵逐行 union）与**空白格定位**（等分位墨迹显著缺失）
- 区域完整性校验：check_option_crop 墨迹/尺寸 + 边缘截断信号
- 与 VLM 声明区域按 IoU 对账：confirmed / disagreed / added（VLM 缺失时像素补位）
- 原图坐标（bbox_page）/ 局部坐标（bbox_local 相对所属带）/ **像素哈希**（crop 内容
  sha256）三者关联，供跨运行/跨条件审计
注意：本模块只做定位与完整性，不推断图形语义。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

MIN_BAND_INK = 60          # 行带最少内容像素（滤噪条）
MIN_CELL_INK = 40          # 单元格内容下限
BLANK_MAX_INK = 15         # 空白格内容上限（像素）——有框无内容的 "?" 位
GAP_TOL = 2                # 行带内允许的行间断


def _row_profile(mask: np.ndarray) -> np.ndarray:
    return mask.sum(axis=1)


def _bands(mask: np.ndarray, min_ink: int = MIN_BAND_INK) -> List[Tuple[int, int]]:
    """行投影分带（gap 容忍 GAP_TOL 行）。"""
    prof = _row_profile(mask)
    present = prof >= 2
    out, y, h = [], 0, len(prof)
    while y < h:
        if not present[y]:
            y += 1
            continue
        start = y
        gap = 0
        last = y
        while y < h and (present[y] or gap < GAP_TOL):
            if present[y]:
                last = y
                gap = 0
            else:
                gap += 1
            y += 1
        if int(prof[start:last + 1].sum()) >= min_ink:
            out.append((start, last + 1))
    return out


def _col_segments(mask: np.ndarray, y0: int, y1: int, min_ink: int = MIN_CELL_INK,
                  gap: int = 3) -> List[Tuple[int, int]]:
    """带内列投影分段（跳空白列）。"""
    strip = mask[y0:y1, :]
    prof = strip.sum(axis=0)
    present = prof >= 2
    out, x, w = [], 0, len(prof)
    while x < w:
        if not present[x]:
            x += 1
            continue
        start = x
        g = 0
        last = x
        while x < w and (present[x] or g < gap):
            if present[x]:
                last = x
                g = 0
            else:
                g += 1
            x += 1
        if int(prof[start:last + 1].sum()) >= min_ink:
            out.append((start, last + 1))
    return out


def _iou(a: Sequence[int], b: Sequence[int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix, iy = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if ix2 <= ix or iy2 <= iy:
        return 0.0
    inter = (ix2 - ix) * (iy2 - iy)
    return inter / max(1, aw * ah + bw * bh - inter)


def _crop_hash(mask: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> str:
    return hashlib.sha256(np.ascontiguousarray(
        mask[y0:y1, x0:x1].astype(np.uint8)).tobytes()).hexdigest()[:16]


def localize_page(mask: np.ndarray, n_options: int,
                  vlm_options: Optional[List[Dict[str, Any]]] = None,
                  vlm_stem: Optional[Sequence[int]] = None,
                  ) -> Dict[str, Any]:
    """返回 {bands, stem_regions, option_regions, blank_cells, notes}。

    bbox_page=[x,y,w,h] 原图坐标；option_regions 按 x 序与题面选项字母对齐。
    """
    H, W = mask.shape
    bands = _bands(mask)
    vlm_options = vlm_options or []
    notes: List[str] = []
    if not bands:
        return {"bands": [], "stem_regions": [], "option_regions": [],
                "blank_cells": [], "notes": ["空图"]}

    # ---- 图形带/选项带分离：末带若列分段数≈n_options 判为选项带；
    #      2 行排布选项（2×2）识别：末两带列位置对齐且各自分段数≈n_options/2
    option_band_idx: Optional[int] = None
    option_cells: List[Tuple[int, int]] = []
    two_row = False
    if n_options >= 2:
        if len(bands) >= 1:
            y0, y1 = bands[-1]
            segs = _col_segments(mask, y0, y1)
            if len(segs) == n_options:
                option_band_idx, option_cells = len(bands) - 1, segs
        if option_band_idx is None and len(bands) >= 2:
            s1 = _col_segments(mask, *bands[-1])
            s0 = _col_segments(mask, *bands[-2])
            half = max(1, n_options // 2)
            if len(s0) == half and len(s1) == n_options - half and bands[-2][1] - bands[-2][0] > 20:
                option_band_idx = len(bands) - 2
                option_cells = sorted(s0 + s1, key=lambda c: (c[0]))
                two_row = True
    fig_bands = [b for i, b in enumerate(bands)
                if i < (option_band_idx if option_band_idx is not None else len(bands))]

    # ---- 选项区：逐格 bbox + 完整性 + 哈希 + 与 VLM 对账
    option_regions: List[Dict[str, Any]] = []
    for i, (x0, x1) in enumerate(option_cells):
        ys = bands[-1] if not two_row else (
            (bands[-2][0], bands[-1][1]) if (x0, x1) in s1 else (bands[-2][0], bands[-2][1]))
        if two_row:
            yband = next((b for b in bands[option_band_idx:]
                          if (x0, x1) in _col_segments(mask, b[0], b[1])), bands[-1])
            ys = yband
        ink = int(mask[ys[0]:ys[1], x0:x1].sum())
        crop = mask[ys[0]:ys[1], x0:x1]
        h, w = crop.shape
        from .diagram_layout import check_option_crop
        comp = check_option_crop(crop)
        touch = (x0 <= 1 or x1 >= W - 1 or ys[0] <= 1 or ys[1] >= H - 1)
        rec: Dict[str, Any] = {
            "index": i, "bbox_page": [x0, ys[0], x1 - x0, ys[1] - ys[0]],
            "bbox_local": [0, 0, x1 - x0, ys[1] - ys[0]], "band": list(ys),
            "ink": ink, "completeness": comp, "touches_border": bool(touch),
            "pixel_sha": _crop_hash(mask, x0, ys[0], x1, ys[1]),
            "source": "pixel_localizer", "confidence": round(
                min(1.0, ink / max(1, 0.35 * (x1 - x0) * (ys[1] - ys[0])))
                * (1.0 if comp["passed"] else 0.4), 3),
        }
        # VLM 对账
        best = max(vlm_options, key=lambda v: _iou(v.get("bbox") or [0, 0, 0, 0],
                                                   rec["bbox_page"]), default=None)
        if best:
            iou = _iou(best["bbox"], rec["bbox_page"])
            rec["vlm_iou"] = round(iou, 3)
            rec["source"] = ("vlm+pixel_confirmed" if iou >= 0.5 else "vlm_disagreed")
        option_regions.append(rec)
    if option_band_idx is None and vlm_options:
        notes.append("options_band_not_detected_vlm_only")
    elif option_band_idx is None and not vlm_options:
        notes.append("options_band_missing_no_vlm")

    # ---- 图形区：各带 union + 行组矩阵；空白格在等分格上检测
    stem_regions: List[Dict[str, Any]] = []
    blank_cells: List[Dict[str, Any]] = []
    for (y0, y1) in fig_bands:
        segs = _col_segments(mask, y0, y1, gap=6)
        if not segs:
            continue
        xs0, xs1 = segs[0][0], segs[-1][1]
        bbox = [xs0, y0, xs1 - xs0, y1 - y0]
        rec = {"bbox_page": bbox, "n_cells_x": len(segs),
               "pixel_sha": _crop_hash(mask, xs0, y0, xs1, y1),
               "sub_cells": [{"bbox_page": [sx0, y0, sx1 - sx0, y1 - y0],
                              "ink": int(mask[y0:y1, sx0:sx1].sum()),
                              "blank_candidate": int(mask[y0:y1, sx0:sx1].sum()) <= BLANK_MAX_INK}
                             for (sx0, sx1) in segs]}
        # 矩阵行组（子带嵌套）
        inner = _bands(mask[y0:y1, xs0:xs1], min_ink=20)
        if len(inner) >= 2:
            rec["grid_rows"] = len(inner)
        blank_cells += [f"stem_cell_blank@{xs0 + sx0},{y0}"
                        for (sx0, sx1) in segs
                        if int(mask[y0:y1, sx0:sx1].sum()) <= BLANK_MAX_INK]
        v = max((rec, ), key=None) if False else rec
        if vlm_stem:
            rec["vlm_iou"] = round(_iou(vlm_stem, bbox), 3)
            rec["source"] = ("vlm+pixel_confirmed" if rec["vlm_iou"] >= 0.5
                             else "vlm_disagreed")
        else:
            rec["source"] = "pixel_localizer"
        stem_regions.append(rec)
    return {"bands": [{"y0": a, "y1": b} for a, b in bands],
            "stem_regions": stem_regions, "option_regions": option_regions,
            "blank_cells": blank_cells, "notes": notes,
            "image_size": [int(W), int(H)]}


def grid_blank_cells(mask: np.ndarray, stem_bbox: Sequence[int],
                     rows: int, cols: int) -> List[Dict[str, Any]]:
    """题干矩阵金标网格的**空白格定位**：等分格中墨迹显著缺失者。"""
    x, y, w, h = stem_bbox
    cell_w, cell_h = w / max(1, cols), h / max(1, rows)
    inks, out = [], []
    for r in range(rows):
        row_inks = []
        for c in range(cols):
            x0, x1 = int(x + c * cell_w) + 2, int(x + (c + 1) * cell_w) - 2
            y0, y1 = int(y + r * cell_h) + 2, int(y + (r + 1) * cell_h) - 2
            row_inks.append(int(mask[y0:y1, x0:x1].sum()))
        inks.append(row_inks)
    flat = [v for row in inks for v in row]
    med = float(np.median([v for v in flat if v > 0] or [1]))
    for r in range(rows):
        for c in range(cols):
            if inks[r][c] <= max(BLANK_MAX_INK, 0.08 * med):
                out.append({"row": r, "col": c, "ink": inks[r][c],
                            "bbox_page": [int(x + c * cell_w), int(y + r * cell_h),
                                          int(cell_w), int(cell_h)],
                            "pixel_sha": _crop_hash(
                                mask, int(x + c * cell_w), int(y + r * cell_h),
                                int(x + (c + 1) * cell_w), int(y + (r + 1) * cell_h))})
    return out
