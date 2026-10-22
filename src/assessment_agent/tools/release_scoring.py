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
    """返回该行达到的最高层：0 未释放候选错误/无、1 正确候选、3 最终释放。

    rec 字段：status、answer、gold、verify（action 字符串）、final_answer
    （**必须由实验脚本保存 Verifier 真实返回**；缺失时不得回退 answer，
    R13 裁决，行停留在第 1 层并由 summarize_rows 计入 release_evidence_missing）。
    """
    if rec.get("status") != "solved":
        return 0
    correct = rec.get("answer") == rec.get("gold")
    if not correct:
        return 0
    action = rec.get("verify")
    if action not in RELEASE_ACTIONS:
        return 1  # 正确候选但验证未通过（reject/unverified/无验证记录）
    # R13 裁决：释放证据必须来自 Verifier 实际返回并保存的 final_answer；
    # 字段缺失不得回退到 Solver answer（否则记为 release_evidence_missing，停在第 1 层）。
    fa = rec.get("final_answer")
    if fa is None or fa == "":
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
    release_evidence_missing: List[str] = []
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
            if rec.get("final_answer") in (None, ""):
                release_evidence_missing.append(r["id"])
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
        "release_evidence_missing": release_evidence_missing,
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
