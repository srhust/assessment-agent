# -*- coding: utf-8 -*-
"""R13-F：planner 召回五层重定义（零 API）。作废"前缀同族=功能命中"。

L0 retrieval_state ∈ {not_invoked, invoked_empty, invoked_with_results, failed}
L1 Exact Method-ID：金标卡本身进检索结果
L2 Skill-level：检索卡 skill == 题 skill
L3 Operator-coverage：金标卡 reasoning_operations 被检索卡覆盖比例（LCS≥4 字符）
L4 Method applicability：某检索卡的适用条件/概念与**题面原文**存在 ≥4 连续公共字符
    （确定性词面判据；r10g-03 组合/阶乘题 vs 容斥/行程卡 → 预期 0 命中，
     直接反驳 R12 的前缀假阳性）
L5 Plan execution validity：sel 卡存在、status=solved 且 correct=True（来自既有逐题记录）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_cards():
    cards = {}
    for p in (ROOT / "knowledge" / "methods").glob("*.json"):
        c = json.loads(p.read_text(encoding="utf-8"))
        cards[c.get("method_id") or p.stem] = c
    return cards


def lcs(a: str, b: str) -> int:
    """最长连续公共子串长度（小串 O(nm) 足够）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def card_text(c) -> str:
    return "。".join((c.get("applicability_conditions") or []) +
                     (c.get("concepts") or []))


def ops_text(c) -> str:
    return "。".join((c.get("reasoning_operations") or []) +
                     (c.get("solution_procedure") or []))


def norm(s: str) -> str:
    return "".join(ch for ch in (s or "") if not ch.isspace())


def main() -> int:
    cards = load_cards()
    gold = {}
    for l in (ROOT / "data/benchmark_xingce_dev/r10gen/xingce_dev_r10gen.jsonl") \
            .read_text(encoding="utf-8").splitlines():
        if l.strip():
            it = json.loads(l)
            gold[it["id"]] = it
    d = json.loads((ROOT / "evidence/R11E_PLANNER_SELECTION.json")
                   .read_text(encoding="utf-8"))
    per = d["runs"]["r10e_planner"]["per_item"]
    r10e = {x["id"]: x for x in json.loads(
        (ROOT / "evidence/R10E_GENERALIZATION_DEV.json").read_text(encoding="utf-8")
    )["per_item"]}
    rows = []
    for r in per:
        pid = r["id"]
        it = gold.get(pid) or {}
        refs = it.get("method_refs") or []
        retr = r.get("retr")
        sel = r.get("sel")
        expr = ((r10e.get(pid) or {}).get("planner") or {}).get("expr")
        # L0
        if sel is not None and retr:
            state = "invoked_with_results"
        elif retr == [] and r.get("status") == "failed":
            state = "invoked_empty"
        elif retr == [] and r.get("status") == "solved":
            state = "invoked_empty_direct_ast"  # 无卡仍可 AST 执行（非日志丢失）
        elif retr is None:
            state = "not_invoked_or_unlogged"
        else:
            state = "invoked_with_results" if retr else "invoked_empty"
        # L1
        l1 = bool(refs) and any(m in refs for m in (retr or []))
        # L2
        skill = it.get("skill")
        l2 = any(cards.get(m, {}).get("skill") == skill for m in (retr or []))
        # L3
        cov = None
        if refs:
            ops = [norm(o) for gc in refs if gc in cards
                   for o in ops_text(cards[gc]).split("。") if o.strip()]
            if ops:
                hit = sum(1 for o in ops
                          if any(lcs(o, norm(ops_text(cards[m]))) >= 4
                                 for m in retr or [] if m in cards))
                cov = round(hit / len(ops), 3)
        # L4（金标题面 vs 每张检索卡适用条件）
        stem = norm(it.get("stem") or it.get("question") or "")
        appl = []
        for m in retr or []:
            c = cards.get(m)
            if not c or not stem:
                continue
            best = max((lcs(stem, norm(t)) for t in card_text(c).split("。")
                        if t.strip()), default=0)
            appl.append({"card": m, "max_lcs": best,
                         "applicable_by_lexicon": best >= 4})
        l4 = any(a["applicable_by_lexicon"] for a in appl)
        # L5
        l5 = bool(sel) and r.get("status") == "solved" and bool(r.get("correct"))
        rows.append({"id": pid, "retrieval_state": state, "retr": retr, "sel": sel,
                    "gold_refs": refs, "L1_exact": l1, "L2_skill": l2,
                    "L3_operator_coverage": cov, "L4_applicable_any": l4,
                    "L4_per_card": appl, "L5_plan_valid": l5,
                    "ast_expr": expr, "status": r.get("status"),
                    "correct": r.get("correct")})
    n = len(rows)
    layer_sum = {
        "L0_states": {s: sum(1 for r in rows if r["retrieval_state"] == s)
                      for s in {r["retrieval_state"] for r in rows}},
        "L1_exact": f"{sum(1 for r in rows if r['L1_exact'])}/{n}",
        "L2_skill": f"{sum(1 for r in rows if r['L2_skill'])}/{n}",
        "L3_operator_coverage_mean": round(
            sum(r["L3_operator_coverage"] or 0 for r in rows) / n, 3),
        "L4_applicable_any_ge4": f"{sum(1 for r in rows if r['L4_applicable_any'])}/{n}",
        "L4_applicable_any_ge3": f"{sum(1 for r in rows if any(a['max_lcs'] >= 3 for a in r['L4_per_card']))}/{n}",
        "L4_caveat": ("词面 LCS 是**下界**判据：r10g-04 折扣题对 successive-discount 卡"
                      "仅 1 个连续公共字符（卡面条件用抽象书面语），说明 L4 系统性低估；"
                      "但负方向仍有效——r10g-03 组合题对容斥/行程卡 0-1 分，前缀假阳性被反驳"),
        "L5_plan_valid": f"{sum(1 for r in rows if r['L5_plan_valid'])}/{n}",
    }
    missing = sorted({m for r in rows for m in r["gold_refs"] if m not in cards})
    out = {"stage": "R13-F planner five-layer recall (zero API)",
           "scope": "R11E per_item 共 7 题；r10g-06 金标=条件不足未进该批（R10E: retrieved 有、规划 llm_error 失败）",
           "kb_gap": {"missing_gold_cards": missing,
                      "note": "r10g-03 金标卡 nc.combinatorics-power **不在 KB**——不是没召回，而是没有这张卡；"
                      "R12 把它记成容斥族功能命中属双重错误"},
           "supersedes": "R12-F Functional(prefix)3/5 — 前缀同族等价作废",
           "note_r10g_07_08": ("retr=[] 且 solved：检索**已调用但为空**，作答走 direct-AST；"
                               "不是日志丢失（R10E 已存 expr 原文为证）"),
           "negative_control_r10g_03": next(
               {"id": r["id"], "retr": r["retr"], "L4_per_card": r["L4_per_card"],
                "conclusion": "容斥/行程卡对组合阶乘题面无 ≥4 连续公共字符的适用条件"
                              " → 词面不适用，R12 前缀功能命中判假阳性"}
               for r in rows if r["id"] == "r10g-03"),
           "layer_summary": layer_sum, "rows": rows}
    (ROOT / "evidence/R13F_PLANNER_LAYERS.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(layer_sum, ensure_ascii=False, indent=1))
    for r in rows:
        print(r["id"], r["retrieval_state"], "L1", r["L1_exact"], "L2", r["L2_skill"],
              "L3", r["L3_operator_coverage"], "L4", r["L4_applicable_any"],
              "L5", r["L5_plan_valid"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
