"""R4g P1-2：dev15 报告 false_reject 口径勘误（不改原文件，新增版本化记录）。

dev15_report.json headline.false_reject=1 是临时汇编脚本把"提议=金标但未释放"
一律计为 false_reject；同批 metrics.json 的 false_reject_total=0 且
unverified_answerable_total=1（rot90-01 action=unverified）。两口径不一致。
R4g 起 diagnosis 拆分 outcome：unverified ∧ proposal=gold →
``evidence_insufficient_correct_withheld``（纪律正确的扣留），
false_reject 仅保留"证据完整仍被拒"的真误杀语义。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "evidence" / "metric_errata" / "r4g_dev15_false_reject_semantics.json"


def main() -> int:
    dev = json.loads((ROOT / "evidence" / "real_vlm_dev15" /
                      "dev15-20261019T065016Z" / "dev15_report.json")
                     .read_text(encoding="utf-8"))
    metrics = json.loads((ROOT / "evidence" / "real_vlm_dev15" /
                          "dev15-20261019T065016Z" / "eval_output" / "metrics.json")
                         .read_text(encoding="utf-8"))
    cap = metrics.get("capability", {})
    outcomes = cap.get("verification_outcomes", {})
    rot90 = next(c for c in dev["cases"] if c["case_id"] == "ft-rot90-01")
    doc = {
        "id": "r4g_dev15_false_reject_semantics",
        "issued_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code_fix": "diagnosis.derive_verification_outcome 拆分 unverified+proposal=gold "
                    "为 evidence_insufficient_correct_withheld；metrics 单列计数",
        "defect": "dev15_report.headline.false_reject（临时汇编口径）与 metrics.json "
                  "标准口径不一致：把'提议=金标但证据不足未释放'计为 false_reject",
        "impact": {
            "dev15_report_false_reject_as_published": dev["headline"]["false_reject"],
            "metrics_json_false_reject_total": cap.get("false_reject_total"),
            "metrics_json_unverified_answerable_total":
                cap.get("unverified_answerable_total"),
            "corrected": {
                "verifier_false_reject": 0,
                "evidence_insufficient_correct_withheld": 1,
                "affected_cases": ["ft-rot90-01"],
                "evidence": {
                    "case": rot90["case_id"], "proposal": rot90["solver_proposed"],
                    "gold": rot90["gold"], "verification_action":
                        rot90["verification_action"],
                    "outcome_published": outcomes.get("unverified_answerable", 0),
                    "blocking_check": "figure.independent_substitution（独立重建不能"
                                      "确认约束）→ 扣留是纪律正确行为，不计验证器缺陷",
                },
            },
        },
        "discipline": "原始 dev15_report.json / metrics.json 一字不改；本文件为追加式勘误。"
                      "false_accept=0 仅为观察值（15 题无答案被接受），不足以证明低误放率"
                      "——评审口径如实保留。",
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("勘误写入：", OUT)
    print(json.dumps(doc["impact"], ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
