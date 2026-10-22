# -*- coding: utf-8 -*-
"""R12-A2：对 R11 六条件实验按三层口径重新计分并产出勘误（零 API，不改原件）。

人工裁决：R11 汇总把 solved+reject 计成了释放。本脚本从原始 per_item 数据
用 tools.release_scoring 同一实现重算，写 evidence/R11_ERRATA_SCORING.{json,md}。
原件 R11_D_SIX_CONDITION.json / R11B_VISUAL_AUDIT.json / R11G_REAL_VISION.json 保持不动。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.release_scoring import (  # noqa: E402
    summarize_rows, audit_requests_sent, audit_network_timeouts)

# 人工裁决指定的设计性不可唯一判定题（R12 工单原文；来源=裁决而非自选）
DESIGN_AMBIGUOUS = {"ft-sym-14", "ft-conflict-15"}


def main() -> int:
    d = json.loads((ROOT / "evidence/R11_D_SIX_CONDITION.json").read_text(encoding="utf-8"))
    rows = json.loads(json.dumps(d["per_item"]))  # 深拷贝，原件只读
    for r in rows:
        r["design_abstain"] = r["id"] in DESIGN_AMBIGUOUS
    rescored = {c: summarize_rows(rows, c) for c in "ABCDEF"}

    g = json.loads((ROOT / "evidence/R11G_REAL_VISION.json").read_text(encoding="utf-8"))
    snap = g["usage_audit"]
    real_sent = audit_requests_sent(snap)
    real_timeouts = audit_network_timeouts(snap)

    b = json.loads((ROOT / "evidence/R11B_VISUAL_AUDIT.json").read_text(encoding="utf-8"))
    srm = [x["id"] for x in b["per_item"] if x["error_source"] == "solver_rule_missing"]
    dev_gap = [i for i in srm if i not in DESIGN_AMBIGUOUS]

    # 逐题核对（工单点名的两案）
    spot = {}
    for pid in ("ft-mirror-04", "ft-mirror-05"):
        row = next(r for r in d["per_item"] if r["id"] == pid)
        spot[pid] = {"E": row["conds"].get("E"), "F": row["conds"].get("F"),
                     "gold": row["gold"]}

    out = {
        "stage": "R12-A errata",
        "reason": "人工裁决：solved+reject/unverified 不得计为释放；R11 汇总口径 "
                  "status==solved and verify!=unverified 把 reject 也算了释放",
        "R11_reported": d["conditions"],
        "R12_rescored_three_layer": rescored,
        "headline_corrections": {
            "E_final_released": f"{rescored['E']['final_released']}/13（原报告 2/13）",
            "F_final_released": f"{rescored['F']['final_released']}/13（原报告 1/13）",
            "gateway_timeouts": f"真实 HTTP 层 ReadTimeout={real_timeouts}，"
                                f"脚本外层 network_timeouts={g['network_timeouts']} 统计的是被捕获异常，"
                                "不能宣称网关零超时",
            "requests_sent_real": real_sent,
            "distractor_item": "第 6 题传了 extra_text → Pipeline 跳过 VisionParser，"
                               "非合格 image_only 样本；合格视觉解析样本数=5",
            "scoring_baseline_note": "R11 A=10/13 为 Verifier 门控口径，与 R9 A=13/13"
                                     "（Solver 层正确候选口径）不可直接比较，非退化",
        },
        "audit_spot_checks": spot,
        "visual_audit_dev_gap": {
            "solver_rule_missing_all": srm,
            "design_ambiguous_excluded": sorted(DESIGN_AMBIGUOUS),
            "true_dev_gap_items": dev_gap,
            "note": "设计性不可唯一题按裁决从研发缺口剔除，属正确弃权目标而非求解失败",
        },
        "inputs_readonly": ["evidence/R11_D_SIX_CONDITION.json",
                            "evidence/R11B_VISUAL_AUDIT.json",
                            "evidence/R11G_REAL_VISION.json"],
    }
    (ROOT / "evidence/R11_ERRATA_SCORING.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# R11 计分勘误（R12-A 产出；原件保留不覆盖）", "",
          "| 条件 | R11 报告释放 | 正确候选 | 验证接受 | **最终释放** |", "|---|---|---|---|---|"]
    for c in "ABCDEF":
        v = rescored[c]
        md.append(f"| {c} | {d['conditions'][c]['released']} | "
                  f"{v['correct_candidates']} | {v['verified_accepted']} | "
                  f"**{v['final_released']}** |")
    md += ["",
           f"- E 真实最终释放 **{rescored['E']['final_released']}/13**（原报 2）；"
           f"F **{rescored['F']['final_released']}/13**（原报 1）。"
           "mirror-05 在 E/F 均提交正确候选 B 但 Verifier 明确 reject——不计释放。",
           f"- mirror-04：E accept（计入最终释放），F unverified（不计）。"
           f" gated_unverified={rescored['F']['gated_unverified']}",
           "- 网关审计：requests_sent=%d，error_types ReadTimeout=%d；R11 报告"
           "\u201c0 网络超时\u201d表述更正为\u201c外层脚本未捕获到异常，HTTP 层实际 1 次 ReadTimeout\u201d。" % (real_sent, real_timeouts),
           "- 第 6 题（新排版干扰）：传了 extra_text，Pipeline 按文本路径跳过 VisionParser，"
           "从 image_only 合格样本中剔除；合格视觉解析端到端样本=5（其中 1 次 HTTP 超时后未重放）。",
           "- 口径澄清：R11 A=10 为最终 Verifier 门控释放；R9 A=13/13 为 Solver 层正确候选数，"
           "两者口径不同，不构成退化对比。",
           f"- solver_rule_missing 剔除设计性题 {sorted(DESIGN_AMBIGUOUS)} 后，"
           f"真实研发缺口 {len(dev_gap)} 项：{dev_gap}",
           ""]
    (ROOT / "evidence/R11_ERRATA_SCORING.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({c: (v["correct_candidates"], v["verified_accepted"],
                          v["final_released"]) for c, v in rescored.items()},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
