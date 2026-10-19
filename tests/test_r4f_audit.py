"""R4f 阶段A 审计回归：pooled IoU 修正、勘误一致性、干净克隆可重放。

评审缺陷 P0/P1：
- `option_region_iou_pooled` 旧公式 Σmean_i/Σn_i 重复除以每题选项数 →
  修正为逐题框数加权 Σ(n_i·mean_i)/Σn_i；
- 首/二跑 pooled 勘误值（0.4479 / 0.3977）必须由**入库证据**独立重算复现；
- evidence 包内裁剪 + SHA-256 清单：干净克隆逐字节可校验；
- 回放脚本：run_ref↔基准题号映射、expected/failure 不再 null、code_sha 非空
  且=实际 git SHA(+dirty)、逐文件内容哈希；离线重现几何输出（零 API、
  零绝对路径依赖）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

from assessment_agent.evaluation.vision_metrics import aggregate_vision

ROOT = Path(__file__).resolve().parents[1]
FIRST = ROOT / "evidence" / "real_vlm_first_run" / "smoke-20261019T022717Z"
SECOND = ROOT / "evidence" / "real_vlm_second_run" / "smoke-20261019T051724Z"


def _per_case_vision(run_dir: Path) -> list:
    rows = []
    for f in sorted((run_dir / "eval_output" / "cases").glob("*.json")):
        rows.append(json.loads(f.read_text(encoding="utf-8")).get("vision") or {})
    return rows


# ---------------------------------------------------------------- P0：pooled 公式

def test_pooled_is_weighted_by_box_count():
    scored = [
        {"option_iou_mean": 0.5, "option_iou_scored": 4,
         "diagram_iou_mean": 0.8, "diagram_iou_scored": 1},
        {"option_iou_mean": 1.0, "option_iou_scored": 1,
         "diagram_iou_mean": 0.4, "diagram_iou_scored": 1},
    ]
    agg = aggregate_vision(scored)
    # (0.5*4 + 1.0*1)/5 = 0.6；旧缺陷公式 = (0.5+1.0)/5 = 0.3
    assert agg["option_region_iou_pooled"]["value"] == 0.6
    assert agg["option_region_iou_pooled"]["denominator"] == 5
    assert agg["diagram_region_iou_pooled"]["value"] == 0.6   # (0.8+0.4)/2
    # 每题均值口径不变
    assert agg["option_region_iou_mean"]["value"] == 0.75


def test_pooled_handles_missing_and_zero_counts():
    agg = aggregate_vision([
        {"option_iou_mean": None, "option_iou_scored": 0},
        {"option_iou_mean": 0.4, "option_iou_scored": 0},   # 分母 0 → 不计
        {"option_iou_mean": 0.8, "option_iou_scored": 2},
    ])
    assert agg["option_region_iou_pooled"]["value"] == 0.8
    assert agg["option_region_iou_pooled"]["denominator"] == 2


def test_errata_matches_independent_recompute_from_committed_evidence():
    """勘误文件必须能由干净克隆内的逐题 vision 数据重算复现（不信任文件本身）。"""
    errata = json.loads((ROOT / "evidence" / "metric_errata" /
                         "r4f_option_region_iou_pooled.json").read_text(encoding="utf-8"))
    by_run = {rec["run"].split("/")[-1]: rec for rec in errata["records"]}
    for run_dir, name in ((FIRST, "smoke-20261019T022717Z"),
                          (SECOND, "smoke-20261019T051724Z")):
        agg = aggregate_vision(_per_case_vision(run_dir))
        rec = by_run[name]
        assert agg["option_region_iou_pooled"]["value"] == \
            rec["corrected"]["option_region_iou_pooled"]["value"], name
        # 原始报告的缺陷值可由旧公式精确复现（证明缺陷来源，且原始记录未被改写）
        rows = _per_case_vision(run_dir)
        means = [v["option_iou_mean"] for v in rows if v.get("option_iou_mean") is not None]
        boxes = sum(int(v.get("option_iou_scored") or 0) for v in rows)
        legacy = round(sum(means) / boxes, 4) if boxes and means else None
        assert legacy == rec["original_reported"]["option_region_iou_pooled"], name
    assert by_run["smoke-20261019T022717Z"]["corrected"]["option_region_iou_pooled"]["value"] == 0.4479
    assert by_run["smoke-20261019T051724Z"]["corrected"]["option_region_iou_pooled"]["value"] == 0.3977


# ---------------------------------------------------------------- P1：清单与自包含

def test_evidence_manifest_verifies_bytes():
    for run_dir in (FIRST, SECOND):
        man = json.loads((run_dir / "manifest.sha256.json").read_text(encoding="utf-8"))
        assert man["file_count"] == len(man["files"]) >= 40
        checked = 0
        for rel, info in man["files"].items():
            p = run_dir / rel
            assert p.is_file(), f"清单声明的文件缺失：{rel}"
            if hashlib.sha256(p.read_bytes()).hexdigest() != info["sha256"]:
                raise AssertionError(f"字节不一致：{rel}")
            checked += 1
        assert checked == man["file_count"]


def test_crops_committed_for_both_runs():
    """评审 404 路径必须存在（干净克隆自包含）。"""
    for run_dir, ts in ((FIRST, "20261019T022717Z"), (SECOND, "20261019T051724Z")):
        for case in ("ft-grid-10", "ft-overlay-12", "ft-rot90-01"):
            regions = run_dir / "runs" / f"e2e-smoke-{ts}-{case}" / "regions"
            assert (regions / "diagram_d1.png").is_file()
            for lab in "ABCD":
                assert (regions / f"option_{lab}.png").is_file()


# ---------------------------------------------------------------- P1：回放可复现

def _load_replay_module():
    spec = importlib.util.spec_from_file_location(
        "r4e_replay", ROOT / "scripts" / "r4e_replay_first_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clean_clone_replay(tmp_path, capsys):
    """无需原机器绝对路径、零 API：直接对入库裁剪重放，几何输出与已发布
    报告一致（grid-10 契约 verified 且无异常；overlay-12 结构化残差阻塞）。"""
    mod = _load_replay_module()
    rc = mod.main(["--label", "pytest_replay", "--results", str(tmp_path)])
    assert rc == 0
    reports = list(tmp_path.glob("pytest_replay-*/replay_report.json"))
    assert len(reports) == 1
    d = json.loads(reports[0].read_text(encoding="utf-8"))
    assert d["api_calls_made"] == 0
    # code_sha 非空 = 实际 git SHA（+dirty 允许，测试工作树可能未提交）
    assert d["code_sha"] and d["code_sha"] != "-dirty"
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                         capture_output=True, text=True).stdout.strip()
    if sha:
        assert d["code_sha"].startswith(sha[:12]) or d["code_provenance"]["git_sha"] == "UNAVAILABLE"
    assert len(d["code_provenance"]["module_sha256"]) >= 4

    by = {c["case_id"]: c for c in d["cases"]}
    assert set(by) == {"ft-grid-10", "ft-overlay-12", "ft-rot90-01"}   # 基准题号映射
    for cid, c in by.items():
        assert c["expected"] == "A"
        assert c["first_run"]["failure_class"]            # 不再是 null
        assert not str(c["replay_inputs"]["stem_crop"]).startswith(("E:", "e:\\"))
        crop = ROOT / c["replay_inputs"]["stem_crop"]
        assert crop.is_file()
        assert hashlib.sha256(crop.read_bytes()).hexdigest() == \
            c["replay_inputs"]["stem_crop_sha256"]
    grid = by["ft-grid-10"]["transform"]
    assert grid["status"] != "error"                      # 不再广播崩溃
    assert grid["coord_contract"]["stem_canvas"] == [138, 142]
    ov = by["ft-overlay-12"]["transform"]
    codes = [b["code"] for b in (ov["blockers"] or [])]
    assert "overlay_residual_over_tol" in codes
    assert not [f for f in (ov.get("symmetry_unresolvable") or []) if "canvas_mismatch" in str(f)] \
        or ov["coord_contract"] is not None
    assert by["ft-rot90-01"]["transform"]["status"] == "not_applicable"


def test_published_r4f_reports_have_mapping_and_provenance():
    for lab in ("r4f_first_run", "r4f_second_run"):
        d = json.loads((ROOT / "evidence" / "r4e_replay" / lab /
                        "replay_report.json").read_text(encoding="utf-8"))
        assert d["code_sha"].startswith("a71d708")
        assert all(c["expected"] == "A" for c in d["cases"])
        assert all(c["question_id"].startswith("q_") for c in d["cases"])
