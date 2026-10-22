# -*- coding: utf-8 -*-
"""R13-A4 安全门回归：错误/假设布局不得以可释放状态向后传递（零 API）。

覆盖人工裁决点：
  1) 三反例（overlay-13--vertical / grid-10--tight / overlay-13--distractor）非释放资格；
  2) 30 页扰动集中"尺寸≠金标却 release_eligible"必须为 0；
  3) 原生 15 页尺寸 15/15 且除 overlay-13（目标未定位→诚实 ambiguous）外全可释放；
  4) row_layer 严格 Verifier final_answer（缺字段/空值 → 停留第 1 层并进
     release_evidence_missing 桶）；
  5) 复核释放资格门：sev.status 非 ok → (False, 不对外作答)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from assessment_agent.tools.diagram_grid_evidence import (  # noqa: E402
    build_grid_evidence, merge_stem_regions)
from assessment_agent.tools.figure_page_localizer import localize_page  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402
from assessment_agent.tools.release_scoring import (  # noqa: E402
    row_layer, summarize_rows)

ITEMS = {json.loads(l)["id"]: json.loads(l)
         for l in (ROOT / "data/benchmark_figshots/dev/figshots.jsonl")
         .read_text(encoding="utf-8").splitlines() if l.strip()}


def ev_for(mask, item, n_opts):
    srs = merge_stem_regions(localize_page(mask, n_options=n_opts)["stem_regions"])
    if not srs or not srs[0].get("cells"):
        return None
    sr = srs[0]
    x, y, w, h = sr["bbox_page"]
    return build_grid_evidence(mask, "t", [x, y, x + w, y + h], 0, 0,
                               expects_blank=True, cells_hint=sr["cells"],
                               rows_hint=int(sr.get("sub_band_count") or 1),
                               stem_continuation_suspect=bool(
                                   sr.get("continuation_suspect")))


def test_three_counterexamples_not_releasable():
    for pid in ("ft-overlay-13--vertical", "ft-grid-10--tight",
                "ft-overlay-13--distractor"):
        src = ITEMS[pid.split("--")[0]]
        m = load_mask(str(ROOT / "data/benchmark_figshots_perturb/images"
                          / f"{pid}.png"))
        ev = ev_for(m, src, len(src["options"]))
        assert ev is not None, pid
        assert ev["release_eligible"] is False, (pid, ev["verification_status"])
        assert ev["verification_status"] in ("ambiguous", "assumed_target"), pid


def test_perturb30_no_wrong_grid_is_releasable():
    pert = json.loads((ROOT / "evidence/R10D_PERTURB_CORPUS.json")
                      .read_text(encoding="utf-8"))["items"]
    violations = []
    for it in pert:
        src = ITEMS.get(it["source_item"])
        if not src:
            continue
        m = load_mask(str(ROOT / "data" / it["image"].replace("\\", "/")))
        ev = ev_for(m, src, len(src["options"]))
        if ev is None:
            continue
        size = [ev.get("grid_rows"), ev.get("grid_cols")]
        gold = list(src["regions"].get("stem_matrix_grid") or [])
        if size != gold and ev.get("release_eligible"):
            violations.append((it["id"], size, gold))
    assert violations == [], violations


def test_native15_size_and_only_overlay13_not_eligible():
    nonelig = []
    for pid, item in ITEMS.items():
        m = load_mask(str(ROOT / "data/benchmark_figshots/images/dev"
                          / f"{pid}.png"))
        ev = ev_for(m, item, len(item["options"]))
        assert ev is not None, pid
        assert [ev.get("grid_rows"), ev.get("grid_cols")] == \
            list(item["regions"]["stem_matrix_grid"]), pid
        if not ev.get("release_eligible"):
            nonelig.append(pid)
    assert nonelig == ["ft-overlay-13"], nonelig


def test_row_layer_requires_verifier_final_answer():
    base = {"status": "solved", "answer": "A", "gold": "A", "verify": "accept"}
    assert row_layer(dict(base)) == 1                 # 缺 final_answer：不得回退
    assert row_layer({**base, "final_answer": None}) == 1
    assert row_layer({**base, "final_answer": ""}) == 1
    assert row_layer({**base, "final_answer": "A"}) == 3
    m = summarize_rows([{"id": "x", "answerability": "solved", "gold": "A",
                         "conds": {"E": base}}], "E")
    assert m["final_released"] == 0
    assert m["release_evidence_missing"] == ["x"]


def test_recheck_release_gate_on_assumed_status():
    from assessment_agent.skills.figure_reasoning import FigureReasoningSkill
    import tempfile
    pid = "ft-mirror-04"
    item = ITEMS[pid]
    with tempfile.TemporaryDirectory() as td:
        mask = load_mask(str(ROOT / "data/benchmark_figshots/images/dev"
                             / f"{pid}.png"))
        res = localize_page(mask, n_options=len(item["options"]))
        prov = {
            "image": str(ROOT / "data/benchmark_figshots/images/dev"
                         / f"{pid}.png"),
            "n_options": len(item["options"]),
            "stem_crop_source": "pixel_grid_evidence",
            "option_cells": [{"pixel_sha": c["pixel_sha"],
                              "completeness": c.get("completeness", {})}
                             for c in res["option_regions"]],
            "stem_evidence": {"grid": [1, 3], "target_cell": [0, 2],
                              "target_basis": "no_empty_assume_row_end",
                              "status": "assumed_target",
                              "canvas_sha": "deadbeef", "canvas_position_sha": ""},
        }
        from assessment_agent.schemas import Question
        q = Question(stem=item["stem"], options=[],
                     image_path=prov["image"], source="image")
        skill = FigureReasoningSkill()
        ok, why = skill._recheck_region_provenance(q, prov)
        assert ok is False and "不对外作答" in why
