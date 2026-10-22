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
        # R12-C 跨界惩罚：完整墨迹段（图形）被槽边界切到两个槽 = 该假设把
        # 一个图形拆成多格（幻影格来源），每个跨界段扣 0.75。
        boundaries = [sl["bbox_page"][0] for sl in slots[1:]]
        straddle = sum(sum(1 for bx in boundaries if a < bx < b)
                       for (a, b) in segs)
        scored.append({"rows": rows, "cols": cols, "visible": visible,
                       "emptyish": emptyish, "fit": fit, "bonus": bonus,
                       "straddle": straddle, "n_cells": rows * cols,
                       "score": fit * 3 + bonus * 2
                               - 0.5 * (len(slots) - visible - emptyish)
                               - 0.75 * straddle})
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
                      rows_hint: int = 1,
                  stem_continuation_suspect: bool = False) -> Dict[str, Any]:
    """按选定 (rows, cols) 生成格证据；多假设打平时 status=ambiguous。"""
    # 确定性等距分支：≥2 个等间距可见格 + 期望空白 → 1×(N+1)，目标在末槽
    pitch_ev = None
    hints = list(cells_hint or [])
    # R13-D：等距外推分支在**显式行列**时同样启用——否则复核（显式网格）走均分槽、
    # 求解（自动）走外推槽，两套协议制造虚假哈希漂移（mirror-04 F unverified 根因）。
    if expects_blank and len(hints) >= 2:
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
    if pitch_ev and (not (rows and cols)
                     or (int(rows) == 1 and int(cols) == pitch_ev["n"])):
        # R14-C：显式网格若与节距外推**格数一致**也走外推槽——复核传显式
        # grid 时此前仍走均分槽，与求解自动链不同源，制造第二类虚假漂移
        # （rot90-01 复核实测 override：显式 [1,4] vs 像素最优 [1,3]）。
        # 格数不一致的显式网格保持 override 降级，安全语义不放松。
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
    # ---- R13-A4 安全门：错误/假设布局不得以已验证状态向后传递 ----
    notes_g = []
    if stem_continuation_suspect:
        notes_g.append("stem_continuation_suspect")
        status, confidence = "ambiguous", "low"
    if not (rows and cols) and int(rows_hint or 0) >= 2 \
            and chosen["rows"] != int(rows_hint):
        notes_g.append(f"rows_hint_mismatch(chosen={chosen['rows']},hint={rows_hint})")
        status, confidence = "ambiguous", "low"
    if status in ("ok", "assumed_target", "override") \
            and not (pitch_ev and chosen["cols"] == pitch_ev["n"]):
        sx0, sy0, sx1, sy1 = [int(v) for v in stem_box]
        sa = ((sx1 - sx0) / max(1, chosen["cols"])) / max(1.0, (sy1 - sy0) / max(1, chosen["rows"]))
        hint_inks = [c for c in (cells_hint or []) if c.get("bbox_page")]
        if hint_inks:
            asps = []
            for c in hint_inks:
                wdt = max(1, int(c["bbox_page"][2]))
                hgt = max(1, int(c["bbox_page"][3]))
                asps.append(wdt / hgt)
            med = float(np.median(asps))
            if med > 0 and (sa / med > 2.4 or med / sa > 2.4):
                notes_g.append(f"slot_aspect_mismatch(slot={round(sa,2)},ink={round(med,2)})")
                status, confidence = "ambiguous", "low"
    safety_notes = list(notes_g)
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
        entry = {"row": s["row"], "col": s["col"],
                 "cell_bbox_page": s["bbox_page"],
                 "cell_bbox_local": [s["bbox_page"][0] - x0, s["bbox_page"][1] - y0,
                                     s["bbox_page"][2] - x0, s["bbox_page"][3] - y0],
                 "ink": ink, "cell_kind": kind,
                 "cell_pixel_sha": _crop_hash(mask, cx0, cy0, cx1, cy1)}
        if kind == "visible":
            sub = mask[cy0:cy1, cx0:cx1] > 0
            ys, xs = sub.nonzero()
            entry["ink_bbox_page"] = [int(cx0 + xs.min()), int(cy0 + ys.min()),
                                      int(cx0 + xs.max() + 1), int(cy0 + ys.max() + 1)]
            entry["ink_offset_in_slot"] = [int(xs.min()), int(ys.min())]
        cells.append(entry)
    vis_sorted = [c for c in cells if c["cell_kind"] == "visible"]
    ink_deltas: List[List[int]] = []
    for a, b in zip(vis_sorted, vis_sorted[1:]):
        ia, ib = a.get("ink_bbox_page"), b.get("ink_bbox_page")
        if ia and ib:
            ink_deltas.append([ib[0] - ia[0], ib[1] - ia[1]])
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
    return {"verification_status": status,
                 "release_eligible": status == "ok", "confidence": confidence,
            "image_sha": image_sha, "stem_bbox_page": [x0, y0, x1, y1],
            "grid_rows": chosen["rows"], "grid_cols": chosen["cols"],
            "visible_cells": [list(v) for v in visible],
            "blank_cells": [list(b) for b in blanks],
            "unknown_cells": [],   # 与 blank_or_undetected 合并上报，不臆断区分
            "target_cell": target, "target_basis": target_basis,
                 "safety_notes": safety_notes,
            "cells": cells,
            "ink_deltas": ink_deltas,
            "layout_hypotheses": hypos[:4],
            "evidence_source": "pixel_localizer",
            "vlm_declared": vlm_declared, "vlm_agreement": vlm_agreement,
            "expects_blank": bool(expects_blank)}


