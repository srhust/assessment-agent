"""R3a 资料分析：统计图读数（柱状/折线）与像素级一致性复核。

纪律：
- 数值只允许来自**图上标注文本**（视觉抽取证据），绝不允许按柱长反推猜测精确数值；
- 像素柱高仅用于**独立复核**：柱高比例必须与标注读数比例一致，否则 contradicted；
- 无法测量（无裁剪图、非柱状、检测失败）→ 复核不通过但不下结论：unverified 由上层决定。
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_VALUE_TOKEN = re.compile(
    r"([\u4e00-\u9fa5A-Za-z]{1,4})组\s*(\d+(?:\.\d+)?)\s*(件|万元|亿元|元|人|吨|套|户|个|台|辆)?"
)


def chart_values_from_texts(text_regions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """从 text_regions（含 id=chart 或带"N组X件"样式的文本）提取图上读数。

    返回 {"ok": True, "series": {label: Fraction}, "unit": str, "bbox": ..., "evidence": [...]}
    或 {"ok": False, "failure": {"code", "detail"}}。
    """
    series: "Dict[str, Fraction]" = {}
    order: List[str] = []
    evidence: List[Dict[str, Any]] = []
    unit = ""
    conflict = False
    for region in text_regions:
        text = str(region.get("text") or "")
        if not text:
            continue
        for m in _VALUE_TOKEN.finditer(text):
            label, num, u = m.group(1), m.group(2), m.group(3) or ""
            if not re.fullmatch(r"[\u4e00-\u9fa5A-Za-z]{1,4}", label):
                continue
            value = Fraction(num)
            if label in series and series[label] != value:
                conflict = True
            if label not in series:
                series[label] = value
                order.append(label)
                if u:
                    unit = unit or u
                evidence.append({"label": label, "raw": m.group(0), "region_id": region.get("id", "")})
    if conflict:
        return {"ok": False, "failure": {"code": "chart_value_conflict"}}
    if len(order) < 2:
        return {"ok": False, "failure": {"code": "chart_values_insufficient",
                                          "detail": {"found": len(order)}}}
    bbox = next((r.get("bbox") for r in text_regions if r.get("id") == "chart"), None)
    return {"ok": True, "series": series, "order": order, "unit": unit, "bbox": bbox,
            "evidence": evidence}


def measure_bars(crop_path: str) -> Dict[str, Any]:
    """在柱状图裁剪上测量柱高。

    方法：先找基线（最底部覆盖 >40% 宽度的横线），把基线邻域抹断（否则坐标轴会把
    所有柱连成一个组件），再取"底部仍在基线附近、宽度足够"的组件作为柱体，
    柱高 = 基线 − 组件顶端。标注文字（悬空小组件）与类别名（基线下方）被排除。
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {"ok": False, "why": "missing_cv2"}
    path = Path(crop_path)
    if not path.is_file():
        return {"ok": False, "why": "no_crop"}
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return {"ok": False, "why": "decode_error"}
    mask = (image < 128).astype(np.uint8)
    height, width = mask.shape
    if height < 24 or width < 24:
        return {"ok": False, "why": "too_small"}
    baseline = -1
    for y in range(height - 1, -1, -1):
        if mask[y].sum() > 0.4 * width:
            baseline = y
            break
    if baseline < 8:
        return {"ok": False, "why": "no_baseline"}
    work = mask.copy()
    lo = max(0, baseline - 4)
    work[lo:baseline + 3, :] = 0  # 抹断坐标轴与柱底边线
    count, _, stats, _ = cv2.connectedComponentsWithStats((work * 255).astype(np.uint8), 8)
    bars: List[Tuple[int, int]] = []  # (x, top)
    for idx in range(1, count):
        x, y, w, h, area = stats[idx, :5]
        bottom = y + h - 1
        # 柱体轮廓残段：宽足够（≥18px）、底部落在被抹断的基线邻域、向上有实际高度
        if w >= 18 and abs(bottom - lo) <= 6 and (baseline - y) >= 12:
            bars.append((int(x), int(y)))
    if len(bars) < 2:
        return {"ok": False, "why": "bars_not_found", "found": len(bars)}
    bars.sort()
    heights = [max(1, baseline - top) for _, top in bars]
    return {"ok": True, "heights": heights, "baseline": int(baseline), "xs": [b[0] for b in bars]}


def ratio_consistency(values: Sequence[Fraction], heights: Sequence[int],
                      tol: float = 0.15) -> Dict[str, Any]:
    """标注读数比例 vs 像素柱高比例：全部柱都必须在容差内一致。"""
    if len(values) != len(heights) or len(values) < 2:
        return {"ok": None, "why": "count_mismatch", "values": len(values), "heights": len(heights)}
    max_v = max(values)
    max_h = max(heights)
    if max_v <= 0 or max_h <= 0:
        return {"ok": None, "why": "degenerate"}
    deviations = []
    for value, height_px in zip(values, heights):
        expected = float(value) / float(max_v)
        actual = height_px / max_h
        deviations.append(abs(expected - actual))
    worst = max(deviations)
    if worst <= tol:
        return {"ok": True, "worst_deviation": round(worst, 4)}
    return {"ok": False, "worst_deviation": round(worst, 4),
            "detail": {"expected": [float(v) / float(max_v) for v in values],
                        "measured": [h / max_h for h in heights]}}


def read_bar_chart(crop_path: Optional[str], series: Dict[str, Fraction],
                   order: Sequence[str]) -> Dict[str, Any]:
    """组合入口：像素测量并与读数一致性复核。ok=None 表示不可复核（≠矛盾）。"""
    if not crop_path:
        return {"ok": None, "why": "no_crop"}
    measured = measure_bars(crop_path)
    if not measured.get("ok"):
        return {"ok": None, "why": measured.get("why", "measurement_failed")}
    values = [series[name] for name in order]
    if len(values) != len(measured["heights"]):
        return {"ok": None, "why": "bar_count_differs",
                "measured": len(measured["heights"]), "labels": len(values)}
    result = ratio_consistency(values, measured["heights"])
    result["measurements"] = {"heights": measured["heights"], "baseline": measured["baseline"]}
    return result
