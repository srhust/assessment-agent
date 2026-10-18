"""R3c 报告：口径分离的 verbal/epistemic 指标 + 主基准回归 + 通道分离。

与 R3b 报告的差别（P1 修复）：把"遵守规则子集策略"与"客观答题能力"彻底分开：

- objective_answer_accuracy：分母 = **全部有金标准答案的题**（含当前规则子集不可解的题）；
- supported_subset_accuracy：分母 = 预声明可解子集（expected_status=solved）；
- policy_compliance：应作答时作答且答对 + 应弃权时确实未作答（沿用 expected_status 判定，
  对外是否作答以 verification.action/是否产出 final_answer 计，不再与内部 status=failed 混用）；
- answered_coverage：有金标准题中实际给出通过验证答案的比例；
- selective_accuracy：全部被接受且有金标准的回答中的正确比例；
- false_accept：有金标准但接受了错误选项；
- over_answer：undecidable/主观题被输出确定答案（与 false_accept 分列，原因不同）；
- unverified_abstention：solver 产生候选但验证未通过、最终未对外作答；
- router_accuracy：分母 = 全部样本（unknown 路由、拒路由、错误路由均计入分母并列出）。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.config import Settings  # noqa: E402
from assessment_agent.evaluation.runner import EvaluationRunner, load_cases  # noqa: E402
from assessment_agent.pipeline import Pipeline  # noqa: E402
from assessment_agent.schemas import Question, QuestionOption, VerbalReasoningPlan  # noqa: E402
from assessment_agent.skills.base import SkillContext  # noqa: E402
from assessment_agent.skills.verbal_understanding import VerbalUnderstandingSkill  # noqa: E402
from assessment_agent.tools.verbal_tool import spans_valid  # noqa: E402

MAIN_ROWS = [("bench_dev", "oracle_solver"), ("bench_dev", "auto_router"),
             ("bench_holdout", "oracle_solver"), ("screens", "auto_router")]
R3B_BASELINE = {"bench_dev::oracle_solver": (25, 30), "bench_dev::auto_router": (25, 30),
                "bench_holdout::oracle_solver": (5, 14), "screens::auto_router": (9, 9)}


def solve_one(skill, ctx, stem, options):
    q = Question(stem=stem,
                 options=[QuestionOption(label=l, text=t) for l, t in options],
                 question_type="single_choice")
    t0 = time.perf_counter()
    sol = skill.solve(q, ctx)
    ver = skill.verify(q, sol, ctx) if sol.status == "solved" else None
    lat = round((time.perf_counter() - t0) * 1000, 2)
    accepted = bool(ver and ver.action == "accept")
    final = getattr(ver, "final_answer", None) if accepted else None
    spans_total = spans_bad = claims = 0
    if sol.meta and "verbal_plan" in sol.meta:
        plan = VerbalReasoningPlan.model_validate(sol.meta["verbal_plan"])
        spans_bad = len(spans_valid(plan, stem))
        for a in plan.option_analyses:
            spans_total += len([e for e in a.evidence if e.start >= 0])
        claims = len(plan.unsupported_claims)
    return {"stem": stem, "status": sol.status, "solved_candidate": sol.status == "solved",
            "accepted": accepted, "answer": final,
            "unverified_abstention": sol.status == "solved" and not accepted,
            "spans_total": spans_total, "spans_bad": spans_bad, "claims": claims,
            "plan_source": (sol.meta or {}).get("plan_source"), "latency_ms": lat}


def metrics_for(records) -> dict:
    gold = [r for r in records if r["gold"] is not None]
    subset = [r for r in records if r["expected"] == "solved"]
    answered_gold = [r for r in gold if r["accepted"]]
    accepted_all = [r for r in records if r["accepted"]]
    wrong = [r for r in answered_gold if r["answer"] != r["gold"]]
    over = [r for r in accepted_all if r["gold"] is None]
    policy = sum(
        1 for r in records
        if (r["expected"] == "solved" and r["accepted"] and r["answer"] == r["gold"])
        or (r["expected"] in {"failed", "undecidable"} and not r["accepted"])
    )
    ev_tot = sum(r["spans_total"] for r in records)
    ev_bad = sum(r["spans_bad"] for r in records)
    return {
        "n": len(records),
        "objective_answer_accuracy": _r(sum(1 for r in answered_gold if r["answer"] == r["gold"]), len(gold)),
        "supported_subset_accuracy": _r(sum(1 for r in subset if r["accepted"] and r["answer"] == r["gold"]), len(subset)),
        "policy_compliance": _r(policy, len(records)),
        "answered_coverage": _r(len(answered_gold), len(gold)),
        "selective_accuracy": _r(len(answered_gold) - len(wrong), len(answered_gold)),
        "false_accept": len(wrong),
        "over_answer": len(over),
        "unverified_abstention": sum(1 for r in records if r["unverified_abstention"]),
        "internal_failed": sum(1 for r in records if r["status"] == "failed"),
        "unsupported": sum(1 for r in records if r["status"] == "unsupported"),
        "evidence_citation_validity": (1.0 if ev_tot == 0 else _r(ev_tot - ev_bad, ev_tot)),
        "unsupported_claim_rate_of_accepted": _r(sum(1 for r in accepted_all if r["claims"] > 0), len(accepted_all)),
        "mean_latency_ms": round(mean(r["latency_ms"] for r in records), 3) if records else None,
        "p95_latency_ms": (round(sorted(r["latency_ms"] for r in records)[max(0, int(0.95 * len(records)) - 1)], 3)
                           if records else None),
    }


def _r(num, den):
    return {"hit": num, "den": den, "value": round(num / den, 4) if den else None}


SPLIT_SKILL = {"verbal_dev": "verbal_understanding", "verbal_epistemic_dev": "verbal_understanding",
               "language_dev": "language_reasoning", "language_blind": "language_reasoning",
               "verbal_blind2": "verbal_understanding"}


def collect(settings, splits):
    from assessment_agent.skills.language_reasoning import LanguageReasoningSkill
    skills = {"verbal_understanding": VerbalUnderstandingSkill(),
              "language_reasoning": LanguageReasoningSkill()}
    ctx = SkillContext(settings=settings, llm=None)
    out = {}
    for split in splits:
        recs = []
        for c in load_cases(ROOT / "data", split):
            skill = skills[SPLIT_SKILL.get(split, "verbal_understanding")]
            r = solve_one(skill, ctx, c.stem, [(o.label, o.text) for o in c.options])
            r["id"], r["gold"], r["expected"] = c.id, c.answer, c.expected_status
            recs.append(r)
        out[split] = {"metrics": metrics_for(recs), "records": recs}
    return out


def archived_metrics(split_dir: Path) -> dict:
    """盲集首跑归档（不重跑）：predictions.jsonl → 口径分离指标。"""
    recs = []
    for line in (split_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        accepted = r["status"] == "solved" and r.get("predicted") is not None
        recs.append({"id": r["case_id"], "gold": r.get("expected"), "expected": r["expected_status"],
                    "accepted": accepted, "answer": r.get("predicted") if accepted else None,
                    "status": r["status"], "unverified_abstention": False,
                    "spans_total": 0, "spans_bad": 0, "claims": 0, "latency_ms": r.get("latency_ms") or 0.0})
    m = metrics_for(recs)
    m["note"] = "首跑归档（冻结，不重评）"
    return m


def router_check(settings, splits):
    pipeline = Pipeline(settings)
    rows = {}
    for split in splits:
        want_skill = SPLIT_SKILL.get(split, "verbal_understanding")
        cases = load_cases(ROOT / "data", split)
        hits, misses = 0, []
        for c in cases:
            run = pipeline.run(text=c.stem, options=[{"label": o.label, "text": o.text} for o in c.options],
                               save=False)
            routed = run.route.skill
            hits += 1 if routed == want_skill else 0
            if routed != want_skill:
                misses.append([c.id, routed])
        rows[split] = _r(hits, len(cases)) | {"misses": misses}  # 分母=全部样本，unknown 不排除
    return rows


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = ROOT / "results" / f"r3c_report_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env().model_copy(update={"offline": True})

    runner = EvaluationRunner(Pipeline(settings), data_dir=ROOT / "data", results_dir=out_dir / "main")
    main_rows = {}
    for split, mode in MAIN_ROWS:
        s = runner.run(split, eval_mode=mode, save=False)
        cap = s.metrics["capability"]
        main_rows[f"{split}::{mode}"] = {
            "objective_hit": cap["overall_answer_accuracy"]["hit"],
            "objective_den": cap["objective_total"],
            "correct_total": s.metrics["correct"], "total": s.metrics["total"],
            "false_accept": cap["false_accept_total"], "over_answer": cap["over_answer_total"],
            "route_accuracy": s.metrics.get("route_accuracy")}

    verbal_splits = ["verbal_dev", "verbal_epistemic_dev", "language_dev"]
    v = collect(settings, verbal_splits)
    rt = router_check(settings, verbal_splits)
    firstrun = ROOT / "results" / "blind_firstrun_r3c"
    blind_first = {}
    for name in ("language_blind", "verbal_blind2"):
        p = firstrun / name
        if p.exists():
            blind_first[name] = archived_metrics(p)

    desc = settings.describe()
    channels = {
        "offline_rules": "EXECUTED（本报告全部数字）",
        "mock_llm": "EXECUTED（tests/ ScriptedLLM 单元层；verbal LLM 通道绑定行为）",
        "real_llm_text": "NOT EXECUTED（未配置 LLM_API_KEY）" if not desc["api_key_present"] else "CONFIGURED（未在本报告启用）",
        "real_vlm": "NOT EXECUTED（未配置 vision model）" if not desc["vision_model"] else "CONFIGURED（未在本报告启用）",
    }
    payload = {"generated_at": ts, "main_benchmarks": main_rows,
               "verbal": {k: vv["metrics"] for k, vv in v.items()},
               "verbal_records": {k: vv["records"] for k, vv in v.items()},
               "blind_first_run": blind_first,
               "router": rt, "channels": channels}
    (out_dir / "r3c_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# R3c 报告（离线规则通道；真实 LLM/VLM 未执行）", "", f"- 生成：{ts} UTC", "",
             "## 主基准回归（对照 R3b）", ""]
    for k, row in main_rows.items():
        base = R3B_BASELINE.get(k)
        cur = (row["objective_hit"], row["objective_den"]) if "bench" in k else (row["correct_total"], row["total"])
        flag = "==" if base and base == cur else ("R3B " + str(base) if base else "")
        lines.append(f"- {k}: {cur[0]}/{cur[1]} 误放 {row['false_accept']} {flag}")
    lines += ["", "## verbal / epistemic 口径分离指标", ""]
    for split, vv in v.items():
        m = vv["metrics"]
        lines += [f"### {split}（n={m['n']}）", "",
                  f"- objective_answer_accuracy（全部有金标准，含规则不可解题）: {m['objective_answer_accuracy']}",
                  f"- supported_subset_accuracy（预声明可解子集）: {m['supported_subset_accuracy']}",
                  f"- policy_compliance（该答则答对、该弃则弃）: {m['policy_compliance']}",
                  f"- answered_coverage: {m['answered_coverage']}  selective_accuracy: {m['selective_accuracy']}",
                  f"- false_accept（金标准下接受错项）: {m['false_accept']}  over_answer（越权作答）: {m['over_answer']}",
                  f"- unverified_abstention: {m['unverified_abstention']}  internal_failed: {m['internal_failed']}  unsupported: {m['unsupported']}",
                  f"- evidence_citation_validity: {m['evidence_citation_validity']}  unsupported_claim_rate: {m['unsupported_claim_rate_of_accepted']}",
                  f"- latency mean/p95 ms: {m['mean_latency_ms']}/{m['p95_latency_ms']}", ""]
    lines += ["## 盲集首跑（冻结归档，不重评）", ""]
    for name, m in blind_first.items():
        lines.append(f"### {name}（n={m['n']}，{m['note']}）")
        lines.append(f"- objective {m['objective_answer_accuracy']}  subset {m['supported_subset_accuracy']}"
                     f"  policy {m['policy_compliance']}  coverage {m['answered_coverage']}")
        lines.append(f"- false_accept {m['false_accept']}  over_answer {m['over_answer']}"
                     f"  unverified_abst {m['unverified_abstention']}  internal_failed {m['internal_failed']}")
        lines.append("")
    lines += ["## Router Accuracy（分母=全部样本，unknown/误路由均计入）", ""]
    for split, rr in rt.items():
        lines.append(f"- {split}: {rr['hit']}/{rr['den']} = {rr['value']}，misses={rr['misses']}")
    lines += ["", "## 通道分离", ""]
    for k, vv in channels.items():
        lines.append(f"- {k}: {vv}")
    (out_dir / "r3c_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"[r3c-report] {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
