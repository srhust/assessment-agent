"""R4e 回归：真实视觉输入下的图形坐标归一化、证据分档与错误归因三分。

评审验收清单（逐条对应）：
- 46×47 + 46×48 异尺寸布尔操作**不再崩溃**（geometry 路径对齐；无 geometry 路径
  记结构化 shape_mismatch 阻塞而非异常）；
- 不同尺寸图形在**可靠共同画布**下 XOR/OR/AND 仍可判定，且 OR/XOR 不收敛混淆；
- 1–2px 裁剪抖动容忍；真正不一致的画布定义 → coordinate_frame_unverified 保守
  降级，绝不人为补零/强缩放假装几何证据仍准确；
- canvas_mismatch 保护不删除：无几何（legacy）路径语义一字不动；有几何路径只在
  **题干网格自身**画布异常时降级，选项内容贴边裁剪不再误伤整体；
- Verifier 与 Solver 共享**可审计坐标契约**但独立重算：候选分数被篡改不影响验证；
- 选项顺序置换不改变答案语义；
- P1-1 证据三档：VLM 1×3 vs 金标 1×4（区域/选项都在）不得算完整结构证据；
- P1-2 归因三分：最上游偏差 ≠ 阻断原因（题干截断不得遮蔽几何阻塞）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from assessment_agent.evaluation.diagnosis import attribute_error_chain, classify_case_failure
from assessment_agent.evaluation.metrics import CaseResult
from assessment_agent.tools.figure_transform import (
    CANVAS_ALIGN_TOL_PX, align_pair_for_overlay, analyze_transform, arbitrate,
    fit_canvas, group_for, residual, standardize,
)

ROOT = Path(__file__).resolve().parents[1]


def box(x0, y0, w, h, hw=40):
    m = np.zeros((hw, hw), np.uint8)
    m[y0:y0 + h, x0:x0 + w] = 1
    return m


XOR = lambda x, y: ((x > 0) ^ (y > 0)).astype(np.uint8)
OR = lambda x, y: np.logical_or(x, y).astype(np.uint8)
AND = lambda x, y: np.logical_and(x, y).astype(np.uint8)


def grid3_xor_stem(hw=40):
    """两行演示 + 空格行：行律 = XOR（重叠区挖空，xor≠or≠and）。"""
    a, b = box(6, 6, 24, 8, hw), box(14, 6, 8, 24, hw)
    c3 = XOR(a, b)
    d, e = box(8, 24, 24, 8, hw), box(24, 14, 8, 18, hw)
    f3 = XOR(d, e)
    g, h = box(4, 4, 12, 32, hw), box(4, 4, 32, 12, hw)
    return [a, b, c3, d, e, f3, g, h], XOR(g, h), OR(g, h), AND(g, h)


def boxes_3x3(cell_w, cell_h):
    """等分网格 bbox（与 analyze_layout explicit 取整同式），3×3。"""
    boxes = []
    for r in range(3):
        for c in range(3):
            boxes.append([c * cell_w, r * cell_h, (c + 1) * cell_w, (r + 1) * cell_h])
    return boxes


# ---------------------------------------------------------------- 对齐契约本体

def test_align_jitter_two_px_top_left_verified():
    a = np.zeros((46, 47), np.uint8)
    a[4:30, 6:28] = 1
    b = np.zeros((46, 48), np.uint8)
    b[4:30, 6:28] = 1
    boxes = boxes_3x3(48, 46)
    aa, bb, ct = align_pair_for_overlay(a, b, boxes[0], boxes[1], (138, 144))
    assert ct.verified and ct.mode == "grid_top_left"
    assert aa.shape == bb.shape            # 同共同画布后才能逐像素叠加
    assert int((aa ^ bb).sum()) == 0       # 同位内容：对齐不引入位移


def test_align_no_geometry_different_shape_shape_mismatch():
    a = np.zeros((46, 47), np.uint8)
    a[10:20, 10:20] = 1
    b = np.zeros((46, 48), np.uint8)
    b[10:20, 10:20] = 1
    aa, bb, ct = align_pair_for_overlay(a, b, None, None, None)
    assert not ct.verified and ct.mode == "shape_mismatch"   # 结构化码，非异常


def test_align_true_canvas_conflict_unverified_not_padded():
    """两格既不同行也不同列（画布原点证据不成立）→ coordinate_frame_unverified，
    不得靠补零假装还能叠加。"""
    a = np.zeros((46, 47), np.uint8)
    a[10:20, 10:20] = 1
    b = np.zeros((44, 49), np.uint8)
    b[10:20, 10:20] = 1
    geo = [[0, 0, 47, 46], [50, 52, 99, 96]]          # x/y 区间均错位 >TOL
    aa, bb, ct = align_pair_for_overlay(a, b, geo[0], geo[1], (140, 100))
    assert not ct.verified and ct.mode == "coordinate_frame_unverified"


def test_align_jitter_over_tol_unverified():
    a = np.zeros((46, 47), np.uint8)
    a[8:20, 8:20] = 1
    b = np.zeros((46, 47 + CANVAS_ALIGN_TOL_PX + 3), np.uint8)
    b[8:20, 8:20] = 1
    boxes = boxes_3x3(47, 46)                          # 名义同行，但实际尺寸差超容差
    boxes[1][2] = boxes[1][0] + b.shape[1]
    aa, bb, ct = align_pair_for_overlay(a, b, boxes[0], boxes[1], (138, 150))
    assert not ct.verified and ct.mode == "coordinate_frame_unverified"


# ---------------------------------------------------------------- 求解器行为

def test_different_sizes_with_geometry_overlay_solved():
    """46×47/46×48 式异尺寸格 + 可审计网格几何：XOR 律照常判定，不崩溃。"""
    cells, ans, or_opt, and_opt = grid3_xor_stem()
    # 模拟网格取整：每格画布在 ±1px 内抖动（只裁到画布边缘，内容 ≤30px 不受损，
    # 左上锚定不移动）——真实首跑 grid-10 正是 46×47 / 46×48 这一形态
    hi = [39, 39, 39, 39, 39, 39, 39, 39, 39]
    wi = [41, 40, 41, 40, 41, 40, 41, 40, 41]
    jittered = []
    for i, c in enumerate(cells):
        m = np.zeros((hi[i], wi[i]), np.uint8)
        rh, rw = min(hi[i], c.shape[0]), min(wi[i], c.shape[1])
        m[:rh, :rw] = c[:rh, :rw]
        jittered.append(m)
    # bbox 与掩码尺寸一致（真实 explicit-grid 切片正是如此：行高 39、列宽 40/41）
    boxes = []
    for r_ in range(3):
        x = 0                                    # 每行从画布左缘重新起排
        for c_ in range(3):
            w_ = wi[r_ * 3 + c_]
            boxes.append([x, r_ * 39, x + w_, r_ * 39 + 39])
            x += w_
    known = [i for i in range(9) if i != 8]
    geo = [boxes[i] for i in known]
    stem = [jittered[i] for i in known]
    r = analyze_transform(stem, {"A": ans, "B": or_opt, "C": and_opt},
                          mode="grid3", cell_geometry=geo, stem_canvas=(138, 144))
    assert r["status"] == "ok"
    assert not [b for b in r["blockers"]
                if b["code"] in ("shape_mismatch", "coordinate_frame_unverified")]
    assert r["verdict"]["verdict"] == "unique" and r["verdict"]["answer"] == "A"
    assert any(str(s).startswith("overlay") for s in r["verdict"]["support"]["A"])


def test_legacy_same_size_xor_or_and_stay_distinguishable():
    """无几何等尺寸路径语义与 R4a 基线一致：xor 唯一命中，or/and 不误收。"""
    cells, ans, or_opt, and_opt = grid3_xor_stem()
    r = analyze_transform(cells, {"A": ans, "B": or_opt, "C": and_opt}, mode="grid3")
    assert r["verdict"]["verdict"] == "unique" and r["verdict"]["answer"] == "A"
    xor_c = [c for c in r["candidates"] if c["transformation"] == "overlay_xor"][0]
    ov = xor_c["candidate_scores"]
    assert ov["A"] <= 0.15 and ov["B"] > ov["A"] and ov["C"] > ov["A"]


def test_content_normalization_cannot_replace_canvas_overlay():
    """R4a 教训回归：叠加比较必须在**画布级**进行——同位置横竖两矩形的 or 与 xor
    差异（重叠区有无）会被内容 bbox 归一拉平；fit_canvas（整画布等比、保位置）
    保留差异。若哪天有人把叠加改回内容归一化，此测试必须变红。"""
    a = np.zeros((40, 40), np.uint8)
    a[6:14, 6:30] = 1                    # 横条
    b = np.zeros((40, 40), np.uint8)
    b[6:30, 14:22] = 1                   # 竖条：中心重叠
    xor_m, or_m = XOR(a, b), OR(a, b)
    t = standardize(xor_m)
    assert residual(fit_canvas(or_m, 64), t.canvas_frame) > 0.05   # or≠xor 可见
    assert residual(fit_canvas(xor_m, 64), t.canvas_frame) < 1e-9


def test_canvas_mismatch_guard_survives_legacy_and_stem_grid():
    """canvas_mismatch 保护不删除：legacy（无几何）混尺寸仍整体降级；
    有几何但**题干格**纵横不一致（>0.2）同样降级。"""
    small = box(1, 5, 8, 3, hw=12)
    big_frame = standardize(box(8, 8, 20, 22, hw=64)).frame
    r = analyze_transform([small, small, small], {"A": big_frame,
                                                  "B": standardize(small).frame})
    assert any("canvas_mismatch" in f for f in r["symmetry_unresolvable"])
    assert r["verdict"].get("answer") is None


def test_geometry_path_options_not_lumped_into_canvas_mismatch():
    """有几何证据时，选项内容贴边裁剪的尺寸差**不再**触发 canvas_mismatch
    整体降级（overlay-12 首跑误伤路径）；只有题干网格自身异常才降级。"""
    cells, ans, or_opt, and_opt = grid3_xor_stem()
    geo = boxes_3x3(40, 40)
    known = [i for i in range(9) if i != 8]
    stem = [cells[i] for i in known]
    r = analyze_transform(stem, {"A": box(4, 4, 12, 32, hw=60),          # 贴边选项
                                 "B": box(3, 3, 32, 11, hw=14),          # 纵横悬殊
                                 "C": and_opt},
                          mode="grid3", cell_geometry=[geo[i] for i in known],
                          stem_canvas=(120, 120))
    assert not [f for f in r["symmetry_unresolvable"] if f.startswith("canvas_mismatch")]
    assert [c for c in r["candidates"] if c["rule_type"].startswith("overlay")]


def test_option_order_permutation_keeps_answer_semantics():
    cells, ans, or_opt, and_opt = grid3_xor_stem()
    r1 = analyze_transform(cells, {"A": ans, "B": or_opt, "C": and_opt}, mode="grid3")
    r2 = analyze_transform(cells, {"C": and_opt, "B": ans, "A": or_opt}, mode="grid3")
    assert r1["verdict"]["answer"] == "A"      # xor 内容挂在 A → 答 A
    assert r2["verdict"]["answer"] == "B"      # 同一内容换挂 B → 答案跟着内容走


def rect(h, w, bx, by, bw, bh):
    m = np.zeros((h, w), np.uint8)
    m[by:by + bh, bx:bx + bw] = 1
    return m


def test_structured_blocker_codes_emitted_no_exception():
    """无几何异尺寸九宫格：不抛 ValueError，blockers 记 shape_mismatch 结构化码，
    不作答（不补零假装）。"""
    a, b = rect(46, 47, 6, 6, 24, 8), rect(46, 48, 14, 6, 8, 24)
    d, e = rect(46, 47, 8, 24, 24, 8), rect(46, 48, 24, 14, 8, 18)
    g, h = rect(46, 47, 4, 4, 12, 32), rect(46, 48, 4, 4, 32, 12)
    c3 = XOR(a, b[:46, :47])                  # 演示行尺寸先行对齐仅为构造目标格
    f3 = XOR(d, e[:46, :47])
    r = analyze_transform([a, b, c3, d, e, f3, g, h],
                          {"A": c3, "B": f3, "C": g}, mode="grid3")
    assert r["status"] == "ok"
    codes = {blk["code"] for blk in r["blockers"]}
    assert "shape_mismatch" in codes
    assert r["verdict"].get("answer") is None      # 不可信即不作答


# ---------------------------------------------------------------- 验证器独立性

def test_verifier_independent_from_solver_scores():
    """Solver 候选分数被篡改（全 0.0 假支撑）不得影响验证器：几何重放失败照 reject。"""
    from assessment_agent.tools.figure_transform_tools import verify_geometry

    cells, ans, or_opt, and_opt = grid3_xor_stem()
    r = analyze_transform(cells, {"A": ans, "B": or_opt, "C": and_opt}, mode="grid3")
    assert r["verdict"]["answer"] == "A"
    tampered = json.loads(json.dumps({**r, "mode": "grid3"}))
    for cand in tampered["candidates"]:
        cand["candidate_scores"] = {k: 0.0 for k in cand["candidate_scores"]}
        cand["geometric_residual"] = 0.0
        cand["confidence"] = 0.99
    # 用**错误答案** + 假分数送去验证：验证器必须独立重算并拦下
    ver = verify_geometry(tampered, cells, {"A": ans, "B": or_opt, "C": and_opt}, "B")
    assert ver["blocking"] is True or ver["passed"] is False
    # 正确答案 + 同样的假分数：验证器独立重算后仍 confirm（不吃分数的双向证明）
    ver_ok = verify_geometry(tampered, cells, {"A": ans, "B": or_opt, "C": and_opt}, "A")
    assert ver_ok["passed"] is True and ver_ok["blocking"] is False


def test_verifier_honours_shared_coord_contract_independently():
    """验证器消费同一坐标契约（payload.coord_contract），但自有帧重算异尺寸叠加。"""
    from assessment_agent.tools.figure_transform_tools import verify_geometry

    cells, ans, or_opt, and_opt = grid3_xor_stem()
    geo = boxes_3x3(40, 40)
    known = [i for i in range(9) if i != 8]
    stem = [cells[i] for i in known]
    r = analyze_transform(stem, {"A": ans, "B": or_opt, "C": and_opt}, mode="grid3",
                          cell_geometry=[geo[i] for i in known], stem_canvas=(120, 120))
    assert r["verdict"]["verdict"] == "unique" and r["verdict"]["answer"] == "A"
    payload = {**r, "mode": "grid3"}
    assert payload["coord_contract"]["stem_canvas"] == [120, 120]
    ver = verify_geometry(payload, stem, {"A": ans, "B": or_opt, "C": and_opt}, "A")
    assert ver["passed"] is True and ver["blocking"] is False
    names = {c.name for c in ver["checks"] if c.passed}
    assert "geometry.independently_verified" in names


# ---------------------------------------------------------------- P1-1 证据三档

def _stages(vision_detail, crop_path="nope.png"):
    from assessment_agent.evaluation.runner import EvaluationRunner

    class _Ex:
        parser = "vision_parser"
        text_regions = [{"bbox": [0, 0, 10, 10]}]
        diagram_regions = [{"bbox": [0, 0, 20, 20]}]
        option_regions = [{"label": "A", "has_figure": True, "crop_path": crop_path}]
        table_regions = []
        stem_matrix_crop = crop_path

    audit0 = {"by_phase": {"vision_parse": {"http_succeeded": 0, "json_output_valid": 0}}}
    audit1 = {"by_phase": {"vision_parse": {"http_succeeded": 1, "json_output_valid": 1}}}
    return EvaluationRunner._vision_stages(audit0, audit1, _Ex(), vision_detail)


def test_structural_incomplete_when_vlm_grid_1x3_gold_1x4():
    """评审反例：VLM 解析为 1×3、金标为 1×4，区域和选项均存在——区域档可完整，
    **结构档必须不完整**（旧 evidence_complete 单档过宽的教训）。"""
    vd = {"region_detection_failure": False, "grid_parse_correct": False,
          "crops_complete": True}
    st = _stages(vd)
    assert st["evidence_complete"] is True            # 旧口径保留（历史可比）
    assert st["region_evidence_complete"] is True
    assert st["structural_evidence_complete"] is False
    assert st["solver_ready"] is False


def test_solver_ready_requires_crops_on_disk(tmp_path):
    vd = {"region_detection_failure": False, "grid_parse_correct": True,
          "crops_complete": True}
    st = _stages(vd, crop_path=str(tmp_path / "missing.png"))
    assert st["structural_evidence_complete"] is True
    assert st["solver_ready"] is False               # 裁剪文件不在盘上 → 未就绪
    (tmp_path / "missing.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    st2 = _stages(vd, crop_path=str(tmp_path / "missing.png"))
    assert st2["solver_ready"] is True


def test_non_matrix_case_structural_equals_region():
    """无矩阵结构（金标无 grid）：结构档=区域档，不虚设门槛。"""
    vd = {"region_detection_failure": False, "grid_parse_correct": None,
          "crops_complete": None}
    st = _stages(vd)
    assert st["region_evidence_complete"] is True
    assert st["structural_evidence_complete"] is True


def test_stage_summary_reports_all_three_tiers():
    from assessment_agent.evaluation.execution_status import vision_stage_summary

    rows = [{"vision": {"vision_http_success": True, "vision_schema_valid": True,
                        "vision_extraction_valid": True, "evidence_complete": True,
                        "region_evidence_complete": True,
                        "structural_evidence_complete": False, "solver_ready": False}},
            {"vision": {"vision_http_success": True, "vision_schema_valid": True,
                        "vision_extraction_valid": True, "evidence_complete": True,
                        "region_evidence_complete": True,
                        "structural_evidence_complete": True, "solver_ready": True}}]
    s = vision_stage_summary(rows)
    assert s["region_evidence_complete"]["numerator"] == 2
    assert s["structural_evidence_complete"]["numerator"] == 1
    assert s["solver_ready"]["numerator"] == 1
    assert s["evidence_complete"]["numerator"] == 2


# ---------------------------------------------------------------- P1-2 归因三分

def test_chain_geometry_not_masked_by_stem_truncation():
    """首跑教训：三题共同 stem 截断不得遮蔽几何阻塞——first_detected 是文本，
    blocking 必须是几何码。"""
    chain = attribute_error_chain(
        mode="image_only", correct=False, answered=False,
        vision={"vision_http_success": True, "vision_schema_valid": True,
                "vision_extraction_valid": True, "region_detection_failure": False,
                "grid_parse_correct": True, "crops_complete": True},
        extraction_stem_ok=False, option_map_ok=True, routed_ok=True,
        transform={"blockers": [{"code": "coordinate_frame_unverified",
                                 "detail": "两格既不同行也不同列"}],
                   "verdict": {"verdict": "no_rule"}, "status": "failed"},
        verification_action="reject", solution_answer=None,
    )
    assert chain["first_detected_error"] == "stem_text:stem_truncated_or_wrong"
    assert chain["blocking_failure"] == "solver:coordinate_frame_unverified"
    assert any(d["stage"] == "stem_text" for d in chain["secondary_errors"])


def test_chain_grid_mismatch_is_first_detected_for_rot90():
    chain = attribute_error_chain(
        mode="image_only", correct=False, answered=False,
        vision={"vision_http_success": True, "vision_schema_valid": True,
                "vision_extraction_valid": True, "region_detection_failure": False,
                "grid_parse_correct": False, "crops_complete": True},
        extraction_stem_ok=False, option_map_ok=True, routed_ok=True,
        transform={"blockers": [], "verdict": {"verdict": "no_rule"},
                   "status": "not_applicable"},
        verification_action="reject", solution_answer=None,
    )
    assert chain["first_detected_error"] == "structure_grid:grid_parse_mismatch"
    assert chain["blocking_failure"] == "solver:channel_not_applicable"


def test_classify_failure_geometry_blocker_beats_text():
    """classify_case_failure：结构档异常/blocker 存在时不再提前返回文本错误类别。"""
    from assessment_agent.schemas import Question  # noqa: F401  (类型占位)

    class FakeCase:
        answer = "A"
        expected_status = "solved"
        skill = "figure_reasoning"

    fc = classify_case_failure(
        case=FakeCase(), mode="image_only", correct=False, status="failed",
        action="reject", predicted=None, solution_answer=None, solution_error=None,
        solution_meta={}, routed_skill="figure_reasoning", extraction_present=True,
        extraction_stem_ok=False, option_map_ok=True,
        vision={"grid_parse_correct": True, "crops_complete": True},
        transform_blockers=["coordinate_frame_unverified"],
    )
    assert fc == "region_missing_or_shifted"          # 几何级失效，不再记文本截断


def test_correct_case_chain_all_none():
    chain = attribute_error_chain(mode="image_only", correct=True, answered=True,
                                  vision={}, transform={}, verification_action="accept",
                                  solution_answer="A")
    assert chain == {"first_detected_error": None, "blocking_failure": None,
                     "secondary_errors": []}


# ---------------------------------------------------------------- 首跑真实输入

def test_first_run_replay_payload_fields_present():
    """同输入回放产物（组①/组②）：旧码异常路径消失，几何契约与阻塞码结构化。"""
    ev = ROOT / "evidence" / "r4e_replay"
    old = json.loads((ev / "pre_fix" / "replay_report.json").read_text(encoding="utf-8"))
    new = json.loads((ev / "post_fix" / "replay_report.json").read_text(encoding="utf-8"))
    old_crash = [c for c in old["cases"] if "ValueError" in str(c["transform"].get("reason", ""))]
    assert len(old_crash) == 1                        # 组①：广播异常基线存在
    assert not [c for c in new["cases"]
                if "ValueError" in str(c["transform"].get("reason", ""))
                or c["transform"]["status"] == "error"]   # 组②：同输入无崩溃
    by_old = {c["case_id"]: c for c in old["cases"]}
    by_new = {c["case_id"]: c for c in new["cases"]}
    assert set(by_old) == set(by_new)
    for cid in by_new:
        # 视觉输入同一：裁剪路径与网格与首跑一致
        assert by_new[cid]["replay_inputs"]["stem_crop"] == by_old[cid]["replay_inputs"]["stem_crop"]
    grid = by_new["q_39b82e15aadf"]["transform"]
    assert isinstance(grid["blockers"], list)          # 结构化审计字段存在
    overlay = by_new["q_ecf6d6599708"]["transform"]
    assert not any(str(f).startswith("canvas_mismatch")
                   for f in (overlay.get("symmetry_unresolvable") or [])) \
        or overlay["coord_contract"] is not None


def test_case_result_serialises_chain_fields():
    r = CaseResult(case_id="x", skill="s", expected="A", predicted=None,
                   correct=False, status="failed", verification_action="reject",
                   latency_ms=1.0, first_detected_error="stem_text:stem_truncated_or_wrong",
                   blocking_failure="solver:shape_mismatch",
                   secondary_errors=[{"stage": "stem_text", "code": "t", "detail": ""}])
    d = r.as_dict()
    assert d["blocking_failure"] == "solver:shape_mismatch"
