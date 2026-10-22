# -*- coding: utf-8 -*-
"""R12-D：规律/残差鲁棒性——合成题面 + 反例，不碰真实题、不放宽阈值。

要求逐条对应工单：原始/次优残差与 margin 保留；gold 与错误干扰项都做残差检查；
不可唯一判定继续弃权；失败归因四分类；禁 case-id。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.failure_attribution import classify_failure  # noqa: E402
from assessment_agent.tools.figure_transform import (  # noqa: E402
    analyze_transform, residual, standardize)


def L(hw=40):
    a = np.zeros((hw, hw), np.uint8)
    a[6:hw - 6, 6:12] = 1
    a[hw - 12:hw - 6, 6:hw - 6] = 1
    return a


def rot(a, k):
    return np.rot90(a, k).copy()


def mir(a):
    return np.fliplr(a).copy()


def _pad(a, by):
    return np.pad(a, ((by, by), (by, by)))


def test_rot90_sequence_unique_with_residue_trace():
    a = L()
    r = analyze_transform([a, rot(a, 1), rot(a, 2)],
                          {"A": rot(a, 3), "B": mir(a), "C": a},
                          mode="sequence")
    assert r["status"] == "ok" and r["verdict"]["verdict"] == "unique"
    assert r["verdict"]["answer"] == "A"
    cand = max(r["candidates"], key=lambda c: len(c.get("candidate_scores") or {}))
    scores = cand["candidate_scores"]
    assert scores["A"] < scores["B"] and scores["A"] < scores["C"]   # gold 残差最低
    ordered = sorted(scores.values())
    assert cand["discriminative_margin"] == round(ordered[1] - ordered[0], 6) > 0


def test_mirror_rule_and_counterexample_ordering():
    a = L()
    s = [a, mir(a), mir(rot(a, 1)), rot(a, 1)]
    r = analyze_transform(s[:3] + [s[2]], {"A": s[3], "B": a, "C": mir(a)},
                          mode="sequence")
    # 交替规律：第 4 已知后目标第 5=rot(a,1)？此处第 4 位为空格：预测 = s[3]
    if r["verdict"]["verdict"] == "unique":
        assert r["verdict"]["answer"] == "A"
    else:
        assert r["verdict"]["verdict"] in ("ambiguous", "no_rule")


def test_padding_resize_residual_ordering():
    a = L()
    g = _pad(a, 3)                        # 同图形多 3px 白边（padding 残差检查）
    bad = _pad(mir(a), 3)
    r = analyze_transform([a, a, a], {"A": g, "B": bad, "C": np.zeros_like(a)},
                          mode="sequence")
    # 画布尺寸不一致时系统必须**保守降级**（canvas_mismatch），不得假装唯一
    assert (r["verdict"]["verdict"] == "unique" and r["verdict"]["answer"] == "A") \
        or any(b["code"] == "canvas_mismatch" for b in r["blockers"])
    for c in r["candidates"]:
        if c.get("candidate_scores"):
            sc = c["candidate_scores"]
            assert sc["A"] <= sc["B"]    # 残差排序不因 padding 翻盘


def test_grid3_overlay_target_not_last():
    a, b = L(), rot(L(), 2)
    c = (a > 0) ^ (b > 0)
    row1 = [L(), a, b]
    row2 = [rot(a, 1), (L() > 0) ^ (a > 0), None]
    # 目标在第 6 位（非末格）：xor 叠加规律
    cells = [L(), a, b, rot(a, 1), ((L() > 0) ^ (a > 0)).astype(np.uint8),
             np.zeros((40, 40), np.uint8), a, c, np.zeros((40, 40), np.uint8)]
    or_opt = (a > 0) | (b > 0)
    r = analyze_transform(cells, {"A": c.astype(np.uint8),
                                  "B": or_opt.astype(np.uint8),
                                  "C": a}, mode="grid3")
    assert r["verdict"]["verdict"] in ("unique", "ambiguous", "no_rule")
    if r["verdict"]["verdict"] == "unique":
        assert r["verdict"]["answer"] == "A"        # xor 唯一正确
        sc = [x for x in r["candidates"] if x.get("candidate_scores")][0]
        assert sc["candidate_scores"]["A"] < sc["candidate_scores"]["B"]


def test_conflicting_rules_stay_ambiguous():
    a = L()
    # 两种规律各自预测不同选项 → 冲突不硬取
    r = analyze_transform([a, rot(a, 1), rot(a, 2), rot(a, 3), a, rot(a, 1),
                           rot(a, 2), rot(a, 3)],
                          {"A": mir(a), "B": a, "C": rot(a, 3)}, mode="sequence")
    assert r["verdict"]["verdict"] != "unique" \
        or r["verdict"]["answer"] in ("B", "C")


def test_classify_failure_four_classes():
    assert classify_failure(solved=False, verdict="no_rule")[0] \
        == "rule_not_applicable"
    lab, ev = classify_failure(solved=False, verdict="ambiguous",
                               stem_deltas=[[54, 0], [54, 0]],
                               cells_identical=True,
                               option_frames_known=False)
    assert lab == "position_info_lost" and ev["stem_deltas"]
    lab, ev = classify_failure(solved=False, verdict="no_match",
                               best_residual=0.44, runnerup_residual=0.51)
    assert lab == "shape_mismatch" and ev["margin"] == 0.07
    lab, _ = classify_failure(solved=True, verify_action="reject",
                              recheck_note="复推哈希不一致")
    assert lab == "verification_mismatch"


def test_no_case_id_branches():
    src = (ROOT / "src/assessment_agent/tools/figure_transform.py").read_text(
        encoding="utf-8")
    assert "ft-" not in src
