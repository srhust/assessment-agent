"""图形题独立复核（R2c）：候选代入 + 感知复测，**与 Solver 不共享最终规则决策**。

背景：旧复核只是"换二值化阈值重跑同一套规则"，同一处系统性错误会稳定复现，
发现不了——独立基准里的图形误放（verifier_false_accept）全部经过这种"自证"。
本模块改为：

1. **感知复测**：对每个选项裁剪**单独**测量原始特征（个数/边数/对称/墨迹/质心），
   不经过合成画布与规则仲裁；同时校验裁剪完整性（存在、非空白）。
2. **候选代入复核**：从题干独立重建可检验约束，要求**被选选项满足全部约束**，
   且在一个**判别性约束**（非常数等差/等比/边数/墨迹/真实平移/九宫格叠加）上
   唯一匹配其他选项：
   - ``contradicted``：候选违反任一独立约束（实测矛盾）→ 上层按错误拒答处理；
   - ``unverified``：约束无法重建 / 只有弱一致性约束 / 唯一性不成立 / 布局无法
     独立解析 → 受控弃权，**绝不默认通过**，也不冒充"验证纠错"（归因区分二者）；
   - ``confirmed``：全部约束满足且判别性唯一 → 才允许 accept。

这里不假设所有图形规律都能被确定性证明：不能确认时如实 unverified。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .figure_tools import analyze_layout, cell_features, load_mask, normalized_cell_mask

GRID_IOU_ACCEPT = 0.6
GRID_IOU_GAP = 0.12
INK_TOLERANCE = 0.15
CENTROID_TOLERANCE = 0.06
CENTROID_MIN_STEP = 0.012

# Solver 规律名 → 可独立重建的约束族（要求"声称的规律本身"必须可复现，
# 不接受由别的弱约束凑出来的答案）
_RULE_CONSTRAINT = {"count": "count", "sides": "sides", "fill": "ink", "centroid": "centroid", "symmetry": "symmetry"}


def _iou64(left: Optional[np.ndarray], right: Optional[np.ndarray]) -> float:
    if left is None or right is None:
        return 0.0
    union = left | right
    if not union.any():
        return 0.0
    return float((left & right).sum()) / float(union.sum())


def _compose(left: np.ndarray, right: np.ndarray, operator: str) -> np.ndarray:
    if operator == "xor":
        return left ^ right
    if operator == "or":
        return left | right
    return left & right


def _measure_options(option_images: Sequence[Tuple[str, str]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for label, path in option_images:
        p = Path(path)
        if not p.is_file():
            out[label] = {"missing": True}
            continue
        features = cell_features(load_mask(p, None))
        out[label] = {"missing": False, "features": features, "path": str(p)}
    return out


def _sequence_constraints(stem_features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从题干格序列独立重建可检验约束（只依赖原始特征，不做规则仲裁）。

    ``discriminative``：约束是否携带真实规律信息。常数个数/一致对称性只是
    **一致性检查**，不能用来独立确认"答案唯一"——否则会被绘制差异伪证
    （例如某个选项碰巧少一个连通域就"唯一匹配"了常数约束）。
    """
    constraints: List[Dict[str, Any]] = []
    if len(stem_features) < 3:
        return constraints
    counts = [int(f["shape_count"]) for f in stem_features]
    inks = [float(f["ink_pixels"]) for f in stem_features]
    centroids = [f["centroid"] for f in stem_features]

    diffs = [b - a for a, b in zip(counts, counts[1:])]
    if all(c > 0 for c in counts) and len(set(diffs)) == 1:
        discriminative = diffs[0] != 0
        target = counts[-1] + diffs[0]
        constraints.append({
            "kind": "count",
            "target": target,
            "discriminative": discriminative,
            "detail": f"题干图形个数 {counts}（{'等差 d=' + str(diffs[0]) if discriminative else '恒定'}）→ 下一格应为 {target}",
            "test": lambda f, t=target: int(f["shape_count"]) == t,
        })
    else:
        ratios = [b / a for a, b in zip(counts, counts[1:]) if a]
        if (
            all(c > 0 for c in counts)
            and len(ratios) == len(diffs)
            and len(set(round(r, 6) for r in ratios)) == 1
            and ratios[0] != 1
        ):
            nxt = counts[-1] * ratios[0]
            if float(nxt).is_integer():
                constraints.append({
                    "kind": "count",
                    "target": int(nxt),
                    "discriminative": True,
                    "detail": f"题干图形个数 {counts}（等比 r={ratios[0]:g}）→ 下一格应为 {int(nxt)}",
                    "test": lambda f, t=int(nxt): int(f["shape_count"]) == t,
                })
    if all(int(f["shape_count"]) == 1 for f in stem_features):
        sides = [int(f["sides"]) for f in stem_features]
        side_diffs = [b - a for a, b in zip(sides, sides[1:])]
        if all(s >= 3 for s in sides) and len(set(side_diffs)) == 1 and side_diffs[0] != 0:
            target = sides[-1] + side_diffs[0]
            if 3 <= target <= 12:
                constraints.append({
                    "kind": "sides",
                    "target": target,
                    "discriminative": True,
                    "detail": f"单图形边数 {sides} → 下一格应为 {target} 边形",
                    "test": lambda f, t=target: int(f["shape_count"]) == 1 and int(f["sides"]) == t,
                })
    flags = [(bool(f["vertical_symmetric"]), bool(f["horizontal_symmetric"])) for f in stem_features]
    if len(set(flags)) == 1:
        want_v, want_h = flags[0]
        constraints.append({
            "kind": "symmetry",
            "target": f"垂直={want_v} 水平={want_h}",
            "discriminative": False,
            "detail": f"题干对称性一致（垂直={want_v}，水平={want_h}）",
            "test": lambda f, v=want_v, h=want_h: bool(f["vertical_symmetric"]) == v
            and bool(f["horizontal_symmetric"]) == h,
        })
    rel = [b - a for a, b in zip(inks, inks[1:])]
    if all(i > 0 for i in inks) and len(set(round(r, 3) for r in rel)) == 1 and abs(rel[0]) > 0.03 * max(1.0, abs(inks[0])):
        target = inks[-1] + rel[0]
        constraints.append({
            "kind": "ink",
            "target": round(target, 1),
            "discriminative": True,
            "detail": f"题干墨迹面积 {[round(i) for i in inks]} 近似等差 → 下一格 ≈ {round(target)}",
            "test": lambda f, t=target: abs(float(f["ink_pixels"]) - t) <= INK_TOLERANCE * max(1.0, t),
        })
    # 质心平移：仅当步长一致且存在真实净位移才可检验（噪声漂移/旋转不构成约束）
    steps = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(centroids, centroids[1:])]
    if steps:
        dxs = [s[0] for s in steps]
        dys = [s[1] for s in steps]
        uniform = (max(dxs) - min(dxs)) <= 0.02 and (max(dys) - min(dys)) <= 0.02
        moving = abs(sum(dxs) / len(dxs)) >= CENTROID_MIN_STEP or abs(sum(dys) / len(dys)) >= CENTROID_MIN_STEP
        if uniform and moving:
            dx = sum(dxs) / len(dxs)
            dy = sum(dys) / len(dys)
            pc = (centroids[-1][0] + dx, centroids[-1][1] + dy)
            constraints.append({
                "kind": "centroid",
                "target": [round(pc[0], 3), round(pc[1], 3)],
                "discriminative": True,
                "detail": f"题干质心按均步长平移 → 下一格质心 ≈ ({pc[0]:.2f}, {pc[1]:.2f})",
                "test": lambda f, p=pc: abs(f["centroid"][0] - p[0]) <= CENTROID_TOLERANCE
                and abs(f["centroid"][1] - p[1]) <= CENTROID_TOLERANCE,
            })
    return constraints


