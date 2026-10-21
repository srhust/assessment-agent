# -*- coding: utf-8 -*-
"""R10-A7：用当前新 Verifier 对旧 Planner 单跑存盘候选做零 API 重放。

不覆盖 R9 planner 单跑的任何原件（predictions/metrics/llm_evidence 原样保留）。
存盘 solver_meta 仅 {expression,value,exact,method}（runner 截断）：
word/expr 路径的核验可忠实重放（本重放的靶子——stem_direct_parse 是否翻转
false_accept——只依赖这些字段）；planner.dependency_graph 行会如实标注降级。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from assessment_agent.config import Settings
    from assessment_agent.schemas import Question, QuestionOption, Solution
    from assessment_agent.skills.base import SkillContext
    from assessment_agent.skills.numeric_calculation import NumericCalculationSkill
    from assessment_agent.tools import default_toolbox

    gold = {}
    for f in ("data/benchmark_xingce_dev/dev/xingce_dev.jsonl",
              "data/benchmark_xingce_dev/variants/xingce_dev_variants.jsonl"):
        for l in (ROOT / f).read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                gold[r["id"]] = r
    sk = NumericCalculationSkill()
    ctx = SkillContext(settings=Settings.from_env(), llm=None, toolbox=default_toolbox())

    out: Dict[str, Any] = {"meta_truncation_note": "存盘 solver_meta 仅 4 字段；重放针对新核验通道",
                           "arms": {}}
    for split in ("xingce_dev", "xingce_dev_variants"):
        dirs = [p for p in (ROOT / "results" / split).iterdir()
                if "rules_planner" in p.name and (p / "predictions.jsonl").is_file()]
        if not dirs:
            continue
        dirs.sort(key=lambda p: p.stat().st_mtime)
        d = dirs[-1]
        rows: List[Dict[str, Any]] = []
        flips = keeps = 0
        for l in (d / "predictions.jsonl").read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            r = json.loads(l)
            g = gold.get(r["case_id"])
            if not g:
                continue
            sm = dict(r.get("solver_meta") or {})
            q = Question(id=g["id"], stem=g["stem"], skill=g["skill"],
                         options=[QuestionOption(label=o["label"], text=o.get("text", ""))
                                  for o in g["options"]])
            sol = Solution(status=r.get("status"), skill="numeric_calculation",
                           answer=r.get("predicted_option"),
                           method=sm.get("method") or "sympy_exact", meta=sm)
            ver = sk.verify(q, sol, ctx)
            new_action = getattr(ver, "action", None) if ver else None
            old_action = r.get("verification_action")
            was_fa = old_action == "accept" and r.get("correct") is False
            now_blocked = was_fa and new_action != "accept"
            gold_skill = (g.get("skill") or "")
            if new_action is None and gold_skill not in ("numeric_calculation",):
                cls = "cross_skill_noop(重放仅过 nc 核验器，其他技能核验器不在此重放范围)"
            elif new_action == "unverified" and str(sm.get("method", "")).startswith("word_problem") \
                    and "word_problem" not in sm:
                cls = "truncation_artifact(存盘 meta 无完整计划，重放低估；live 运行含计划可复核)"
            elif was_fa and now_blocked:
                cls = "target_block(新通道翻转的真实 false_accept)"
            elif old_action != new_action:
                cls = "other_change"
            else:
                cls = "faithful_unchanged"
            if was_fa:
                if now_blocked:
                    flips += 1
                else:
                    keeps += 1
            rows.append({"case_id": r["case_id"], "old_action": old_action,
                         "new_action": new_action, "correct": r.get("correct"),
                         "was_false_accept": was_fa, "now_blocked": now_blocked,
                         "classification": cls})
        out["arms"][f"{split}/{d.name}"] = {
            "rows": len(rows),
            "false_accepts_old": sum(1 for x in rows if x["was_false_accept"]),
            "blocked_by_new_verifier": flips,
            "still_accepted_wrong": keeps,
            "action_changes": [{"case": x["case_id"], "old": x["old_action"], "new": x["new_action"],
                                "classification": x["classification"]}
                               for x in rows if x["old_action"] != x["new_action"]],
            "classification_counts": {k: sum(1 for x in rows if x["classification"] == k)
                                      for k in {x["classification"] for x in rows}},
            "detail": rows,
        }
    (ROOT / "evidence/R10A_VERIFIER_REPLAY.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "detail"}
                      for k, v in out["arms"].items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
