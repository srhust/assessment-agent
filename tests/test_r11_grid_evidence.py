# -*- coding: utf-8 -*-
"""R11-C：DiagramGridEvidence 真实截图契约测试（零 API）。"""
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from assessment_agent.tools.diagram_grid_evidence import (  # noqa: E402
    build_grid_evidence, known_sequence)
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402

FIG = Path(__file__).resolve().parents[1] / "data" / "benchmark_figshots"
import json  # noqa: E402

GOLD = {}
for l in (FIG / "dev" / "figshots.jsonl").read_text(encoding="utf-8").splitlines():
    if l.strip():
        d = json.loads(l)
        GOLD[d["id"]] = d


def _ev(pid):
    p = FIG / "images" / "dev" / f"{pid}.png"
    mask = load_mask(p)
    r = GOLD[pid]["regions"]
    x, y, w, h = r["stem_matrix_bbox"]
    return mask, [x, y, x + w, y + h]


def test_sequence_rot90_01_three_visible_trailing_blank():
    mask, stem = _ev("ft-rot90-01")
    ev = build_grid_evidence(mask, "rot90-01", stem, 1, 4, expects_blank=True)
    assert ev["verification_status"] in ("ok", "override")
    kinds = {(c["row"], c["col"]): c["cell_kind"] for c in ev["cells"]}
    assert kinds[(0, 3)] != "visible"
    vis = sum(1 for c in ev["cells"] if c["cell_kind"] == "visible")
    assert vis == 3  # 视觉桥复核事实：页面确实只有 3 个前提图形，空格无问号
    seq = known_sequence(ev)
    assert seq["mode"] == "sequence" and seq["target_index_rowmajor"] == 3


def test_grid3_blank_at_center_target_not_last():
    mask, stem = _ev("ft-grid-10")
    ev = build_grid_evidence(mask, "grid-10", stem, 3, 3, expects_blank=True)
    seq = known_sequence(ev)
    assert seq["mode"] == "grid3"
    assert seq["target_cell"] == [2, 2]
    assert len(seq["cell_indices"]) == 8
    # 每格像素哈希齐备（内容可核对）；重复图形哈希相同是合法事实，不要求全互异
    shas = [c["cell_pixel_sha"] for c in ev["cells"]]
    assert all(len(s) >= 8 for s in shas)
    assert len(set(shas)) > 1


def test_vlm_selfreport_does_not_decide_grid():
    """VLM 自报 1×2（R10 C 条件实录）不得减少证据格数：仍按像素建 1×4。"""
    mask, stem = _ev("ft-rot90-01")
    ev = build_grid_evidence(mask, "rot90-01", stem, 1, 4, expects_blank=True,
                             vlm_declared={"rows": 1, "cols": 2, "bbox": list(stem)})
    assert ev["grid_rows"] * ev["grid_cols"] == 4
    assert ev["vlm_agreement"]["declared_grid"] == [1, 2]
    assert known_sequence(ev)["mode"] == "sequence"


def test_ambiguous_multi_blank_stays_unsupported():
    mask = np.zeros((120, 300), dtype=np.uint8)
    for i, x in enumerate((20, 110)):   # 3×3 里只放 2 个可见格
        mask[30:90, x:x + 60] = 200
    ev = build_grid_evidence(mask, "synth", [0, 0, 300, 120], 3, 3, expects_blank=True)
    assert len(ev["blank_cells"]) >= 2
    assert known_sequence(ev)["mode"] == "unsupported"


def test_no_layout_bands_honest_abstain():
    from assessment_agent.tools.diagram_grid_evidence import known_sequence
    ev = {"verification_status": "no_layout", "reason": "no_hypothesis"}
    assert known_sequence(ev) == {"mode": "unsupported", "reason": "no_hypothesis"}
