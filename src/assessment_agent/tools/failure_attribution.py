# -*- coding: utf-8 -*-
"""R12-D：求解失败归因（纯函数，禁 ft-* case-id 分支）。

把“没解出来”分为四类可核对的原因：
  shape_mismatch        候选形状确实不对（残差序列正确但最优超容差）
  position_info_lost    题干有位移信号而选项帧内位置不可观测/被归一化抹平
  rule_not_applicable   无任何规律候选适用（no_rule / 规则全部不可构造）
  verification_mismatch Solver 候选与独立复推证据不一致
返回 (label, evidence dict)；E 阶段六条件重跑逐题携带。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def classify_failure(*, solved: bool,
                     verdict: Optional[str] = None,
                     stem_deltas: Optional[list] = None,
                     option_frames_known: bool = True,
                     cells_identical: bool = False,
                     verify_action: Optional[str] = None,
                     recheck_note: Optional[str] = None,
                     best_residual: Optional[float] = None,
                     runnerup_residual: Optional[float] = None,
                     tol: float = 0.15) -> Tuple[str, Dict[str, Any]]:
    ev: Dict[str, Any] = {}
    if verify_action == "reject" or (recheck_note and "不一致" in recheck_note):
        ev.update({"verify": verify_action, "recheck": recheck_note})
        return "verification_mismatch", ev
    if not solved:
        if verdict in (None, "no_rule"):
            ev["reason"] = "无规律候选可构造"
            return "rule_not_applicable", ev
        has_motion = bool(stem_deltas) and any(
            (abs(d[0]) + abs(d[1] if len(d) > 1 else 0)) > 0 for d in stem_deltas)
        if has_motion and cells_identical and not option_frames_known:
            ev.update({"stem_deltas": stem_deltas,
                       "note": "题干存在位移信号，选项帧内绝对位置未观测，"
                               "形状归一化后不可判别"})
            return "position_info_lost", ev
        if best_residual is not None:
            ev.update({"best_residual": best_residual,
                       "runnerup_residual": runnerup_residual,
                       "margin": (runnerup_residual - best_residual)
                       if runnerup_residual is not None else None})
        ev["verdict"] = verdict
        return "shape_mismatch", ev
    return "solved", ev
