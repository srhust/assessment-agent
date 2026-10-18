"""R4a figtrans 基准冻结守卫：test 首评唯一性 + manifest 完整性 + split 注册。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[1] / "data" / "benchmark_figtrans"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_and_files_consistent():
    man = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    for split in ("dev", "test"):
        info = man["splits"][split]
        f = DATA / info["file"]
        assert f.is_file(), f
        assert _sha(f) == info["sha256"], f"{split} sha 漂移（数据被改动？）"
        rows = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(rows) == info["count"]
    assert man["independence_declaration"]


def test_dev_honest_failures_kept():
    """dev 必须保留歧义/降级诚实样本（不得删困难题换准确率）。"""
    man = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in
            (DATA / man["splits"]["dev"]["file"]).read_text(encoding="utf-8").splitlines()
            if l.strip()]
    failed = [r for r in rows if r["expected_status"] == "failed"]
    assert len(failed) >= 3
    tags = " ".join(" ".join(r["tags"]) for r in failed)
    assert "诚实failed" in tags
    # 变换类型覆盖（指令九类中 dev 至少含 7 类 group）
    groups = {r["group"] for r in rows}
    assert {"rotate90", "rotate180", "rotate270", "mirror", "composition",
            "translate", "scale", "grid3", "overlay", "ambiguous", "degraded"} <= groups


def test_test_set_frozen_and_first_run_attested():
    """test 冻结：首评 metrics 的 data_sha256_at_firstrun == 当前文件 sha；
    test 目录内容只许生成器一次性 publish（存在即冻结）。"""
    man = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    ts = man["splits"]["test"]["sha256"]
    first = json.loads((DATA / "test_firstrun_metrics.json").read_text(encoding="utf-8"))
    assert first["data_sha256_at_firstrun"] == ts
    assert _sha(DATA / "test" / "figtrans.jsonl") == ts
    assert first["summary"]["total"] == man["splits"]["test"]["count"]
    # 诚实披露：首评发现的 false accept 必须记录在案，不得抹掉
    assert any(h["label"].startswith("false_accept") for h in first["honesty_notes"])


def test_splits_registered_in_runner_and_cli():
    from assessment_agent.evaluation.runner import SPLITS, _SPLIT_DIRS

    assert "figtrans_dev" in SPLITS and "figtrans_test" in SPLITS
    assert _SPLIT_DIRS["figtrans_dev"] == "benchmark_figtrans/dev"
    assert _SPLIT_DIRS["figtrans_test"] == "benchmark_figtrans/test"


def test_phase6_smoke_report_honest():
    """真实 VLM 冒烟报告：未配置环境必须 NOT_EXECUTED 且无 mock 冒充痕迹。"""
    first = json.loads((DATA / "phase6_vlm_smoke.json").read_text(encoding="utf-8"))
    assert first["status"] in {"EXECUTED", "NOT_EXECUTED", "EXECUTION_FAILED"}
    if first["status"] == "NOT_EXECUTED":
        assert "LLM_API_KEY" in first["reason"] or "未配置" in first["reason"]
        # 未执行 ⇒ 不得存在任何伪造的 case 结果
        assert "cases" not in first
        assert first["report_fields_on_executed"]


def test_ablation_arms_recorded():
    ab = json.loads((DATA / "ablation_r4a.json").read_text(encoding="utf-8"))
    arms = {r["arm"] for r in ab["rows"]}
    assert {"r0_six_only", "r1_transform_only", "r2_full_orig_verifier",
            "r3_full_indep_verifier"} <= arms
    by = {(r["arm"], r["split"]): r for r in ab["rows"]}
    # 指令底线：完整臂相对原六类臂在变换集上有真实提升，且两臂 bench_dev 不回退
    fig_r0 = by[("r0_six_only", "figtrans_dev")]["fig_correct"]
    fig_r3 = by[("r3_full_indep_verifier", "figtrans_dev")]["fig_correct"]
    assert fig_r3 > fig_r0
    bench_r0 = by[("r0_six_only", "bench_dev")]["overall_answer_accuracy"]["value"]
    bench_r3 = by[("r3_full_indep_verifier", "bench_dev")]["overall_answer_accuracy"]["value"]
    assert bench_r3 >= bench_r0
