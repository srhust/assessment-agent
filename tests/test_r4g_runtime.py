"""R4g 测试：验证器误杀 vs 证据不足扣留的归因拆分（P1-2）+ 运行时字段（P1-1）。

纪律锚点：金标只用于评分对照；online 决策不引用金标派生量；
false_reject 与 evidence_insufficient_correct_withheld 是**两个正交口径**，
不得把"正确防御下的扣留"误计为"验证器缺陷"。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assessment_agent.evaluation.diagnosis import (  # noqa: E402
    ROOT_CAUSES, VERIFICATION_OUTCOMES, derive_root_cause,
    derive_verification_outcome)
from assessment_agent.evaluation.metrics import compute_capability_metrics  # noqa: E402


def out(*, answerability="has_unique_answer", action, predicted, solution_answer, gold):
    return derive_verification_outcome(
        answerability=answerability, action=action, predicted=predicted,
        solution_answer=solution_answer, gold=gold)


class TestAttributionSplit:
    def test_withheld_is_not_false_reject(self):
        """P1-2 核心：提议=金标但 unverified 扣留 ≠ verifier 误杀。"""
        assert out(action="unverified", predicted=None,
                   solution_answer="A", gold="A") == \
            "evidence_insufficient_correct_withheld"
        assert out(action="reject", predicted=None,
                   solution_answer="A", gold="A") == "false_reject"

    def test_two_classes_are_distinct_labels(self):
        assert "evidence_insufficient_correct_withheld" in VERIFICATION_OUTCOMES
        assert "false_reject" in VERIFICATION_OUTCOMES
        assert derive_root_cause(outcome="false_reject",
                                 failure_class=None) == "verifier"
        assert derive_root_cause(
            outcome="evidence_insufficient_correct_withheld",
            failure_class=None) == "evidence_insufficient_withheld"

    def test_root_cause_enum_has_new_entry(self):
        assert "evidence_insufficient_withheld" in ROOT_CAUSES

    def test_wrong_proposal_withheld_still_answerable(self):
        # 提议≠金标（错误候选）被 unverified → 仍是普通受控弃权，不谎报命中
        assert out(action="unverified", predicted=None,
                   solution_answer="B", gold="A") == "unverified_answerable"

    def test_no_gold_no_withheld_claim(self):
        # 无金标时不得声称"命中扣留"——unverified 路径如实返回受控弃权，
        # withheld 标签只在 gold 存在且 solution_answer==gold 时出现（既有优先级）
        assert out(answerability="objective_unlabeled", action="unverified",
                   predicted=None, solution_answer="A", gold=None) == \
            "unverified_answerable"
        assert out(answerability="has_unique_answer", action="unverified",
                   predicted=None, solution_answer="A", gold=None) == \
            "unverified_answerable"


def _cap(results):
    return compute_capability_metrics(results)


class TestCapabilityTotals:
    """聚合层：两口径独立计数，互不并入。"""

    def _row(self, cid, outcome, rc):
        from assessment_agent.evaluation.metrics import CaseResult
        return CaseResult(
            case_id=cid, skill="figure_reasoning",
            expected="A", predicted=None,
            correct=(outcome == "correct_accept"), status="failed",
            verification_action="reject", latency_ms=1.0,
            predicted_option="A" if outcome == "correct_accept" else None,
            verification_outcome=outcome, root_cause=rc,
            answerability="has_unique_answer", capability_status="supported",
        )

    def test_totals_are_orthogonal(self):
        rows = [
            self._row("a", "evidence_insufficient_correct_withheld",
                      "evidence_insufficient_withheld"),
            self._row("b", "false_reject", "verifier"),
            self._row("c", "false_accept", "solver_rule_selection"),
        ]
        cap = _cap(rows)
        assert cap["evidence_insufficient_correct_withheld_total"] == 1
        assert cap["false_reject_total"] == 1
        assert cap["false_accept_total"] == 1

    def test_withheld_not_in_false_reject_total(self):
        rows = [self._row("a", "evidence_insufficient_correct_withheld",
                          "evidence_insufficient_withheld")]
        cap = _cap(rows)
        assert cap["false_reject_total"] == 0  # 扣留绝不并入误杀计数
        assert cap["evidence_insufficient_correct_withheld_total"] == 1


class TestOnlineDecisionIndependence:
    """P1-1：online 决策不得引用 gold 派生的 structural/solver_ready。"""

    def test_vision_stages_runtime_keys_present(self):
        from assessment_agent.evaluation.runner import EvaluationRunner
        import inspect
        src = inspect.getsource(EvaluationRunner._vision_stages)
        for key in ("runtime_layout_consistent", "layout_source",
                    "layout_hypotheses", "layout_ambiguity_reason",
                    "crop_content_integrity", "solver_ready_runtime"):
            assert key in src, key

    def test_runtime_fail_closed_on_recompute_error(self):
        """无 image_path 时 runtime 字段保持 None（不冒充 gold 口径）。"""
        from assessment_agent.evaluation.runner import EvaluationRunner
        stages = EvaluationRunner._vision_stages({}, {}, None, {})
        assert stages["runtime_layout_consistent"] is None
        assert stages["solver_ready_runtime"] is None


def _stages_for_real_case(root, cid, detail_over=None):
    """用真实 dev15 evidence payload 走 _vision_stages（离线、零 API）。"""
    import json
    from pathlib import Path as P

    from assessment_agent.evaluation.runner import EvaluationRunner
    from assessment_agent.schemas import ExtractionResult

    rd = (root / "evidence" / "real_vlm_dev15" / "dev15-20261019T065016Z" / "runs"
          / f"e2e-dev15-20261019T065016Z-{cid}")
    q = json.loads((rd / "question.json").read_text(encoding="utf-8"))
    vision = dict((q.get("meta") or {}).get("vision") or {})
    for d in vision.get("diagram_regions") or []:
        d["crop_path"] = str(rd / "regions" / P(str(d.get("crop_path") or "x")).name)
    for o in vision.get("option_regions") or []:
        o["crop_path"] = str(rd / "regions" / P(str(o.get("crop_path") or "x")).name)
    extraction = ExtractionResult(**vision)
    audit = {"by_phase": {"vision_parse": {"http_succeeded": 1, "json_output_valid": 1}}}
    detail = {"region_detection_failure": False,
              "grid_parse_correct": None, "crops_complete": None}
    detail.update(detail_over or {})
    return EvaluationRunner._vision_stages({}, audit, extraction, detail,
                                           image_path=str(rd / "image" / "original.png"))


class TestRuntimeFieldsRealData:
    """真实 VLM payload 的运行时自检：不读金标即可判定布局是否可用。"""

    def test_rot270_multi_region_runtime_consistent(self, tmp_path=None):
        root = Path(__file__).resolve().parents[1]
        st = _stages_for_real_case(root, "ft-rot270-03")
        assert st["runtime_layout_consistent"] is True
        assert st["layout_source"] == "multi_region_merged"
        assert st["solver_ready_runtime"] is True
        assert st["crop_content_integrity"] is True

    def test_rot180_unresolved_stays_unverified(self):
        root = Path(__file__).resolve().parents[1]
        st = _stages_for_real_case(root, "ft-rot180-02")
        assert st["runtime_layout_consistent"] is False
        assert st["solver_ready_runtime"] is False
        assert st["layout_ambiguity_reason"] == "layout_unverified"

    def test_runtime_independent_of_gold_disagreement(self):
        """评审口径的机器验证：rot90-01 gold 网格比对失败（1×3 vs 1×4）时，
        旧 gold 口径 structural=False，但 runtime_* 纯几何判定**不受污染**仍 True。
        若未来有人把 grid_parse_correct 接回在线决策，此测试即失败。"""
        root = Path(__file__).resolve().parents[1]
        st = _stages_for_real_case(root, "ft-rot90-01",
                                   detail_over={"grid_parse_correct": False})
        assert st["structural_evidence_complete"] is False      # gold 口径（离线）
        assert st["runtime_layout_consistent"] is True          # 几何口径（在线）
        assert st["layout_source"] == "pixel_recount"
