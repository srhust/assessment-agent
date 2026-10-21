"""图形规律引擎：从统一网格与单元格特征推断规律，并与选项比对。

支持的规律（序列模式）：
- ``count``      : 每格图形个数（常数 / 等差，整数）
- ``regions``    : 每格封闭区域（面）数 holes（常数 / 等差，整数；R9-C 新增）
- ``sides``      : 单图形多边形的边数（3..12，常数 / 等差）
- ``fill``       : 图形墨迹面积占比（等差）
- ``centroid``   : 图形质心的平移（等步长向量）
- ``symmetry``   : 垂直/水平镜像对称性（常数或周期 2 交替）

支持的规律（九宫格模式）：
- ``overlay``    : 每行第三格 = 前两格的 异或 / 并集 / 交集（去同存异、叠加、取交集）

原则：只接受能解释**全部已知单元格**的规律；多条规律给出不同答案时明确失败；
没有任何规律能唯一匹配选项时也失败——不做优先级硬凑，也不猜答案。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .figure_tools import (
    analyze_layout,
    backend_status,
    cell_mask_iou,
    layout_summary,
    load_mask,
    normalized_cell_mask,
)
from .base import Tool, ToolUnavailable

PRIORITY = {"overlay": 10, "sides": 20, "count": 25, "regions": 27, "centroid": 30,
            "symmetry": 35, "fill": 40}
EPS = 1e-6


@dataclass
class RuleHit:
    rule: str
    label: Optional[str]
    detail: str
    predicted: Dict[str, Any] = field(default_factory=dict)
    score: Optional[float] = None

    @property
    def usable(self) -> bool:
        return self.label is not None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "label": self.label,
            "detail": self.detail,
            "predicted": self.predicted,
            "score": None if self.score is None else round(float(self.score), 6),
        }


# ------------------------------------------------------------------ 数列外推
def _arith_next(values: Sequence[float], *, integer: bool = False) -> Optional[float]:
    """常数或等差数列的下一项；无法解释全部已知项时返回 None。"""
    if len(values) < 2:
        return None
    diffs = [values[index + 1] - values[index] for index in range(len(values) - 1)]
    if all(abs(diff) < EPS for diff in diffs):
        return float(values[0])
    step = diffs[0]
    if all(abs(diff - step) < 1e-3 for diff in diffs):
        prediction = values[-1] + step
        if integer:
            rounded = round(prediction)
            if abs(prediction - rounded) > 1e-6:
                return None
            return float(rounded)
        return float(prediction)
    return None


def _unique_label(
    scored: Sequence[Tuple[str, float]],
    *,
    tolerance: float,
    margin: float,
) -> Tuple[Optional[str], Optional[float], bool]:
    """返回 (label, score, ambiguous)。"""
    within = sorted((item for item in scored if item[1] <= tolerance), key=lambda item: item[1])
    if not within:
        return None, None, False
    if len(within) == 1:
        return within[0][0], within[0][1], False
    if within[1][1] - within[0][1] >= margin:
        return within[0][0], within[0][1], False
    return None, None, True


# -------------------------------------------------------------------- 规则
def rule_regions(features: Sequence[Dict[str, Any]], options: Sequence[Tuple[str, Dict[str, Any]]]) -> Optional[RuleHit]:
    """面/封闭区域数（holes，欧拉孔计数）常数或等差外推——数量类「面」算子。"""
    if any("holes" not in item for item in features):
        return None
    values = [float(item["holes"]) for item in features]
    predicted = _arith_next(values, integer=True)
    if predicted is None:
        return None
    # 与 figure_verify 口径一致：恒定面数不作为规律主张（面数在叠加/平移规律
    # 下本非不变量，恒定外推易把正解引到弱一致性上——工单红线：不借弱约束自证）
    if len(values) >= 2 and all(abs(v - values[0]) < 1e-9 for v in values):
        return None
    target = int(predicted)
    matches = [label for label, option in options
               if "holes" in option and option["holes"] == target]
    if len(matches) != 1:
        return RuleHit(
            rule="regions",
            label=None,
            detail=f"封闭区域数外推为 {target}，但有 {len(matches)} 个选项匹配（无法唯一确定）",
            predicted={"holes": target},
        )
    return RuleHit(
        rule="regions",
        label=matches[0],
        detail=f"每格封闭区域数为 {[int(v) for v in values]}，按规律下一格应为 {target} 个",
        predicted={"holes": target},
    )


def rule_count(features: Sequence[Dict[str, Any]], options: Sequence[Tuple[str, Dict[str, Any]]]) -> Optional[RuleHit]:
    values = [float(item["shape_count"]) for item in features]
    predicted = _arith_next(values, integer=True)
    if predicted is None:
        return None
    target = int(predicted)
    matches = [label for label, option in options if option["shape_count"] == target]
    if len(matches) != 1:
        return RuleHit(
            rule="count",
            label=None,
            detail=f"图形个数外推为 {target}，但有 {len(matches)} 个选项匹配（无法唯一确定）",
            predicted={"shape_count": target},
        )
    return RuleHit(
        rule="count",
        label=matches[0],
        detail=f"每格图形个数为 {[int(value) for value in values]}，按等差规律下一格应为 {target} 个",
        predicted={"shape_count": target},
    )


def rule_sides(features: Sequence[Dict[str, Any]], options: Sequence[Tuple[str, Dict[str, Any]]]) -> Optional[RuleHit]:
    if any(item["shape_count"] != 1 for item in features):
        return None
    sides = [int(item["sides"]) for item in features]
    if any(value < 3 or value > 12 for value in sides):
        return None
    predicted = _arith_next([float(value) for value in sides], integer=True)
    if predicted is None:
        return None
    target = int(predicted)
    if target < 3 or target > 12:
        return None
    matches = [
        label
        for label, option in options
        if option["shape_count"] == 1 and int(option["sides"]) == target
    ]
    if len(matches) != 1:
        return RuleHit(
            rule="sides",
            label=None,
            detail=f"边数外推为 {target}，但有 {len(matches)} 个选项匹配",
            predicted={"sides": target},
        )
    return RuleHit(
        rule="sides",
        label=matches[0],
        detail=f"单图形边数为 {sides}，下一格应为 {target} 边形",
        predicted={"sides": target},
    )


def rule_fill(features: Sequence[Dict[str, Any]], options: Sequence[Tuple[str, Dict[str, Any]]]) -> Optional[RuleHit]:
    """面积规律：用墨迹像素数（同一图片内绘制尺度一致，可直接跨格比较）。"""
    values = [float(item["ink_pixels"]) for item in features]
    predicted = _arith_next(values)
    if predicted is None or predicted <= 50:
        return None
    tolerance = max(20.0, 0.06 * predicted)
    margin = max(10.0, 0.02 * predicted)
    scored = [(label, abs(float(option["ink_pixels"]) - predicted)) for label, option in options]
    label, score, ambiguous = _unique_label(scored, tolerance=tolerance, margin=margin)
    if ambiguous:
        return RuleHit(
            rule="fill",
            label=None,
            detail=f"墨迹面积外推到 {predicted:.0f}px，多个选项接近，无法唯一确定",
            predicted={"ink_pixels": round(predicted, 1)},
        )
    if label is None:
        return None
    return RuleHit(
        rule="fill",
        label=label,
        detail=f"墨迹面积按等差变化到约 {predicted:.0f}px（容差 {tolerance:.0f}px）",
        predicted={"ink_pixels": round(predicted, 1)},
        score=score,
    )


def rule_centroid(features: Sequence[Dict[str, Any]], options: Sequence[Tuple[str, Dict[str, Any]]]) -> Optional[RuleHit]:
    points = [tuple(item["centroid"]) for item in features]
    if len(points) < 3:
        return None
    deltas = [
        (points[index + 1][0] - points[index][0], points[index + 1][1] - points[index][1])
        for index in range(len(points) - 1)
    ]
    dx, dy = deltas[0]
    if not all(abs(delta[0] - dx) < 0.02 and abs(delta[1] - dy) < 0.02 for delta in deltas):
        return None
    predicted = (points[-1][0] + dx, points[-1][1] + dy)
    scored = [
        (
            label,
            float(np.hypot(option["centroid"][0] - predicted[0], option["centroid"][1] - predicted[1])),
        )
        for label, option in options
    ]
    label, score, ambiguous = _unique_label(scored, tolerance=0.06, margin=0.025)
    if ambiguous:
        return RuleHit(
            rule="centroid",
            label=None,
            detail=f"质心按等步长平移到 ({predicted[0]:.2f}, {predicted[1]:.2f})，多个选项接近",
            predicted={"centroid": [round(value, 4) for value in predicted]},
        )
    if label is None:
        return None
    return RuleHit(
        rule="centroid",
        label=label,
        detail=(
            f"质心每次平移 ({dx:+.2f}, {dy:+.2f})，下一格质心应在 "
            f"({predicted[0]:.2f}, {predicted[1]:.2f})"
        ),
        predicted={"centroid": [round(value, 4) for value in predicted]},
        score=score,
    )


def rule_symmetry(features: Sequence[Dict[str, Any]], options: Sequence[Tuple[str, Dict[str, Any]]]) -> Optional[RuleHit]:
    pattern = [(bool(item["vertical_symmetric"]), bool(item["horizontal_symmetric"])) for item in features]
    if all(item == pattern[0] for item in pattern):
        predicted = pattern[0]
        prefix = "已知各格对称性相同"
    elif all(pattern[index] == pattern[index % 2] for index in range(len(pattern))):
        predicted = pattern[len(pattern) % 2]
        prefix = "对称性按奇偶交替"
    else:
        return None
    matches = [
        label
        for label, option in options
        if (bool(option["vertical_symmetric"]), bool(option["horizontal_symmetric"])) == predicted
    ]
    if len(matches) != 1:
        return RuleHit(
            rule="symmetry",
            label=None,
            detail=f"对称性应为 垂直={predicted[0]} 水平={predicted[1]}，但有 {len(matches)} 个选项匹配",
            predicted={"vertical": predicted[0], "horizontal": predicted[1]},
        )
    return RuleHit(
        rule="symmetry",
        label=matches[0],
        detail=f"{prefix}（垂直对称={predicted[0]}，水平对称={predicted[1]}）",
        predicted={"vertical": predicted[0], "horizontal": predicted[1]},
    )


# ------------------------------------------------------------------ 叠加规则
_OVERLAY_OPS: Dict[str, Callable[[np.ndarray, np.ndarray], np.ndarray]] = {
    "xor": lambda left, right: np.logical_xor(left, right),
    "or": lambda left, right: np.logical_or(left, right),
    "and": lambda left, right: np.logical_and(left, right),
}
_OVERLAY_NAMES = {"xor": "去同存异（异或）", "or": "直接叠加（并集）", "and": "取交集"}


def rule_overlay(
    mask: np.ndarray,
    rows: Sequence[Sequence[Optional[Dict[str, Any]]]],
    target_row: Sequence[Optional[Dict[str, Any]]],
    option_cells: Sequence[Dict[str, Any]],
) -> Optional[RuleHit]:
    if len(rows) < 1 or len(target_row) < 3:
        return None
    scores: Dict[str, List[float]] = {}
    for name, op in _OVERLAY_OPS.items():
        values: List[float] = []
        for row in rows:
            if any(cell is None for cell in row) or len(row) < 3:
                continue
            first, second, third = row[0], row[1], row[2]
            predicted = op(
                normalized_cell_mask(mask, first["bbox"]),
                normalized_cell_mask(mask, second["bbox"]),
            )
            values.append(cell_mask_iou(predicted, normalized_cell_mask(mask, third["bbox"])))
        scores[name] = values

    consistent = {name: float(min(values)) for name, values in scores.items() if values and min(values) >= 0.88}
    if not consistent:
        return RuleHit(
            rule="overlay",
            label=None,
            detail="未找到能同时解释所有完整行的叠加规则",
            predicted={"iou": {name: round(min(values), 4) for name, values in scores.items() if values}},
        )
    best = max(consistent.items(), key=lambda item: item[1])
    rivals = [name for name, value in consistent.items() if name != best[0] and best[1] - value < 0.05]
    if rivals:
        return RuleHit(
            rule="overlay",
            label=None,
            detail=f"叠加规则不唯一：{best[0]} 与 {rivals} 都能解释全部已知行",
            predicted={"iou": {name: round(value, 4) for name, value in consistent.items()}},
        )

    operation = _OVERLAY_OPS[best[0]]
    predicted_mask = operation(
        normalized_cell_mask(mask, target_row[0]["bbox"]),
        normalized_cell_mask(mask, target_row[1]["bbox"]),
    )
    scored = [
        (chr(ord("A") + index), cell_mask_iou(predicted_mask, normalized_cell_mask(mask, cell["bbox"])))
        for index, cell in enumerate(option_cells)
    ]
    ranked = sorted(scored, key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < 0.80:
        return RuleHit(
            rule="overlay",
            label=None,
            detail=f"叠加规律（{_OVERLAY_NAMES[best[0]]}）的最佳选项相似度仅 {ranked[0][1]:.2f}",
            predicted={"operator": best[0]},
        )
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.04:
        return RuleHit(
            rule="overlay",
            label=None,
            detail=f"两个选项与叠加结果的相似度过于接近（{ranked[0][1]:.2f} vs {ranked[1][1]:.2f}）",
            predicted={"operator": best[0]},
        )
    return RuleHit(
        rule="overlay",
        label=ranked[0][0],
        detail=f"每行均为{_OVERLAY_NAMES[best[0]]}（最低行内相似度 {best[1]:.2f}），据此推算目标行",
        predicted={
            "operator": best[0],
            "ious": {name: round(min(values), 4) for name, values in scores.items() if values},
        },
        score=ranked[0][1],
    )


# ------------------------------------------------------------------ 求解入口
_SEQUENCE_RULES: Sequence[Callable[..., Optional[RuleHit]]] = (
    rule_count,
    rule_regions,
    rule_sides,
    rule_centroid,
    rule_symmetry,
    rule_fill,
)


def _aligned(band: Dict[str, Any], columns: int) -> List[Optional[Dict[str, Any]]]:
    mapping = {cell["column"]: cell for cell in band["cells"]}
    return [mapping.get(column) for column in range(columns)]


def _is_blank(cell: Optional[Dict[str, Any]]) -> bool:
    if cell is None:
        return True
    features = cell["features"]
    # R2c：真实考题的九宫格目标格通常印着“？”而不是留白——问号类标记同样视为空缺，
    # 否则叠加规律在带问号标记的截图上完全失效（这是目标格检测，不是新增规律类别）
    return bool(features["blank"] or features.get("target_marker"))


def _failed(
    reason: str,
    *,
    mode: str,
    layout: Dict[str, Any],
    candidates: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "status": "failed",
        "mode": mode,
        "answer": None,
        "rule": None,
        "agreement": [],
        "confidence": 0.0,
        "reason": reason,
        "steps": [reason],
        "candidates": candidates or [],
        "layout": layout_summary(layout),
        "options": [],
    }


def solve_figure(
    path: str,
    *,
    threshold: Optional[float] = None,
    cell_gap: int = 15,
    band_gap: int = 8,
    grid: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """解析一张图形题图片并给出答案，或给出明确的失败原因。

    ``grid=(rows, cols)`` 时可显式指定网格（含空白格），用于排列不规则、无法自动
    推断网格的题目；此时位置/平移类规律才可用。
    """
    layout = analyze_layout(
        path,
        threshold=threshold,
        cell_gap=cell_gap,
        band_gap=band_gap,
        grid=grid,
    )
    bands = layout["bands"]
    if len(bands) < 2:
        return _failed("未检测到选项区：需要题干行与选项行两块区域", mode="unknown", layout=layout)
    option_band = bands[-1]
    option_cells = option_band["cells"]
    option_columns = [cell["column"] for cell in option_cells]
    if len(option_cells) < 2:
        return _failed(f"选项区只检测到 {len(option_cells)} 个图形，至少需要 2 个", mode="unknown", layout=layout)
    if len(option_cells) > 6:
        return _failed(
            f"选项区检测到 {len(option_cells)} 个图形，超过 6 个，布局解析可能失败",
            mode="unknown",
            layout=layout,
        )
    if option_columns != list(range(len(option_columns))) or any(
        cell["features"]["blank"] for cell in option_cells
    ):
        return _failed(
            "选项行列不连续或存在空白格，可能是图片截断、题干与选项未分开，或网格推断有误",
            mode="unknown",
            layout=layout,
        )
    options = [(chr(ord("A") + index), cell["features"]) for index, cell in enumerate(option_cells)]

    question_bands = bands[:-1]
    question_columns = max(len(band["cells"]) for band in question_bands)
    if question_columns < 1:
        return _failed("题干区没有检测到图形", mode="unknown", layout=layout)
    question_rows = [_aligned(band, question_columns) for band in question_bands]

    if len(question_rows) == 1:
        return _solve_sequence(question_rows[0], options, layout)
    return _solve_grid(question_rows, option_cells, options, layout, path, threshold)


def _solve_sequence(
    row: Sequence[Optional[Dict[str, Any]]],
    options: Sequence[Tuple[str, Dict[str, Any]]],
    layout: Dict[str, Any],
) -> Dict[str, Any]:
    blanks = [index for index, cell in enumerate(row) if _is_blank(cell)]
    if blanks and blanks != [len(row) - 1]:
        return _failed("空白格不在行末，当前只支持“求下一格”的序列题", mode="sequence", layout=layout)
    known_cells = [cell for cell in row if not _is_blank(cell)]
    # R11-C（人工裁决授权）：2 个已知格不再一票否决——但必须有**显式末位空格**
    # 与均匀格律证据才进入仲裁；且要求全部命中规则指向同一选项（比 3+ 已知
    # 更严，不是放松阈值）。无空格证据/规则不唯一 → 照常弃权。
    explicit_blank = bool(blanks) and blanks == [len(row) - 1] and len(row) >= 3
    if len(known_cells) < 2:
        return _failed(
            f"题干可见图形只有 {len(known_cells)} 个，不足以判断规律（弃权）",
            mode="sequence",
            layout=layout,
        )
    if len(known_cells) < 3 and not (explicit_blank and layout.get("grid_regular")):
        return _failed(
            f"题干可见图形只有 {len(known_cells)} 个且缺少显式末位空格/均匀格律证据（弃权）",
            mode="sequence",
            layout=layout,
        )
    known = [cell["features"] for cell in known_cells]  # type: ignore[index]

    rules = list(_SEQUENCE_RULES)
    if not layout.get("grid_regular"):
        rules = [rule for rule in rules if rule is not rule_centroid]
    hits: List[RuleHit] = []
    for rule in rules:
        try:
            hit = rule(known, options)
        except Exception:  # noqa: BLE001 - 单个规则异常不影响其它规则
            hit = None
        if hit is not None:
            hits.append(hit)
    if not hits:
        return _failed(
            "没有支持范围内的规律能解释已知图形（当前支持：个数、边数、面积、平移、对称性）",
            mode="sequence",
            layout=layout,
        )
    if len(known_cells) == 2:
        labels = {h.label for h in hits if h.usable and h.label}
        if len(labels) != 1:
            return _failed(
                "已知格仅 2 且规律不唯一（多规则指向不同选项），弃权",
                mode="sequence",
                layout=layout,
            )
    return _decide(hits, options, mode="sequence", layout=layout)


def _solve_grid(
    rows: Sequence[Sequence[Optional[Dict[str, Any]]]],
    option_cells: Sequence[Dict[str, Any]],
    options: Sequence[Tuple[str, Dict[str, Any]]],
    layout: Dict[str, Any],
    path: str,
    threshold: Optional[float],
) -> Dict[str, Any]:
    target_rows = [row for row in rows if any(_is_blank(cell) for cell in row)]
    if len(target_rows) != 1:
        return _failed(
            f"九宫格检测到 {len(target_rows)} 行存在空缺，当前只支持目标格唯一且位于行末的情形",
            mode="grid",
            layout=layout,
        )
    target_row = target_rows[0]
    if not _is_blank(target_row[-1]) or any(_is_blank(cell) for cell in target_row[:-1]):
        return _failed("九宫格的目标空格不在行末，当前不支持该布局", mode="grid", layout=layout)
    complete_rows = [row for row in rows if all(not _is_blank(cell) for cell in row)]
    if not complete_rows:
        return _failed("九宫格没有可用于推导的完整行", mode="grid", layout=layout)
    hit = rule_overlay(
        load_mask(path, threshold),
        complete_rows,  # type: ignore[arg-type]
        target_row,
        option_cells,
    )
    if hit is None:
        return _failed(
            "九宫格未匹配到支持的叠加规律（当前支持：去同存异 / 叠加 / 取交集）",
            mode="grid",
            layout=layout,
        )
    return _decide([hit], options, mode="grid", layout=layout)


def _decide(
    hits: Sequence[RuleHit],
    options: Sequence[Tuple[str, Dict[str, Any]]],
    *,
    mode: str,
    layout: Dict[str, Any],
) -> Dict[str, Any]:
    usable = [hit for hit in hits if hit.usable]
    notes = [hit.as_dict() for hit in hits]
    if not usable:
        reasons = "; ".join(hit.detail for hit in hits)
        return _failed(f"已有规律无法唯一匹配选项：{reasons}", mode=mode, layout=layout, candidates=notes)

    labels = {hit.label for hit in usable}
    if len(labels) > 1:
        detail = "; ".join(f"{hit.rule}→{hit.label}" for hit in usable)
        return _failed(
            f"多条规律给出不同答案（{detail}），无法确定唯一解",
            mode=mode,
            layout=layout,
            candidates=notes,
        )

    answer = usable[0].label
    agreeing = sorted({hit.rule for hit in usable})
    return {
        "status": "solved",
        "mode": mode,
        "answer": answer,
        "rule": usable[0].rule,
        "agreement": agreeing,
        "confidence": 0.92 if len(agreeing) > 1 else 0.85,
        "reason": None,
        "steps": [f"[{hit.rule}] {hit.detail}" for hit in usable],
        "candidates": notes,
        "layout": layout_summary(layout),
        "options": [
            {
                "label": label,
                "shape_count": features["shape_count"],
                "sides": features["sides"],
                "area_ratio": round(float(features["area_ratio"]), 4),
                "centroid": [round(float(value), 3) for value in features["centroid"]],
                "symmetric": [bool(features["vertical_symmetric"]), bool(features["horizontal_symmetric"])],
            }
            for label, features in options
        ],
        "predicted": usable[0].predicted,
    }


class FigureRuleTool(Tool):
    """图形规律推断工具：布局解析 + 六类规律 + 选项匹配。

    成功返回 ``status="solved"`` 与答案；无法唯一确定时返回 ``status="failed"``
    以及具体原因和每条候选规律的说明，不返回猜测答案。
    """

    id = "figure_rules"
    description = "图形题求解：统一网格解析、个数/边数/面积/平移/对称/叠加规律、选项匹配"
    requires = ("numpy", "opencv-python-headless")
    stage = 1

    def available(self) -> bool:
        status = backend_status()
        return bool(status["numpy"] and status["cv2"])

    def unavailable_reason(self) -> Optional[str]:
        status = backend_status()
        missing = [name for name in ("numpy", "cv2") if not status[name]]
        return f"缺少依赖: {', '.join(missing)}（pip install opencv-python-headless）" if missing else None

    def run(
        self,
        path: str = "",
        grid: Optional[Sequence[int]] = None,
        threshold: Optional[float] = None,
        cell_gap: int = 15,
        band_gap: int = 8,
        **_kwargs: Any,
    ) -> Dict[str, Any]:
        if not self.available():
            raise ToolUnavailable(self.unavailable_reason() or "图形依赖缺失")
        if not path:
            raise ValueError("figure_rules 需要 path 参数（题目截图路径）")
        return solve_figure(
            path,
            threshold=threshold,
            cell_gap=int(cell_gap),
            band_gap=int(band_gap),
            grid=tuple(int(value) for value in grid) if grid else None,
        )
