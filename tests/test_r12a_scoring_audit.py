# -*- coding: utf-8 -*-
"""R12-A 回归：三层释放计分、真实 HTTP 审计口径、实验脚本不被口径漂移击穿。

对应人工裁决：solved+reject 计成释放是 P0 计分缺陷；预算/超时审计必须来自
真实 HTTP 层计数；image_only 实验不得经由文本路径跳过 VisionParser。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.tools.release_scoring import (  # noqa: E402
    audit_network_timeouts, audit_requests_sent, row_layer, summarize_rows)


def _row(pid, status, answer, gold, verify, final=None, ab="solved", design=False):
    rec = {"status": status, "answer": answer, "verify": verify}
    if final is not None:
        rec["final_answer"] = final
    return {"id": pid, "gold": gold, "answerability": ab,
            "design_abstain": design, "conds": {"X": rec}}


def test_reject_and_unverified_are_not_releases():
    rows = [
        _row("ok", "solved", "A", "A", "accept", final="A"),
        _row("rej", "solved", "B", "B", "reject"),          # mirror-05 情形
        _row("unv", "solved", "A", "A", "unverified"),      # mirror-04-F 情形
        _row("wrong", "solved", "C", "A", "accept", final="C"),
        _row("nosolve", "failed", None, "A", None),
    ]
    m = summarize_rows(rows, "X")
    assert m["final_released"] == 1
    assert m["lists"]["released"] == ["ok"]
    assert "rej" in m["lists"]["correct_candidates"] + m["gated_rejects"]
    assert m["gated_rejects"] == ["rej"] and m["gated_unverified"] == ["unv"]
    assert m["wrong_releases"] == ["wrong"]


def test_row_layer_gradation():
    assert row_layer({"status": "solved", "answer": "A", "gold": "A",
                      "verify": "reject"}) == 1        # 正确候选未接受
    assert row_layer({"status": "solved", "answer": "A", "gold": "A",
                      "verify": "accept", "final_answer": "A"}) == 3
    assert row_layer({"status": "solved", "answer": "A", "gold": "A",
                      "verify": "accept", "final_answer": None}) == 1
    assert row_layer({"status": "failed"}) == 0


def test_design_abstain_rows_never_count_as_released_gap():
    rows = [_row("sym", "failed", None, "A", None, ab="solved", design=True),
            _row("conf", "solved", "A", "A", "accept", final="A", design=True)]
    m = summarize_rows(rows, "X")
    assert m["effective_answerable_n"] == 0          # 设计性题不占研发缺口分母
    assert m["design_false_accepts"] == ["conf"]     # 但错误放行仍被点名
    assert m["design_abstain_ok"] == ["sym"]


def test_audit_helpers_read_real_http_counters():
    snap = {"requests_sent": 6, "requests_succeeded": 5,
            "error_types": {"ReadTimeout": 1},
            "by_phase": {"vision_parse": {"http_succeeded": 5}}}
    assert audit_requests_sent(snap) == 6
    assert audit_network_timeouts(snap) == 1
    # R11 缺陷回归：旧代码从 by_phase 读 calls/requests（不存在）恒为 0
    assert "calls" not in snap["by_phase"]["vision_parse"]


def test_g1_script_uses_pure_image_only_and_real_budget():
    src = (ROOT / "scripts/g1_r11_real_vision.py").read_text(encoding="utf-8")
    assert "text=None" in src and "extra_text" not in src
    assert "audit_requests_sent(snap)" in src
    assert "network_timeouts_http_layer" in src


def test_d1_default_output_does_not_overwrite_r11_original():
    src = (ROOT / "scripts/d1_r11_six_condition.py").read_text(encoding="utf-8")
    assert "R12A_SIX_CONDITION_RESCORED.json" in src
    import re
    assert not re.search(r'"evidence/R11_D_SIX_CONDITION\.json"\)\.write_text', src)
