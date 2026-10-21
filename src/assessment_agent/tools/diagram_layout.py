"""R4g P0-1/P0-2：图形式布局重建器（DiagramLayoutAssembler）。

修复"VLM 多区域输出与 Solver 单图输入"的接口缺陷：当 VLM 把序列题干
解析成多个独立 ``stem_figure``（或声明错误/缺失网格）时，旧适配层只取
第一个区域，其余已检测图形被丢弃。本模块在**零金标、零新模型调用**前提
下用原图像素证据重建统一几何表示：

- 保留全部有效题干图形区域（不静默丢弃任何一个）；
- 证据主通道是**内容单元分组**（行带墨迹列切分 + 间隙聚类）：可见单元
  与 VLM 区域一一映射后，每单元成为画布一格（纯平移、不缩放、不填充）；
- 只有空间证据足够（单元数≥2、间距均匀、与区域映射一致）才确认网格；
- 空白目标格必须由像素证据确认（带内在末单元之后存在"框线证据"或"额外
  未声明墨迹单元"）；figshots 类页面目标格完全不可见时，k+1 假设仅记录
  进 layout_hypotheses 并拒绝——``layout_unverified`` 不冒充确认；
- 绝不按 case_id/答案/金标选择布局；也不因存在多个区域就自动推定行列数。

确定性像素校验（P0-2 六项）：
1. ``regions_in_image``       所有区域在原图内；
2. ``sequence_band``          多个局部图形属于同一行带；
3. ``pitch_support``          单元间距/中心支持当前网格假设；
4. ``stem_option_overlap``    题干与选项区域不空间重叠；
5. ``crop_content_integrity`` 选项裁剪保留足够墨迹（check_option_crop）；
6. ``undeclared_units``       原图可见但 VLM 未声明的图形单元（含目标格）。

输出 JSON 可序列化 resolution dict；``_canvas`` 为等分画布 mask（bool 数组，
可经 cv2.imwrite 落盘后交给现有 Solver 以显式 grid 消费——Solver 与
Verifier 不改动）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

MIN_CELL_PX = 10               # 内容单元最小宽度（像素）
BAND_Y_TOL = 8                 # 行带中心 y 容差（px）
BAND_Y_REL = 0.60              # 行带高度相对容差
CELL_SEP_MIN = 2               # 列切分最小空白（px）
GAP_CLUSTER_RATIO = 2.2        # 组间/组内间隙比 ≥ 此值 → 单元边界
PITCH_UNIFORM_CV = 0.35        # 单元中心间距均匀性容忍（变异系数）
FRAME_COL_RATIO = 0.72         # 框线列：竖墨占比阈值（帧证据假设用）
MIN_INK_RATIO = 0.0015         # 选项裁剪"有内容"的最小墨迹占比
OVERLAP_IOU = 0.05             # 题干/选项重叠判定的最小 IoU
COVER_DIST_REL = 0.75          # 单元中心与区域中心的覆盖容忍（按带宽比例）


@dataclass
class Check:
    name: str
    passed: bool
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def _bbox_rect(bbox: Sequence[int]) -> tuple:
    x, y, w, h = (int(v) for v in bbox)
    return x, y, x + w, y + h


def _iou(r1: tuple, r2: tuple) -> float:
    ix0, iy0 = max(r1[0], r2[0]), max(r1[1], r2[1])
    ix1, iy1 = min(r1[2], r2[2]), min(r1[3], r2[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    a1 = max(0, r1[2] - r1[0]) * max(0, r1[3] - r1[1])
    a2 = max(0, r2[2] - r2[0]) * max(0, r2[3] - r2[1])
    return inter / max(min(a1, a2), 1)


def _segments_with_gap(present: np.ndarray, min_len: int) -> List[tuple]:
    """布尔序列 → [(start, end_exclusive)] 连续段（丢弃短段）。"""
    segs: List[tuple] = []
    start = None
    for i, v in enumerate(present):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                segs.append((start, i))
            start = None
    if start is not None and len(present) - start >= min_len:
        segs.append((start, len(present)))
    return segs


def _content_units(mask: np.ndarray, y0: int, y1: int) -> List[tuple]:
    """行带 [y0:y1] 的墨迹列切分 + 间隙聚类 → 内容单元 [(x0, x1)]。

    先按 >=CELL_SEP_MIN 空白切成列段；再把相邻列段按间隙分簇：间隙 >=
    GAP_CLUSTER_RATIO * (簇内典型间隙) 视为单元边界（一格多图元时组内
    间隙小、跨格间隙大）。无典型间隙信息时用 MIN_CELL_PX 兜底。
    """
    band = mask[y0:y1, :]
    if band.shape[0] < 3:
        return []
    segs = _segments_with_gap(band.any(axis=0), 1)
    segs = [(a, b) for a, b in segs if b - a >= 1]
    if not segs:
        return []
    gaps = [b[0] - a[1] for a, b in zip(segs, segs[1:])]
    if not gaps:
        return list(segs)
    small = [g for g in gaps if g <= max(6, MIN_CELL_PX // 2)]
    typical = float(np.median(small)) if small else 1.0
    threshold = max(typical * GAP_CLUSTER_RATIO, MIN_CELL_PX)
    units: List[list] = [list(segs[0])]
    for (a, b), g in zip(segs[1:], gaps):
        if g >= threshold:
            units.append([a, b])
        else:
            units[-1][1] = b
    return [(u[0], u[1]) for u in units if u[1] - u[0] >= MIN_CELL_PX]


def _frame_boundaries(mask: np.ndarray, y0: int, y1: int, x0: int, x1: int) -> List[int]:
    """带内长垂直线（框边界列）：适用于**有边框**的页面（扰动测试等）。"""
    band = mask[y0:y1, x0:x1]
    if band.shape[1] == 0 or band.shape[0] < MIN_CELL_PX:
        return []
    present = band.mean(axis=0) >= FRAME_COL_RATIO
    return [x0 + s for s, _e in _segments_with_gap(present, 1)]


def _uniform(values: Sequence[float], cv_max: float) -> Dict[str, Any]:
    if not values:
        return {"uniform": False, "cv": None, "reason": "no_values"}
    arr = np.asarray(values, dtype=float)
    cv = float(arr.std() / max(arr.mean(), 1e-9))
    return {"uniform": bool(cv <= cv_max), "cv": round(cv, 4),
            "median": round(float(np.median(arr)), 2)}


def assemble_stem_layout(
    mask: np.ndarray,
    stem_regions: List[Dict[str, Any]],
    option_regions: Optional[List[Dict[str, Any]]] = None,
    *,
    trust_vlm_grid: bool = True,
    profile: str = "merge_pixel",
    expects_blank_target: bool = False,
) -> Dict[str, Any]:
    """重建题干形式布局。

    ``expects_blank_target``（R9-B）：调用方从**题干文字自身**判定的补全线索
    （"补全/填/?/？/选择最符合规律的一项"等）。仅当其为真才尝试尾随空格
    外推；完整序列的题干不会带该线索，因此不会被多造一格。

    ``profile``：
      - ``merge_pixel``（臂 C）：多区域合并 + 确定性像素校验（默认）；
      - ``merge``（臂 B）：多区域直接按 VLM 区域合并为格，不要求像素
        切分共识（pitch/mapping 仍记录为证据但不阻断）；单区域保持
        declared 直通；
      - ``pixel_only``（臂 D）：忽略 VLM 网格声明与角色，仅用 bbox 并集
        定位题带，纯像素内容切分重建（不使用金标布局）。
    """
    height, width = mask.shape
    option_regions = option_regions or []
    checks: List[Check] = []
    hypotheses: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    # ------------------------------------------------ 校验 1：区域在原图内
    valid, invalid = [], []
    for reg in stem_regions:
        x0, y0, x1, y1 = _bbox_rect(reg.get("bbox") or [0, 0, 0, 0])
        inside = 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height and (x1 - x0) >= 2 and (y1 - y0) >= 2
        (valid if inside else invalid).append(reg)
    checks.append(Check("regions_in_image", not invalid, {
        "n_valid": len(valid), "n_invalid": len(invalid),
        "invalid_ids": [str(r.get("region_id")) for r in invalid]}))

    # ------------------------------------------------ 校验 4：不与选项重叠
    overlaps = []
    for reg in valid:
        r1 = _bbox_rect(reg["bbox"])
        for op in option_regions:
            ob = op.get("figure_bbox") or op.get("bbox")
            if not ob:
                continue
            if _iou(r1, _bbox_rect(ob)) >= OVERLAP_IOU:
                overlaps.append({"stem": str(reg.get("region_id")), "option": op.get("label")})
    checks.append(Check("stem_option_overlap", not overlaps, {"overlaps": overlaps}))

    if not valid:
        return _resolution(source="none", mode="unverified", rows=None, cols=None,
                           verified=False, reason="no_valid_stem_regions", checks=checks,
                           hypotheses=hypotheses, rejected=rejected, units=[],
                           declared_cols=None, canvas=None)

    # ------------------------------------------------ 校验 2：行带聚类
    def _cy(r: Dict[str, Any]) -> float:
        return (_bbox_rect(r["bbox"])[1] + _bbox_rect(r["bbox"])[3]) / 2.0

    bands: List[List[Dict[str, Any]]] = []
    for reg in sorted(valid, key=_cy):
        x0, y0, x1, y1 = _bbox_rect(reg["bbox"])
        tol = max(BAND_Y_TOL, BAND_Y_REL * max(1, y1 - y0))
        for band in bands:
            if abs(_cy(reg) - _cy(band[0])) <= tol:
                band.append(reg)
                break
        else:
            bands.append([reg])
    same_band = len(bands) == 1
    checks.append(Check("sequence_band", same_band,
                        {"n_bands": len(bands), "band_sizes": [len(b) for b in bands]}))

    # ------------------------------------------------ 矩阵通道保持现状
    matrix_regions = [r for r in valid if str(r.get("role")) == "stem_matrix"]
    if trust_vlm_grid and matrix_regions and len(valid) == len(matrix_regions):
        reg = matrix_regions[0]
        grid = reg.get("grid") or {}
        return _resolution(source="matrix_passthrough", mode="grid",
                           rows=grid.get("rows"), cols=grid.get("cols"), verified=True,
                           reason=None, checks=checks, hypotheses=hypotheses,
                           rejected=rejected,
                           units=[{"x0": _bbox_rect(reg["bbox"])[0],
                                   "x1": _bbox_rect(reg["bbox"])[2]}],
                           declared_cols=grid.get("cols"), canvas=None)

    # ------------------------------------------------ 内容单元切分（主证据）
    by0 = max(0, min(_bbox_rect(r["bbox"])[1] for r in valid) - 6)
    by1 = min(height, max(_bbox_rect(r["bbox"])[3] for r in valid) + 7)
    if profile == "merge":
        # 臂 B（R9-B 修订）：VLM 区域按其**声明网格**展开为单元（单区域 1×2
        # 亦重组为 2 格——多区域重新组合），仍不引入像素门槛。
        units = _expand_region_cells(valid)
    else:
        units = _content_units(mask, by0, by1)
    centers = [(u[0] + u[1]) / 2.0 for u in units]
    gaps = [round(b - a, 2) for a, b in zip(centers, centers[1:])]
    pitch = _uniform(gaps, PITCH_UNIFORM_CV) if gaps else {"uniform": False, "cv": None}
    checks.append(Check("pitch_support", bool(pitch.get("uniform")), {
        "unit_centers_x": [round(c, 1) for c in centers], "gaps": gaps, "pitch": pitch}))

    # ------------------------------------------------ 单元 ↔ VLM 区域映射
    region_rects = [_bbox_rect(r["bbox"]) for r in
                    sorted(valid, key=lambda r: _bbox_rect(r["bbox"])[0])]
    if profile == "merge":
        # 展开后单元数可多于区域数：单元按中心包含映射回所属区域
        mapping = []
        for i, (ux0, ux1) in enumerate(units):
            uc = (ux0 + ux1) / 2.0
            best = next((j for j, (rx0, _ry0, rx1, _ry1) in enumerate(region_rects)
                         if rx0 - 2 <= uc <= rx1 + 2), None)
            mapping.append({"unit": i, "region_index": best, "score": 1.0,
                            "bbox": [ux0, by0, ux1 - ux0, by1 - by0],
                            "basis": "declared_grid_split"})
    else:
        mapping = []
        for i, (ux0, ux1) in enumerate(units):
            best, best_score = None, 0.0
            for j, (rx0, ry0, rx1, ry1) in enumerate(region_rects):
                rc = (rx0 + rx1) / 2.0
                if rx0 <= ux1 and rx1 >= ux0:
                    score = 1.0 if ux0 >= rx0 - 2 and ux1 <= rx1 + 2 else \
                        max(0.0, 1.0 - abs(rc - (ux0 + ux1) / 2.0) /
                            max(COVER_DIST_REL * (ux1 - ux0), 1.0))
                else:
                    score = _iou((ux0, by0, ux1, by1), (rx0, ry0, rx1, ry1))
                if score > best_score:
                    best, best_score = j, score
            mapping.append({"unit": i, "region_index": best,
                            "score": round(best_score, 3),
                            "bbox": [units[i][0], by0,
                                     units[i][1] - units[i][0], by1 - by0]})
    covered = [m for m in mapping if m["region_index"] is not None]
    undeclared = [m for m in mapping if m["region_index"] is None]
    regions_unmatched = [j for j in range(len(region_rects))
                         if j not in {m["region_index"] for m in covered}]
    # 接受条件：所有 VLM 区域都要能落到某个单元（不允许静默丢弃/孤立区域）；
    # 单元多于区域是**合法发现**（单区域跨多单元、VLM 漏报的可见图形）——
    # 由 undeclared_units 校验项如实记录，不作为拒绝理由，也不借此推定行列数。
    mapping_ok = bool(units) and not regions_unmatched
    checks.append(Check("undeclared_units", True, {  # 信息性：发现即记录证据
        "units_total": len(units), "units_covered": len(covered),
        "undeclared_units": undeclared, "regions_unmatched": regions_unmatched}))

    # ------------------------------------------------ 声明网格与像素对照
    declared = None
    if trust_vlm_grid and len(valid) == 1:
        g = valid[0].get("grid") or {}
        if g.get("rows") and g.get("cols"):
            declared = int(g["cols"])

    blank_confirmed_cols: Optional[int] = None
    # (a) 框线证据：带边框页面里"最后一个框无 VLM 区域覆盖"可确认目标空格
    bounds = _frame_boundaries(mask, by0, by1, units[0][0] if units else 0,
                               units[-1][1] + 60 if units else 0) \
        if profile != "merge" else []
    frames = [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a >= MIN_CELL_PX]
    # (b) 像素外推候选：cols=k+1 仅记录，不确认（figshots 目标格无像素证据）
    hypotheses.append({
        "kind": "content_units", "cols": len(units),
        "mapping_complete": mapping_ok, "pitch": pitch,
        "accepted": bool(len(units) >= 2 and pitch.get("uniform") and mapping_ok)})
    if len(units) >= 1:
        hypotheses.append({
            "kind": "extrapolated_blank_target", "cols": len(units) + 1,
            "accepted": False,
            "reject_reason": "target_cell_not_pixel_visible"
                             "（末单元之后无框线/墨迹证据确认空白目标格）"})
        rejected.append({"hypothesis": f"cols_{len(units) + 1}",
                         "reason": "无法以像素证据唯一确认空白目标格 → 不冒充确认"})
    if frames:
        frame_uniform = _uniform([f[1] - f[0] for f in frames], PITCH_UNIFORM_CV)
        last_frame_covered = bool(units) and any(
            abs((f[0] + f[1]) / 2 - (rx0 + rx1) / 2) <= 0.6 * (f[1] - f[0])
            for f in [frames[-1]] for (rx0, _, rx1, _) in region_rects)
        if frame_uniform.get("uniform") and len(frames) == len(units) + 1 \
                and not last_frame_covered:
            blank_confirmed_cols = len(frames)
            hypotheses.append({"kind": "frame_blank_target", "cols": len(frames),
                               "evidence": {"frames": frames, "uniformity": frame_uniform},
                               "accepted": True})

    # ---- R9-B：目标空白格定向二次观察（题干补全线索 + 三重门控；无线索不外推）----
    option_fig_count = sum(1 for o in option_regions if o.get("has_figure"))
    blank_evidence: Optional[Dict[str, Any]] = None
    if (expects_blank_target and blank_confirmed_cols is None
            and accepted_content_pre(pitch, profile, units, mapping_ok)):
        blank_evidence = _infer_trailing_blank(mask, units, by0, by1, option_fig_count, width)
        if blank_evidence and blank_evidence.get("accepted"):
            blank_confirmed_cols = len(units) + 1
            hypotheses.append({
                "kind": "trailing_blank_inferred", "cols": blank_confirmed_cols,
                "accepted": True, "evidence": blank_evidence})
        else:
            hypotheses.append({
                "kind": "trailing_blank_inferred", "cols": len(units) + 1,
                "accepted": False, "evidence": blank_evidence})

    cols = blank_confirmed_cols if blank_confirmed_cols else len(units)
    accepted_content = accepted_content_pre(pitch, profile, units, mapping_ok)

    if not trust_vlm_grid and accepted_content:
        return _resolution(source="pixel_only", mode="sequence", rows=1, cols=cols,
                           verified=True, reason="pixel_recount_deterministic",
                           checks=checks, hypotheses=hypotheses, rejected=rejected,
                           units=mapping, declared_cols=declared,
                           canvas=_compose_canvas(mask, units, by0, by1,
                                                  blank_target=blank_confirmed_cols))

    if accepted_content:
        consistent = (declared is None) or (declared == cols)
        return _resolution(
            source=("multi_region_merged" if len(valid) > 1 else "pixel_recount"),
            mode="sequence", rows=1, cols=cols, verified=True,
            reason=(None if declared is None else
                    "vlm_grid_confirmed_by_pixels" if declared == cols else
                    "frame_evidence_overrides_declaration" if blank_confirmed_cols else
                    "pixels_recount_over_declaration"),
            checks=checks, hypotheses=hypotheses, rejected=rejected,
            units=mapping, declared_cols=declared,
            canvas=_compose_canvas(mask, units, by0, by1,
                                   blank_target=blank_confirmed_cols))

    # ------------------------------------------------ 声明兜底（无像素共识）
    if trust_vlm_grid and declared:
        if profile == "merge":
            # 臂 B 单区域：保持 R4f 直通语义（接口修复臂不做像素复核）
            return _resolution(source="vlm_single_region", mode="sequence",
                               rows=int(valid[0].get("grid", {}).get("rows", 1)),
                               cols=declared, verified=True,
                               reason="declared_passthrough", checks=checks,
                               hypotheses=hypotheses, rejected=rejected,
                               units=mapping, declared_cols=declared, canvas=None)
        hypotheses.append({"kind": "vlm_declared_single", "cols": declared,
                           "accepted": False,
                           "reject_reason": "no_consistent_pixel_units"})
        return _resolution(source="vlm_single_region", mode="sequence",
                           rows=int(valid[0].get("grid", {}).get("rows", 1)),
                           cols=declared, verified=False,
                           reason="layout_unverified", checks=checks,
                           hypotheses=hypotheses, rejected=rejected,
                           units=mapping, declared_cols=declared, canvas=None)

    rejected.append({"hypothesis": "layout",
                     "reason": f"内容单元证据不足：units={len(units)} "
                               f"pitch={pitch} mapping_ok={mapping_ok}"})
    return _resolution(source="none", mode="unverified", rows=None, cols=None,
                       verified=False, reason="layout_unverified", checks=checks,
                       hypotheses=hypotheses, rejected=rejected, units=mapping,
                       declared_cols=declared, canvas=None)


def _expand_region_cells(regions: List[Dict[str, Any]]) -> List[tuple]:
    """VLM 区域 → 单元列表：按区域自身声明的 grid.cols 等宽切分（多区域重组）。

    仅使用区域自带的 bbox 与声明网格，不引用页面尺寸或固定坐标。"""
    units: List[tuple] = []
    for r in regions:
        x0, _y0, x1, _y1 = _bbox_rect(r.get("bbox") or [0, 0, 0, 0])
        g = r.get("grid") or {}
        try:
            cols = max(1, int(g.get("cols") or 1))
        except (TypeError, ValueError):
            cols = 1
        w = (x1 - x0) / cols
        for i in range(cols):
            units.append((int(round(x0 + i * w)), int(round(x0 + (i + 1) * w))))
    return sorted(units)


def accepted_content_pre(pitch: Dict[str, Any], profile: str,
                         units: List[tuple], mapping_ok: bool) -> bool:
    """内容单元是否构成可接受共识（与原 accepted_content 同判据，提前定义供空格外推复用）。"""
    return bool(len(units) >= 2 and mapping_ok and (
        pitch.get("uniform") or profile == "merge"))


def _infer_trailing_blank(mask: np.ndarray, units: List[tuple], by0: int, by1: int,
                          option_fig_count: int, width: int) -> Optional[Dict[str, Any]]:
    """R9-B 目标空白格定向二次观察：均匀 pitch 外推一格，三重门控确认其为待填空位。

    仅用页面自身几何/像素，不引用任何 case_id、固定尺寸或坐标：
      1. **结构门控**：带图形的答案选项数 ≥ 有墨迹题干格数
         （补全型题：可选答案不少于已画图形 ⇒ 存在待填位）；
      2. **外推落位**：按单元中心中位 pitch 向右外推一格，须落在图宽内；
      3. **像素复核**：外推格与其右侧同带区域墨迹占比均 < MIN_INK_RATIO
         （定向二次观察确认"这一格确实是空的"，非未检出的图形）。
    调用方须已判定为题干含补全线索（``expects_blank_target``），
    本函数不再据选项数单独臆断空格存在——避免完整序列被多造一格。
    三条全真才确认目标空格；任一不满足返回 None（不冒充确认）。
    """
    if len(units) < 1:
        return None
    centers = [(u[0] + u[1]) / 2.0 for u in units]
    widths = [u[1] - u[0] for u in units]
    cellw = float(np.median(widths))
    if len(units) >= 2:
        gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
        pitch = float(np.median(gaps))
    else:
        pitch = cellw
    if option_fig_count < len(units):
        return {"rejected": "structural", "options": option_fig_count, "inked_cells": len(units)}
    # 末单元本身无墨 ⇒ 布局已含空位，无需（也禁止）再外推一格
    last = mask[by0:by1, units[-1][0]:units[-1][1]]
    if last.size and float(last.mean()) <= MIN_INK_RATIO:
        return {"rejected": "last_unit_already_blank"}
    cx = centers[-1] + pitch
    x0 = int(round(cx - cellw / 2))
    x1 = int(round(cx + cellw / 2))
    if x0 < 0 or x1 > width or x1 - x0 < MIN_CELL_PX:
        return {"rejected": "out_of_bounds", "x0": x0, "x1": x1, "width": width}
    sub = mask[by0:by1, x0:min(x1, width)]
    if sub.size == 0 or float(sub.mean()) > MIN_INK_RATIO:
        return {"rejected": "cell_has_ink", "ink": round(float(sub.mean()) if sub.size else -1, 5)}
    tail = mask[by0:by1, min(x1, width):width]
    if tail.size and float(tail.mean()) > MIN_INK_RATIO:
        return {"rejected": "ink_after_target", "tail_ink": round(float(tail.mean()), 5)}
    return {"accepted": True, "x0": x0, "x1": x1, "pitch": round(pitch, 1),
            "cellw": round(cellw, 1), "ink": round(float(sub.mean()), 5)}


def _compose_canvas(mask: np.ndarray, units: Sequence[tuple], by0: int, by1: int,
                    *, blank_target: Optional[int] = None) -> Optional[np.ndarray]:
    """各单元内容纯平移到等宽画布（不缩放；空白目标格保持纯白）。

    units 为映射 dict 列表或 (x0,x1) 元组列表。
    """
    rects = [(u["bbox"][0], u["bbox"][0] + u["bbox"][2]) if isinstance(u, dict)
             else (u[0], u[1]) for u in units]
    if not rects:
        return None
    cw = max(w for a, b in rects for w in [b - a]) + 2
    ch = max(1, by1 - by0)
    n_cols = blank_target if blank_target else len(rects)
    canvas = np.zeros((ch, cw * n_cols), dtype=bool)
    for i, (x0, x1) in enumerate(rects):
        if i >= len(rects):
            break
        sub = mask[by0:by1, x0:x1]
        off = (cw - (x1 - x0)) // 2
        canvas[:sub.shape[0], i * cw + off: i * cw + off + sub.shape[1]] |= sub
    return canvas


def check_option_crop(crop_mask: np.ndarray) -> Dict[str, Any]:
    """校验 5：单个选项裁剪的内容完整性（墨迹占比与最小尺寸）。"""
    h, w = crop_mask.shape
    ratio = float(crop_mask.mean()) if crop_mask.size else 0.0
    ok = (ratio >= MIN_INK_RATIO) and (min(h, w) >= MIN_CELL_PX)
    return {"passed": bool(ok), "ink_ratio": round(ratio, 5),
            "size": [int(w), int(h)], "threshold": MIN_INK_RATIO}


def _resolution(*, source: str, mode: str, rows: Optional[int], cols: Optional[int],
                verified: bool, reason: Optional[str], checks: List[Check],
                hypotheses: List[Dict[str, Any]], rejected: List[Dict[str, Any]],
                units: List[Dict[str, Any]], declared_cols: Optional[int],
                canvas: Optional[np.ndarray]) -> Dict[str, Any]:
    return {
        "layout_source": source, "mode": mode,
        "rows": rows, "cols": cols, "verified": verified,
        "ambiguity_reason": reason,
        "declared_cols": declared_cols,
        "pixel_cols_consistent_with_declared":
            (declared_cols == cols) if (declared_cols and cols) else None,
        "checks": [c.to_dict() for c in checks],
        "hypotheses": hypotheses, "rejected": rejected,
        "units": units,
        "_canvas": canvas,
    }
