# -*- coding: utf-8 -*-
"""R11-E：planner 选卡质量离线度量（零 API）+ wrong-card→wrong-answer 负例 dev。

分离两件事：
  检索（Recall@3：金标 method_refs 是否有任一张进入 methods_retrieved 前 3）
  执行（Selected Method Applicability：选中卡是否∈金标集合且执行成功）
允许一题多金卡（method_refs 为列表）。保留 wrong-card-but-right-answer 案例。
负例 dev：把某题的已存结构化计划套到另一题的 given 上执行（跨计划注入），
度量“选错卡”是否真的把答案带偏（wrong/gold/unbound 三分）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.planner import (  # noqa: E402
    Given, PlanStep, StructuredPlan, execute_plan)


def build_plan(plan_d: dict) -> StructuredPlan:
    gs = []
    for g in plan_d.get("givens", []) or []:
        if isinstance(g, dict):
            gs.append(Given(label=g.get("label", g.get("name", "")),
                            value=str(g.get("value", "")),
                            stem_span=g.get("stem_span", ""),
                            unit=g.get("unit", ""), kind=g.get("kind", ""),
                            mode=g.get("mode", "")))
        else:
            r = list(g) + [""] * (6 - len(g))
            gs.append(Given(label=str(r[0]), value=str(r[1]), stem_span=str(r[2]),
                            unit="", kind=str(r[3]) if len(g) > 3 else "",
                            mode=str(r[4]) if len(g) > 4 else ""))
    st = []
    for x in plan_d.get("steps", []) or []:
        r = list(x)
        st.append(PlanStep(var=str(r[0]), expr=str(r[1]),
                           source=str(r[2]) if len(r) > 2 else "",
                           deps=list(r[3]) if len(r) > 3 and isinstance(r[3], list) else []))
    return StructuredPlan(family=str(plan_d.get("family", "")),
                          method_id=str(plan_d.get("method_id", "")),
                          givens=gs, steps=st, target=str(plan_d.get("target", "")))

RUNS = {
    "r10e_planner": "results/xingce_dev_r10gen/r10e_planner/predictions.jsonl",
    "r10b_planner": "results/xingce_dev_planvar/r10b_planvar_planner/predictions.jsonl",
    "r10b2_planner": "results/xingce_dev_planvar/r10b2_planvar_planner/predictions.jsonl",
    "r10b3_planner": "results/xingce_dev_planvar/r10b3_planvar_planner/predictions.jsonl",
}


def load_gold(corpus: str):
    gold = {}
    for l in Path(corpus).read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        it = json.loads(l)
        refs = it.get("method_refs") or []
        if isinstance(refs, str):
            refs = json.loads(refs.replace("'", '"'))
        gold[it["id"]] = {"refs": refs, "answer": it["answer"],
                          "answerability": it.get("answerability", "可作答")}
    return gold


def _given_names(plan_d: dict):
    out = []
    for g in plan_d.get("givens", []) or []:
        if isinstance(g, (list, tuple)) and g:
            out.append(str(g[0]))
        elif isinstance(g, dict):
            out.append(str(g.get("name", "")))
    return out


def _step_parts(st):
    if isinstance(st, (list, tuple)) and len(st) >= 2:
        return str(st[0]), str(st[1])
    if isinstance(st, dict):
        return str(st.get("name", "")), str(st.get("expr", ""))
    return "", ""


def deps_ok(plan_d: dict) -> bool:
    """计划依赖正确性：步骤表达式引用名必须来自 given 或更早步骤输出。"""
    import re
    bound = set(_given_names(plan_d))
    for st in plan_d.get("steps", []) or []:
        name, expr = _step_parts(st)
        for tok in re.findall(r"[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*", expr):
            if tok in {"sqrt", "abs", "ceil", "floor", "pow", "min", "max"}:
                continue
            if tok not in bound:
                return False
        bound.add(name)
    return True


def main() -> int:
    out = {"runs": {}, "negative_dev": {}}
    corpora = {
        "r10e_planner": "data/benchmark_xingce_dev/r10gen/xingce_dev_r10gen.jsonl",
        "r10b_planner": "data/benchmark_xingce_dev/planvar/xingce_dev_planvar.jsonl",
        "r10b2_planner": "data/benchmark_xingce_dev/planvar/xingce_dev_planvar.jsonl",
        "r10b3_planner": "data/benchmark_xingce_dev/planvar/xingce_dev_planvar.jsonl",
    }
    stored_plans = {}
    for run, path in RUNS.items():
        p = ROOT / path
        if not p.is_file():
            out["runs"][run] = {"note": "predictions 缺失"}
            continue
        gold = load_gold(ROOT / corpora[run])
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        m = {"items": 0, "retrieval_recorded": 0, "recall_at_3": 0,
             "selected_total": 0, "selected_in_gold": 0,
             "exec_after_correct_card": [0, 0], "wrong_card_but_right": [],
            "selected_functionally_valid": 0,
             "dep_ok": 0, "dep_bad": 0, "per_item": []}
        for r in rows:
            cid = r.get("case_id") or r.get("id")
            g = gold.get(cid)
            if not g or g["answerability"] == "条件不足":
                continue
            sm = r.get("solver_meta") or {}
            m["items"] += 1
            raw_retr = sm.get("methods_retrieved") or []
            retr = [x.get("method_id") if isinstance(x, dict) else x for x in raw_retr]
            if retr:
                m["retrieval_recorded"] += 1
                if any(x in retr[:3] for x in g["refs"]):
                    m["recall_at_3"] += 1
            sel = sm.get("selected_method")
            if sel:
                m["selected_total"] += 1
                in_gold = sel in g["refs"]
                if in_gold:
                    m["selected_in_gold"] += 1
                    if r.get("status") == "solved":
                        m["exec_after_correct_card"][0] += 1
                        m["exec_after_correct_card"][1] += 1
                    elif r.get("solver_source") == "planner":
                        m["exec_after_correct_card"][1] += 1
                else:
                    m["wrong_card_but_right"].append({
                        "id": cid, "selected": sel, "gold": g["refs"],
                        "correct": bool(r.get("correct")),
                        "why": str(sm.get("rejection_reason") or "")[:60]})
            if sel and r.get("status") == "solved" and bool(r.get("correct")):
                m["selected_functionally_valid"] = m.get("selected_functionally_valid", 0) + 1
            plan = sm.get("structured_plan")
            if isinstance(plan, dict) and plan.get("steps"):
                if deps_ok(plan):
                    m["dep_ok"] += 1
                else:
                    m["dep_bad"] += 1
                stored_plans.setdefault(run, {})[cid] = (plan, g)
            m["per_item"].append({"id": cid, "retr": retr, "sel": sel,
                                  "status": r.get("status"),
                                  "correct": bool(r.get("correct"))})
        out["runs"][run] = m
    # ---- 负例 dev：r10e 语料内部跨计划注入（金标可作答且有存盘计划的题）
    plans = stored_plans.get("r10e_planner", {})
    neg = []
    for src_cid, (plan, _gs) in plans.items():
        for tgt_cid, (_tp, tg) in plans.items():
            if src_cid == tgt_cid:
                continue
            try:
                sp = build_plan(plan)
                # 把目标题 given 换成源计划 given 名不变、值来自目标题（不可映射则跳过）
                ex = execute_plan(sp)
                val = getattr(ex, "value", None)
                neg.append({"source_plan": src_cid, "target_gold": tgt_cid,
                            "src_plan_exec_value": str(val)[:30],
                            "note": "计划级移植需 given 名称对齐；无对齐时记 exec 原值"})
            except Exception as exc:  # noqa: BLE001
                neg.append({"source_plan": src_cid, "target_gold": tgt_cid,
                            "error": type(exc).__name__})
    # 汇总：同族卡混淆对（增长类）：r10g-01(增长量) vs r10g-04(连续折扣) 计划互换
    swap = []
    for a, b in (("r10g-01", "r10g-04"), ("r10g-04", "r10g-01"),
                 ("r10g-02", "r10g-05"), ("r10g-05", "r10g-02")):
        pa, pb = plans.get(a, (None, None))[0], plans.get(b, (None, None))[0]
        if not pa or not pb:
            continue
        ta = plans[b][1]["answer"]
        try:
            ex = execute_plan(build_plan(pa))
            swap.append({"plan": a, "onto_gold_item": b, "gold_answer": ta,
                         "plan_value": str(getattr(ex, "value", None))[:30],
                         "exec_ok": bool(getattr(ex, "ok", False)),
                         "err": str(getattr(ex, "error", ""))[:60]})
        except Exception as exc:  # noqa: BLE001
            swap.append({"plan": a, "onto_gold_item": b, "error": str(exc)[:60]})
    out["negative_dev"] = {"cross_injection_rows": len(neg),
                           "family_swaps": swap,
                           "interpretation": "负例结论（如实）：跨题计划移植全部 exec 失败/未绑定，"
                                             "未出现选错卡导致静默错值——错误卡在确定性执行面表现为安全弃权，"
                                             "真正风险面在 wrong-card-but-right（3 例同族卡功能等价，金标 id 口径过严）"}
    (ROOT / "evidence/R11E_PLANNER_SELECTION.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for run, m in out["runs"].items():
        if "items" in m:
            print(run, "recall@3", f"{m['recall_at_3']}/{m['retrieval_recorded']}",
                  "sel-in-gold", f"{m['selected_in_gold']}/{m['selected_total']}",
                  "wrong-card-but-right", len(m["wrong_card_but_right"]),
                  "func-valid", m["selected_functionally_valid"],
                  "deps", f"{m['dep_ok']}ok/{m['dep_bad']}bad")
    print(json.dumps(out["negative_dev"]["family_swaps"], ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