def _grid_substitution(
    stem_crop: str,
    grid: Tuple[int, int],
    measurements: Dict[str, Dict[str, Any]],
    chosen_label: str,
) -> Dict[str, Any]:
    layout = analyze_layout(Path(stem_crop), grid=grid)
    rows: List[List[Optional[Dict[str, Any]]]] = []
    cols = int(grid[1])
    for band in layout["bands"]:
        mapping = {cell["column"]: cell for cell in band["cells"]}
        rows.append([mapping.get(c) for c in range(cols)])

    def blank(cell: Optional[Dict[str, Any]]) -> bool:
        return cell is None or bool(cell["features"]["blank"] or cell["features"].get("target_marker"))

    target_rows = [row for row in rows if any(blank(cell) for cell in row)]
    if len(target_rows) != 1 or not blank(target_rows[0][-1]) or any(blank(cell) for cell in target_rows[0][:-1]):
        return {
            "verdict": "unverified",
            "reasons": [f"独立布局解析得到 {len(target_rows)} 个空缺行（复核要求唯一行末目标格），无法确认候选代入"],
        }
    mask_full = load_mask(Path(stem_crop), None)

    def cell_norm(cell: Dict[str, Any]) -> np.ndarray:
        # 与引擎一致：按单元格框（非墨迹框）裁切归一化，保证叠加对齐语义相同
        return normalized_cell_mask(mask_full, cell["bbox"])

    complete = [row for row in rows if not any(blank(cell) for cell in row)]
    if len(complete) < max(1, len(rows) - 2):
        return {"verdict": "unverified", "reasons": ["独立解析的完整行数与九宫格结构不符，无法确认叠加算子"]}
    operators: List[Tuple[str, List[float]]] = []
    for operator in ("xor", "or", "and"):
        scores = [
            _iou64(_compose(cell_norm(row[0]), cell_norm(row[1]), operator), cell_norm(row[2]))
            for row in complete
        ]
        if scores and all(s >= GRID_IOU_ACCEPT for s in scores):
            operators.append((operator, scores))
    if not operators:
        return {"verdict": "unverified", "reasons": ["独立复测无法在任何完整行上确认 xor/or/and 叠加算子"]}
    chosen = measurements.get(chosen_label)
    if not chosen or chosen.get("missing") or chosen["features"]["blank"]:
        return {"verdict": "unverified", "reasons": [f"选项 {chosen_label} 裁剪缺失或空白"]}
    target_row = target_rows[0]
    ious: Dict[str, float] = {}
    for label, item in measurements.items():
        if item.get("missing"):
            ious[label] = 0.0
            continue
        opt_mask = load_mask(Path(item["path"]), None)
        opt_h, opt_w = opt_mask.shape[:2]
        # bbox 约定是 [x0, y0, x1, y1]，而 NumPy shape 是 [height, width]——
        # 非方形选项裁剪必须按 (宽, 高) 传参，否则裁切会截断/转置（R3a 修复）
        opt_norm = normalized_cell_mask(opt_mask, [0, 0, opt_w, opt_h])
        for operator, _ in operators:
            expected = _compose(cell_norm(target_row[0]), cell_norm(target_row[1]), operator)
            ious[label] = max(ious.get(label, 0.0), _iou64(opt_norm, expected))
    chosen_iou = ious.get(chosen_label, 0.0)
    rivals = [value for label, value in ious.items() if label != chosen_label]
    detail_ious = {k: round(v, 3) for k, v in ious.items()}
    if chosen_iou < GRID_IOU_ACCEPT:
        return {
            "verdict": "contradicted",
            "reasons": [f"候选 {chosen_label} 代入后与叠加应得图形 IoU={chosen_iou:.2f} < {GRID_IOU_ACCEPT}"],
            "measurements": {"ious": detail_ious},
        }
    if any(value >= GRID_IOU_ACCEPT - 1e-9 and chosen_iou - value < GRID_IOU_GAP for value in rivals):
        return {
            "verdict": "unverified",
            "reasons": ["存在与候选同等满足叠加约束的其他选项，唯一性无法独立确认"],
            "measurements": {"ious": detail_ious},
        }
    return {
        "verdict": "confirmed",
        "reasons": [f"候选代入后与 {'/'.join(op for op, _ in operators)} 叠加应得图形一致（IoU {chosen_iou:.2f}）"],
        "measurements": {"ious": detail_ious, "chosen_iou": round(chosen_iou, 3)},
    }


