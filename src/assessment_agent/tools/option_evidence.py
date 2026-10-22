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
