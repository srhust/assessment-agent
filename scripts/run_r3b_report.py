"""R3b 全量报告：主基准回归 + verbal 指标 + 通道分离表（offline / mock / real-LLM / real-VLM）。

- 主基准（bench_dev / bench_holdout / screens）与 R3a 基线数值并列，验证零误放、零图形回归；
- verbal 指标：Overall Answer Accuracy、Verbal Accuracy、Answerable Coverage、Selective Accuracy、
  False Accept Rate、Unsupported Claim Rate、Evidence Citation Validity、Abstention Accuracy、
  Router Accuracy、Latency；
- 真实 LLM/VLM 通道未配置时如实标记 NOT EXECUTED，不冒充已验证能力。
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

R3A_BASELINE = {  # HEAD=702878f 的已存档数字（同口径）
    "bench_dev_oracle": {"overall_hit": 24, "objective": 30, "false_accept": 0},
    "bench_dev_auto": {"overall_hit": 24, "objective": 30, "false_accept": 0},
    "bench_holdout_oracle": {"overall_hit": 5, "objective": 14, "false_accept": 0, "over_answer": 1},
    "screens_auto": {"correct": 9, "total": 9, "false_accept": 0},
}


def _run_main(pipeline_settings, out_dir):
    runner = EvaluationRunner(Pipeline(pipeline_settings), data_dir=ROOT / "data",
                              results_dir=out_dir / "main")
    runs = [("bench_dev", "oracle_solver"), ("bench_dev", "auto_router"),
             ("bench_holdout", "oracle_solver"), ("bench_holdout", "auto_router"),
             ("screens", "auto_router")]
    rows = {}
    for split, mode in runs:
        s = runner.run(split, eval_mode=mode, save=False)
        cap = s.metrics["capability"]
        rows[f"{split}::{mode}"] = {
            "total": s.metrics["total"], "correct": s.metrics["correct"],
            "objective": cap["objective_total"],
            "overall_answer_accuracy": cap["overall_answer_accuracy"],
            "false_accept": cap["false_accept_total"],
            "false_reject": cap["false_reject_total"],
            "over_answer": cap["over_answer_total"],
            "route_accuracy": s.metrics.get("route_accuracy"),
        }
    return rows


def _run_verbal(settings, out_dir):
    skill = VerbalUnderstandingSkill()
    ctx = SkillContext(settings=settings, llm=None)  # 离线规则通道：不挂 LLM
    result = {}
    for split in ("verbal_dev", "verbal_blind"):
        cases = load_cases(ROOT / "data", split)
        recs = []
        for c in cases:
            q = Question(stem=c.stem, options=c.options, question_type="single_choice")
            t0 = time.perf_counter()
            sol = skill.solve(q, ctx)
            ver = skill.verify(q, sol, ctx) if sol.status == "solved" else None
            lat = round((time.perf_counter() - t0) * 1000.0, 2)
            action = ver.action if ver else None
            spans_total = spans_bad = 0
            claims = 0
            if "verbal_plan" in (sol.meta or {}):
                plan = VerbalReasoningPlan.model_validate(sol.meta["verbal_plan"])
                problems = spans_valid(plan, c.stem)
                for a in plan.option_analyses:
                    spans_total += len([e for e in a.evidence if e.start >= 0])
                spans_bad = len(problems)
                claims = len(plan.unsupported_claims)
            accepted = action == "accept"
            recs.append({
                "id": c.id, "expected": c.expected_status, "gold": c.answer,
                "status": sol.status, "answer": sol.answer, "action": action,
                "judged_correct": (
                    (c.expected_status == "solved" and accepted and sol.answer == c.answer)
                    or (c.expected_status == "failed" and sol.status == "failed")
                    or (c.expected_status == "undecidable" and not accepted)
                ),
                "false_accept": bool(accepted and c.answer is not None and sol.answer != c.answer),
                "over_claimed_answer": bool(accepted and c.expected_status == "undecidable"),
                "spans_total": spans_total, "spans_bad": spans_bad, "unsupported_claims": claims,
                "plan_source": (sol.meta or {}).get("plan_source"), "latency_ms": lat,
            })
        n = len(recs)
        solved = [r for r in recs if r["action"] == "accept"]
        solved_with_gold = [r for r in solved if r["gold"] is not None]
        answerable = [r for r in recs if r["expected"] == "solved"]
        abstain = [r for r in recs if r["expected"] in {"failed", "undecidable"}]
        answerable_hit = sum(1 for r in answerable if r["action"] == "accept" and r["answer"] == r["gold"])
        over_claimed = sum(1 for r in solved if r["expected"] == "undecidable")
        wrong_on_gold = sum(1 for r in solved_with_gold if r["answer"] != r["gold"])
        result[split] = {
            "total": n,
            "judged_correct": sum(r["judged_correct"] for r in recs),
            "verbal_accuracy_on_expected_solved": round(answerable_hit / len(answerable), 4) if answerable else None,
            "answered_coverage": round(len(solved) / n, 4),
            "answerable_coverage": round(sum(1 for r in answerable if r["status"] == "solved") / len(answerable), 4) if answerable else None,
            "selective_accuracy": round(
                sum(1 for r in solved_with_gold if r["answer"] == r["gold"]) / len(solved_with_gold), 4) if solved_with_gold else None,
            "false_accept": wrong_on_gold + over_claimed,
            "over_claimed": over_claimed,
            "abstention_accuracy": round(
                sum(1 for r in abstain if r["action"] != "accept") / len(abstain), 4) if abstain else None,
            "evidence_spans_total": sum(r["spans_total"] for r in recs),
            "evidence_spans_invalid": sum(r["spans_bad"] for r in recs),
            "evidence_citation_validity": round(
                1 - sum(r["spans_bad"] for r in recs) / max(1, sum(r["spans_total"] for r in recs)), 4),
            "unsupported_claims": sum(r["unsupported_claims"] for r in recs),
            "unsupported_claim_rate_of_accepted": round(
                sum(1 for r in solved if r["unsupported_claims"] > 0) / len(solved), 4) if solved else None,
            "mean_latency_ms": round(mean(r["latency_ms"] for r in recs), 3),
            "p95_latency_ms": round(sorted(r["latency_ms"] for r in recs)[max(0, int(0.95 * n) - 1)], 3),
            "records": recs,
        }
    return result


def _router_verbal(settings):
    pipeline = Pipeline(settings)
    hits = total = 0
    misses = []
    for split in ("verbal_dev", "verbal_blind"):
        for c in load_cases(ROOT / "data", split):
            run = pipeline.run(text=c.stem,
                                options=[{"label": o.label, "text": o.text} for o in c.options],
                                save=False)
            total += 1
            if run.route.skill == "verbal_understanding":
                hits += 1
            else:
                misses.append((c.id, run.route.skill))
    return {"total": total, "correct": hits, "accuracy": round(hits / total, 4), "misses": misses}


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = ROOT / "results" / f"r3b_report_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env()
    offline = settings.model_copy(update={"offline": True})

    main_rows = _run_main(offline, out_dir)
    verbal_rows = _run_verbal(offline, out_dir)
    router_rows = _router_verbal(offline)

    desc = settings.describe()
    channels = {
        "offline_rules": "EXECUTED（本报告全部数字）",
        "mock_llm_da_bind": "EXECUTED（tests/test_r3b_reliability.py ScriptedLLM 单元层）",
        "real_llm_text": "NOT EXECUTED（未配置 LLM_API_KEY）" if not desc["api_key_present"] else "CONFIGURED（本报告未启用）",
        "real_vlm_screens": "NOT EXECUTED（未配置 vision model）" if not desc["vision_model"] else "CONFIGURED（本报告未启用）",
    }
    payload = {"generated_at": ts, "main_benchmarks": main_rows, "r3a_baseline": R3A_BASELINE,
                "verbal": {k: {kk: vv for kk, vv in v.items() if kk != "records"}
                            for k, v in verbal_rows.items()},
                "verbal_records": {k: v["records"] for k, v in verbal_rows.items()},
                "verbal_router": router_rows, "channels": channels}
    (out_dir / "r3b_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# R3b 报告（离线规则通道；真实 LLM/VLM 未执行）", "", f"- 生成：{ts} UTC", "",
             "## 主基准 vs R3a 基线", "",
             "| 实验 | R3a | R3b | 误放 |", "|---|---|---|---|"]
    key_map = {"bench_dev_oracle": "bench_dev::oracle_solver",
               "bench_dev_auto": "bench_dev::auto_router",
               "bench_holdout_oracle": "bench_holdout::oracle_solver",
               "screens_auto": "screens::auto_router"}
    for key, base in R3A_BASELINE.items():
        row = main_rows.get(key_map[key])
        if not row:
            continue
        if key.startswith("screens"):
            lines.append(f"| {key} | {base['correct']}/{base['total']} | {row['correct']}/{row['total']} | {row['false_accept']} |")
        else:
            lines.append(f"| {key} | {base['overall_hit']}/{base['objective']} "
                         f"| {row['overall_answer_accuracy'].get('hit')}/{row['objective']} | {row['false_accept']} |")
    lines += ["", "## verbal 指标（离线规则通道）", "",
              "| split | 判定正确 | 期望可解集答对率 | answerable cov | answered cov | selective | 误放 | 越权作答 | 弃权正确 | 引用有效率 | 接受答案含无依据claim比例 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for split, v in verbal_rows.items():
        lines.append(f"| {split} | {v['judged_correct']}/{v['total']} | {v['verbal_accuracy_on_expected_solved']} "
                     f"| {v['answerable_coverage']} | {v['answered_coverage']} | {v['selective_accuracy']} | {v['false_accept']} "
                     f"| {v['over_claimed']} | {v['abstention_accuracy']} | {v['evidence_citation_validity']} "
                     f"| {v['unsupported_claim_rate_of_accepted']} |")
    lines += ["", f"- verbal 路由（auto）：{router_rows['correct']}/{router_rows['total']} = {router_rows['accuracy']:.1%}，misses={router_rows['misses']}", "",
              "## 通道分离", ""]
    for k, v in channels.items():
        lines.append(f"- {k}: {v}")
    (out_dir / "r3b_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"[r3b-report] {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
