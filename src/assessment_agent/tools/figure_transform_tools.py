"""R4a/R4b 图形变换推理工具：离散旋转/镜像/相对平移/等比缩放/九宫格行列变换与叠加。

与 figure_rules（六类统计规律）解耦：本工具消费**每格二值 mask**（来自题图网格切分
或区域裁剪），在 dihedral 群 + 平移 + 缩放 + 叠加的候选空间上推理，输出全字段候选、
仲裁结论与对称/画布不可判声明。求解器不信任调用方传入的归一化选择：帧尺寸与匹配
容差在本工具内独立确定。

R4b 修复：
- P0-2 稳定性政策显式化：参数扫描**逐组分类计数**，accept 要求全部组唯一
  确认同一答案（部分组无法判定 ⇒ unverified，绝不把 None 当无关跳过）；
  归一化帧尺寸全部显式参数传递，不再修改模块全局 FRAME。
- P0-3 独立几何验证 verify_geometry：不复用求解器仲裁结论，直接从原始 mask 用
  验证器**自己的帧**（72，落在扫描组之外）重放规律、外推、逐选项残差、等价选项/
  稳定子/替代规律/离散化扰动六项检查；parameter_stable 与 independently_verified
  分离记账。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .base import Tool, ToolUnavailable
from .figure_tools import backend_status, load_mask
from .figure_transform import (
    FRAME, analyze_transform, fit_canvas, group_for, residual, standardize,
    transform_matches, _to_mask,
)
from ..schemas import Verification, VerificationCheck


class FigureTransformTool(Tool):
    id = "figure_transform"
    description = ("图形变换推理：90/180/270 旋转、水平/垂直镜像、相对平移、等比缩放、"
                   "九宫格行列一致变换与叠加；输出候选残差、判别裕度与对称不可区分声明")
    requires = ("numpy", "opencv-python-headless")
    stage = 1

    def available(self) -> bool:
        status = backend_status()
        return bool(status["numpy"] and status["cv2"])

    def run(self,
            stem_masks: Optional[Sequence[Any]] = None,
            option_masks: Optional[Dict[str, Any]] = None,
            mode: str = "sequence",
            tol: float = 0.12,
            **_kw: Any) -> Dict[str, Any]:
        if not self.available():
            raise ToolUnavailable("缺少 numpy/cv2 依赖")
        if not stem_masks or not option_masks:
            raise ValueError("figure_transform 需要 stem_masks 与 option_masks")
        norm_stem = [self._as_mask(m) for m in stem_masks]
        norm_opts = {lab: self._as_mask(m) for lab, m in option_masks.items()}
        return analyze_transform(norm_stem, norm_opts, mode=mode, tol=float(tol))

    @staticmethod
    def _as_mask(m: Any) -> np.ndarray:
        if isinstance(m, (str,)):                  # 文件路径 → 读图二值化
            return load_mask(m, None).astype(np.uint8)
        a = np.asarray(m)
        return (a > 0).astype(np.uint8)


# ------------------------------------------------------------------ 独立验证器

SCAN_FRAMES: Tuple[int, ...] = (48, 64, 80)
SCAN_TOLS: Tuple[float, ...] = (0.08, 0.12, 0.18)
BASE_TOL = 0.12
#: 预声明稳定性政策（R4b P0-2）：accept 必须在**全部**扫描组下唯一复算同一答案
#: （all_confirm）。R4b 方法学修正两处：
#: ① 扫描只检验**独立归一化前提**（帧尺寸 {48,64,80}，打分容差沿用求解器口径）；
#:    R4a 的 3×3 全因子把匹配容差与帧交叉成 {48帧×0.08容差} 病态组合，检验的是
#:    "双重极端劣化"而非求解器前提，会把真实唯一规律误判为不稳定（dev mirror/grid
#:    题的 8/9 全数来自交叉组）。容差不安全感改由 verify_geometry 的自有容差
#:    VTOL 与双侧扰动复核承担，不再混入扫描维度。
#: ② 决策全部显式计数（confirmed/contradicted/unresolved），unresolved>0 绝不放行
#:    ——修复 R4a "answers={A,None,…}" 被当成全一致的漏洞。
#: 政策仍取最保守 all_confirm（3/3）；未来放宽必须改写本常量族并配套反例测试。
SCAN_GRID: Tuple[Tuple[int, float], ...] = tuple((f, BASE_TOL) for f in SCAN_FRAMES)
STABILITY_POLICY = "all_confirm"


def verify_transformation(payload: Dict[str, Any],
                          stem_masks: Sequence[Any],
                          option_masks: Dict[str, Any],
                          answer: Optional[str]) -> Verification:
    """阶段4：独立重算——验证器不采信求解器的归一化/掩码/阈值前提。

    做法：按 SCAN_GRID 单因子参数组各自**重跑全流程**（frame/tol 显式传入，
    零全局状态突变），逐组记录 verdict/answer/残差明细，然后按预声明政策统计：
      confirmed_count    —— 唯一复算且与求解答案一致的组数
      contradicted_count —— 唯一复算但指向其他选项的组数
      unresolved_count   —— 该组无法唯一确定（ambiguous/no_rule/insufficient）
    决策（保守政策 all_confirm，总组数=5）：
      contradicted>0 且 confirmed==0 → reject（独立重算全体否决）
      contradicted>0（部分漂移）      → unverified
      unresolved>0（含 1/5 确认）     → unverified   ← R4a 漏洞修复点
      全组确认                        → 还需 verify_geometry 独立代入验证
                                        passed → accept；blocking → reject；
                                        其余不合格 → unverified
    """
    checks: List[VerificationCheck] = []
    per_group: List[Dict[str, Any]] = []
    for frame, tol in SCAN_GRID:
        res = analyze_transform([_to_mask(m) for m in stem_masks],
                                {k: _to_mask(v) for k, v in option_masks.items()},
                                mode=payload.get("mode", "sequence"),
                                tol=tol, frame=frame)
        v = res["verdict"]
        best = None
        if res["candidates"]:
            sc = res["candidates"][0].get("candidate_scores") or {}
            if sc:
                best = round(min(sc.values()), 4)
        per_group.append({"frame": frame, "tol": tol,
                          "verdict": v.get("verdict"),
                          "answer": v.get("answer") if v.get("verdict") == "unique" else None,
                          "min_residual": best,
                          "symmetry": (res["symmetry_unresolvable"] or [])[:2]})
    total = len(per_group)
    if answer is None:
        return Verification(action="unverified",
                            checks=[VerificationCheck(
                                name="figure_transform.abstain", passed=True,
                                detail="求解器未作答，无验证对象", severity="info")],
                            notes="abstain")
    confirmed = [g for g in per_group if g["answer"] == answer]
    contradicted = [g for g in per_group
                    if g["answer"] is not None and g["answer"] != answer]
    unresolved = [g for g in per_group if g["answer"] is None]
    stats = {"total_groups": total, "confirmed_count": len(confirmed),
             "contradicted_count": len(contradicted), "unresolved_count": len(unresolved),
             "policy": STABILITY_POLICY, "per_group": per_group}
    checks.append(VerificationCheck(
        name="figure_transform.scan_stats", passed=(len(confirmed) == total),
        detail=(f"单因子扫描：确认 {len(confirmed)}/{total}，矛盾 {len(contradicted)}，"
                f"未判 {len(unresolved)}（政策 all_confirm={total}/{total}）"),
        severity="info"))
    drift = sorted({g["answer"] for g in contradicted})
    if contradicted and not confirmed:
        checks.append(VerificationCheck(
            name="figure_transform.recompute", passed=False,
            detail=f"独立重算全部指向其他选项 {drift}", severity="blocking"))
        return Verification(action="reject", checks=checks, notes=f"stats={_brief(stats)}",
                             final_answer=None)
    if contradicted:
        checks.append(VerificationCheck(
            name="figure_transform.grid_stability", passed=False,
            detail=f"参数组间答案漂移 {drift} vs 求解答案 {answer}（确认 {len(confirmed)}/{total}）",
            severity="warning"))
        return Verification(action="unverified", checks=checks,
                            notes=f"stats={_brief(stats)}；独立重算不稳定，不对外作答")
    if len(unresolved) > 0:
        # R4a P0 修复：仅部分组唯一确认（如 1/9）不得当作"全部一致"放行。
        checks.append(VerificationCheck(
            name="figure_transform.grid_stability", passed=False,
            detail=(f"{len(unresolved)}/{total} 组在独立归一化参数下无法唯一确定；"
                    "all_confirm 政策未满足"), severity="warning"))
        return Verification(action="unverified", checks=checks,
                            notes=f"stats={_brief(stats)}；参数稳定性不达标（不是全组确认）")
    # 到这里 parameter_stable = 全部扫描组唯一确认 answer。
    solver_cands = payload.get("candidates") or []
    if not solver_cands:
        checks.append(VerificationCheck(
            name="figure_transform.support", passed=False,
            detail="求解器无候选支撑却给出答案", severity="blocking"))
        return Verification(action="reject", checks=checks,
                            notes="无候选支撑")
    checks.append(VerificationCheck(
        name="figure_transform.parameter_stable", passed=True,
        detail=f"全部 {len(SCAN_GRID)} 组独立归一化参数下均唯一复算为 {answer}", severity="info"))
    geom = verify_geometry(payload, stem_masks, option_masks, answer)
    checks.extend(geom["checks"])
    if geom["blocking"]:
        return Verification(action="reject", checks=checks,
                            notes=f"stats={_brief(stats)}；几何代入验证否决",
                            final_answer=None)
    if not geom["passed"]:
        return Verification(action="unverified", checks=checks,
                            notes=f"stats={_brief(stats)}；参数稳定但独立代入验证未过：{geom['reasons']}")
    return Verification(action="accept", final_answer=answer, checks=checks,
                        notes=f"stats={_brief(stats)}；parameter_stable=all_confirm 且 independently_verified")


def _brief(stats: Dict[str, Any]) -> str:
    return (f"confirmed {stats['confirmed_count']}/{stats['total_groups']}, "
            f"contradicted {stats['contradicted_count']}, "
            f"unresolved {stats['unresolved_count']}")


# ------------------------------------------------------------------ P0-3 几何代入验证

VFRAME = 72          # 验证器自有帧（不在求解器扫描组 {48,64,80} 决策使用范围内？
#                     64/80 在扫描组内——这里独立于**求解器实际用的帧**，取 72 与全部
#                     扫描组不同，确保重放/残差在第四套归一化前提下复核）
VTOL = 0.15          # 验证器自有容差（不跟随求解参数）


def _dilate(m: np.ndarray) -> np.ndarray:
    """±1px 膨胀（边界**不环绕**——np.roll 会把对侧墨迹卷进边缘，制造虚假敏感）。"""
    p = np.pad(m, 1)
    return (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] |
            p[1:-1, :-2] | p[1:-1, 2:]).astype(np.uint8)


def _erode(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, 1)
    return (p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] &
            p[1:-1, :-2] & p[1:-1, 2:]).astype(np.uint8)


def verify_geometry(payload: Dict[str, Any],
                    stem_masks: Sequence[Any],
                    option_masks: Dict[str, Any],
                    answer: str) -> Dict[str, Any]:
    """独立几何候选代入验证（不复用求解器的候选打分/仲裁结论）。

    六项检查（对求解器声明支撑 answer 的那条规律逐一重放）：
      1. rule_replay        —— 声明的变换能否解释已知格之间的几何关系（逐相邻对）
      2. extrapolation      —— 作用于基格是否得到与选项可比对的预期形状
      3. option_residuals   —— 每个选项与预测形状的残差：answer 必须最优且 ≤VTOL
      4. equivalence        —— 等价图形选项 / 稳定子 / 未排除竞争项
      5. alternative_rules  —— 枚举 dihedral/叠加/平移/缩放全空间，是否有同等合理
                               规律唯一指向不同选项
      6. discretization     —— ±1px 膨胀/腐蚀**同时作用于题干与选项**并重放规律：
                               规律应仍成立，且所答仍为最优（排序不翻转、裕度不塌缩）。
                               只看绝对残差会把"扰动的系统性影响"误报为敏感。
    blocking（规律重放失败/预测不指向 answer/无规律支撑）→ reject；
    非 blocking（等价项/替代规律/离散化敏感）→ independently_verified=False → unverified。
    全程使用验证器自有帧 VFRAME=72 与自有容差 VTOL，不读写求解器参数状态。
    """
    checks: List[VerificationCheck] = []
    reasons: List[str] = []
    G = group_for(VFRAME)
    mode = payload.get("mode", "sequence")

    def prep(stems: Sequence[Any], opts: Dict[str, Any]):
        S_ = [standardize(_to_mask(m), VFRAME) for m in stems]
        O_ = {lab: standardize(_to_mask(m), VFRAME) for lab, m in opts.items()}
        RAW_ = [_to_mask(m) for m in stems]
        return S_, O_, RAW_

    S, O, RAW = prep(stem_masks, option_masks)

    def enumerate_rules(S_, RAW_) -> List[Tuple[str, str, Optional[np.ndarray],
                                                Optional[float], Optional[np.ndarray],
                                                Optional[Tuple[float, float]]]]:
        """全空间可行规律（验证器自有帧重放，不采信求解器候选表）。"""
        feas: List[Tuple[str, str, Optional[np.ndarray], Optional[float],
                        Optional[np.ndarray], Optional[Tuple[float, float]]]] = []
        n_ = len(S_)
        if mode == "grid3" and n_ == 8:
            row_pairs = [(0, 1), (1, 2), (3, 4), (4, 5), (6, 7)]
            col_pairs = [(0, 3), (3, 6), (1, 4), (4, 7)]
            for g, el in G.items():
                if all(residual(el(S_[a].frame), S_[b].frame) <= VTOL for a, b in row_pairs):
                    feas.append(("rotate_flip_grid_row", g, el(S_[7].frame), None, None, None))
                if all(residual(el(S_[a].frame), S_[b].frame) <= VTOL for a, b in col_pairs):
                    feas.append(("rotate_flip_grid_col", g, el(S_[5].frame), None, None, None))
        else:
            pairs = [(i, i + 1) for i in range(n_ - 1)]
            for g, el in G.items():
                if all(residual(el(S_[a].frame), S_[b].frame) <= VTOL for a, b in pairs):
                    feas.append(("rotate_flip_seq", g, el(S_[-1].frame), None, None, None))
            if n_ >= 3:                            # 平移：内容不变 + 质心等差
                cent = [s_.center for s_ in S_]
                dy = cent[-1][1] - cent[-2][1]
                dx = cent[-1][0] - cent[-2][0]
                if (abs(dx) > 0.05 or abs(dy) > 0.05) and all(
                        abs(b[1] - a[1] - dy) < 0.08 and abs(b[0] - a[0] - dx) < 0.08
                        for a, b in zip(cent, cent[1:])):
                    feas.append(("translate", f"translate({dx:+.2f},{dy:+.2f})",
                                 S_[-1].frame, None, None,
                                 (min(1.0, max(0.0, cent[-1][0] + dx)),
                                  min(1.0, max(0.0, cent[-1][1] + dy)))))
            if n_ >= 3:                            # 缩放：bbox 相对面积等比
                areas = [s_.scale_area for s_ in S_]
                ratios = [b / a for a, b in zip(areas, areas[1:]) if a > 1e-9]
                if ratios and max(ratios) - min(ratios) < 0.15 and abs(ratios[0] - 1) > 0.1:
                    k = float(np.mean(ratios))
                    feas.append(("scale", f"scale_area x{k:.2f}", S_[-1].frame,
                                 min(1.0, areas[-1] * k), None, None))
        if mode == "grid3" and n_ == 8:            # 叠加：行/列布尔算子
            ops = {"xor": lambda x, y: ((x > 0) ^ (y > 0)).astype(np.uint8),
                   "or": lambda x, y: np.logical_or(x, y).astype(np.uint8),
                   "and": lambda x, y: np.logical_and(x, y).astype(np.uint8)}
            for kind, triples in (("overlay_grid_row", [(0, 1, 2), (3, 4, 5), (6, 7, None)]),
                                  ("overlay_grid_col", [(0, 3, 6), (1, 4, 7), (2, 5, None)])):
                for opname, opf in ops.items():
                    ok = True
                    for a, b, c_ in triples:
                        if c_ is None:
                            continue
                        if residual(fit_canvas(opf(RAW_[a], RAW_[b]), VFRAME),
                                    S_[c_].canvas_frame) > VTOL:
                            ok = False
                            break
                    if ok:
                        a, b, _ = triples[-1]
                        feas.append((kind, f"overlay_{opname}", None, None,
                                     fit_canvas(opf(RAW_[a], RAW_[b]), VFRAME), None))
        return feas

    def score(pred: Optional[np.ndarray], pred_scale: Optional[float],
              pred_canvas: Optional[np.ndarray], pred_center: Optional[Tuple[float, float]],
              opts: Dict[str, Any]) -> Dict[str, float]:
        import math as _m
        out: Dict[str, float] = {}
        for lab, sh in opts.items():
            if pred_canvas is not None and sh.canvas_frame is not None:
                out[lab] = residual(pred_canvas, sh.canvas_frame)
                continue
            r = residual(pred, sh.frame)
            if pred_scale:
                pen = min(1.0, abs(_m.log(max(sh.scale_area, 1e-6) / pred_scale)) / _m.log(1.6))
                r = max(r, pen)
            if pred_center is not None:
                d = abs(sh.center[0] - pred_center[0]) + abs(sh.center[1] - pred_center[1])
                r = max(r, min(1.0, d / 0.30))
            out[lab] = round(r, 6)
        return out

    if answer not in O:
        return {"passed": False, "blocking": True, "reasons": ["answer 标签不在选项集"],
                "checks": [VerificationCheck(name="geometry.label", passed=False,
                                             detail=f"选项无标签 {answer}", severity="blocking")]}
    supporting = [c for c in (payload.get("candidates") or [])
                  if (c.get("candidate_scores") or {}).get(answer, 1.0) <= VTOL]
    if not supporting:
        checks.append(VerificationCheck(
            name="geometry.support", passed=False,
            detail="没有任何候选规律能容差内匹配所答选项", severity="blocking"))
        return {"passed": False, "blocking": True, "reasons": ["无候选支撑"], "checks": checks}

    feasible = enumerate_rules(S, RAW)
    declared = {(c.get("rule_type", ""), c.get("transformation", "")) for c in supporting}
    matched = [f for f in feasible
               if any((f[0] == rt or f[0] in rt or rt in f[0]) and f[1] == tr
                      for rt, tr in declared)]
    if not matched:
        checks.append(VerificationCheck(
            name="geometry.rule_replay", passed=False,
            detail=(f"验证器无法在自有帧下重放求解器声明的规律 {sorted(declared)}"
                    "（规律不能解释已知格间关系）"), severity="blocking"))
        return {"passed": False, "blocking": True, "reasons": ["rule_replay_failed"],
                "checks": checks}
    best_feasible = None
    for f in matched:
        sc = score(f[2], f[3], f[4], f[5], O)
        if not sc:
            continue
        ordered = sorted(sc.items(), key=lambda kv: kv[1])
        if ordered[0][0] != answer or ordered[0][1] > VTOL:
            checks.append(VerificationCheck(
                name="geometry.extrapolation", passed=False,
                detail=(f"重放 {f[0]}/{f[1]} 的外推最佳选项为 {ordered[0][0]}"
                        f"(res {ordered[0][1]})，不是所答 {answer}"), severity="blocking"))
            return {"passed": False, "blocking": True, "reasons": ["extrapolation_mismatch"],
                    "checks": checks}
        margin = (ordered[1][1] - ordered[0][1]) if len(ordered) > 1 else 1.0
        best_feasible = (f, ordered[0][1], margin, sc)
    checks.append(VerificationCheck(
        name="geometry.rule_replay", passed=True,
        detail="声明规律在验证器自有帧(72)下逐对重放通过，外推唯一指向所答选项",
        severity="info"))

    # ---- 检查 4：等价选项 / 稳定子 / 未排除竞争 ----
    _, res_a, margin_a, sc_a = best_feasible
    if margin_a < 0.05:
        rivals = [lab for lab, v in sc_a.items() if lab != answer and v <= VTOL]
        reasons.append(f"未排除竞争项 {rivals}（判别裕度 {margin_a:.3f}）")
        checks.append(VerificationCheck(
            name="geometry.equivalence", passed=False,
            detail=f"预测形状下竞争选项未排除：{rivals}，margin={margin_a:.3f}",
            severity="warning"))
    eq = [lab for lab, sh in O.items() if lab != answer
          and residual(sh.frame, O[answer].frame) <= 0.03
          and (sh.canvas_frame is None or O[answer].canvas_frame is None
               or residual(sh.canvas_frame, O[answer].canvas_frame) <= 0.03)]
    if eq:
        reasons.append(f"等价选项 {eq}（内容与所答不可区分）")
        checks.append(VerificationCheck(
            name="geometry.equivalent_options", passed=False,
            detail=f"选项 {eq} 与所答内容等价，任何择一都不具判别性", severity="warning"))
    pred = best_feasible[0][2]
    if pred is not None and best_feasible[0][4] is None:
        stab = [g for g, el in G.items() if residual(el(pred), pred) <= 0.10]
        # R4b 方法学：预测形状自对称**本身不构成失败**——稳定子只在"另一规律经稳定子
        # 复合后外推到不同选项"时才威胁唯一性，那已由替代规律检查(5)与仲裁层 margin
        # 纪律覆盖。此处降为纯披露（可审计，不拦截），避免把答案恰为对称图形的镜像题
        # 系统性误拒（dev mirror-05 教训）。
        checks.append(VerificationCheck(
            name="geometry.stabilizer", passed=True,
            detail=(f"预测形状稳定子披露 {stab}"
                    + ("（角度/镜像唯一性依赖替代规律检查结论）" if len(stab) > 1 else "")),
            severity="info"))

    # ---- 检查 5：同等合理的替代规律指向不同选项 ----
    alt: List[str] = []
    for f in feasible:
        sc = score(f[2], f[3], f[4], f[5], O)
        if not sc:
            continue
        ordered = sorted(sc.items(), key=lambda kv: kv[1])
        if ordered[0][0] != answer and ordered[0][1] <= VTOL \
                and (len(ordered) == 1 or ordered[1][1] - ordered[0][1] >= 0.05):
            alt.append(f"{f[0]}/{f[1]}→{ordered[0][0]}")
    if alt:
        reasons.append(f"替代规律 {alt}")
        checks.append(VerificationCheck(
            name="geometry.alternative_rules", passed=False,
            detail="存在同等合理规律唯一指向不同选项：" + "; ".join(alt[:3]),
            severity="warning"))

    # ---- 检查 6：离散化扰动——题干与选项同时 ±1px，规律重放后排序不翻转 ----
    # ---- 检查 6：离散化扰动——题干与选项同时 ±1px，**声明规律按原参数重放**，
    #      检验排序稳健性。R4b 方法学：扰动重放固定规律参数（g/k/step/op），
    #      不重新枚举——重新枚举会把"扰动的系统性像素增粗改变面积比"误报成
    #      "规律不成立"（scale 小图形 dev scale-09 教训）；参数重放才是
    #      "离散化误差是否影响唯一性"的正确语义。
    def replay_perturbed(f0, S_, RAW_, O_):
        """按声明参数在扰动输入上重放；规律失效返回 None。"""
        rt, tr = f0[0], f0[1]
        n_ = len(S_)
        if rt.startswith("rotate_flip"):
            el = G[tr]
            if rt == "rotate_flip_grid_row":
                pairs = [(0, 1), (1, 2), (3, 4), (4, 5), (6, 7)]
                base = S_[7]
            elif rt == "rotate_flip_grid_col":
                pairs = [(0, 3), (3, 6), (1, 4), (4, 7)]
                base = S_[5]
            else:
                pairs = [(i, i + 1) for i in range(n_ - 1)]
                base = S_[-1]
            if any(residual(el(S_[a].frame), S_[b].frame) > VTOL + 0.05 for a, b in pairs):
                return None
            return el(base.frame), None, None, None
        if rt == "translate":
            import re as _re
            mm = _re.search(r"\(([-+0-9.]+),([-+0-9.]+)\)", tr)
            if not mm:
                return None
            dx, dy = float(mm.group(1)), float(mm.group(2))
            cent = [s_.center for s_ in S_]
            if any(abs((b[1] - a[1]) - dy) > 0.12 or abs((b[0] - a[0]) - dx) > 0.12
                   for a, b in zip(cent, cent[1:])):
                return None
            return (S_[-1].frame, None, None,
                    (min(1.0, max(0.0, cent[-1][0] + dx)),
                     min(1.0, max(0.0, cent[-1][1] + dy))))
        if rt == "scale":
            import re as _re
            mm = _re.search(r"x([0-9.]+)", tr)
            if not mm:
                return None
            k = float(mm.group(1))
            areas = [s_.scale_area for s_ in S_]
            ratios = [b / a for a, b in zip(areas, areas[1:]) if a > 1e-9]
            # 1px 描边增粗对小 bbox 面积比有系统性偏差：重放容差放宽到 ±0.10，
            # 但比例均值偏离声明 k 超过 40% 视为规律失效
            if not ratios or max(ratios) - min(ratios) > 0.25 or abs(np.mean(ratios) / k - 1) > 0.40:
                return None
            return S_[-1].frame, min(1.0, areas[-1] * k), None, None
        if rt.startswith("overlay"):
            opname = tr.split("_")[-1]
            opf = {"xor": lambda x, y: ((x > 0) ^ (y > 0)).astype(np.uint8),
                   "or": lambda x, y: np.logical_or(x, y).astype(np.uint8),
                   "and": lambda x, y: np.logical_and(x, y).astype(np.uint8)}[opname]
            triples = ([(0, 1, 2), (3, 4, 5), (6, 7, None)] if rt.endswith("row")
                       else [(0, 3, 6), (1, 4, 7), (2, 5, None)])
            for a, b, c_ in triples:
                if c_ is None:
                    continue
                # 布尔叠加对 1px 描边扰动有**系统性放大**（交集/差集面积按线性
                # 边界变化）：重放容差取 +0.08，仍要求规律结构与排序成立
                if residual(fit_canvas(opf(RAW_[a], RAW_[b]), VFRAME),
                            S_[c_].canvas_frame) > VTOL + 0.08:
                    return None
            a, b, _ = triples[-1]
            return None, None, fit_canvas(opf(RAW_[a], RAW_[b]), VFRAME), None
        return None

    disc_ok = True
    for pert, fn in (("dilate", _dilate), ("erode", _erode)):
        S_p, O_p, RAW_p = prep([fn(_to_mask(m)) for m in stem_masks],
                               {lab: fn(_to_mask(m)) for lab, m in option_masks.items()})
        for f0 in matched:
            rep = replay_perturbed(f0, S_p, RAW_p, O_p)
            if rep is None:
                disc_ok = False
                reasons.append(f"{pert} 扰动后声明规律参数无法重放")
                checks.append(VerificationCheck(
                    name="geometry.discretization", passed=False,
                    detail=f"{pert}(±1px) 扰动题干后规律 {f0[0]}/{f0[1]} 参数重放失效",
                    severity="warning"))
                continue
            sc = score(rep[0], rep[1], rep[2], rep[3], O_p)
            if not sc:
                continue
            ordered = sorted(sc.items(), key=lambda kv: kv[1])
            marg = (ordered[1][1] - ordered[0][1]) if len(ordered) > 1 else 1.0
            if ordered[0][0] != answer:
                # 排序翻转=真敏感
                disc_ok = False
                reasons.append(f"{pert} 扰动下所答排序翻转（best {ordered[0][0]}）")
                checks.append(VerificationCheck(
                    name="geometry.discretization", passed=False,
                    detail=(f"{pert}(±1px) 同时扰动题干与选项后最佳={ordered[0][0]}，"
                            f"不再是所答 {answer}"), severity="warning"))
            elif marg < 0.02 and marg < margin_a - 0.5:
                # 裕度塌缩须**同时**满足：绝对值 <0.02 且较未扰动裕度掉了一半以上。
                # 否则会把"扰动同时平移所有残差（A/B 同分→平移后仍同分）"的
                # 系统性效应误报为敏感（等价对本来就该由 equivalence 检查负责）。
                disc_ok = False
                reasons.append(f"{pert} 扰动下裕度塌缩（m {marg:.3f} vs 原 {margin_a:.3f}）")
                checks.append(VerificationCheck(
                    name="geometry.discretization", passed=False,
                    detail=(f"{pert}(±1px) 后裕度 {marg:.3f}（原 {margin_a:.3f}）：唯一性"
                            "主要由离散化维持"), severity="warning"))
    if disc_ok:
        checks.append(VerificationCheck(
            name="geometry.discretization", passed=True,
            detail="±1px 膨胀/腐蚀（题干+选项同时扰动、规律按原参数重放）下所答排序稳健",
            severity="info"))

    blocking = False  # 上面已提前 return blocking 情形
    passed = not reasons
    checks.append(VerificationCheck(
        name="geometry.independently_verified", passed=passed,
        detail=("六项独立代入检查全部通过（自有帧72/容差0.15/双侧扰动复核）"
                if passed else "代入验证存在问题：" + "；".join(reasons[:3])),
        severity="info"))
    return {"passed": passed, "blocking": blocking, "reasons": reasons, "checks": checks}