def substitution_check(
    stem_crop: str,
    option_images: Sequence[Tuple[str, str]],
    chosen_label: str,
    solver_rule: Optional[str],
    grid: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """独立复核入口：返回 {"verdict": confirmed|contradicted|unverified, "reasons", "measurements"}。"""
    if not Path(stem_crop).is_file():
        return {"verdict": "unverified", "reasons": [f"题干裁剪不存在: {stem_crop}"], "measurements": {}}
    measurements = _measure_options(option_images)
    chosen = measurements.get(chosen_label)
    if chosen is None or chosen.get("missing") or chosen["features"]["blank"]:
        return {
            "verdict": "unverified",
            "reasons": [f"候选选项 {chosen_label!r} 的裁剪缺失/空白，感知完整性不成立"],
            "measurements": {},
        }

    if grid:
        return _grid_substitution(stem_crop, (int(grid[0]), int(grid[1])), measurements, chosen_label)

    layout = analyze_layout(Path(stem_crop))
    stem_cells = [cell for band in layout["bands"] for cell in band["cells"]]
    constraints = _sequence_constraints([cell["features"] for cell in stem_cells])
    chosen_summary = {k: chosen["features"][k] for k in ("shape_count", "sides", "ink_pixels", "centroid")}
    if not constraints:
        return {
            "verdict": "unverified",
            "reasons": ["无法从题干独立重建任何可检验约束（个数/边数/对称/面积/质心均无稳定规律），不默认通过"],
            "measurements": {"chosen": chosen_summary},
        }
    violated = [c for c in constraints if not c["test"](chosen["features"])]
    if violated:
        return {
            "verdict": "contradicted",
            "reasons": [f"候选 {chosen_label} 违反独立约束：{c['detail']}" for c in violated],
            "measurements": {"chosen": chosen_summary,
                             "constraints": [{"kind": c["kind"], "target": c["target"]} for c in constraints]},
        }
    wanted = _RULE_CONSTRAINT.get(solver_rule or "")
    active_by_kind = {c["kind"]: c for c in constraints}
    if wanted and wanted not in active_by_kind:
        return {
            "verdict": "unverified",
            "reasons": [f"Solver 声称规律 {solver_rule!r}，但独立复测无法从题干重建该约束"
                        f"（可重建：{sorted(active_by_kind)}），不能借无关约束自证"],
            "measurements": {"chosen": chosen_summary, "constraints": sorted(active_by_kind)},
        }
    primary = active_by_kind.get(wanted) if wanted else None
    if primary is None:
        discriminative = [c for c in constraints if c["discriminative"]]
        if not discriminative:
            return {
                "verdict": "unverified",
                "reasons": ["题干仅能重建弱一致性约束（常数个数/对称一致），无法独立确认唯一答案"],
                "measurements": {"chosen": chosen_summary, "constraints": sorted(active_by_kind)},
            }
        primary = discriminative[0]
    if not primary["discriminative"]:
        return {
            "verdict": "unverified",
            "reasons": [f"Solver 声称的规律对应约束（{primary['kind']}）只是恒定量，不构成可确认的规律信息"],
            "measurements": {"chosen": chosen_summary, "constraints": sorted(active_by_kind)},
        }
    matches = [
        label
        for label, item in measurements.items()
        if not item.get("missing") and not item["features"]["blank"] and primary["test"](item["features"])
    ]
    if len(matches) > 1:
        return {
            "verdict": "unverified",
            "reasons": [f"按 {primary['kind']} 约束独立复测，{len(matches)} 个选项同等匹配（{matches}），唯一性不成立"],
            "measurements": {"matches": matches},
        }
    if not matches:
        return {"verdict": "contradicted", "reasons": [f"{primary['kind']} 约束下没有任何选项匹配，与 Solver 结论矛盾"]}
    return {
        "verdict": "confirmed",
        "reasons": [f"候选满足全部独立重建约束（{sorted(active_by_kind)}）且在 {primary['kind']} 上唯一"],
        "measurements": {"chosen": chosen_summary, "constraints": sorted(active_by_kind)},
    }
