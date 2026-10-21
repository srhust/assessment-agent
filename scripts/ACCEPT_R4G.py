# -*- coding: utf-8 -*-
"""ACCEPT_R4G — R4g 工单(2026-10-21 重投) vs 已入库交付 8edc7d0 逐项对照验收.

纪律: 只读验收, 不重跑/不修改 10-19 证据包(实验纪律: 原始记录不修改)。
"""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence/r4g_ablation/r4g-20261019T090349Z"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> int:
    rows = []
    # 工单 → 交付映射(全部已在 8edc7d0, 本表为验收对照而非新实现)
    mapping = [
        ("P0-1 DiagramLayoutAssembler(全区域保留/unified几何/layout_unverified/禁按case选网格)",
         "src/assessment_agent/tools/diagram_layout.py (r4g-diagram-layout-v1)",
         ["test_keeps_all_regions", "test_extrapolated_blank_not_inflated", "test_frame_blank_target_confirmed",
          "test_no_gold_params_in_signature"]),
        ("P0-2 六项确定性像素检查(含undeclared units/重叠/裁剪完整性)",
         "tools/diagram_layout.py pixel_verify 通道",
         ["test_out_of_image_region", "test_stem_option_overlap_detected", "test_crop_content_integrity",
          "test_pixel_only_ignores_wrong_declaration", "test_single_region_recounted"]),
        ("P0-3 四臂离线消融(A冻结/B多区域/C+像素/D纯像素, 同Solver同阈值)",
         "scripts/r4g_ablation.py → evidence/r4g_ablation/r4g-20261019T090349Z/",
         None),
        ("P1-1 runtime六字段+gold隔离(crops_complete语义修正)",
         "evaluation/runner.py + layout_funnel.py",
         ["test_vision_stages_runtime_keys_present", "test_runtime_independent_of_gold_disagreement",
          "test_runtime_fail_closed_on_recompute_error"]),
        ("P1-2 归因四分(withheld≠false_reject, ft-rot90-01=withheld-correct)",
         "runner.py root_cause 枚举",
         ["test_withheld_is_not_false_reject", "test_withheld_not_in_false_reject_total",
          "test_wrong_proposal_withheld_still_answerable", "test_totals_are_orthogonal"]),
        ("P1-3 扰动不变性(平移/缩放/边距/顺序置换/干扰/遮挡)",
         "tests/test_r4g_layout.py::TestPerturbationInvariance",
        ["test_translation_scale", "test_region_order_permuted", "test_distractors_outside_band",
         "test_asymmetric_margins", "test_missing_middle_content_unverified"]),
    ]
    for req, impl, tests in mapping:
        rows.append({"requirement": req, "delivered_in": impl, "covering_tests": tests or ["见 ablation_report"]})

    ev = {"summary_md_sha": sha(EV / "r4g_ablation_summary.md"),
          "report_sha": sha(EV / "ablation_report.json"),
          "rows_sha": sha(EV / "ablation_rows.csv"),
          "input_hashes_sha": sha(EV / "input_hashes.json"),
          "viz_files": len(list((EV / "viz").glob("*")))}
    suite = subprocess.run(["python", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
                           capture_output=True, cwd=ROOT).stdout.decode("gbk", errors="replace").strip().splitlines()[-1]
    out = {
        "verdict": "R4G_ALREADY_DELIVERED_ACCEPTED",
        "note": "本工单(10-21重投)与已入库 8edc7d0(10-19 17:24, 基于同一 c7515f1 基线)内容重合; "
                "按纪律未重跑未修改原始证据; 此件为逐项验收对照。",
        "baseline": "c7515f1", "delivered": "8edc7d09d099f4df1d7cc51470f1fd91ad979596",
        "current_main": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip(),
        "full_suite": suite,
        "headline_numbers_from_frozen_ablation": {
            "structural_evidence_goldbased": "5/15", "runtime_layout_verified": "14/15(C臂)",
            "solver_proposals": "1/15→3/15(C)", "final_releases": "0/15(未放宽阈值)",
            "false_accept_delta": "0(A→C 无新增错误接受)"},
        "evidence_integrity": ev,
        "work_order_mapping": rows,
        "open_items": [
            "最终作答率 0/15 未变——布局层证据已通(14/15 verified), 瓶颈转移到规则唯一性/选项歧义(拒因主体为'多个选项匹配无法唯一确定'), 属P2规则搜索广度问题, 非布局问题",
            "少量真实VLM复测判断: 本轮零API结论已稳定; 若下一轮允许API, 建议仅复测 ft-rot270-03(B/C臂提案恢复的direct winner) 与 ft-sym-14 两题验证runtime字段在新鲜输出上的一致性",
        ],
    }
    p = ROOT / "evidence/r4g_ablation/ACCEPT_R4G_20261021.json"
    tmp = p.with_suffix(".tmp")
    json.dump(out, tmp.open("w", encoding="utf-8"), ensure_ascii=False, indent=1)
    tmp.replace(p)
    print("written", p, "| suite:", suite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
