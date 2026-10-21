# -*- coding: utf-8 -*-
"""R10-D：页面级图形区域定位器（确定性像素通道，v2）。

工单：图形突破 = 区域定位与内容完整性（不扩 Solver 规则）。
* 图形区/选项区**分别**定位：行带分离 → 带内列投影分格 → gap 聚类为单元
* 标签字母（A/B/C/D 单字）按墨迹高度比剪除，不进单元格框
* 文本行识别（窄行+高分段密度）与图形行区分，题干图形取选项带上方相邻图形带
* 空白格定位：矩阵等分格墨迹显著缺失（grid_blank_cells）
* 完整性：check_option_crop 墨迹/尺寸 + 触边信号；置信度随行记录
* 三坐标关联：bbox_page（原图）/ bbox_local（相对所属带）/ pixel_sha（裁剪内容哈希）
* 与 VLM 声明区域 IoU 对账：vlm+pixel_confirmed / vlm_disagreed / pixel_localizer
定位正确性口径 = **slot 指派 + 包含率**（检测框落入金标格），不拿墨迹外接框
硬比整格 IoU；自洽（同图两跑一致）单列指标，绝不冒充真实定位正确。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

MIN_BAND_INK = 60
BLANK_MAX_INK = 15
GAP_TOL = 2
MERGE_GAP = 25           # 单元内列间隙上限（像素）
LABEL_H_RATIO = 0.55     # 组内墨迹高度 < 0.55*最大 视为标签字母
TEXT_MIN_SEGS = 9        # 文本行：小 gap 聚类后段数下限
TEXT_MAX_H = 30          # 文本行：带高上限


def _iou(a: Sequence[int], b: Sequence[int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix, iy = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if ix2 <= ix or iy2 <= iy:
        return 0.0
    inter = (ix2 - ix) * (iy2 - iy)
    return inter / max(1, aw * ah + bw * bh - inter)


def _containment(inner: Sequence[int], outer: Sequence[int]) -> float:
    ix, iy = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix2 = min(inner[0] + inner[2], outer[0] + outer[2])
    iy2 = min(inner[1] + inner[3], outer[1] + outer[3])
    inter = max(0, ix2 - ix) * max(0, iy2 - iy)
    return inter / max(1, inner[2] * inner[3])


def _crop_hash(mask: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> str:
    return hashlib.sha256(np.ascontiguousarray(
        mask[y0:y1, x0:x1].astype(np.uint8)).tobytes()).hexdigest()[:16]


def row_bands(mask: np.ndarray, min_ink: int = MIN_BAND_INK) -> List[Tuple[int, int]]:
    prof = mask.sum(axis=1)
    present = prof >= 2
    out, y, h = [], 0, len(prof)
    while y < h:
        if not present[y]:
            y += 1
            continue
        start, gap, last = y, 0, y
        while y < h and (present[y] or gap < GAP_TOL):
            if present[y]:
                last, gap = y, 0
            else:
                gap += 1
            y += 1
        if int(prof[start:last + 1].sum()) >= min_ink:
            out.append((start, last + 1))
    return out


def col_segments(mask: np.ndarray, y0: int, y1: int, min_col_ink: int = 2,
                 gap: int = 3, min_ink: int = 40) -> List[Tuple[int, int]]:
    prof = mask[y0:y1, :].sum(axis=0)
    present = prof >= min_col_ink
    out, x, w = [], 0, len(prof)
    while x < w:
        if not present[x]:
            x += 1
            continue
        start, g, last = x, 0, x
        while x < w and (present[x] or g < gap):
            if present[x]:
                last, g = x, 0
            else:
                g += 1
            x += 1
        if int(prof[start:last + 1].sum()) >= min_ink:
            out.append((start, last + 1))
    return out


def _split_point(prof: np.ndarray, a: int, b: int) -> Optional[int]:
    """区间 [a,b) 内最长空白列段中点；无 ≥3px 空段返回 None。"""
    best_len, best_start = 0, -1
    run = 0
    for x in range(a + 4, b - 4):
        if prof[x] == 0:
            run += 1
            if run > best_len:
                best_len, best_start = run, x - run + 1
        else:
            run = 0
    if best_len < 3:
        return None
    return best_start + best_len // 2


def cluster_cells(mask: np.ndarray, y0: int, y1: int, merge_gap: int = MERGE_GAP,
                  prune_labels: bool = True,
                  expect: Optional[int] = None) -> List[Tuple[int, int]]:
    """列分段 → gap 聚类 → 剪标签字母 →（可选）校正到 expect 个单元。"""
    segs = col_segments(mask, y0, y1)
    if not segs:
        return []
    groups: List[List[Tuple[int, int]]] = [[segs[0]]]
    for s in segs[1:]:
        if s[0] - groups[-1][-1][1] < merge_gap:
            groups[-1].append(s)
        else:
            groups.append([s])
    out = []
    for g in groups:
        if prune_labels and len(g) > 1:
            heights = [(mask[y0:y1, a:b].any(axis=1)).sum() for a, b in g]
            mx = max(heights)
            # 标签字母：窄段（≤18px 宽）且明显矮于组内最高段
            keep = [s for s, hgt in zip(g, heights)
                    if not ((s[1] - s[0]) <= 18 and hgt < 0.75 * mx)]
            g = keep or g
        out.append((g[0][0], g[-1][1]))
    if expect:
        prof = mask[y0:y1, :].sum(axis=0)
        guard = 0
        # 超宽单元先分裂（跨格粘连：宽度 >1.9× 中位）
        while guard < 8:
            guard += 1
            ws = sorted(c[1] - c[0] for c in out)
            med = ws[len(ws) // 2] or 1
            over = [c for c in out if (c[1] - c[0]) > 1.9 * med and
                    _split_point(prof, c[0], c[1]) is not None]
            if not over:
                break
            a, b = max(over, key=lambda c: c[1] - c[0])
            mid = _split_point(prof, a, b)
            out.remove((a, b))
            out.extend([(a, mid), (mid, b)])
            out.sort()
        while len(out) < expect and guard < 16:
            guard += 1
            # 分裂最宽单元：在其内部最长空白列段处切开
            a, b = max(out, key=lambda c: c[1] - c[0])
            inner = prof[a:b]
            run_best, run_cur, start = (0, -1, -1), (0, -1, -1), None
            for i, v in enumerate(inner):
                if v == 0:
                    if start is None:
                        start = i
                    run_cur = (i - start + 1, start, i)
                    if run_cur[0] > run_best[0]:
                        run_best = run_cur
                else:
                    start = None
            if run_best[0] < 2 or run_best[1] == 0 or run_best[2] >= (b - a) - 1:
                break
            mid = a + run_best[1] + run_best[0] // 2
            out.remove((a, b))
            out.extend([(a, mid), (mid, b)])
            out.sort()
        while len(out) > expect and guard < 16:
            guard += 1
            gaps = [(out[i + 1][0] - out[i][1], i) for i in range(len(out) - 1)]
            gmin, i = min(gaps)
            out[i:i + 2] = [(out[i][0], out[i + 1][1])]
    return out


def is_text_band(mask: np.ndarray, y0: int, y1: int) -> bool:
    """文本行判据：带矮（≤24px）且出现**词组级宽簇**（字距小、gap15 并成 ≥70px 连排）
    或极多细段（≥12 段且中位宽 ≤14）。图形带单元簇宽 20-64px，不满足。"""
    if (y1 - y0) > 24:
        return False
    segs = col_segments(mask, y0, y1, min_col_ink=1, gap=2, min_ink=8)
    widths = sorted(b - a for a, b in segs)
    if len(segs) >= 12 and widths and widths[len(widths) // 2] <= 14:
        return True
    clusters = cluster_cells(mask, y0, y1, merge_gap=15, prune_labels=False)
    return any((b - a) >= 70 for a, b in clusters)


def localize_page(mask: np.ndarray, n_options: int,
                  vlm_options: Optional[List[Dict[str, Any]]] = None,
                  vlm_stem: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    H, W = mask.shape
    bands = row_bands(mask)
    vlm_options = vlm_options or []
    notes: List[str] = []
    result: Dict[str, Any] = {"bands": [{"y0": a, "y1": b} for a, b in bands],
                              "stem_regions": [], "option_regions": [],
                              "blank_cells": [], "notes": notes,
                              "image_size": [int(W), int(H)]}
    if not bands:
        notes.append("空图")
        return result

    # ---- 选项带：从底部带开始尝试（含末两带拼 2×2），要求聚类后恰为 n_options
    opt_cells: Optional[List[Tuple[Tuple[int, int], Tuple[int, int]]]] = None  # ((x0,x1),(y0,y1))
    opt_span: Optional[Tuple[int, int]] = None
    def try_band_pair(bidx: List[int]) -> Optional[List]:
        cells = []
        for bi in bidx:
            y0, y1 = bands[bi]
            natural = cluster_cells(mask, y0, y1)
            exp = n_options if len(bidx) == 1 else None
            got = natural if exp is None else cluster_cells(mask, y0, y1, expect=exp)
            # 文本否决只在**自然聚类数不符**时生效（数都对上就不是文字行）
            if exp is not None and len(natural) != n_options and is_text_band(mask, y0, y1):
                return None
            for cx0, cx1 in got:
                cells.append(((cx0, cx1), (y0, y1)))
        return cells if len(cells) == n_options else None
    for bidx in ([len(bands) - 1], [len(bands) - 2, len(bands) - 1]):
        if len(bidx) > 1 and len(bands) < 2:
            continue
        got = try_band_pair(bidx)
        if got:
            opt_cells, opt_span = got, (bands[bidx[0]][0], bands[bidx[-1]][1])
            break
    if opt_cells is None:
        notes.append("options_band_not_detected")

    # ---- 图形带：选项带上方、非文本、与紧邻带 x 范围重叠者可拼多行矩阵
    hi = len(bands)
    if opt_cells:
        first_opt_band = min(bi for bi, b in enumerate(bands)
                             if any(bc[1] == b for bc in opt_cells))
        hi = first_opt_band
    fig_band_groups: List[List[int]] = []
    fig_band_groups_x: List[Tuple[int, int]] = []
    for bi in range(hi):
        y0, y1 = bands[bi]
        if is_text_band(mask, y0, y1):
            continue
        segs = cluster_cells(mask, y0, y1, merge_gap=15)
        if not segs:
            continue
        if fig_band_groups:
            py0, py1 = bands[fig_band_groups[-1][-1]]
            px0, px1 = fig_band_groups_x[-1]
            gx0, gx1 = segs[0][0], segs[-1][1]
            overlap = _iou([px0, 0, px1 - px0, 1], [gx0, 0, gx1 - gx0, 1])
            if (y0 - py1) <= 40 and overlap > 0.5:
                fig_band_groups[-1].append(bi)
                fig_band_groups_x[-1] = (min(px0, gx0), max(px1, gx1))
                continue
        fig_band_groups.append([bi])
        fig_band_groups_x.append((segs[0][0], segs[-1][1]))
    stem_regions = []
    for group, xr in zip(fig_band_groups, fig_band_groups_x):
        y0, y1 = bands[group[0]][0], bands[group[-1]][1]
        row_cells = []
        for gbi in group:
            b = bands[gbi]
            segs2 = col_segments(mask, b[0], b[1], min_ink=8)  # 低阈含"?"小墨
            merged: List[Tuple[int, int]] = [segs2[0]] if segs2 else []
            for s in segs2[1:]:
                if s[0] - merged[-1][1] < 15:
                    merged[-1] = (merged[-1][0], s[1])
                else:
                    merged.append(s)
            row_cells.append(merged)
        cols = max(len(rc) for rc in row_cells) if row_cells else 0
        cells_out = []
        for ri, rc in enumerate(row_cells):
            for ci, (cx0, cx1) in enumerate(rc):
                b = bands[group[ri]]
                ink = int(mask[b[0]:b[1], cx0:cx1].sum())
                cells_out.append({"row": ri, "col": ci,
                                  "bbox_page": [cx0, b[0], cx1 - cx0, b[1] - b[0]],
                                  "ink": ink, "blank_candidate": ink <= BLANK_MAX_INK * 3,
                                  "pixel_sha": _crop_hash(mask, cx0, b[0], cx1, b[1])})
        bbox = [xr[0], y0, xr[1] - xr[0], y1 - y0]
        rec: Dict[str, Any] = {"bbox_page": bbox, "grid": [len(group), cols],
                               "cells": cells_out,
                               "pixel_sha": _crop_hash(mask, xr[0], y0, xr[1], y1),
                               "source": "pixel_localizer"}
        for c in cells_out:
            if c["blank_candidate"]:
                result["blank_cells"].append({"row": c["row"], "col": c["col"],
                                              "bbox_page": c["bbox_page"],
                                              "ink": c["ink"], "pixel_sha": c["pixel_sha"]})
        if vlm_stem:
            rec["vlm_iou"] = round(_iou(vlm_stem, bbox), 3)
            agree = rec["vlm_iou"] >= 0.5 or _containment(bbox, vlm_stem) >= 0.8
            rec["vlm_slot_agree"] = bool(agree)
            rec["source"] = ("vlm+pixel_confirmed" if agree else "vlm_disagreed")
        stem_regions.append(rec)
    result["stem_regions"] = stem_regions

    # ---- 选项区记录
    if opt_cells:
        from .diagram_layout import check_option_crop
        for i, ((x0, x1), (y0, y1)) in enumerate(opt_cells):
            crop = mask[y0:y1, x0:x1]
            comp = check_option_crop(crop)
            rec: Dict[str, Any] = {
                "index": i, "bbox_page": [x0, y0, x1 - x0, y1 - y0],
                "bbox_local": [x0, 0, x1 - x0, y1 - y0], "band": [y0, y1],
                "ink": int(crop.sum()), "completeness": comp,
                "touches_border": bool(x0 <= 1 or x1 >= W - 1 or y1 >= H - 1),
                "pixel_sha": _crop_hash(mask, x0, y0, x1, y1),
                "source": "pixel_localizer",
                "confidence": round(min(1.0, comp["ink_ratio"] * 12) *
                                    (1.0 if comp["passed"] else 0.4), 3)}
            bx, by, bw, bh = rec["bbox_page"]
            cx, cy = bx + bw / 2, by + bh / 2
            covering = [v for v in vlm_options
                        if v.get("bbox") and v["bbox"][0] <= cx < v["bbox"][0] + v["bbox"][2]
                        and v["bbox"][1] <= cy < v["bbox"][1] + v["bbox"][3]]
            if covering:
                best = covering[0]
                vb = best["bbox"]
                slot_agree = _containment(rec["bbox_page"], vb) >= 0.5
                iou = _iou(vb, rec["bbox_page"])
                rec["vlm_iou"] = round(iou, 3)
                rec["vlm_slot_agree"] = bool(slot_agree)
                rec["source"] = ("vlm+pixel_confirmed" if (iou >= 0.5 or slot_agree)
                                 else "vlm_disagreed")
            result["option_regions"].append(rec)
    return result


def grid_blank_cells(mask: np.ndarray, stem_bbox: Sequence[int],
                     rows: int, cols: int) -> List[Dict[str, Any]]:
    """矩阵等分格中的空白格（补全题的 "?" 位）：墨迹显著缺失。"""
    x, y, w, h = stem_bbox
    cw, ch = w / max(1, cols), h / max(1, rows)
    inks = []
    for r in range(rows):
        inks.append([int(mask[int(y + (r + .25) * ch):int(y + .85 * (r + 1) * ch),
                              int(x + (c + .15) * cw):int(x + (c + .9) * cw)].sum())
                     for c in range(cols)])
    med = float(np.median([v for row in inks for v in row if v > 0] or [1]))
    out = []
    for r in range(rows):
        for c in range(cols):
            if inks[r][c] <= max(BLANK_MAX_INK, 0.08 * med):
                out.append({"row": r, "col": c, "ink": inks[r][c],
                            "bbox_page": [int(x + c * cw), int(y + r * ch),
                                          int(cw), int(ch)],
                            "pixel_sha": _crop_hash(
                                mask, int(x + c * cw), int(y + r * ch),
                                int(x + (c + 1) * cw), int(y + (r + 1) * ch))})
    return out
