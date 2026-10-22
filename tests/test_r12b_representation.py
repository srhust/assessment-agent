# -*- coding: utf-8 -*-
"""R12-B：双重表示保真测试——新排版、新形状（不只在原题上调实现）。

Shape-normalized 允许居中（形状族），Position-preserving 必须逐像素等于原页面
裁切（平移/位置族），两表示由同一证据产生且哈希分别记录、分别独立复推。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.diagram_grid_evidence import (  # noqa: E402
    build_grid_evidence, frame_boxes, render_canvas)

CELL = 24
SHIFT = 3          # 渲染器：图形每步在格内右移 SHIFT → 墨迹绝对步长 CELL+SHIFT
STEP = CELL + SHIFT


def _synthetic_translate(rows: int = 1, cols: int = 4, shape: str = "square"):
    """新排版新形状：1×4 与 2×3 都测；方块/三角墨迹，非 L 形族。"""
    w = cols * STEP
    h = rows * (CELL + 10)
    m = np.zeros((h, w), dtype=bool)
    truth = []
    for r in range(rows):
        for c in range(cols):
            if (r, c) == (rows - 1, cols - 1):
                continue                      # 末格空白
            x0 = r * 2 + c * STEP             # 绝对起点（格内偏移逐格 +SHIFT）
            y0 = 4 + r * (CELL + 10) + 6
            if shape == "square":
                m[y0:y0 + 12, x0:x0 + 12] = True
            else:  # 三角（新形状）
                for k in range(10):
                    m[y0 + k, x0:x0 + (2 * k + 1)] = True
            truth.append((r, c, x0, y0))
    return m, truth


def _evidence(m):
    h, w = m.shape
    return build_grid_evidence(m, "syn", [0, 0, w, h], 0, 0, expects_blank=True)


def test_ink_absolute_boxes_and_deltas_preserved():
    m, truth = _synthetic_translate()
    ev = _evidence(m)
    vis = [c for c in ev["cells"] if c["cell_kind"] == "visible"]
    assert [c["ink_bbox_page"][0] for c in vis] == [t[2] for t in truth]
    assert ev["ink_deltas"] == [[STEP, 0], [STEP, 0]]


def test_position_canvas_is_pixel_exact_page_crop():
    m, _ = _synthetic_translate()
    ev = _evidence(m)
    pos = render_canvas(m, ev, mode="position")
    cs = ev["cells"]
    bx0 = min(c["cell_bbox_page"][0] for c in cs)
    by0 = min(c["cell_bbox_page"][1] for c in cs)
    bx1 = max(c["cell_bbox_page"][2] for c in cs)
    by1 = max(c["cell_bbox_page"][3] for c in cs)
    assert np.array_equal(pos, m[by0:by1, bx0:bx1])   # 未做任何平移/居中


def test_shape_canvas_still_centers_for_shape_family():
    m, _ = _synthetic_translate()
    ev = _evidence(m)
    shape = render_canvas(m, ev, mode="shape")
    # 统一 padding-cell 网格：每格尺寸相同、墨迹位于格中心区
    cell_h = shape.shape[0] // ev["grid_rows"]
    for c in [c for c in ev["cells"] if c["cell_kind"] == "visible"]:
        r, col = c["row"], c["col"]
        sub = shape[r * cell_h:(r + 1) * cell_h,
                    col * (shape.shape[1] // ev["grid_cols"]):
                    (col + 1) * (shape.shape[1] // ev["grid_cols"])]
        nz = sub.nonzero()
        cx = (nz[1].min() + nz[1].max()) / 2
        assert abs(cx - sub.shape[1] / 2) <= 1


def test_two_row_new_layout_triangle_fidelity():
    m, truth = _synthetic_translate(rows=2, cols=3, shape="triangle")
    ev = build_grid_evidence(m, "syn", [0, 0, *m.shape[::-1]], 2, 3,
                             expects_blank=True)
    assert [ev["grid_rows"], ev["grid_cols"]] == [2, 3]
    vis = [c for c in ev["cells"] if c["cell_kind"] == "visible"]
    tb = {(t[0], t[1]): (t[2], t[3]) for t in truth}
    for c in vis:
        assert (c["ink_bbox_page"][0], c["ink_bbox_page"][1]) == tb[(c["row"], c["col"])]
    assert ev["target_cell"] == [1, 2]


def test_frame_boxes_gap_midpoints():
    frames = frame_boxes([0, 0, 100, 20], [[10, 4, 20, 16], [30, 4, 44, 16],
                                           [70, 4, 80, 16]])
    assert frames == [[0, 0, 25, 20], [25, 0, 57, 20], [57, 0, 100, 20]]


def test_wiring_records_dual_hashes_and_deltas(tmp_path: Path):
    sys.path.insert(0, str(ROOT))
    import tempfile
    import tests.test_r10d_wiring as T
    from assessment_agent.skills import SkillContext, SkillRegistry
    from tests.conftest import make_settings
    q, s = T._question("ft-rot90-01")
    q.meta["vision"]["stem_matrix_crop"] = "does_not_exist.png"
    q.meta["vision"]["option_regions"] = []
    sol = SkillRegistry().require("figure_reasoning").solve(
        q, SkillContext(settings=make_settings(Path(tempfile.mkdtemp()))))
    sev = sol.meta["region_provenance"]["stem_evidence"]
    assert sev["canvas_position_sha"] and sev["canvas_sha"]
    assert sev["consumed_representation"] == "shape_normalized"
    assert len(sev["ink_deltas"]) == 2          # 3 可见格 → 2 个位移
    assert all(d[0] > 0 for d in sev["ink_deltas"])