def render_canvas(mask: np.ndarray, evidence: Dict[str, Any],
                  mode: str = "shape") -> np.ndarray:
    """R12-B 双重表示：
    mode="shape"    Shape-normalized：墨迹紧框居中入统一 padding-cell（形状/对称用）；
    mode="position" Position-preserving：直接裁原页面 [stem 带 ∪ 全部槽] 的**原始像素**，
                    不平移不居中——格间绝对位移与格内偏移无损保留（平移/叠加/位置族用）。
    两种表示由同一证据对象产生，像素哈希分别记录。"""
    if mode == "position":
        cs = evidence["cells"]
        bx0 = min(c["cell_bbox_page"][0] for c in cs)
        by0 = min(c["cell_bbox_page"][1] for c in cs)
        bx1 = max(c["cell_bbox_page"][2] for c in cs)
        by1 = max(c["cell_bbox_page"][3] for c in cs)
        return np.asarray(mask[by0:by1, bx0:bx1])
    assert mode == "shape", f"unknown canvas mode: {mode}"
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


def _cols_aligned(cells_a: list, cells_b: list, tol: int = 12) -> bool:
    """两组条带的列起点须互相贴合（九宫格行特征）；题干×选项行的列不对齐 → 拒并。"""
    if not cells_a or not cells_b:
        return True
    xa = sorted({c["bbox_page"][0] for c in cells_a})
    xb = sorted({c["bbox_page"][0] for c in cells_b})
    return all(min(abs(a - b) for b in xb) <= tol for a in xa)



