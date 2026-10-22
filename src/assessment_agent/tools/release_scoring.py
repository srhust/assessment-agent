# -*- coding: utf-8 -*-
"""R12-A：释放口径工具（正确候选 / 验证接受 / 最终释放 三层）。

人工裁决（R12 工单）：`solved + reject/unverified` **不得**计为释放。
最终释放 = verification.action ∈ {accept, repair} 且 final_answer 非空。
本模块为纯函数，实验脚本与回归测试共用，防止再次出现口径漂移。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

RELEASE_ACTIONS = ("accept", "repair")


def row_layer(rec: Dict[str, Any]) -> int:
    """返回该行达到的最高层：0 未释放候选错误/无、1 正确候选、2 接受、3 最终释放。

    rec 字段：status、answer、gold、verify（action 字符串）、final_answer（可缺省，
    缺省视为与 answer 同值——Verifier accept 不重写数字答案，repair 才可能改；
    脚本侧应显式存 final_answer）。
    """
    if rec.get("status") != "solved":
        return 0
    correct = rec.get("answer") == rec.get("gold")
    if not correct:
        return 0
    action = rec.get("verify")
    if action not in RELEASE_ACTIONS:
        return 1  # 正确候选但验证未通过（reject/unverified/无验证记录）
    fa = rec.get("final_answer", rec.get("answer"))
    if not fa:
        return 1
    return 3 if fa == rec.get("gold") else 1


def summarize_rows(rows: Iterable[Dict[str, Any]], cond: str,
                   answerable_key: str = "answerability") -> Dict[str, Any]:
    """rows=per_item 列表（含 conds[cond]），返回三层计数与错误释放/假接受明细。"""
    correct_candidates: List[str] = []
    wrong_candidates: List[str] = []
    accepted: List[str] = []
    released: List[str] = []
    wrong_releases: List[str] = []
    gated_rejects: List[str] = []
    gated_unverified: List[str] = []
    design_abstain_ok: List[str] = []
    design_false_accepts: List[str] = []
    answerable_n = 0
    for r in rows:
        rec = (r.get("conds") or {}).get(cond) or {}
        gold = r.get("gold")
        answerable = r.get(answerable_key) == "solved"
        design = r.get("design_abstain") is True
        if answerable and not design:
            answerable_n += 1
        solved = rec.get("status") == "solved"
        if solved and answerable:
            if rec.get("answer") == gold:
                correct_candidates.append(r["id"])
            else:
                wrong_candidates.append(r["id"])
                if row_layer({**rec, "gold": gold}) >= 3 or (
                        rec.get("verify") in RELEASE_ACTIONS
                        and rec.get("answer") != gold):
                    wrong_releases.append(r["id"])
        if solved and rec.get("verify") in RELEASE_ACTIONS and rec.get("answer") == gold:
            accepted.append(r["id"])
            if row_layer({**rec, "gold": gold}) >= 3:
                released.append(r["id"])
        if solved and rec.get("verify") == "reject":
            gated_rejects.append(r["id"])
        if solved and rec.get("verify") == "unverified":
            gated_unverified.append(r["id"])
        if not answerable or design:
            if solved and rec.get("verify") in RELEASE_ACTIONS:
                design_false_accepts.append(r["id"])
            elif not solved:
                design_abstain_ok.append(r["id"])
    return {
        "correct_candidates": len(correct_candidates),
        "wrong_candidates": len(wrong_candidates),
        "verified_accepted": len(accepted),
        "final_released": len(released),
        "of_effective_answerable": f"{len(released)}/{answerable_n}",
        "effective_answerable_n": answerable_n,
        "wrong_releases": wrong_releases,
        "gated_rejects": gated_rejects,
        "gated_unverified": gated_unverified,
        "design_abstain_ok": design_abstain_ok,
        "design_false_accepts": design_false_accepts,
        "lists": {"correct_candidates": correct_candidates,
                  "released": released},
    }


def audit_requests_sent(snapshot: Dict[str, Any]) -> int:
    """R12：预算与超时以真实 HTTP 审计为准——requests_sent（含每次重试）、
    error_types 计数；禁止读 by_phase 内不存在的 calls/requests 键。"""
    return int(snapshot.get("requests_sent") or 0)


def audit_network_timeouts(snapshot: Dict[str, Any]) -> int:
    n = 0
    for k, v in (snapshot.get("error_types") or {}).items():
        kl = str(k).lower()
        if "timeout" in kl or "connection" in kl or "connect" in kl:
            n += int(v or 0)
    return n
