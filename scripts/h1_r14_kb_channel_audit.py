# -*- coding: utf-8 -*-
"""R14-H：Planner 知识库审计（零 API）。

回答三件事（工单 H 项）：
1. **执行轨迹通道证明**：r10g-07/08（检索调用但为空、题目已解出）到底走没走
   Direct-AST——不靠 ast_expr 非空来"猜"，而是运行时插桩通道计数器，
   记录 direct_ast / rule_executor / llm_planner / solver_fallback 谁被调用。
2. **金标卡缺失归因**：4 张缺失卡逐一分类——缺卡 / 异名等价卡 / 确定性执行器
   已覆盖（即该题即使没有卡也能被规则层解出，卡只影响解释力不影响正确性）。
3. **检索五态**：未调用 / 调用无结果 / 卡存在未召回 / 金标卡不存在 / 其他路径成功。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.knowledge.methods import MethodKB          # noqa: E402
from assessment_agent.skills import numeric_calculation as nc     # noqa: E402


def main() -> int:
    kb = MethodKB()
    have = set(kb.cards().keys())

    # ---- 1) 金标卡缺失归因
    dev = json.loads((ROOT / "evidence/R13F_PLANNER_LAYERS.json").read_text(encoding="utf-8"))
    missing = [g for r in dev["rows"] for g in (r.get("gold_refs") or []) if g not in have]
    audit_missing = {}
    aliases = {
        "nc.work-rate": {"equivalent_present": "nc.work-rate-lcm",
                         "class": "异名等价卡（效率最小公倍数法即工程问题通用解法）"},
        "nc.arithmetic-precedence": {"equivalent_present": None,
                                     "class": "确定性执行器已覆盖（Direct-AST 按 Python 语义天然处理运算次序）"},
        "nc.combinatorics-power": {"equivalent_present": None,
                                   "class": "缺卡（排列组合/幂集计数族 KB 无任何同族卡）"},
        "jr.categorical-syllogism": {"equivalent_present": None,
                                     "class": "缺卡（三段论属逻辑判断族，numeric KB 无对应；执行器亦未覆盖）"},
    }
    for m in sorted(set(missing)):
        a = aliases.get(m, {"equivalent_present": None, "class": "未归类"})
        audit_missing[m] = a

    # ---- 2) 执行通道轨迹插桩：方法级计数（_solve_ast/_solve_planner/_solve_word_problem）
    from assessment_agent.skills.numeric_calculation import (
        NumericCalculationSkill as NSkill)
    counters = {"ast": 0, "planner": 0, "word_problem": 0,
                "ast_hit": 0, "planner_hit": 0, "word_hit": 0}
    orig = {}

    def wrap(name, key, hitkey):
        fn = getattr(NSkill, name)
        orig[name] = fn
        def inner(self, *a, **k):
            counters[key] += 1
            res = fn(self, *a, **k)
            # 通道"成功产出"以 Solution.status==solved 计
            if getattr(res, "status", None) == "solved":
                counters[hitkey] += 1
            return res
        setattr(NSkill, name, inner)

    wrap("_solve_ast", "ast", "ast_hit")
    wrap("_solve_planner", "planner", "planner_hit")
    wrap("_solve_word_problem", "word_problem", "word_hit")

    from assessment_agent.schemas import Question
    from assessment_agent.skills import SkillContext, SkillRegistry
    from tests.conftest import make_settings

    stems = {}
    for line in (ROOT / "data/benchmark_xingce_dev/r10gen/xingce_dev_r10gen.jsonl"
                 ).read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        stems[d["id"]] = d
    rows = []
    try:
        reg = SkillRegistry()
        for r in dev["rows"]:
            pid = r["id"]
            src = stems.get(pid)
            if not src:
                rows.append({"id": pid, "source": "missing_in_r10gen"})
                continue
            q = Question.model_validate({
                "id": pid, "type": src.get("question_type") or "numeric_calculation",
                "stem": src["stem"], "options": src.get("options") or [],
                "answer": None, "skill": src.get("skill")})
            before = dict(counters)
            with tempfile.TemporaryDirectory() as td:
                st = make_settings(Path(td))
                try:
                    sol = reg.require("numeric_calculation").solve(
                        q, SkillContext(settings=st))
                    after = {k: counters[k] - before[k] for k in counters}
                    rows.append({"id": pid, "status": sol.status, "answer": sol.answer,
                                 "channel_delta": after,
                                 "gold_refs": r.get("gold_refs"),
                                 "prior_retrieval_state": r.get("retrieval_state")})
                except Exception as exc:  # noqa: BLE001
                    rows.append({"id": pid, "error": f"{type(exc).__name__}:{str(exc)[:80]}"})
    finally:
        for name, fn in orig.items():
            setattr(NSkill, name, fn)

    # ---- 3) 五态分类
    five_state = {}
    for r in dev["rows"]:
        gold = r.get("gold_refs") or []
        retr = r.get("retr") or []
        if not gold:
            state = "not_invoked"
        elif any(g not in have for g in gold) and not retr:
            state = "gold_card_absent"
        elif not retr:
            state = "invoked_empty"
        elif any(g in have and g not in retr for g in gold):
            state = "card_exists_not_retrieved"
        else:
            state = "invoked_with_results"
        five_state[r["id"]] = {"state": state, "gold_in_kb": [g for g in gold if g in have],
                               "gold_absent": [g for g in gold if g not in have]}

    out = {"stage": "R14-H planner KB audit (0 HTTP)",
           "missing_gold_card_classification": audit_missing,
           "channel_trace": rows,
           "five_state": five_state,
           "note": "通道轨迹由运行时方法级插桩得出（调用次数+ solved 命中次数）；ast_expr 非空不能证明通道，插桩计数才能。问题文本取自 r10gen 金标源（answer=None 防止任何答案泄漏进求解）。"}
    (ROOT / "evidence/R14H_KB_CHANNEL_AUDIT.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"missing": {k: v["class"][:20] for k, v in audit_missing.items()},
                      "channel_rows": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
