# -*- coding: utf-8 -*-
"""R13-C：选项证据质量门（零 case-id、纯像素/几何判据）。

行测图形页版式不变量：**字母标签在其图形左侧、同一横带**。据此把选项带
切成 label-anchored 帧（geometry_frame），修复两类系统性缺陷：
  1. 紧墨裁剪丢失格内偏移（平移类不可解）；
  2. 标签碎片被误当独立选项/图形与标签错配。
每格产出 OptionEvidence；验证器只接受**全部消费格过门**的释放。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def _row_runs(mask: np.ndarray, y0: int, y1: int, min_ink: int = 2) -> List[Tuple[int, int, int]]:
    """列投影分段：返回 (x0,x1,ink)。"""
    band = np.asarray(mask[y0:y1, :], dtype=np.int64)
    col = band.sum(axis=0)
    runs: List[Tuple[int, int, int]] = []
    x = 0
    w = col.shape[0]
    while x < w:
        if col[x] >= min_ink:
            x0 = x
            ink = 0
            while x < w and col[x] >= min_ink:
                ink += int(col[x])
                x += 1
            runs.append((x0, x, ink))
        else:
            x += 1
    # 合并 2px 以内的缝隙（抗走样）
    merged: List[List[Any]] = []
    for x0, x1, ink in runs:
        if merged and x0 - merged[-1][1] <= 2:
            merged[-1][1] = x1
            merged[-1][2] += ink
        else:
            merged.append([x0, x1, ink])
    return [(a, b, c) for a, b, c in merged]


def label_anchor_frames(mask: np.ndarray, band_y0: int, band_y1: int,
                        h: int, min_label_gap: int = 8) -> List[Dict[str, Any]]:
    """把选项带按"小高度墨团=标签 + 其后大墨团=图形"配对成帧。

    标签判据：run 高度 ≤ 0.45×带主高度且与右侧图形间距 ≤ 0.9×带高；
    找不到标签的 run 仍是合法图形帧（label_association_verified=False）。
    """
    runs = _row_runs(mask, band_y0, band_y1)
    if not runs:
        return []
    heights = []
    for x0, x1, _ink in runs:
        col = np.asarray(mask[band_y0:band_y1, x0:x1], dtype=np.int64)
        rows = col.sum(axis=1)
        nz = np.nonzero(rows)[0]
        heights.append(int(nz[-1] - nz[0] + 1) if len(nz) else 0)
    main_h = max(heights) if heights else 1
    frames: List[Dict[str, Any]] = []
    i = 0
    n = len(runs)
    while i < n:
        x0, x1, ink = runs[i]
        hh = heights[i]
        if hh <= 0.60 * main_h and hh <= 15:
            # 疑似标签：与下一个 run 配对
            if i + 1 < n:
                gx0, gx1, gink = runs[i + 1]
                gap = gx0 - x1
                if gap <= 1.6 * main_h + 2:
                    frames.append({"frame": [x0, band_y0, gx1, band_y1],
                                   "label_x": (x0, x1),
                                   "figure_x": (gx0, gx1),
                                   "label_association_verified": True})
                    i += 2
                    continue
            frames.append({"frame": [x0, band_y0, x1, band_y1],
                           "label_x": (x0, x1), "figure_x": None,
                           "label_association_verified": False})
            i += 1
            continue
        frames.append({"frame": [x0, band_y0, x1, band_y1],
                       "label_x": None, "figure_x": (x0, x1),
                       "label_association_verified": None})
        i += 1
    return frames


def option_evidence(mask: np.ndarray, band: Tuple[int, int],
                    frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """帧 → OptionEvidence 记录（哈希、完整性、切割检测）。"""
    y0, y1 = band
    h = max(1, y1 - y0)
    out: List[Dict[str, Any]] = []
    for k, fr in enumerate(frames):
        x0, _, x1, _ = fr["frame"]
        sub = np.asarray(mask[y0:y1, x0:x1], dtype=np.uint8)
        nz = np.nonzero(sub.any(axis=0))[0]
        if len(nz) == 0:
            continue
        if fr.get("figure_x") is None:
            continue  # 纯标签碎片不构成选项帧
        cx0, cx1 = x0 + int(nz[0]), x0 + int(nz[-1]) + 1
        colsum = sub.any(axis=1)
        ry0 = int(np.nonzero(colsum)[0][0]) + y0
        ry1 = int(np.nonzero(colsum)[0][-1]) + 1 + y0
        crop = mask[ry0:ry1, cx0:cx1]
        ink = int(np.asarray(crop).sum())
        import hashlib
        sha = hashlib.sha256(np.ascontiguousarray(crop).tobytes()).hexdigest()[:16]
        # 切割：裁剪顶/触带边界且边界行墨密度高（字母不会贴边满行）
        rw = max(1, cx1 - cx0)
        # 真切割 = 裁剪贴带边界 **且带外同一 x 区间仍有墨**（高而完整的图形不算切割）
        outside = 0
        if ry0 <= y0 + 1 and y0 - 2 >= 0:
            outside += int(np.asarray(mask[y0 - 2:y0, cx0:cx1]).sum())
        if ry1 >= y1 - 1 and y1 + 2 <= mask.shape[0]:
            outside += int(np.asarray(mask[y1:y1 + 2, cx0:cx1]).sum())
        touches = outside > 0
        out.append({
            "index": k,
            "bbox_page": [cx0, ry0, cx1 - cx0, ry1 - ry0],
            "cell_bbox": [fr["frame"][0], y0, fr["frame"][2], y1],
            "geometry_frame": list(fr["frame"]),
            "content_pixel_sha": sha,
            "ink": ink,
            "ink_completeness": min(1.0, ink / max(1.0, 0.25 * (y1 - y0) * (cx1 - cx0))),
            "clipping_detected": bool(touches),
            "label_association_verified": fr["label_association_verified"],
            "provenance": "localizer+label_anchor",
        })
    return out


def options_release_ok(evidences: List[Dict[str, Any]], n_expected: int) -> Tuple[bool, List[str]]:
    reasons = []
    if len(evidences) != n_expected:
        reasons.append(f"选项帧数 {len(evidences)} ≠ 期望 {n_expected}")
    for e in evidences:
        if e.get("clipping_detected"):
            reasons.append(f"#{e['index']} 图形疑似被切割")
        if e.get("label_association_verified") is not True:
            reasons.append(f"#{e['index']} 标签关联未证实（False/None 均不放行）")
    return (not reasons), reasons

def strip_label_from_crop(mask, band_mask=None, band_x0: int = 0):
    """R13-G：把"字母在左、图形在右"的宽裁剪中的标签笔触剥掉（与门同一不变量）。

    判据完全复用 label_anchor_frames 规则：左端小高度 run（≤0.6×主高、≤15px）
    且与下一 run 间距 ≤1.6×主高 → 视为标签，置零。无此形态则原样返回。

    R14-E：overlay-12 版式（小图 8×8 **比字母 13px 矮**）让 0.60×主高主判据
    永远失效——字母成了"主墨"、图被当附属。第二条路走**页级行线互证**：
    调用方给出整条选项带 `band_mask`（与裁剪同一次域分解）时，若本 run 是
    窄低墨（≤18px 宽、≤16px 高、墨量 < 邻右图形），且**同一垂直行线**在带
    内其他列还再现 ≥2 次（字母基线=版式不变量）→ 视为标签剥除。
    不给 band 时主判据原样（dev 合成页、二次进入适配层均如此，不回归）。
    """
    import numpy as np
    m = np.asarray(mask)
    if m.ndim != 2 or not m.any():
        return mask
    runs = _row_runs(m, 0, m.shape[0])
    if len(runs) < 2:
        return mask

    def _span(arr, x0: int, x1: int):
        nz = np.nonzero(arr[:, x0:x1].any(axis=1))[0]
        return (int(nz[0]), int(nz[-1] + 1)) if len(nz) else (0, 0)

    heights = [_span(m, a, b)[1] - _span(m, a, b)[0] for a, b, _ in runs]
    x0, x1, ink0 = runs[0]
    ink1 = runs[1][2]
    # 主判据（R13-G 原语义，dev 页/既有测试全部保持）：main_h 取全部 run 的
    # 最大高——字母 run 不得是最高者（字母 ≤0.6×主高且 ≤15）且与图间距有限。
    main_h = max(heights)
    if (heights[0] <= 0.60 * main_h and heights[0] <= 15
            and runs[1][0] - x1 <= int(1.6 * main_h) + 2):
        out = m.copy()
        out[:, x0:x1] = 0
        return out
    # R14-E 第二路（小图版式：overlay-12 图 8×8 比字母 13px 矮，主判据恒假）：
    # 首 run 是**字母尺寸**（高 ≤16、宽 ≤18）、墨量 < 邻右 run，且调用方提供
    # 整带 `band_mask`（页级行线互证；窄墨段=字母段——图形的整块填充段高/墨
    # 都过不了字母尺寸门），同一条垂直行线在带内其他列再现 ≥2 次、且该带在
    # 首 run 左侧无墨（本 run 是带最左字母）→ 字母基线不变量成立，剥除。
    if band_mask is not None and (x1 - x0) <= 18 and heights[0] <= 16 \
            and ink0 < ink1:
        bm = np.asarray(band_mask)
        bx0, bx1 = x0 + band_x0, x1 + band_x0
        a0, b0 = _span(bm, bx0, bx1)
        band_runs = _row_runs(bm, 0, bm.shape[0])
        if not any(r1 <= bx0 for r0, r1, _ in band_runs):
            same_line = sum(
                1 for r0, r1, rink in band_runs
                if not (r1 <= bx1 + 1 and r0 >= bx0 - 1)      # 排除自身
                and (r1 - r0) <= 18 and (b0 - a0) <= 16
                and _span(bm, r0, r1) == (a0, b0)
                and rink <= max(1, int(1.6 * ink0)))
            if same_line >= 2:
                out = m.copy()
                out[:, x0:x1] = 0
                return out
    return mask
