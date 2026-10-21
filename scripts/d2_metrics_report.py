# -*- coding: utf-8 -*-
"""Stage D：统一 8-metric 报告 × 三臂消融（rules_only / rules+templates / full_pipeline）。

指标集合为**本轮显式声明**（原工单八指标清单未能从记忆库恢复，声明供人工裁定）：
  M1 solved_rate            给出答案的题率（status=solved / 总数）
  M2 accuracy_all           全题准确率（弃权计错——工单"正确弃权≠答对"纪律）
  M3 precision_answered     已答题精度（答对/已答）
  M4 false_answer_rate      误答率（已答中答错，含 verify 放行后仍错）
  M5 abstain_rate           弃权率（status!=solved）
  M6 correct_abstain_rate   弃权题中金标为"条件不足"类的占比（题类由 tags 判定）
  M7 verify_coverage        已答题中带独立复核动作(accept/reject)的占比
  M8 latency_p95_ms         p95 延迟
臂定义（本轮零 API 可跑两臂；full_pipeline 需真实 LLM，留待 Stage E 预算内执行）：
  rules_only        ASSESSMENT_R2C_DISABLE=word_problem（纯表达式+ solver 核）
  rules+templates   默认开关的 oracle_solver（确定性模板 = 离线规划等价臂）
  full_pipeline     LLM 抽取/规划 + 本地执行复核（占位，pending-budget）
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
CORPORA = {
    "xingce_dev": "xingce_dev",
    "dev_variants": "xingce_dev_variants",
}
ARMS = {
    "rules_only": {"ASSESSMENT_R2C_DISABLE": "word_problem"},
    "rules+templates": {},
}


def run_arm(arm: str, env_extra: Dict[str, str], split: str, run_name: str) -> Path:
    import time
    run_name = f"{run_name}_{time.strftime('%H%M%S')}"
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env["PYTHONIOENCODING"] = "utf-8"
    env["ASSESSMENT_R4G_LAYOUT"] = "off"
    env.update(env_extra)
    subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "assessment_agent", "eval",
         "--split", split, "--eval-mode", "oracle_solver",
         "--run-name", run_name, "--no-overwrite"],
        cwd=ROOT, env=env, check=True, capture_output=True,
    )
    # run_name 目录归一化（runner 以 run-name 建目录）
    base = ROOT / "results" / split
    cand = [p for p in base.iterdir() if (p / "predictions.jsonl").is_file()]
    cand.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cand[0] / "predictions.jsonl"


def metrics(pred_path: Path) -> Dict[str, Any]:
    rows = [json.loads(l) for l in pred_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    n = len(rows)
    solved = [r for r in rows if r.get("status") == "solved"]
    abstained = [r for r in rows if r.get("status") != "solved"]
    correct_solved = [r for r in solved if r.get("correct")]
    ins_items = [r for r in rows if "insufficient_conditions" in (r.get("tags") or [])]
    # 弃权题中"应弃权"（条件不足类且确实没答）
    correct_abstain = [r for r in abstained if "insufficient_conditions" in (r.get("tags") or [])]
    verified = [r for r in solved if r.get("verification_action") in ("accept", "reject")]
    lat = sorted(float(r.get("latency_ms") or 0.0) for r in rows)
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] if lat else 0.0
    return {
        "n": n,
        "M1_solved_rate": round(len(solved) / n, 4) if n else None,
        "M2_accuracy_all": round(len(correct_solved) / n, 4) if n else None,
        "M3_precision_answered": round(len(correct_solved) / len(solved), 4) if solved else None,
        "M4_false_answer_rate": round((len(solved) - len(correct_solved)) / len(solved), 4) if solved else None,
        "M5_abstain_rate": round(len(abstained) / n, 4) if n else None,
        "M6_correct_abstain_rate": (round(len(correct_abstain) / len(ins_items), 4)
                                    if ins_items else "n/a(无条件不足类)"),
        "M7_verify_coverage": round(len(verified) / len(solved), 4) if solved else None,
        "M8_latency_p95_ms": round(p95, 1),
        "detail": {"abstain_noninsufficient": [r["case_id"] for r in abstained
                                               if "insufficient_conditions" not in (r.get("tags") or [])]},
    }


def main() -> None:
    report: Dict[str, Dict[str, Any]] = {}
    for corpus, split in CORPORA.items():
        report[corpus] = {}
        for arm, env_extra in ARMS.items():
            run_name = f"r9d_{corpus}_{arm.replace('+', '_')}"
            path = run_arm(arm, env_extra, split, run_name)
            report[corpus][arm] = metrics(path)
            report[corpus][arm]["pred_dir"] = str(path.relative_to(ROOT))
        report[corpus]["full_pipeline"] = "pending-budget(Stage E 真实 LLM 臂，预算需人工确认)"

    out = ROOT / "evidence" / "R9D_METRICS_ABLATION.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# R9-D 统一 8-metric × 消融（oracle_solver 离线两臂；full_pipeline 待 Stage E 预算）", ""]
    for corpus, arms in report.items():
        lines.append(f"## {corpus}")
        lines.append("| 指标 | rules_only | rules+templates |")
        keys = ["M1_solved_rate", "M2_accuracy_all", "M3_precision_answered", "M4_false_answer_rate",
                "M5_abstain_rate", "M6_correct_abstain_rate", "M7_verify_coverage", "M8_latency_p95_ms"]
        for k in keys:
            a = arms["rules_only"][k]
            b = arms["rules+templates"][k]
            lines.append(f"| {k} | {a} | {b} |")
        lines.append("")
    (ROOT / "evidence" / "R9D_METRICS_ABLATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:1200])
    print("written:", out)


if __name__ == "__main__":
    main()
