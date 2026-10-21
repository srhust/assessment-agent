"""R4a 几何变换推理核心（独立模块，不修改原六类规律的计算语义）。

输入：二值 mask（numpy 数组或 List[List[int]]），每格一个。
职责：
- 标准表示：内容最小包围框（bbox）裁剪 + 纵横比元数据 + 帧内位置（质心/包围框）——
  平移用**帧内相对坐标**度量，缩放用 **bbox 相对面积**度量，不用绝对像素面积；
- dihedral 群（恒等/90/180/270/水平镜像/垂直镜像/两对角镜像，共 8 个）离散变换；
- 对称性检测：逐元素残差 → 每个图形的"变换不可区分集"（稳定子），旋转对称图形
  不得凭单一匹配断言角度唯一；
- 规律候选：序列/九宫格相邻项一致变换外推 + 帧内平移等差 + 缩放比例等比 + 叠加（XOR/并）；
- 仲裁：多候选必须**共同支持同一选项**；两同等合理规律支持不同答案 → ambiguous。

纪律：答案必须来自变换残差 + 判别裕度的确定性比较；禁止用选项绝对位置、文件名、
标签顺序、人工 grid 注来决定答案（这些字段在本模块根本不可见）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

FRAME = 64          # 帧边长（内容保纵横比居中）
MIN_INK = 12        # 视为空白所需的最少前景像素
EPS = 1e-6


# ------------------------------------------------------------------ 标准形状

def _to_mask(x: Any) -> np.ndarray:
    m = np.asarray(x, dtype=np.uint8)
    return (m > 0).astype(np.uint8)


@dataclass
class Shape:
    """一格的标准化表示：帧内 mask + 内容 bbox 元数据 + 帧内相对位置。"""
    frame: np.ndarray                 # FRAME×FRAME uint8（0/1）
    aspect: float                     # 内容包围框 高/宽
    scale_area: float                 # 内容 bbox 面积 / 帧面积（相对尺度）
    ink_area: float                   # 前景像素数 / 帧面积
    center: Tuple[float, float]       # 内容质心的帧内相对坐标 [0,1]
    blank: bool = False
    canvas: Tuple[int, int] = (0, 0)  # 原始格画布尺寸（一致性核对用）
    canvas_frame: Optional[np.ndarray] = None   # 画布级归一化（保留位置/空洞结构）

    @property
    def sig(self) -> str:
        return "blank" if self.blank else "ink"


def standardize(x: Any, frame: Optional[int] = None) -> Shape:
    frame = frame or FRAME
    m = _to_mask(x)
    ys, xs = np.nonzero(m)
    if len(xs) < MIN_INK:
        empty = np.zeros((frame, frame), dtype=np.uint8)
        return Shape(empty, 1.0, 0.0, 0.0, (0.5, 0.5), blank=True)
    h, w = m.shape
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    crop = m[y0:y1, x0:x1]
    ch, cw = crop.shape
    aspect = (ch / cw) if cw else 1.0
    # 内容保纵横比居中放入 frame
    k = min(frame / ch, frame / cw)
    nh, nw = max(1, int(round(ch * k))), max(1, int(round(cw * k)))
    resized = _resize_nearest(crop, nh, nw)
    canvas = np.zeros((frame, frame), dtype=np.uint8)
    oy, ox = (frame - nh) // 2, (frame - nw) // 2
    canvas[oy:oy + nh, ox:ox + nw] = resized
    cy_rel = ((ys.mean() - y0) / ch) if ch else 0.5
    cx_rel = ((xs.mean() - x0) / cw) if cw else 0.5
    # 帧内相对位置 = 内容包围框中心在原帧中的相对坐标（平移度量用）
    fy, fx = (y0 + y1) / 2.0 / h, (x0 + x1) / 2.0 / w
    scale_area = (ch * cw) / float(h * w)
    return Shape(canvas, round(aspect, 4), round(scale_area, 6),
                 round(float(m.sum()) / float(h * w), 6), (round(fx, 4), round(fy, 4)),
                 canvas=(int(h), int(w)),
                 canvas_frame=fit_canvas(m, frame))


def _resize_nearest(a: np.ndarray, nh: int, nw: int) -> np.ndarray:
    h, w = a.shape
    if (h, w) == (nh, nw):
        return a.copy()
    yi = (np.arange(nh) * h) // nh
    xi = (np.arange(nw) * w) // nw
    return a[yi][:, xi].astype(np.uint8)


# ------------------------------------------------------------------ dihedral 群

# 每个元素 = (名称, 作用于帧的函数)；先转置/翻转，纵横比由 _fit_frame 重归一
def _rot90(a: np.ndarray) -> np.ndarray:
    return np.rot90(a, 1)


def fit_canvas(mask: np.ndarray, frame: Optional[int] = None) -> np.ndarray:
    """画布级归一化：整格画布等比缩到 FRAME×FRAME，**保留**相对位置与内部空洞。
    叠加律（xor/or/and）判别不能用 bbox 归一化——那会把"去同存异"与"并集"的
    中心重叠差异抹平（R4a dev 暴露：选项 A/B 残差 0.11 假近邻）。"""
    frame = frame or FRAME
    m = (mask > 0).astype(np.uint8)
    h, w = m.shape
    if h == 0 or w == 0:
        return np.zeros((frame, frame), dtype=float)
    ys = (np.arange(frame) * h) // frame
    xs = (np.arange(frame) * w) // frame
    return m[ys[:, None], xs[None, :]].astype(float)


def _fit_frame(a: np.ndarray, frame: Optional[int] = None) -> np.ndarray:
    frame = frame or FRAME
    ys, xs = np.nonzero(a)
    if len(xs) < MIN_INK:
        return np.zeros((frame, frame), dtype=np.uint8)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    crop = a[y0:y1, x0:x1]
    ch, cw = crop.shape
    k = min(frame / ch, frame / cw)
    r = _resize_nearest(crop, max(1, int(round(ch * k))), max(1, int(round(cw * k))))
    out = np.zeros((frame, frame), dtype=np.uint8)
    oy, ox = (frame - r.shape[0]) // 2, (frame - r.shape[1]) // 2
    out[oy:oy + r.shape[0], ox:ox + r.shape[1]] = r
    return out


GROUP: Dict[str, Any] = {
    "I":    lambda a: a,
    "R90":  lambda a: _fit_frame(np.rot90(a, 1)),
    "R180": lambda a: _fit_frame(np.rot90(a, 2)),
    "R270": lambda a: _fit_frame(np.rot90(a, 3)),
    "FH":   lambda a: _fit_frame(np.fliplr(a)),
    "FV":   lambda a: _fit_frame(np.flipud(a)),
    "FD":   lambda a: _fit_frame(np.swapaxes(a, 0, 1)),                       # 主对角镜像
    "FA":   lambda a: _fit_frame(np.rot90(np.flipud(a), 1)),                 # 副对角镜像
}

# 原始动作（不重归一）与按帧绑定：R4b P0-2 要求归一化帧尺寸**显式参数传递**，
# 验证器不得通过修改模块全局 FRAME 切换尺度（并发/异常隔离风险、跨请求状态污染）。
_RAW: Dict[str, Any] = {
    "I":    lambda a: a,
    "R90":  lambda a: np.rot90(a, 1),
    "R180": lambda a: np.rot90(a, 2),
    "R270": lambda a: np.rot90(a, 3),
    "FH":   lambda a: np.fliplr(a),
    "FV":   lambda a: np.flipud(a),
    "FD":   lambda a: np.swapaxes(a, 0, 1),
    "FA":   lambda a: np.rot90(np.flipud(a), 1),
}


def group_for(frame: Optional[int] = None) -> Dict[str, Any]:
    """返回绑定到指定帧尺寸的 D4 元素表（默认帧不变，供独立验证器自带帧使用）。"""
    fr = int(frame or FRAME)
    return {name: (lambda raw, f=fr: lambda a, f=f: _fit_frame(raw(a), f))(_RAW[name])
            for name in _RAW}


def compose(t1: str, t2: str) -> str:
    """群元素复合：先 t2 后 t1 的名称（离散查表，二面体群 D4）。"""
    cache = getattr(compose, "_table", None)
    if cache is None:
        cache = {}
        for x, y in product(GROUP, GROUP):
            m = np.zeros((12, 12), dtype=np.uint8)
            m[1:3, 2:4] = m[3:5, 5:7] = m[6:8, 7:9] = 1   # 对角阶梯
            m[2:3, 9:10] = 1                              # 破对称偏移点
            f = GROUP[x](GROUP[y](m))
            for z, g in GROUP.items():
                if np.array_equal(_fit_frame(g(m)), _fit_frame(f)):
                    cache[(x, y)] = z
                    break
        compose._table = cache      # type: ignore[attr-defined]
    return cache[(t1, t2)]


# ------------------------------------------------------------------ 残差与对称

def residual(a: np.ndarray, b: np.ndarray) -> float:
    """对称差 / 并集（0=完全一致）。两 mask 都应是 FRAME 帧。"""
    inter = int(np.logical_and(a, b).sum())
    union = int(np.logical_or(a, b).sum())
    if union == 0:
        return 0.0
    return round(1.0 - inter / union, 6)


def stabilizer(sh: Shape, tol: float = 0.12, frame: Optional[int] = None) -> List[str]:
    """变换不可区分集：施加 g 后残差≤tol 的群元素。旋转对称图形由此显式暴露。"""
    G = group_for(frame)
    return sorted(g for g in G if residual(G[g](sh.frame), sh.frame) <= tol)


def transform_matches(src: Shape, dst: Shape, tol: float = 0.12,
                      frame: Optional[int] = None) -> List[str]:
    """src 经哪些群元素可得到 dst（可能多个→对称歧义）。
    保面积纪律：旋转/镜像不改变 bbox 相对面积——面积明显变化不是旋转/镜像规律
    （那是缩放律的领地），否则会把缩放序列误解释成恒等旋转匹配。"""
    import math
    if src.scale_area > EPS and dst.scale_area > EPS:
        if abs(math.log(dst.scale_area / src.scale_area)) > 0.35:
            return []
    G = group_for(frame)
    return sorted(g for g in G if residual(G[g](src.frame), dst.frame) <= tol)


# ------------------------------------------------------------------ 规律候选

@dataclass
class Candidate:
    """用户指定的候选结构（阶段3）：全部字段可审计。"""
    rule_type: str                          # rotate_flip_seq | translate | scale | overlay
    transformation: str                     # 如 "R90" 或 "R90+FV" 或 "translate(+0.25,+0)"
    source_cells: List[int] = field(default_factory=list)
    predicted_shape: Optional[Dict[str, Any]] = None     # 帧摘要（不存大数组）
    candidate_scores: Dict[str, float] = field(default_factory=dict)  # label→残差
    geometric_residual: float = 1.0
    discriminative_margin: float = 0.0
    confidence: float = 0.0
    unresolved_competitors: List[str] = field(default_factory=list)
    pred_scale: float = 0.0         # 预测格的 bbox 相对面积（0=沿用上一格；等比缩放律改写）
    pred_center: Optional[Tuple[float, float]] = None   # 帧内相对位置预测（None=豁免）
    _pred_canvas: Optional[np.ndarray] = None           # 画布级预测（叠加律专用）

    def as_dict(self) -> Dict[str, Any]:
        return {"rule_type": self.rule_type, "transformation": self.transformation,
                "source_cells": self.source_cells, "predicted_shape": self.predicted_shape,
                "candidate_scores": self.candidate_scores,
                "geometric_residual": self.geometric_residual,
                "discriminative_margin": self.discriminative_margin,
                "confidence": self.confidence,
                "unresolved_competitors": self.unresolved_competitors}


def _predict_translate(centers: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """帧内相对质心的等差位移外推（平移规律）。"""
    if len(centers) < 2:
        return []
    dy = centers[-1][1] - centers[-2][1]
    dx = centers[-1][0] - centers[-2][0]
    steps = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(centers, centers[1:])]
    if all(abs(sx - dx) < 0.08 and abs(sy - dy) < 0.08 for sx, sy in steps):
        return [(dx, dy)]
    return []


def _overlay(target: np.ndarray, a: np.ndarray, b: np.ndarray) -> Dict[str, float]:
    return {"xor": residual(target, ((a > 0) ^ (b > 0)).astype(np.uint8)),
            "or": residual(target, np.logical_or(a, b).astype(np.uint8)),
            "and": residual(target, np.logical_and(a, b).astype(np.uint8))}


# ------------------------------------------------------------------ 共同画布对齐（R4e P0-1）
#
# 叠加律（xor/or/and）是**逐像素**布尔运算，要求两格共享同一画布坐标系。真实视觉
# 裁剪的两格尺寸并不保证相等（VLM bbox / 网格取整 / 每格内容纵横不同），直接对
# 原始掩码做 opf(RAW[a], RAW[b]) 会触发 numpy 广播异常，或（同形但原点不同）静默
# 错位。这里用可审计的格裁剪几何（bbox 在同一题干画布内）把它们对齐到一个
# **共同格画布**，只有当几何证据支持"同一行/列、共享原点"时才放行；否则如实返回
# coordinate_frame_unverified，绝不用无约束补零或强缩放伪造可信叠加。

#: 同行/列格宽的取整抖动容差（像素）。1–2px 属正常网格取整；超过即怀疑坐标系不一致。
CANVAS_ALIGN_TOL_PX = 3


@dataclass
class AlignContract:
    """一次对齐的可审计事实：放进哪个共同画布、原点偏移、抖动、是否放行。"""
    verified: bool
    mode: str                          # grid_top_left | identical | coordinate_frame_unverified | shape_mismatch
    common_canvas: Tuple[int, int]     # (h, w) 对齐后共同画布尺寸
    offsets: Tuple[Tuple[int, int], Tuple[int, int]]   # 每格放入共同画布的 (dy, dx)
    jitter: Tuple[int, int]            # 两格 (dh, dw) 尺寸差
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"verified": self.verified, "mode": self.mode,
                "common_canvas": list(self.common_canvas),
                "offsets": [list(self.offsets[0]), list(self.offsets[1])],
                "jitter": list(self.jitter), "reason": self.reason}


def _pad_to(a: np.ndarray, size: Tuple[int, int], off: Tuple[int, int]) -> np.ndarray:
    """确定性整数平移放置：把 mask 放到 size 画布的 (dy,dx) 原点，超界裁剪、不足补零。

    注意这是**平移**（保内容相对格左上角的位置），不是缩放，也不是无约束补零——
    仅在 AlignContract 判定同行/列共享画布时调用。"""
    h, w = size
    dy, dx = int(off[0]), int(off[1])
    out = np.zeros((h, w), dtype=np.uint8)
    sh, sw = a.shape
    ay0, ax0 = max(0, dy), max(0, dx)
    ay1, ax1 = min(h, dy + sh), min(w, dx + sw)
    if ay1 <= ay0 or ax1 <= ax0:
        return out
    out[ay0:ay1, ax0:ax1] = a[ay0 - dy:ay1 - dy, ax0 - dx:ax1 - dx]
    return out


def align_pair_for_overlay(a: np.ndarray, b: np.ndarray,
                           bbox_a: Optional[Sequence[int]] = None,
                           bbox_b: Optional[Sequence[int]] = None,
                           canvas: Optional[Sequence[int]] = None) -> \
        Tuple[np.ndarray, np.ndarray, AlignContract]:
    """返回对齐到共同画布的两格掩码 + 可审计契约。

    - 有 bbox 且两格来自同一题干画布、处于**同一行或同一列**（y 或 x 区间对齐）：
      按网格原点整数平移对齐（内容位置保留），尺寸差 ≤CANVAS_ALIGN_TOL_PX 视为取整
      抖动放行 → mode=grid_top_left。
    - 无几何信息但两格尺寸完全相同：legacy 逐像素叠加（调用方自证同格画布，如合成
      等尺寸金标）→ mode=identical。
    - 其余（尺寸不同又无共享画布证据、或行列不对齐、抖动超容差）：不放行，
      mode=coordinate_frame_unverified，调用方必须弃权。
    """
    a = _to_mask(a)
    b = _to_mask(b)
    ha, wa = a.shape
    hb, wb = b.shape
    jitter = (ha - hb, wa - wb)

    if bbox_a is not None and bbox_b is not None and canvas is not None:
        ax0, ay0, ax1, ay1 = (int(v) for v in bbox_a)
        bx0, by0, bx1, by1 = (int(v) for v in bbox_b)
        # 同一行：纵向区间一致（允许 ≤TOL 抖动）；同一列：横向区间一致。
        same_row = abs(ay0 - by0) <= CANVAS_ALIGN_TOL_PX and abs(ay1 - by1) <= CANVAS_ALIGN_TOL_PX
        same_col = abs(ax0 - bx0) <= CANVAS_ALIGN_TOL_PX and abs(ax1 - bx1) <= CANVAS_ALIGN_TOL_PX
        if not (same_row or same_col):
            return a, b, AlignContract(False, "coordinate_frame_unverified",
                                       (max(ha, hb), max(wa, wb)), ((0, 0), (0, 0)), jitter,
                                       "两格既不同行也不同列，无共同画布原点")
        if abs(jitter[0]) > CANVAS_ALIGN_TOL_PX or abs(jitter[1]) > CANVAS_ALIGN_TOL_PX:
            return a, b, AlignContract(False, "coordinate_frame_unverified",
                                       (max(ha, hb), max(wa, wb)), ((0, 0), (0, 0)), jitter,
                                       f"格尺寸差 {jitter} 超过网格取整容差 ±{CANVAS_ALIGN_TOL_PX}")
        ch, cw = max(ha, hb), max(wa, wb)
        # 内容相对各自格左上角放置（格切片本就从 (x0,y0) 起）→ 原点 (0,0) 对齐。
        aa = _pad_to(a, (ch, cw), (0, 0))
        bb = _pad_to(b, (ch, cw), (0, 0))
        return aa, bb, AlignContract(True, "grid_top_left", (ch, cw), ((0, 0), (0, 0)),
                                     jitter, "同行/列网格原点整数平移对齐")

    if a.shape == b.shape:
        return a, b, AlignContract(True, "identical", a.shape, ((0, 0), (0, 0)),
                                   (0, 0), "等尺寸（调用方自证同一格画布）")
    return a, b, AlignContract(False, "shape_mismatch",
                               (max(ha, hb), max(wa, wb)), ((0, 0), (0, 0)), jitter,
                               "尺寸不同且无可审计共同画布几何（不得无约束补零/缩放）")


# ------------------------------------------------------------------ 评分与仲裁

def score_options(pred_frame: np.ndarray, options: Dict[str, Shape],
                  pred_scale: float = 0.0, tol: float = 0.15,
                  pred_center: Optional[Tuple[float, float]] = None,
                  pred_canvas: Optional[np.ndarray] = None
                  ) -> Tuple[Dict[str, float], List[str], float]:
    """组合残差 = max(形状残差, 相对尺度惩罚)。等比缩放律下 scale_area 是判别维度；
    未显式预测尺度（pred_scale=0）时沿用上一格尺度（旋转/镜像保面积）。"""
    def one(sh: Shape) -> float:
        if pred_canvas is not None and sh.canvas_frame is not None:
            return residual(pred_canvas, sh.canvas_frame)
        shape_res = residual(pred_frame, sh.frame)
        if pred_scale < 0.0:              # -1 = 叠加类：尺度惩罚不适用
            return shape_res
        if pred_scale <= EPS:
            score = shape_res
        else:
            pen = min(1.0, abs(np.log(max(sh.scale_area, 1e-6) / pred_scale)) / np.log(1.6))
            score = max(shape_res, pen)
        if pred_center is not None:
            dist = abs(sh.center[0] - pred_center[0]) + abs(sh.center[1] - pred_center[1])
            score = max(score, min(1.0, dist / 0.30))   # 位置维度：平移律判别（0.30 曼哈顿容差标度）
        return round(score, 6)
    scores = {lab: one(sh) for lab, sh in options.items()}
    within = sorted(lab for lab, s in scores.items() if s <= tol)
    ordered = sorted(scores.values())
    margin = round((ordered[1] - ordered[0]), 6) if len(ordered) > 1 else 1.0
    return scores, within, margin


def arbitrate(cands: List[Candidate], tol: float = 0.15,
              margin_min: float = 0.05) -> Dict[str, Any]:
    """阶段3仲裁：多个规律候选必须共同支持同一选项。"""
    if not cands:
        return {"verdict": "no_rule", "answer": None, "support": {}}
    for c in cands:
        scores, within, margin = score_options(c._pred_frame, c.options,
                                               c.pred_scale or 0.0, tol,
                                               c.pred_center,
                                               c._pred_canvas)  # type: ignore
        c.candidate_scores = scores
        c.geometric_residual = min(scores.values()) if scores else 1.0
        c.discriminative_margin = margin
        c.unresolved_competitors = [lab for lab in within[1:]] if len(within) > 1 else []
        c.confidence = 0.9 if len(within) == 1 and margin >= margin_min else 0.4
    agree: Dict[str, List[str]] = {}
    voting: List[Candidate] = []
    for c in cands:
        scores, within, margin = c.candidate_scores, None, c.discriminative_margin
        best = sorted(scores.items(), key=lambda kv: kv[1])
        if len(best) >= 1 and best[0][1] <= tol and (len(best) == 1 or best[1][1] - best[0][1] >= margin_min):
            agree.setdefault(best[0][0], []).append(c.transformation if c.rule_type != "translate" else "translate")
            voting.append(c)
        # best 全 >tol：该规律无可行解，退出投票（记入候选审计，不改判）
    if not agree:
        return {"verdict": "ambiguous", "answer": None, "support": {},
                "detail": {c.rule_type: {k: v for k, v in c.candidate_scores.items()} for c in cands}}
    top = max(agree.items(), key=lambda kv: len(kv[1]))
    if len(top[1]) == len(voting):         # 所有可判规律共同支持同一答案
        return {"verdict": "unique", "answer": top[0][0], "support": {top[0][0]: top[1]}}
    # R4a 阶段3纪律：两条同样合理的规律支持**不同**答案 → ambiguous（不得以多数投票作答）
    return {"verdict": "ambiguous", "answer": None, "support": {top[0][0]: top[1]},
            "conflicting": {lab: rules for lab, rules in agree.items() if lab != top[0][0]},
            "detail": "规律间答案不一致"}


# ------------------------------------------------------------------ 总入口

def _seq_candidates(shapes: Sequence[Shape], frame: Optional[int] = None) -> List[Candidate]:
    """已知序列（空格在末尾/被截断）：全程一致的 dihedral 变换外推一格。"""
    cands: List[Candidate] = []
    n = len(shapes)
    G = group_for(frame)
    if n >= 2:
        step_sets = []
        for i in range(n - 1):
            ms = transform_matches(shapes[i], shapes[i + 1], frame=frame)
            if not ms:
                step_sets = []
                break
            step_sets.append(ms)
        if step_sets:
            common = sorted(set.intersection(*(set(s) for s in step_sets)))
            for g in common:
                c = Candidate(rule_type="rotate_flip_seq", transformation=g,
                              source_cells=list(range(n)))
                c._pred_frame = G[g](shapes[-1].frame)          # type: ignore
                cands.append(c)
    # 平移：帧内相对位置等差（零位移不构成平移规律）→ 预测=同内容帧
    centers = [s.center for s in shapes]
    tr = _predict_translate(centers) if len(centers) >= 3 else []
    if tr and (abs(tr[0][0]) > 0.05 or abs(tr[0][1]) > 0.05):
        dx, dy = tr[0]
        # 平移候选的位置预测 = 末格位置 + 步长（越界即不可解释，由选项位置裁决）
        c = Candidate(rule_type="translate", transformation=f"translate({dx:+.2f},{dy:+.2f})",
                      source_cells=list(range(n)),
                      predicted_shape={"aspect": round(float(shapes[-1].aspect), 3)})
        c._pred_frame = shapes[-1].frame                        # type: ignore
        c.pred_center = (min(1.0, max(0.0, shapes[-1].center[0] + dx)),
                         min(1.0, max(0.0, shapes[-1].center[1] + dy)))
        cands.append(c)
    # 缩放：bbox 相对面积等比（内容形状不变，尺度外推——帧已归一，尺度是独立维度）
    areas = [s.scale_area for s in shapes]
    if len(areas) >= 3:
        ratios = [b / a for a, b in zip(areas, areas[1:]) if a > EPS]
        if ratios and max(ratios) - min(ratios) < 0.15 and abs(ratios[0] - 1) > 0.1:
            k = float(np.mean(ratios))
            c = Candidate(rule_type="scale", transformation=f"scale_area x{k:.2f}",
                          source_cells=list(range(n)),
                          predicted_shape={"aspect": round(float(shapes[-1].aspect), 3),
                                           "scale_area": round(min(1.0, areas[-1] * k), 4)})
            c._pred_frame = shapes[-1].frame                    # type: ignore
            c.pred_scale = min(1.0, areas[-1] * k)
            cands.append(c)
    return cands


def analyze_transform(stem: Sequence[Any], options: Dict[str, Any],
                      mode: str = "sequence",
                      tol: float = 0.12,
                      frame: Optional[int] = None,
                      score_tol: Optional[float] = None,
                      margin_min: float = 0.05,
                      cell_geometry: Optional[Sequence[Optional[Sequence[int]]]] = None,
                      stem_canvas: Optional[Sequence[int]] = None,
                      target_cell: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    """题面已知格 mask 序列（空格截断/置末）+ 选项格 mask → 候选+仲裁+对称声明。

    mode="sequence"：一行 N 已知 + 预测下一格。
    mode="grid3"：8 已知 + 空格位置由 **target_cell=(r,c)** 显式给出（默认
    历史 (2,2)）——行内/列内一致变换分别枚举进入候选池，由仲裁决定共同支持。
    R11-C：空格位置属输入表示契约，不是规律唯一性阈值。
    frame：归一化帧尺寸**显式传入**（R4b P0-2：验证器用它做参数扫描，绝不改全局）。
    score_tol：选项打分容差；默认 None=沿用历史 0.15（保持既有验收），扫描组传入
    真实 tol——R4a 曾把仲裁容差写死 0.15，导致"容差扫描"实际不影响打分，一并修复。
    纪律：只用变换残差+裕度；选项标签仅作为标识符，位置/文件名不可见。
    """
    fr = int(frame or FRAME)
    G = group_for(fr)
    shapes = [standardize(m, fr) for m in stem]
    option_shapes = {lab: standardize(m, fr) for lab, m in options.items()}
    if any(s.blank for s in shapes):
        return {"status": "insufficient", "reason": "blank_cell_in_stem",
                "candidates": [], "verdict": {"verdict": "no_rule", "answer": None,
                                              "support": {}},
                "symmetry_unresolvable": [], "per_cell_stabilizer": {}}
    cands: List[Candidate] = []
    blockers: List[Dict[str, Any]] = []
    align_audits: Dict[str, Dict[str, Any]] = {}
    overlay_eval: Dict[str, Dict[str, Any]] = {}
    # R4e P0-1：格裁剪的 bbox 几何在同一 stem 画布坐标系中给出时，叠加先经
    # align_pair_for_overlay 对齐到共同画布再逐像素运算；几何缺失/不可信时
    # 如实降级为 coordinate_frame_unverified（跳过该算子候选），绝不广播崩溃、
    # 绝不无约束补零或强缩放伪造可信叠加。
    geo: List[Optional[Tuple[int, int, int, int]]] = []
    for i in range(len(stem)):
        bb = cell_geometry[i] if (cell_geometry and i < len(cell_geometry)
                                  and cell_geometry[i] is not None) else None
        geo.append(tuple(int(v) for v in bb) if bb else None)
    scv = tuple(int(v) for v in stem_canvas) if stem_canvas else None
    if mode == "sequence":
        cands += _seq_candidates(shapes, fr)
    elif mode == "grid3":
        if len(shapes) != 8:
            return {"status": "insufficient", "reason": "grid3_needs_8_known_cells",
                    "candidates": [], "verdict": {"verdict": "no_rule", "answer": None,
                                                  "support": {}},
                    "symmetry_unresolvable": [], "per_cell_stabilizer": {}}
        # R11-C：空格位置由显式 target_cell=(r,c) 给出（默认历史 (2,2)），
        # 行/列一致变换与外推基格按几何泛化；不降低任何规律唯一性判据。
        tr, tc = (int(target_cell[0]), int(target_cell[1])) if target_cell else (2, 2)
        if not (0 <= tr <= 2 and 0 <= tc <= 2):
            return {"status": "insufficient", "reason": "target_cell_out_of_grid",
                    "candidates": [], "verdict": {"verdict": "no_rule", "answer": None,
                                                  "support": {}},
                    "symmetry_unresolvable": [], "per_cell_stabilizer": {}}
        # 重建行优先映射：已知序列 vals 按行优先跳过 target 槽位
        vals = list(shapes)
        gcells = [[None] * 3 for _ in range(3)]
        k = 0
        for r in range(3):
            for c in range(3):
                if (r, c) == (tr, tc):
                    continue
                gcells[r][c] = vals[k]
                k += 1
        rows = [[gcells[r][c] for c in range(3)] for r in range(3)]
        cols = [[gcells[r][c] for r in range(3)] for c in range(3)]
        for kind, lines, base_cell in (("row", rows, (tr, tc - 1) if tc > 0 else None),
                                       ("col", cols, (tr - 1, tc) if tr > 0 else None)):
            step_sets: List[set] = []
            for line in lines:
                known = [s for s in line if s is not None]
                for a, b in zip(known, known[1:]):
                    ms = transform_matches(a, b, frame=fr)
                    if not ms:
                        step_sets = []
                        break
                    step_sets.append(set(ms))
                else:
                    continue
                break
            if step_sets and base_cell is not None:
                for g in sorted(set.intersection(*step_sets)):
                    c = Candidate(rule_type=f"rotate_flip_grid_{kind}", transformation=g,
                                  source_cells=list(range(8)))
                    base = gcells[base_cell[0]][base_cell[1]]
                    # 外推方向与演示对一致：行律沿 c-1→c，列律沿 r-1→r；
                    # target 不在第 1 列/第 1 行才有"左侧/上方已知基格"
                    c._pred_frame = G[g](base.frame)            # type: ignore
                    cands.append(c)
        # 整网格行优先序列也试一次（平移/缩放类）
        cands += _seq_candidates(shapes, fr)
        # --- 叠加律：每行(列)第三格 = 前两格布尔叠加，跨行(列)同算子 ---
        def mkop(name):
            f = {"xor": lambda x, y: ((x > 0) ^ (y > 0)).astype(np.uint8),
                 "or": lambda x, y: np.logical_or(x, y).astype(np.uint8),
                 "and": lambda x, y: np.logical_and(x, y).astype(np.uint8)}[name]
            return f
        S = shapes
        RAW = [_to_mask(m) for m in stem]   # 叠加在格画布坐标系进行（内容归一化会丢格间位置）
        overlay_eval = {}

        def aligned_op(x, y, opf):
            aa, bb, contract = align_pair_for_overlay(RAW[x], RAW[y],
                                                      geo[x] if geo else None,
                                                      geo[y] if geo else None, scv)
            align_audits.setdefault(f"{x}-{y}", contract.as_dict())
            if not contract.verified:
                blockers.append({"code": contract.mode,
                                 "stage": "overlay", "cells": [x, y],
                                 "detail": contract.reason,
                                 "shapes": [list(RAW[x].shape), list(RAW[y].shape)]})
                return None
            return opf(aa, bb)

        tgt_lin = tr * 3 + tc
        def _lin(r, c):
            l = r * 3 + c
            if l == tgt_lin:
                return None
            return l - 1 if l > tgt_lin else l
        def _make(kind):
            out = []
            for i in range(3):
                if kind == 'row':
                    trip = tuple(_lin(i, j) for j in range(3))
                else:
                    trip = tuple(_lin(j, i) for j in range(3))
                out.append(trip)
            demos = [t for t in out if None not in t]
            tgt = [t for t in out if None in t][0]
            known = [x for x in tgt if x is not None]
            tgt_pos = tgt.index(None)
            return [(demos[0], demos[1], (known[0], known[1], tgt_pos))]
        lines = {"overlay_grid_row": _make('row'),
                 "overlay_grid_col": _make('col')}
        # 空格不在每行/列第 3 位时：or/and 规则方向不可逆，仅 xor（自反）
        # 允许外推；如实记录，不为其伪造候选。
        _row_reversible = (tc == 2)
        _col_reversible = (tr == 2)
        overlay_eval: Dict[str, Dict[str, Any]] = {}
        for kind, groups in lines.items():
            for (a, b, c_), (d, e, f_), (g, h, _tp) in groups:
                rev = _row_reversible if kind == "overlay_grid_row" else _col_reversible
                op_names = ("xor", "or", "and") if rev else ("xor",)
                for opname, opf in ((n, mkop(n)) for n in op_names):
                    # 语义与 R4a 基线一致：规律在演示行/列对上验证，空格行 (g,h) 外推
                    failed = False
                    worst = 0.0
                    for x, y, z in ((a, b, c_), (d, e, f_)):
                        r_mask = aligned_op(x, y, opf)
                        if r_mask is None:
                            failed = True
                            break
                        if S[z].canvas_frame is None:
                            failed = True
                            break
                        r = residual(fit_canvas(r_mask, fr), S[z].canvas_frame)
                        worst = max(worst, r)
                        if r > tol:
                            failed = True
                            break
                    overlay_eval[f"{kind}:{opname}"] = {
                        "best_demo_residual": round(worst, 3),
                        "accepted": not failed,
                        "alignment": [align_audits.get(f"{a}-{b}", {}).get("mode"),
                                      align_audits.get(f"{d}-{e}", {}).get("mode")],
                        "tol": tol,
                    }
                    if failed:
                        continue
                    pred_mask = aligned_op(g, h, opf)
                    if pred_mask is None:
                        continue
                    c = Candidate(rule_type=kind, transformation=f"overlay_{opname}",
                                  source_cells=[a, b, d, e, g, h],
                                  predicted_shape={"aspect": round(float(S[h].aspect), 3)})
                    # 画布级预测：保留空洞/位置结构（bbox 归一会把 xor/or 拉平）
                    c._pred_canvas = fit_canvas(pred_mask, fr)      # type: ignore
                    c._pred_frame = c._pred_canvas                  # type: ignore
                    c.pred_scale = -1.0        # 叠加结果的 bbox 尺度与格内尺度不可比
                    cands.append(c)
    else:
        return {"status": "insufficient", "reason": f"unknown_mode:{mode}",
                "candidates": [], "verdict": {"verdict": "no_rule", "answer": None,
                                              "support": {}},
                "symmetry_unresolvable": [], "per_cell_stabilizer": {}}
    for c in cands:
        if c.pred_scale == 0.0:                 # 旋转/镜像/平移保面积：沿用上一格尺度
            c.pred_scale = shapes[-1].scale_area
        c.options = option_shapes       # type: ignore[attr-defined]
    # 画布一致性（R4e 口径修正）：
    # - 有可审计格几何（stem_canvas 提供）时，画布事实**只按题干网格格**核对：格来自
    #   同一检测画布的切片，尺寸抖动属网格取整问题，由 align_pair_for_overlay 逐对
    #   裁决；选项裁剪是内容贴边框（不同图形天然不同尺寸/纵横），**不得**再把选项
    #   内容尺寸差当作"画布异常"整体降级（overlay-12 首跑误伤路径）。
    # - 无几何信息（legacy 合成输入）时保留 R4a/R4b 原判定：stem+选项格间纵横/面积
    #   比超限 → 整体降级，既有防误放语义一字不动。
    canv = [s.canvas for s in shapes + list(option_shapes.values()) if s.canvas]
    flags: List[str] = []
    if scv is not None:
        stem_canv = [s.canvas for s in shapes if s.canvas]
        if stem_canv:
            ar = [c[0] / max(1, c[1]) for c in stem_canv]
            area_r = max(c[0] * c[1] for c in stem_canv) / max(1, min(c[0] * c[1] for c in stem_canv))
            if max(ar) - min(ar) > 0.2 or area_r > 1.6:
                flags.append("canvas_mismatch：题干格间画布尺寸/纵横不一致（网格解析异常），"
                             "尺度维度不可信，整体降级")
    elif canv:
        ar = [c[0] / max(1, c[1]) for c in canv]
        area_r = max(c[0] * c[1] for c in canv) / max(1, min(c[0] * c[1] for c in canv))
        if max(ar) - min(ar) > 0.2 or area_r > 1.6:
            flags.append("canvas_mismatch：格间画布尺寸/纵横不一致（裁剪异常），尺度维度不可信，整体降级")
    arb = (arbitrate(cands, tol=(0.15 if score_tol is None else float(score_tol)),
                     margin_min=margin_min) if cands
           else {"verdict": "no_rule", "answer": None, "support": {}})
    # 画布失配时任何答案都建立在可疑的尺度维度上 → 整体降级（保守）
    if flags:
        blockers.append({"code": "canvas_mismatch", "stage": "canvas_check",
                         "cells": [], "detail": flags[0]})
        arb = {"verdict": "ambiguous", "answer": None, "support": {},
               "detail": flags[0]}
    sym = {i: stabilizer(s, frame=fr) for i, s in enumerate(shapes)}
    unverifiable = [f"cell{i}: 对称不可区分 {g}" for i, g in sym.items() if len(g) > 1]
    out = {"status": "ok", "verdict": arb, "candidates": [c.as_dict() for c in cands],
           "symmetry_unresolvable": unverifiable + flags,
           "per_cell_stabilizer": sym, "n_cells": len(shapes),
           "blockers": blockers, "overlay_eval": overlay_eval}
    if mode == "grid3" and overlay_eval and not cands:
        # 无任何候选时，把最接近的算子评估如实记为阻塞原因（供归因链使用）：
        # 拒绝不是黑箱异常，而是"全部演示残差超容差/对齐不可信"的可审计事实
        best = min(overlay_eval.items(), key=lambda kv: kv[1]["best_demo_residual"])
        blockers.append({"code": "overlay_residual_over_tol", "stage": "overlay_eval",
                         "cells": [], "detail": f"{best[0]} 最小演示残差 "
                         f"{best[1]['best_demo_residual']} > 容差 {best[1]['tol']}"})
    # R4e P0-2 坐标契约：求解/验证共享**同一份可审计几何事实**（格 bbox + 画布尺寸 +
    # 逐对对齐裁决），但验证器用自有帧独立重算，不消费本模块的候选分数。
    if cell_geometry is not None or stem_canvas is not None:
        out["coord_contract"] = {
            "stem_canvas": list(scv) if scv else None,
            "cell_bboxes": [list(g) if g else None for g in geo] if geo else None,
            "pair_align": align_audits,
            "tol_px": CANVAS_ALIGN_TOL_PX,
        }
    return out
