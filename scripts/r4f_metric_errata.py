"""R4f 阶段A：生成指标勘误文件（不修改任何原始 Smoke 记录）。

对两次真实 Smoke 的 `option_region_iou_pooled`（及 diagram 同型口径）按
修正公式 Σ(n_i·mean_i)/Σn_i 重算，输出 `evidence/metric_errata/` 勘误记录。
"""
from __future__ import annotations

import glob
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = [
    "evidence/real_vlm_first_run/smoke-20261019T022717Z",
    "evidence/real_vlm_second_run/smoke-20261019T051724Z",
]


def recompute(run_dir: Path) -> dict:
    per, opt_num, opt_den, dia_num, dia_den = [], 0.0, 0, 0.0, 0
    for f in sorted(glob.glob(str(run_dir / "eval_output/cases/*.json"))):
        c = json.loads(Path(f).read_text(encoding="utf-8"))
        v = c.get("vision") or {}
        om, on = v.get("option_iou_mean"), int(v.get("option_iou_scored") or 0)
        dm, dn = v.get("diagram_iou_mean"), int(v.get("diagram_iou_scored") or 0)
        per.append({"case_id": c.get("case_id"), "option_iou_mean": om,
                    "option_n": on, "diagram_iou_mean": dm, "diagram_n": dn})
        if om is not None and on > 0:
            opt_num += om * on
            opt_den += on
        if dm is not None and dn > 0:
            dia_num += dm * dn
            dia_den += dn
    metrics = json.loads((run_dir / "eval_output/metrics.json").read_text(encoding="utf-8"))
    vv = metrics.get("vision", {})
    old_opt = (vv.get("option_region_iou_pooled") or {}).get("value")
    old_opt_mean = (vv.get("option_region_iou_mean") or {}).get("value")
    # 旧缺陷口径复算（Σmean_i / Σn_i）用于交叉验证原报告数字来源
    means = [p["option_iou_mean"] for p in per if p["option_iou_mean"] is not None]
    legacy = round(sum(means) / opt_den, 4) if opt_den and means else None
    return {
        "run": run_dir.as_posix(),
        "per_case": per,
        "original_reported": {
            "option_region_iou_mean": old_opt_mean,
            "option_region_iou_pooled": old_opt,
        },
        "legacy_formula_recomputed": legacy,
        "corrected": {
            "option_region_iou_pooled": {
                "value": round(opt_num / opt_den, 4) if opt_den else None,
                "denominator": opt_den,
            },
            "diagram_region_iou_pooled": {
                "value": round(dia_num / dia_den, 4) if dia_den else None,
                "denominator": dia_den,
            },
        },
    }


def main() -> None:
    errata = {
        "id": "errata-r4f-option-region-iou-pooled",
        "issued_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code_fix": "src/assessment_agent/evaluation/vision_metrics.py aggregate_vision (R4f)",
        "defect": ("旧实现 pooled = Σ(每题均值)/Σ(每题金标框数)：分子是逐题平均IoU的和，"
                   "分母却是框总数，等价于把每题均值又除以了一次该题选项数，系统性低估"
                   "（n 选项等量时约为真值的 1/n）。"),
        "corrected_formula": "pooled = Σ_i (n_i · mean_i) / Σ_i n_i",
        "impact": "不影响 Solver/Verifier 作答与放行判定，仅误导视觉框选质量分析；"
                  "diagram_region_iou_mean 与 option_region_iou_mean 本身无错。",
        "discipline": "原始两次 Smoke 与历史回放文件一字未改；R4f 起新报告直接输出修复后口径。",
        "records": [recompute(ROOT / r) for r in RUNS],
    }
    out = ROOT / "evidence" / "metric_errata"
    out.mkdir(parents=True, exist_ok=True)
    (out / "r4f_option_region_iou_pooled.json").write_text(
        json.dumps(errata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for rec in errata["records"]:
        print(rec["run"].split("/")[-1],
              "| reported", rec["original_reported"]["option_region_iou_pooled"],
              "| legacy-recompute", rec["legacy_formula_recomputed"],
              "| corrected", rec["corrected"]["option_region_iou_pooled"])
    print("written:", out / "r4f_option_region_iou_pooled.json")


if __name__ == "__main__":
    main()
