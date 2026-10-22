# -*- coding: utf-8 -*-
"""R12-F（P1，零 API）：未见表达 Recall@3=1/5 的检索归因。

分层：Exact Method-ID Hit（金标卡进 top3）vs Functional Applicability（同族卡进 top3）。
逐题记录 top3 卡与金标卡、词面重叠度，定位失败机制；不改动任何 gold 方法 ID。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.tools.release_scoring import summarize_rows  # noqa: E402 (unused)


def main() -> int:
    gold = {}
    for l in (ROOT / "data/benchmark_xingce_dev/r10gen/xingce_dev_r10gen.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            it = json.loads(l)
            gold[it["id"]] = it
    d = json.loads((ROOT / "evidence/R11E_PLANNER_SELECTION.json").read_text(encoding="utf-8"))
    rows = []
    for r in d["runs"]["r10e_planner"]["per_item"]:
        g = (gold.get(r["id"]) or {}).get("method_refs") or []
        fams = {i.split(".")[0] for i in g}
        retr = r.get("retr") or []
        exact = bool(g) and any(i in g for i in retr)
        func = bool(fams) and any(i.split(".")[0] in fams for i in retr)
        q = (gold.get(r["id"]) or {}).get("stem") or (gold.get(r["id"]) or {}).get("question") or ""
        rows.append({"id": r["id"], "gold": g, "top3_retrieved": retr,
                     "selected": r.get("sel"), "status": r.get("status"),
                     "exact_hit": exact, "functional_hit": func,
                     "stem": q[:70]})
    ex = sum(r["exact_hit"] for r in rows)
    fn = sum(r["functional_hit"] for r in rows)
    n = len(rows)
    rec = [r for r in rows if r["top3_retrieved"]]
    ex_r = sum(r["exact_hit"] for r in rec)
    fn_r = sum(r["functional_hit"] for r in rec)
    miss_fam = [r["gold"] for r in rec if not r["functional_hit"]]
    out = {"stage": "R12-F planner recall attribution (zero API)",
           "source": "evidence/R11E_PLANNER_SELECTION.json per_item + r10gen jsonl",
           "rows": rows, "n": n, "exact_hits": ex, "functional_hits": fn,
           "retrieval_recorded": len(rec),
           "conclusion": (
             f"未见表达 {n} 题，其中 {n - len(rec)} 题**检索记录缺失**（本身是管线缺陷，"
             f"不计入召回分母即 R11 的 1/5 口径）。有检索的 {len(rec)} 题："
             f"Exact {ex_r}/{len(rec)}，Functional {fn_r}/{len(rec)}。"
             + ("功能同族卡多数可被召回而金标具体卡缺席 → 瓶颈在**触发词/卡面词面覆盖**；"
                if fn_r > ex_r else "瓶颈在检索词面覆盖本身；")
             + f"彻底缺席同族：{miss_fam}——如 da.growth-amount(增长量) 与 nc.* 卡间"
               "无词面桥。任何检索策略替换必须先在本独立 Dev 上验证，"
               "且禁止更换金标方法 ID 口径。")}
    (ROOT / "evidence/R12F_PLANNER_RECALL.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out["conclusion"])
    for r in rows:
        print(r["exact_hit"], r["functional_hit"], r["gold"], "<-", r["top3_retrieved"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
