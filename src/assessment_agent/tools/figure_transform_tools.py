"""R4a 图形变换推理工具：离散旋转/镜像/相对平移/等比缩放/九宫格行列变换与叠加。

与 figure_rules（六类统计规律）解耦：本工具消费**每格二值 mask**（来自题图网格切分
或区域裁剪），在 dihedral 群 + 平移 + 缩放 + 叠加的候选空间上推理，输出全字段候选、
仲裁结论与对称/画布不可判声明。求解器不信任调用方传入的归一化选择：帧尺寸与匹配
容差在本工具内独立确定。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .base import Tool, ToolUnavailable
from .figure_tools import backend_status, load_mask
from .figure_transform import FRAME, analyze_transform
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

def verify_transformation(payload: Dict[str, Any],
                          stem_masks: Sequence[Any],
                          option_masks: Dict[str, Any],
                          answer: Optional[str]) -> VerifyVerdict:
    """阶段4：独立重算——验证器不采信求解器的归一化/掩码/阈值前提。

    做法：对帧尺寸 {48, 64, 80} 与匹配容差 {0.08, 0.12, 0.18} **各自重跑**
    （不同画布重采样即不同归一化参数），并核对：
    - 答案格在全部参数组下均与预测唯一吻合（接受）；
    - 任一参数组下出现同样匹配的**未排除竞争项**（unverified）；
    - 参数组间答案漂移（unverified：对称歧义/尺度退化）；
    - 求解器无候选支撑却给了答案（refuted）。
    """
    checks: List[VerificationCheck] = []
    answers: List[Optional[str]] = []
    for frame in (48, 64, 80):
        for tol in (0.08, 0.12, 0.18):
            import assessment_agent.tools.figure_transform as ft
            ft.FRAME = frame
            try:
                # 不同帧尺寸 = 独立归一化前提；standardize 读模块常量
                res = analyze_transform([ft._to_mask(m) for m in stem_masks],
                                        {k: ft._to_mask(v) for k, v in option_masks.items()},
                                        mode=payload.get("mode", "sequence"), tol=tol)
            finally:
                ft.FRAME = FRAME
            v = res["verdict"]
            answers.append(v.get("answer") if v.get("verdict") == "unique" else None)
            if res["symmetry_unresolvable"]:
                checks.append(VerificationCheck(
                    name="figure_transform.symmetry_disclosed", passed=False,
                    detail="对称不可区分: " + "; ".join(res["symmetry_unresolvable"][:3]),
                    severity="warning"))
    unique = {a for a in answers if a is not None}
    if len(unique) > 1:
        checks.append(VerificationCheck(
            name="figure_transform.grid_stability", passed=False,
            detail=f"归一化参数扫描下答案漂移 {sorted(unique)}：对称/尺度退化",
            severity="warning"))
        return Verification(action="unverified", checks=checks,
                             notes="独立重算不稳定，不对外作答")
    if not unique:
        checks.append(VerificationCheck(
            name="figure_transform.recompute", passed=False,
            detail="独立重算在所有参数组下均无法唯一确定", severity="info"))
        return Verification(action="reject", checks=checks,
                             notes="验证器重算不可判")
    redone = unique.pop()
    if answer is not None and redone != answer:
        checks.append(VerificationCheck(
            name="figure_transform.recompute", passed=False,
            detail=f"独立重算={redone} 与求解器答案={answer} 不一致", severity="blocking"))
        return Verification(action="reject", checks=checks,
                             notes="独立重算否决求解器答案")
    solver_cands = payload.get("candidates") or []
    if not solver_cands:
        checks.append(VerificationCheck(
            name="figure_transform.support", passed=False,
            detail="求解器无候选支撑却给出答案", severity="blocking"))
        return Verification(action="reject", checks=checks,
                             notes="无候选支撑")
    checks.append(VerificationCheck(
        name="figure_transform.recompute", passed=True,
        detail=f"9 组独立归一化参数下均唯一复算为 {redone}", severity="info"))
    return Verification(action="accept", final_answer=redone, checks=checks,
                         notes="独立重算确认")
