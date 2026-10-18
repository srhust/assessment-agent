"""R2c/R3a 消融回归：baseline / +word / +router / +verifier / +DA / r2c_full / full 七臂对照。

目的不是刷总分，而是**逐机制**验证每个修复在 Dev 集上改变了哪类失败：
- baseline（全部开关关闭）≈ R2b 行为：图形误放、应用题失败、关键词撞车路由、资料分析弃权；
- +word：数量关系抽取生效（工具直抽失败时的兜底路径）；
- +router：结构证据优先（图形/表格不再被"规律"关键词劫持），ambiguous 置信封顶；
- +verifier：候选代入独立复核（误放 → 拦截/受控弃权）；
- +DA（R3a）：资料分析 Skill 单独开启——表格/统计图从"能力缺口弃权"变为"可验证作答"；
- r2c_full：R2c 三开关全开但 DA 关闭（隔离 R3a 净增量）；
- full：全部开启。

每臂输出：分题型准确率（客观口径）、answerable coverage、selective accuracy、
false-accept rate（误放 / 已接受客观答案）、verification_outcome 分布。
仅用 Dev 集驱动（screens + bench_dev）；Holdout 不出现在本脚本。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.config import Settings  # noqa: E402
from assessment_agent.evaluation.runner import EvaluationRunner  # noqa: E402
from assessment_agent.pipeline import Pipeline  # noqa: E402

ARMS = {
    # 注意：flags_off_current_code 是"**当前代码**关闭全部 R2c/R3a 开关"的快照——感知修复
    # （目标格标记、格框归一化等）仍生效，与历史 R2b 提交（fb73eec）的 5/13（旧口径）
    # **不是同一实验**，两者分开报告，不可互相换算。
    "flags_off_current_code": (False, False, False, False),
    "word_only": (True, False, False, False),
    "router_only": (False, True, False, False),
    "verifier_only": (False, False, True, False),
    "da_only": (False, False, False, True),          # R3a：资料分析单独开启
    "r2c_full": (True, True, True, False),           # R2c 三开关、DA 关（隔离净增量）
    "full": (True, True, True, True),
}

# 历史提交对照（R2b fb73eec 当时的评测结论，旧 expected_status 口径，原样保留不重跑）
HISTORICAL_REFERENCE = {
    "commit": "fb73eec",
    "note": "R2b 历史提交基线：旧口径 answer_accuracy（分母=solved 标注），与上表 answerability 口径不同源，仅作历史对照行，不参与消融结论",
    "bench_dev": {"oracle_solver": "5/13 (38.5%)", "auto_router": "4/13 (30.8%), route 75%"},
    "bench_holdout": {"oracle_solver": "4/12 (33.3%)", "auto_router": "4/12, route 50%, abstain 25%"},
    "figure_false_accept": "Dev 4 起（本脚本 flags_off_current_code 复现其机制）",
}

SPLITS = ("bench_dev", "screens")
MODES = ("oracle_solver", "auto_router")


def make_settings(word: bool, router: bool, verifier: bool, data: bool = True) -> Settings:
    base = Settings.from_env()
    return base.model_copy(
        update={
            "r2c_word_problem": word,
            "r2c_router_structure": router,
            "r2c_figure_verify": verifier,
            "r3a_data_analysis": data,
        }
    )


def run_arm(arm: str, flags: tuple, out_dir: Path) -> dict:
    settings = make_settings(*flags)
    runner = EvaluationRunner(Pipeline(settings), data_dir=ROOT / "data", results_dir=out_dir / "runs" / arm)
    rows = []
    for split in SPLITS:
        for mode in MODES:
            summary = runner.run(split, eval_mode=mode, save=False)
            m = summary.metrics
            cap = m["capability"]
            objective = cap["objective_total"]
            accepted = [
                r for r in summary.results
                if r.answerability == "has_unique_answer" and r.verification_outcome in {"correct_accept", "false_accept"}
            ]
            false_accept = cap["false_accept_total"]
            per_skill = {}
            for skill, stats in m["per_skill"].items():
                skill_items = [r for r in summary.results if r.skill == skill]
                obj = [r for r in skill_items if r.answerability == "has_unique_answer"]
                per_skill[skill] = {
                    "objective_total": len(obj),
                    "correct": sum(1 for r in obj if r.verification_outcome == "correct_accept"),
                    "overall_answer_accuracy": stats["overall_answer_accuracy"],
                    "selective_accuracy": stats["selective_accuracy"],
                    "answerable_coverage": stats["answerable_coverage"],
                    "false_accept": stats["false_accept_total"],
                    "unverified": sum(1 for r in skill_items if r.verification_outcome == "unverified_answerable"),
                }
            rows.append({
                "arm": arm,
                "split": split,
                "mode": mode,
                "total": m["total"],
                "objective_total": objective,
                "overall_answer_accuracy": cap["overall_answer_accuracy"],
                "supported_answer_accuracy": cap["supported_answer_accuracy"],
                "capability_coverage": cap["capability_coverage"],
                "answered_coverage": cap["overall_answered_coverage"],
                "selective_accuracy": m["selective_accuracy"],
                "route_accuracy": m["route_accuracy"],
                "false_accept_total": false_accept,
                "false_accept_rate": round(false_accept / len(accepted), 4) if accepted else 0.0,
                "false_reject_total": cap["false_reject_total"],
                "unverified_answerable": cap["unverified_answerable_total"],
                "over_answer": cap["over_answer_total"],
                "outcomes": cap["verification_outcomes"],
                "root_causes": cap["root_causes"],
                "per_skill": per_skill,
            })
    return {"arm": arm, "flags": {"word": flags[0], "router": flags[1], "verifier": flags[2],
                                   "data_analysis": flags[3]}, "rows": rows}


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = ROOT / "results" / f"r2c_ablation_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = [run_arm(arm, flags, out_dir) for arm, flags in ARMS.items()]

    payload = {"generated_at": ts, "splits": list(SPLITS), "modes": list(MODES),
               "arms": results, "historical_reference": HISTORICAL_REFERENCE}
    (out_dir / "ablation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# R2c/R3a 消融回归（Dev 集驱动，Holdout 不参与）", "",
             f"- 生成时间：{ts}（UTC）", "- 口径：overall = 分母含未实现但客观有答案样本；false_accept_rate = 误放 / 已接受的客观答案",
             "- `flags_off_current_code` = 当前代码关闭全部 R2c/R3a 开关（感知修复仍在），**不等于**历史 R2b 提交快照",
             "- `r2c_full` 与 `full` 之差 = R3a 资料分析净增量（准确率与能力覆盖）", ""]
    for split in SPLITS:
        for mode in MODES:
            lines += [f"## {split} · {mode}", "",
                      "| 臂 | overall | supported | 能力覆盖 | 作答覆盖 | selective | route | 误放 | 误放率 | 误杀 | unverified | 越权 |",
                      "|---|---|---|---|---|---|---|---|---|---|---|---|"]
            for arm in results:
                row = next(r for r in arm["rows"] if r["split"] == split and r["mode"] == mode)
                def fmt(entry):
                    return "N/A" if not isinstance(entry, dict) or entry.get("value") is None else f"{entry['value']:.1%}({entry['hit']}/{entry['denominator']})"
                lines.append(
                    f"| {arm['arm']} | {fmt(row['overall_answer_accuracy'])} | {fmt(row['supported_answer_accuracy'])} "
                    f"| {fmt(row['capability_coverage'])} | {fmt(row['answered_coverage'])} | {row['selective_accuracy']:.1%} "
                    f"| {row['route_accuracy']:.1%} | {row['false_accept_total']} | {row['false_accept_rate']:.1%} "
                    f"| {row['false_reject_total']} | {row['unverified_answerable']} | {row['over_answer']} |"
                )
            lines.append("")
    lines += [
        "## 历史提交对照（R2b 快照，不参与消融结论）", "",
        f"- 基线提交：{HISTORICAL_REFERENCE['commit']}；图形误放：{HISTORICAL_REFERENCE['figure_false_accept']}",
        f"- bench_dev oracle {HISTORICAL_REFERENCE['bench_dev']['oracle_solver']}；"
        f"auto {HISTORICAL_REFERENCE['bench_dev']['auto_router']}",
        f"- bench_holdout oracle {HISTORICAL_REFERENCE['bench_holdout']['oracle_solver']}；"
        f"auto {HISTORICAL_REFERENCE['bench_holdout']['auto_router']}",
        f"- {HISTORICAL_REFERENCE['note']}", "",
    ]
    (out_dir / "ablation.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:20]))
    print(f"[ablation] 输出目录：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
