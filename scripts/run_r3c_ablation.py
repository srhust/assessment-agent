"""R3c 消融四臂：R3b 基线 / 仅命题事实性 / 仅语言推理 / 完整 R3c。

臂定义（Settings 开关，不改动任何数据）：
- r0_baseline_r3b   : r3c_epistemic=False, r3c_language=False
- r1_epistemic_only : r3c_epistemic=True,  r3c_language=False
- r2_language_only  : r3c_epistemic=False, r3c_language=True
- r3_full_r3c       : 双开
指标口径与 run_r3c_report 一致（objective/policy/coverage/selective/false_accept/
over_answer/unverified_abstention + router auto），按组细分。
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
from assessment_agent.schemas import Question, QuestionOption, LanguageRelationPlan, VerbalReasoningPlan  # noqa: E402
from assessment_agent.skills.base import SkillContext  # noqa: E402
from assessment_agent.skills.language_reasoning import LanguageReasoningSkill  # noqa: E402
from assessment_agent.skills.verbal_understanding import VerbalUnderstandingSkill  # noqa: E402

SPLITS = {"verbal_dev": "verbal_understanding", "verbal_epistemic_dev": "verbal_understanding",
          "language_dev": "language_reasoning"}
ARMS = [("r0_baseline_r3b", {"r3c_epistemic": False, "r3c_language": False}),
        ("r1_epistemic_only", {"r3c_epistemic": True, "r3c_language": False}),
        ("r2_language_only", {"r3c_epistemic": False, "r3c_language": True}),
        ("r3_full_r3c", {"r3c_epistemic": True, "r3c_language": True})]


def run_case(skill, ctx, c):
    q = Question(stem=c.stem, options=[QuestionOption(label=o.label, text=o.text) for o in c.options],
                 question_type="single_choice")
    t0 = time.perf_counter()
    sol = skill.solve(q, ctx)
    ver = skill.verify(q, sol, ctx) if sol.status == "solved" else None
    accepted = bool(ver and ver.action == "accept")
    return {"id": c.id, "group": getattr(c, "group", None) or (c.tags or ["?"])[0]
            if hasattr(c, "tags") else "?",
            "gold": c.answer, "expected": c.expected_status,
            "accepted": accepted, "answer": getattr(ver, "final_answer", None) if accepted else None,
            "status": sol.status,
            "unverified_abstention": sol.status == "solved" and not accepted,
            "lat": round((time.perf_counter() - t0) * 1000, 2)}


def metrics(recs):
    gold = [r for r in recs if r["gold"] is not None]
    subset = [r for r in recs if r["expected"] == "solved"]
    ans = [r for r in gold if r["accepted"]]
    wrong = [r for r in ans if r["answer"] != r["gold"]]
    over = [r for r in recs if r["accepted"] and r["gold"] is None]
    policy = sum(1 for r in recs if (r["expected"] == "solved" and r["accepted"]
                                     and r["answer"] == r["gold"])
                 or (r["expected"] in {"failed", "undecidable"} and not r["accepted"]))
    return {"n": len(recs),
            "objective": f"{sum(1 for r in ans if r['answer']==r['gold'])}/{len(gold)}",
            "subset": f"{sum(1 for r in subset if r['accepted'] and r['answer']==r['gold'])}/{len(subset)}",
            "policy": f"{policy}/{len(recs)}",
            "coverage": f"{len(ans)}/{len(gold)}",
            "selective": f"{len(ans)-len(wrong)}/{len(ans)}" if ans else "0/0",
            "false_accept": len(wrong), "over_answer": len(over),
            "unverified_abst": sum(1 for r in recs if r["unverified_abstention"]),
            "unsupp": sum(1 for r in recs if r["status"] == "unsupported"),
            "lat_mean": round(mean(r["lat"] for r in recs), 2) if recs else None}


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = ROOT / "results" / f"r3c_ablation_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    table, detail = {}, {}
    for arm, flags in ARMS:
        base = Settings.from_env(env={"ASSESSMENT_OFFLINE": "1"})
        st = base.model_copy(update=flags)
        ctx = SkillContext(settings=st, llm=None)
        skills = {"verbal_understanding": VerbalUnderstandingSkill(),
                  "language_reasoning": LanguageReasoningSkill()}
        rows = {}
        for split, skill_id in SPLITS.items():
            recs = [run_case(skills[skill_id], ctx, c) for c in load_cases(ROOT / "data", split)]
            rows[split] = metrics(recs)
            detail[f"{arm}/{split}"] = recs
        # router auto（全 split 合并）
        pipeline = Pipeline(st)
        rh, rd = 0, []
        for split, skill_id in SPLITS.items():
            for c in load_cases(ROOT / "data", split):
                run = pipeline.run(text=c.stem,
                                   options=[{"label": o.label, "text": o.text} for o in c.options],
                                   save=False)
                ok = run.route.skill == skill_id
                rh += ok
                if not ok:
                    rd.append([c.id, run.route.skill])
        n_tot = sum(len(load_cases(ROOT / "data", s)) for s in SPLITS)
        rows["_router_auto"] = {"hit": rh, "den": n_tot,
                                 "value": round(rh / n_tot, 4), "misses": rd[:10]}
        table[arm] = rows
    (out_dir / "r3c_ablation.json").write_text(
        json.dumps({"arms": table, "detail": detail}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# R3c 消融四臂（离线规则通道；真实 LLM/VLM：NOT EXECUTED）", ""]
    for arm, _ in ARMS:
        lines.append(f"## {arm}")
        for split, m in table[arm].items():
            if split == "_router_auto":
                lines.append(f"- router(auto 全部样本): {m['hit']}/{m['den']} = {m['value']} misses={m['misses']}")
            else:
                lines.append(f"- {split}: {json.dumps(m, ensure_ascii=False)}")
        lines.append("")
    (out_dir / "r3c_ablation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"[r3c-ablation] {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
