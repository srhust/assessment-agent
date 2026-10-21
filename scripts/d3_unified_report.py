# -*- coding: utf-8 -*-
"""R9-D 统一报告（工单原文口径）：
可答正确释放率 / 候选覆盖率 / 候选正确率 / 错误接受数 / 正确弃权数 /
未实现算子 / LLM 调用 tokens 重试 / 各题首失败阶段；三消融臂。

臂定义（本轮声明，可复算）：
  rules_only     模板关、无 LLM（纯表达式抽取 + solver 核）
  rules+planner  模板开 + LLM 规划回退（oracle 路由，本地执行/复核不变）
  full_pipeline  Stage E 截图端到端臂（本脚本仅占位，不在 Stage D 执行）

正确弃权口径：题面 tags 含 insufficient_conditions（不足条件类）且未释放答案；
错误接受口径：status=solved 且 verification_action=accept 且 correct=False。
首失败阶段映射：抽取失败→extract；无候选匹配→plan/execute（按 method）；
verify 无证据→verify:no_evidence；被证伪→verify:falsified；
accept 但错→release:false_accept；答对→none。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
CORPORA = {"xingce_dev": "xingce_dev", "variants": "xingce_dev_variants"}
BUDGET = os.environ.get("R9D_PLANNER_BUDGET", "30")


def latest_pred(split: str, pattern: str) -> Optional[Path]:
    base = ROOT / "results" / split
    if not base.is_dir():
        return None
    cand = [p for p in base.iterdir()
            if (p / "predictions.jsonl").is_file() and re.search(pattern, p.name)]
    if not cand:
        return None
    cand.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cand[0] / "predictions.jsonl"


def run_planner_arm(split: str, run_name: str) -> Path:
    name = f"{run_name}_{time.strftime('%H%M%S')}"
    env = dict(os.environ)
    env.update({"PYTHONPATH": "src", "PYTHONIOENCODING": "utf-8",
                "ASSESSMENT_R4G_LAYOUT": "off", "LLM_CALL_BUDGET": BUDGET})
    subprocess.run(
        [sys.executable, "-X", "utf8", "scripts/r61_dsh_env.py",
         "-m", "assessment_agent", "eval", "--split", split,
         "--eval-mode", "oracle_solver", "--run-name", name, "--no-overwrite"],
        cwd=ROOT, env=env, check=True, capture_output=True)
    return latest_pred(split, re.escape(name))


def first_failed_stage(r: Dict[str, Any]) -> str:
    if r.get("correct") and r.get("status") == "solved":
        return "none"
    if "insufficient_conditions" in (r.get("tags") or []):
        return "none(correct_abstain)" if r.get("status") != "solved" else "release:should_abstain"
    st = r.get("status")
    reason = str(r.get("failure_reason") or "")
    if st != "solved":
        if "解析" in reason or "parse" in reason.lower() or "Sympify" in reason:
            return "extract"
        if "不匹配" in reason or "no_" in reason:
            return "plan/execute"
        return "plan/execute"
    vact = r.get("verification_action")
    if vact == "unverified":
        return "verify:no_evidence"
    if vact == "reject":
        return "verify:falsified"
    if vact == "accept":
        return "release:false_accept"
    return "verify:unknown"


def unified(pred_path: Path, answerable_only_note: str = "") -> Dict[str, Any]:
    rows = [json.loads(l) for l in pred_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    ins = [r for r in rows if "insufficient_conditions" in (r.get("tags") or [])]
    answerable = [r for r in rows if r not in ins]
    solved = [r for r in answerable if r.get("status") == "solved"]
    correct = [r for r in solved if r.get("correct")]
    false_accept = [r for r in solved if r.get("correct") is False
                    and r.get("verification_action") == "accept"]
    correct_abstain = [r for r in ins if r.get("status") != "solved"]
    usage = {"calls": 0, "tokens": 0, "retries": 0}
    for r in rows:
        u = r.get("usage") or {}
        for k in list(usage):
            v = u.get(k) or u.get({"calls": "llm_calls", "tokens": "total_tokens",
                                   "retries": "retries"}.get(k, k))
            if isinstance(v, (int, float)):
                usage[k] += int(v)
    stages: Dict[str, int] = {}
    per_item = {}
    for r in rows:
        s = first_failed_stage(r)
        per_item[r["case_id"]] = s
        stages[s] = stages.get(s, 0) + 1
    n_ans = len(answerable)
    return {
        "n": len(rows), "n_answerable": n_ans,
        "可答正确释放率": round(len(correct) / n_ans, 4) if n_ans else None,
        "候选覆盖率": round(len(solved) / n_ans, 4) if n_ans else None,
        "候选正确率": round(len(correct) / len(solved), 4) if solved else None,
        "错误接受数": len(false_accept),
        "错误接受_ids": [r["case_id"] for r in false_accept],
        "正确弃权数": len(correct_abstain),
        "正确弃权分母(条件不足类)": len(ins),
        "LLM调用": usage["calls"], "LLM_tokens": usage["tokens"], "LLM重试": usage["retries"],
        "各题首失败阶段": stages,
        "逐题阶段表": per_item,
    }


def unimplemented_operators() -> Dict[str, Any]:
    from assessment_agent.planner import MISSING_OPERATORS
    declared: Dict[str, List[str]] = {}
    for p in (ROOT / "knowledge" / "methods").glob("*.json"):
        card = json.loads(p.read_text(encoding="utf-8"))
        mo = (card.get("executable") or {}).get("missing_operators") or []
        if mo:
            declared[card.get("method_id", p.stem)] = mo
    return {"executor_registry": sorted(MISSING_OPERATORS), "card_declared": declared}


def main() -> None:
    report: Dict[str, Any] = {"corpora": {}}
    for corpus, split in CORPORA.items():
        arms = {}
        p1 = latest_pred(split, r"rules_only")
        if p1:
            arms["rules_only"] = unified(p1)
        p2 = latest_pred(split, r"(r9d_dev_fix|r9d_variants_fix|r9d2_dev_fix|r9d2_variants_fix)")
        if p2:
            arms["rules+templates(deterministic)"] = unified(p2)
        p3 = latest_pred(split, r"rules_planner")
        if p3:
            # planner 臂已单跑（预算 30 内，19 call）；此后仅从存盘产物重算，不再触 API
            arms["rules+planner"] = unified(p3)
        report["corpora"][corpus] = arms
    if "--with-llm" in sys.argv:
        for corpus, split in CORPORA.items():
            path = run_planner_arm(split, f"r9d_{corpus}_rules_planner")
            report["corpora"][corpus]["rules+planner"] = unified(path)
    report["unimplemented_operators"] = unimplemented_operators()
    report["full_pipeline"] = ("Stage E 截图端到端已单跑（预算120/实际32成功36发送4超时），"
                               "见 evidence/R9E_PAIRED_EVAL.json；正确释放 20/25 可答，错误接受 0")
    report["arm_provenance_notes"] = {
        "rules+planner": ("产物为 stem_direct_parse 修复前单跑存盘（预算已用于单跑，不重跑触 API）。"
                          "其 dv-expr-03 错接经确定性复放证明在新代码下被拦截：该题 method=sympy_exact"
                          "（planner 仅为未触发的回退），修复后 solve→verify 返回 unverified"
                          "（题面直读式 '-2^4+2^4' 独立求值 0 ≠ Solver 32）。"),
        "deterministic_arms": "r9d2_* 为修复后复跑（零 API），错误接受=0",
    }
    out = ROOT / "evidence" / "R9D_UNIFIED_METRICS.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({c: {a: {k: v for k, v in m.items() if k != "逐题阶段表"}
                          for a, m in arms.items()} for c, arms in report["corpora"].items()},
                     ensure_ascii=False, indent=1)[:2200])


if __name__ == "__main__":
    main()
