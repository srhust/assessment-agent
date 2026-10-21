# -*- coding: utf-8 -*-
"""R11-C：DiagramGridEvidence —— 图形题干格完整性证据。

与 figure_page_localizer 分离：定位器负责“找区域”，本模块负责**格契约**：
区分真空白（被预期留空）、未检测（ink 不足且无空白证据）、未知、目标空格；
允许多布局假设，无法唯一确定 → verification_status="ambiguous" → 上层弃权。
格内容用**格盒裁剪**（ink±pad，被间隙中点封顶）并逐格记像素哈希，Solver 的
序列/九宫格输入由证据重建，**不再由 VLM 自报格数决定**。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .figure_page_localizer import (BLANK_MAX_INK, _containment, _crop_hash,
                                    _iou, cluster_cells, col_segments)

MIN_CELL_INK = 30.0          # 可见格判定：格内 ink 下限（与定位器 min_ink 同量级）
BLANK_MAX = BLANK_MAX_INK * 3  # 真空白上限（沿用 D 阶段 45）


def _ink(mask: np.ndarray, box: Sequence[int]) -> int:
    x0, y0, x1, y1 = [int(v) for v in box]
    sub = mask[max(0, y0):y1, max(0, x0):x1]
    return int((sub > 0).sum()) if sub.size else 0


def slot_boxes(x0: int, y0: int, x1: int, y1: int, rows: int, cols: int):
    w = (x1 - x0) / cols
    h = (y1 - y0) / rows
    out = []
    for r in range(rows):
        for c in range(cols):
            out.append({"row": r, "col": c,
                        "bbox_page": [int(x0 + c * w), int(y0 + r * h),
                                      int(x0 + (c + 1) * w), int(y0 + (r + 1) * h)]})
    return out


def layout_hypotheses(mask: np.ndarray, stem_box: Sequence[int],
                      expects_blank: bool,
                      cells_hint: Optional[Sequence[Dict[str, Any]]] = None,
                      rows_hint: int = 1) -> List[Dict[str, Any]]:
    """基于几何给出候选 (rows, cols)：1×N / 2×N / 3×3，按 ink 槽位拟合打分。

    cells_hint：定位器已检出的可见格（小页面 col_segments 阈值切不到时的种子）。
    """
    x0, y0, x1, y1 = [int(v) for v in stem_box]
    band = mask[y0:y1, x0:x1]
    if band.size == 0:
        return []
    h, w = band.shape
    segs = col_segments(mask, y0, y1, min_col_ink=2, min_ink=12)
    n_segs = len(segs)
    cands: List[tuple] = []
    n_vis = len(cells_hint or [])
    if n_vis >= 1:
        cands.append((1, n_vis))
        if expects_blank:
            cands.append((1, n_vis + 1))
    if n_segs >= 1:
        cands.append((1, n_segs))
        # 段落可能吞掉同行合并：再试把段宽按中位格宽拆列
        widths = [e - s for s, e in segs]
        if widths:
            med = float(np.median(widths))
            if med > 0 and w / med >= 2:
                cands.append((1, max(2, int(round(w / med)))))
    aspect = w / max(1.0, h)
    if aspect > 2.0:
        cands.append((1, 3))
        cands.append((1, 4))
    elif 0.6 < aspect <= 2.0 and h >= 3 * BLANK_MAX:
        cands.append((2, 2))
        cands.append((2, 3))
    if h >= 60 and w >= 60:
        cands.append((3, 3))
    seen, scored = set(), []
    for rows, cols in cands:
        if (rows, cols) in seen or rows * cols < 2 or rows * cols > 12:
            continue
        seen.add((rows, cols))
        slots = slot_boxes(x0, y0, x1, y1, rows, cols)
        inks = [_ink(mask, s["bbox_page"]) for s in slots]
        visible = sum(1 for i in inks if i >= MIN_CELL_INK)
        emptyish = sum(1 for i in inks if i <= BLANK_MAX)
        # 拟合优度：可见+可判空白覆盖所有槽位，且格近方
        cell_ar = (w / cols) / max(1.0, h / rows)
        square_ok = 0.35 <= cell_ar <= 2.9
        fit = 1.0 if (visible + emptyish == len(slots) and square_ok) else 0.0
        # 期望含一个空白（补全题）时，emptyish>=1 加分；否则不奖励空白
        bonus = (1 if expects_blank and emptyish >= 1 else 0)
        scored.append({"rows": rows, "cols": cols, "visible": visible,
                       "emptyish": emptyish, "fit": fit, "bonus": bonus,
                       "n_cells": rows * cols,
                       "score": fit * 3 + bonus * 2 + visible - 0.5 * (len(slots) - visible - emptyish)})
    scored.sort(key=lambda d: -d["score"])
    if rows_hint and rows_hint > 1:
        filt = [d for d in scored if d["rows"] == rows_hint]
        if filt:
            return filt
    return scored


def build_grid_evidence(mask: np.ndarray, image_sha: str,
                        stem_box: Sequence[int], rows: int, cols: int,
                        expects_blank: bool = True,
                        vlm_declared: Optional[Dict[str, Any]] = None,
                        cells_hint: Optional[Sequence[Dict[str, Any]]] = None,
                      rows_hint: int = 1) -> Dict[str, Any]:
    """按选定 (rows, cols) 生成格证据；多假设打平时 status=ambiguous。"""
    # 确定性等距分支：≥2 个等间距可见格 + 期望空白 → 1×(N+1)，目标在末槽
    pitch_ev = None
    hints = list(cells_hint or [])
    if not (rows and cols) and expects_blank and len(hints) >= 2:
        starts = sorted(int(c["bbox_page"][0]) for c in hints)
        hs = [int(c["bbox_page"][3]) for c in hints]
        ys = [int(c["bbox_page"][1]) for c in hints]
        diffs = [b - a for a, b in zip(starts, starts[1:])]
        single_row = (max(ys) - min(ys)) <= 8 and (max(hs) <= BLANK_MAX * 4)
        uniform = bool(diffs) and (max(diffs) - min(diffs)) <= max(10, int(0.25 * np.median(diffs)))
        if single_row and uniform:
            pitch = int(round(float(np.median(diffs))))
            x0 = starts[0]
            n_cells = len(starts) + 1
            y0, y1 = min(ys), max(y + h for y, h in zip(ys, hs))
            slots = [[x0 + i * pitch, y0, x0 + (i + 1) * pitch, y1] for i in range(n_cells)]
            pitch_ev = {"slots": slots, "n": n_cells}
    hypos = layout_hypotheses(mask, stem_box, expects_blank, cells_hint=cells_hint,
                          rows_hint=rows_hint)
    status = "ok"
    confidence = "high"
    if pitch_ev and not (rows and cols):
        cols = pitch_ev["n"]
        rows = 1
        # 等距外推是强几何证据：跳过歧义仲裁但仍保留假设列表供审计
        stem_box = [pitch_ev["slots"][0][0], pitch_ev["slots"][0][1],
                    pitch_ev["slots"][-1][2], pitch_ev["slots"][-1][3]]
        hypos = [{"rows": 1, "cols": cols, "pitch_derived": True,
                  "score": 99.0}] + hypos[:3]
    if not hypos:
        return {"verification_status": "no_layout", "reason": "no_hypothesis",
                "image_sha": image_sha, "stem_bbox_page": list(stem_box)}
    best = hypos[0]
    tied = [h for h in hypos if h["score"] >= best["score"] - 1e-9
             and (h["rows"], h["cols"]) != (best["rows"], best["cols"])]
    if rows and cols:
        chosen = {"rows": rows, "cols": cols}
        if (rows, cols) != (best["rows"], best["cols"]):
            confidence = "low"
            status = "override"   # 显式指定与像素最优不符：仍可构建，但降置信
    elif tied:
        status, confidence = "ambiguous", "low"
        chosen = best
    else:
        chosen = best
    x0, y0, x1, y1 = [int(v) for v in stem_box]
    if pitch_ev and chosen["cols"] == pitch_ev["n"]:
        slots = [{"row": 0, "col": i, "bbox_page": bx}
                 for i, bx in enumerate(pitch_ev["slots"])]
    else:
        slots = slot_boxes(x0, y0, x1, y1, chosen["rows"], chosen["cols"])
    cells: List[Dict[str, Any]] = []
    for s in slots:
        ink = _ink(mask, s["bbox_page"])
        if ink >= MIN_CELL_INK:
            kind = "visible"
        elif ink <= BLANK_MAX:
            kind = "blank_or_undetected"
        else:
            kind = "unknown"
        cx0, cy0, cx1, cy1 = s["bbox_page"]
        cells.append({"row": s["row"], "col": s["col"],
                      "cell_bbox_page": s["bbox_page"],
                      "cell_bbox_local": [s["bbox_page"][0] - x0, s["bbox_page"][1] - y0,
                                          s["bbox_page"][2] - x0, s["bbox_page"][3] - y0],
                      "ink": ink, "cell_kind": kind,
                      "cell_pixel_sha": _crop_hash(mask, cx0, cy0, cx1, cy1)})
    blanks = [(c["row"], c["col"]) for c in cells
              if c["cell_kind"] in ("blank_or_undetected", "unknown")]
    visible = [(c["row"], c["col"]) for c in cells if c["cell_kind"] == "visible"]
    if expects_blank and len(blanks) == 1:
        target = list(blanks[0])
        target_basis = "unique_empty_slot"
    elif expects_blank and len(blanks) == 0:
        # 全槽有墨：补全题但空白不可见（渲染缺问号）——按行末槽为目标并降置信
        target = [(len(cells) - 1) // chosen["cols"], chosen["cols"] - 1]
        target_basis = "no_empty_assume_row_end"
        if status == "ok":
            status, confidence = "assumed_target", "low"
    elif expects_blank:
        target = None
        target_basis = f"multiple_empty_slots({len(blanks)})"
        status = "ambiguous" if status == "ok" else status
    else:
        target = None
        target_basis = "no_blank_expected"
    vlm_agreement = None
    if vlm_declared and vlm_declared.get("bbox"):
        vb = vlm_declared["bbox"]
        vlm_agreement = {"iou": round(_iou(list(stem_box), vb), 3),
                         "containment_stem_in_vlm": round(_containment(list(stem_box), vb), 3),
                         "declared_grid": [vlm_declared.get("rows"), vlm_declared.get("cols")]}
    return {"verification_status": status, "confidence": confidence,
            "image_sha": image_sha, "stem_bbox_page": [x0, y0, x1, y1],
            "grid_rows": chosen["rows"], "grid_cols": chosen["cols"],
            "visible_cells": [list(v) for v in visible],
            "blank_cells": [list(b) for b in blanks],
            "unknown_cells": [],   # 与 blank_or_undetected 合并上报，不臆断区分
            "target_cell": target, "target_basis": target_basis,
            "cells": cells,
            "layout_hypotheses": hypos[:4],
            "evidence_source": "pixel_localizer",
            "vlm_declared": vlm_declared, "vlm_agreement": vlm_agreement,
            "expects_blank": bool(expects_blank)}


def render_canvas(mask: np.ndarray, evidence: Dict[str, Any]) -> np.ndarray:
    """把可见格的**墨迹紧框**居中放入统一 padding-cell（金标裁剪约定：
    ink~24px + 12px 四周一侧留白 = 48px 格）。空格保持空白。确定性纯函数。"""
    rows, cols = evidence["grid_rows"], evidence["grid_cols"]
    pad = 12
    inks = []
    for c in evidence["cells"]:
        if c["cell_kind"] != "visible":
            continue
        bx0, by0, bx1, by1 = c["cell_bbox_page"]
        piece = mask[by0:by1, bx0:bx1]
        nz = piece > 0
        if not nz.any():
            continue
        ys, xs = nz.nonzero()
        inks.append(((c["row"], c["col"]), piece[ys.min():ys.max() + 1,
                                                  xs.min():xs.max() + 1]))
    if not inks:
        return np.zeros((ch := 1, 1), dtype=bool)
    ih = max(a.shape[0] for _, a in inks)
    iw = max(a.shape[1] for _, a in inks)
    ch_, cw_ = ih + 2 * pad, iw + 2 * pad
    canvas = np.zeros((rows * ch_, cols * cw_), dtype=bool)
    for (r, c), piece in inks:
        gh, gw = piece.shape
        oy = c_h = (ch_ - gh) // 2
        ox = (cw_ - gw) // 2
        canvas[r * ch_ + oy:r * ch_ + oy + gh, c * cw_ + ox:c * cw_ + ox + gw] = piece
    return canvas


def known_sequence(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """从格证据重建 Solver 输入：行优先可见格序列 + 目标位（格序跳空格）。

    返回 {"mode": sequence|grid3|unsupported, "cell_indices": [...],
          "target_index_rowmajor": int|None}。序列空格不在末位时如实 unsupported
    （不猜测空格前内容参与“下一格”外推）。"""
    if "grid_rows" not in evidence:
        return {"mode": "unsupported",
                "reason": evidence.get("reason") or "no_layout"}
    if evidence.get("verification_status") == "ambiguous":
        return {"mode": "unsupported", "reason": "ambiguous_layout"}
    rows, cols = evidence["grid_rows"], evidence["grid_cols"]
    cells = evidence["cells"]
    tgt = evidence.get("target_cell")
    visible_idx = [i for i, c in enumerate(cells) if c["cell_kind"] == "visible"]
    empty_idx = [i for i, c in enumerate(cells) if c["cell_kind"] != "visible"]
    if len(empty_idx) == 0:
        if rows * cols == cols:
            return {"mode": "sequence", "cell_indices": visible_idx,
                    "target_index_rowmajor": None, "reason": "no_target_slot"}
        return {"mode": "grid3" if rows * cols == 9 else "unsupported",
                "cell_indices": visible_idx, "target_index_rowmajor": None}
    if rows * cols == 9:
        if len(empty_idx) == 1:
            r, c = divmod(empty_idx[0], cols)
            return {"mode": "grid3", "cell_indices": visible_idx,
                    "target_index_rowmajor": empty_idx[0], "target_cell": [r, c]}
        return {"mode": "unsupported", "reason": f"grid3_empty_slots={len(empty_idx)}"}
    # 单行/多行序列：空格必须位于行优先末位，否则不可外推
    if len(empty_idx) == 1 and empty_idx[0] == len(cells) - 1:
        return {"mode": "sequence", "cell_indices": visible_idx,
                "target_index_rowmajor": empty_idx[0]}
    if rows == 1:
        return {"mode": "unsupported", "reason": "blank_not_at_row_end"}
    return {"mode": "unsupported", "reason": "multi_empty_or_internal_blank"}


def merge_stem_regions(sregions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """R11-B：九宫格因行距被拆成多条题干带时，按 x 重叠>0.7 且纵向间隙<=60 合并
    （overlay-13 视觉复核确为 3 行矩阵）。返回合并后的区域列表（保持首元素结构）。"""
    if len(sregions) <= 1:
        return list(sregions)
    def _ov(a, b):
        ix = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
        iw = min(a[2], b[2])
        return ix / iw if iw > 0 else 0.0
    srg = sorted(sregions, key=lambda r: r["bbox_page"][1])
    merged: List[Dict[str, Any]] = []
    for r in srg:
        if merged:
            last = merged[-1]
            ygap = r["bbox_page"][1] - last["y1"]
            if _ov(r["bbox_page"], last["box"]) > 0.7 and ygap <= 60:
                b0, b1 = last["box"], r["bbox_page"]
                last["box"] = [min(b0[0], b1[0]), b0[1],
                               max(b0[0] + b0[2], b1[0] + b1[2]), b1[1] + b1[3]]
                last["y1"] = b1[1] + b1[3]
                last["cells"] = last["cells"] + list(r.get("cells") or [])
                continue
        b = r["bbox_page"]
        merged.append({"box": list(b), "y1": b[1] + b[3],
                       "cells": list(r.get("cells") or []), "n_bands": 1})
    tall = max(merged, key=lambda mm: (mm["y1"] - mm["box"][1]) * mm["box"][2])
    first = dict(sregions[0])
    first["bbox_page"] = tall["box"]
    first["cells"] = tall["cells"]
    first["sub_band_count"] = tall.get("n_bands", 1)
    return [first]
