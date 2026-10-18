"""R4a Phase5 消融：四条能力臂 × (figtrans_dev / bench_dev)。

臂定义（与指令对齐）：
  r0 原六类规则            ASSESSMENT_R4A_DISABLE=transform   —— 关闭变换通道
  r1 仅新变换规则          ASSESSMENT_R4A_DISABLE=six_rules   —— 六类通道禁用
  r2 完整 + 原 Verifier    ASSESSMENT_R4A_DISABLE=indep_verifier —— 独立验证退化为候选自洽
  r3 完整 + 独立 Verifier  （默认全开）

口径全部取自同一评测引擎（primary/capability/per_skill）；题集与筛选规则逐臂一致，
只切换能力臂——不删题、不放宽容差、不提高拒答阈值。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARMS = {
    "r0_six_only": "transform",
    "r1_transform_only": "six_rules",
    "r2_full_orig_verifier": "indep_verifier",
    "r3_full_indep_verifier": "",
}
SPLITS = ("figtrans_dev", "figshots_dev", "bench_dev")


def run_arm(arm: str, disable: str, split: str) -> dict:
    env = dict(os.environ)
    env.update({"PYTHONPATH": "src", "PYTHONIOENCODING": "utf-8",
                "ASSESSMENT_OFFLINE": "1"})
    if disable:
        env["ASSESSMENT_R4A_DISABLE"] = disable
    else:
        env.pop("ASSESSMENT_R4A_DISABLE", None)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "assessment_agent.cli", "eval",
         "--split", split, "--route-mode", "oracle",
         "--eval-mode", "oracle_solver", "--json"],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(ROOT))
    if proc.returncode != 0:
        return {"arm": arm, "split": split, "error": proc.stderr[-300:]}
    d = json.loads(proc.stdout)
    m = d["metrics"]
    ps = (m.get("per_skill") or {}).get("figure_reasoning", {}) or {}
    cap = m.get("capability") or {}
    return {
        "arm": arm, "split": split, "disable": disable or None,
        "fig_total": ps.get("total"), "fig_correct": ps.get("correct"),
        "fig_accuracy": ps.get("accuracy"),
        "false_accept": cap.get("false_accept_total", ps.get("false_accept_total")),
        "false_reject": cap.get("false_reject_total"),
        "over_answer": cap.get("over_answer_total"),
        "objective_answer_accuracy": m.get("answer_accuracy"),
        "answerable_coverage": m.get("answerable_coverage"),
        "selective_accuracy": m.get("selective_accuracy"),
        "overall_answer_accuracy": m.get("overall_answer_accuracy"),
        "unverified_abstention": m.get("abstention_total"),
    }


def main() -> None:
    rows = []
    for arm, disable in ARMS.items():
        for split in SPLITS:
            r = run_arm(arm, disable, split)
            rows.append(r)
            print(json.dumps(r, ensure_ascii=False))
    # R4b：证据文件按轮次命名（ablation_r4a.json 是 R4a 验收凭证，不再覆盖）
    out_name = os.environ.get("ABLATION_OUT", "ablation_r4a.json")
    out = ROOT / "data" / "benchmark_figtrans" / out_name
    out.write_text(json.dumps({
        "generated_at": "2026-10-18",
        "note": "bench_dev 行为全题型口径（figure 子集另见 fig_*）；"
                "各臂题集一致，仅切换能力臂；无删题/无容差调整。"
                "R4b 复跑说明：验证口径已收紧（单因子 all_confirm + 几何六项），"
                "r2/r3 差异只反映独立验证器代数差，题集与容差未动。",
        "harness": os.environ.get("ABLATION_NOTE", ""),
        "rows": rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    main()