def merge_stem_regions(sregions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """R11-B：九宫格因行距被拆成多条题干带时，按 x 重叠>0.7 且纵向间隙<=60 合并
    （overlay-13 视觉复核确为 3 行矩阵）。返回合并后的区域列表（保持首元素结构）。"""
    if not sregions:
        return []
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
            # R12-C 合并门：x 重叠高 + 纵向贴合 + 待并条带格数 ≤ 已并带最大列数+1。
            # 选项行格数必然超过九宫格单行列数 → 天然拒并（scale-09/mirror-05 教训）；
            # 真网格行的空缺格只会让格数变少，不会超。
            mc = max((len([c for c in last["cells"]
                           if abs(c["bbox_page"][1] - y) <= 6])
                      for y in {c["bbox_page"][1] for c in last["cells"]}), default=0)
            if _ov(r["bbox_page"], last["box"]) > 0.7 and ygap <= 60 \
                    and len(r.get("cells") or []) <= mc + 1:
                b0, b1 = last["box"], r["bbox_page"]
                last["box"] = [min(b0[0], b1[0]), b0[1],
                               max(b0[0] + b0[2], b1[0] + b1[2]), b1[1] + b1[3]]
                last["y1"] = b1[1] + b1[3]
                last["cells"] = last["cells"] + list(r.get("cells") or [])
                continue
        b = r["bbox_page"]
        merged.append({"box": list(b), "y1": b[1] + b[3],
                       "cells": list(r.get("cells") or []), "n_bands": 1})
    # R12-C：题干永远位于选项之上——取最上条带组为题干主区，
    # 面积最大者可能是选项行（scale-09 教训）。所有组按 y 重排索引后返回。
    merged.sort(key=lambda mm: mm["box"][1])
    tall = merged[0]
    first = dict(sregions[0])
    first["bbox_page"] = tall["box"]
    # R12-C：合并后必须按条带 y 重排行优先索引——此前直接拼接各区域的局部
    # (row,col)，出现重复 (0,0)/(0,1)，喂给 build_grid_evidence 的 cells_hint
    # 错乱（grid-11/overlay-13 类九宫格受此拖累）。
    bands_y: List[int] = []
    for c in tall["cells"]:
        ytop = c["bbox_page"][1]
        for i, by in enumerate(bands_y):
            if abs(by - ytop) <= 6:
                c["_band"] = i
                break
        else:
            c["_band"] = len(bands_y)
            bands_y.append(ytop)
    band_rank = {i: k for k, i in
                 enumerate(sorted(range(len(bands_y)), key=lambda j: bands_y[j]))}
    ordered = sorted(tall["cells"], key=lambda c: (band_rank[c["_band"]],
                                                   c["bbox_page"][0]))
    band_ids = [c["_band"] for c in ordered]
    col_seq = []
    seen: dict = {}
    for b in band_ids:
        seen[b] = seen.get(b, -1) + 1
        col_seq.append(seen[b])
    clean = []
    for c, b, col in zip(ordered, band_ids, col_seq):
        c.pop("_band", None)
        c["row"] = band_rank[b]
        c["col"] = col
        clean.append(c)
    first["cells"] = clean
    first["sub_band_count"] = max(len(bands_y), tall.get("n_bands", 1))
    # R13-A4 延续性怀疑（纯几何，无 case-id）：被选最上条带组下方约一个格距处
    # 存在格数 >= 2 倍于本组的兄弟组——题干被垂直撕裂，单条带选择不可信。
    _sib = []
    if clean:
        _by1 = max(c["bbox_page"][1] + c["bbox_page"][3] for c in clean)
        _scale = max(8.0, sum(c["bbox_page"][2] for c in clean) / len(clean))
        _xs = sorted(c["bbox_page"][0] for c in clean)
        for g in merged[1:]:
            gc = g.get("cells") or []
            if len(gc) >= 2 and len(gc) >= 2 * len(clean):
                gap = g["box"][1] - _by1
                if abs(gap - _scale) <= 1.2 * _scale:
                    # 列对齐：兄弟组格起点须落在被选组列网格 ±0.5×scale 内过半，
                    # 否则更像"另一排"（选项行常整体左移/更宽间距）
                    tol = 0.5 * _scale
                    aligned = sum(1 for c in gc
                                  if min(abs(c["bbox_page"][0] - x) for x in _xs) <= tol)
                    if aligned >= max(2, int(0.5 * len(gc))):
                        _sib.append(g["box"])
    first["continuation_suspect"] = bool(_sib)
    first["continuation_siblings"] = _sib
    return [first]


def frame_boxes(band: Sequence[int], ink_boxes: Sequence[Sequence[int]]) -> List[List[int]]:
    """按列间隙中点给行带内每个墨迹单元划“布局框”（过合并修复与位置帧共用）。

    band=[x0,y0,x1,y1]；ink_boxes 需按 x 排序、互不重叠。相邻单元间隙中点为框边界，
    行带左右缘封边。确定性、不依赖金标列数。"""
    bx0, by0, bx1, by1 = [int(v) for v in band]
    if not ink_boxes:
        return []
    edges = [bx0]
    for a, b in zip(ink_boxes, ink_boxes[1:]):
        edges.append(int(round((a[2] + b[0]) / 2)))
    edges.append(bx1)
    out = []
    for i, ib in enumerate(ink_boxes):
        out.append([edges[i], by0, edges[i + 1], by1])
    return out
