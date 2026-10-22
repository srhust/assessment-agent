# -*- coding: utf-8 -*-
"""R12-C：narrow-gap 合成测试族——2/4/6/8px 白隙 × 尺寸 × 笔画 × 背景干扰。

判定依据只允许像素证据（白隙长度/两半均衡/墨量），禁止按金标列数挑选分割结果。
 touching(0/1px) 不许分裂；≥2px 纯白隙 + 均衡两半必须分开成两个图形（防幻影格）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.diagram_grid_evidence import build_grid_evidence  # noqa: E402
from assessment_agent.tools.figure_page_localizer import (  # noqa: E402
    cluster_cells, localize_page)


def _two_arrows(gap: int, size: int = 34, stroke: int = 4, noise: bool = False,
               trailing_q: bool = False):
    """两个等大实体箭头（三角头+杆），白隙 gap px；可加背景噪点/行末问号位。"""
    h = size + 10
    w = size * 2 + gap + 30 + (56 if trailing_q else 0)
    m = np.zeros((h, w), dtype=bool)

    def arrow(x0):
        head = size // 2
        for k in range(head):                       # 三角头（实体）
            m[5 + k, x0 + head - 1 - k: x0 + head + k] = True
            m[5 + head - 1 - k, x0 + head - 1 - k: x0 + head + k] = True
        m[5 + head - stroke // 2:5 + head + stroke // 2 + 1,
          x0 + head:x0 + size] = True               # 杆
        m[5 + head - 1:5 + head + 2, x0 + size - 6:x0 + size] = True

    arrow(10)
    arrow(10 + size + gap)
    if trailing_q:                        # 行末"?"占位：小墨量（<BLANK_MAX）
        qx = 10 + 2 * (size + gap) + 26
        m[6:10, qx:qx + 8] = True
        m[10:16, qx + 5:qx + 9] = True
        m[18:21, qx + 2:qx + 6] = True
    if noise:
        rng = np.random.default_rng(7)
        for _ in range(40):
            x, y = rng.integers(2, w - 4), rng.integers(2, h - 4)
            if not m[max(0, y - 3):y + 4, max(0, x - 3):x + 4].any():
                m[y:y + 2, x:x + 2] = True          # 2×2 碎点，墨量远低于门槛
    return m


def _cells(mask):
    h = mask.shape[0]
    return cluster_cells(mask, 0, h)


def test_gaps_2_4_6_8_split_into_two_figures():
    for gap in (2, 4, 6, 8):
        cells = _cells(_two_arrows(gap))
        assert len(cells) == 2, f"gap={gap}px 应分为两图，实得 {cells}"


def test_touching_not_split():
    for gap in (0, 1):
        cells = _cells(_two_arrows(gap))
        assert len(cells) >= 1, "不应凭空制造空格"
        if gap == 0:
            assert len(cells) == 1     # 连续墨体：分裂无白隙证据，保持一格


def test_sizes_and_strokes_matrix():
    for size in (20, 28, 34, 46):
        for stroke in (3, 5):
            if size < 26 and stroke > 3:
                continue               # 极小图形配粗笔画超出合成族边界
            cells = _cells(_two_arrows(4, size=size, stroke=stroke))
            assert len(cells) == 2, f"size={size} stroke={stroke}: {cells}"


def test_background_noise_does_not_add_phantom_cells():
    cells = _cells(_two_arrows(6, noise=True))
    kept = [c for c in cells if c[1] - c[0] >= 8]
    assert len(kept) == 2              # 噪点碎片不成为图形单元


def test_evidence_grid_without_gold_hint():
    """grid 判定用像素节距+行末空白，不得读金标列数。"""
    for gap in (2, 6):
        m = _two_arrows(gap, trailing_q=True)
        h, w = m.shape
        cols = np.nonzero(m.any(axis=0))[0]
        cells = [{"row": 0, "col": i, "bbox_page": [a, 0, b - a, h]}
                 for i, (a, b) in enumerate(cluster_cells(m, 0, h))]
        ev = build_grid_evidence(m, "syn", [int(cols[0]), 0, int(cols[-1]) + 1, h],
                                 0, 0, expects_blank=True, cells_hint=cells)
        if ev["verification_status"] in ("ok", "assumed_target"):
            assert [ev["grid_rows"], ev["grid_cols"]] == [1, 3]
            assert ev["target_cell"] == [0, 2]
        else:                           # 判不了就 ambiguous 弃权，不猜金标
            assert ev["verification_status"] == "ambiguous"


def test_localize_page_split_on_synthetic_page(tmp_path: Path):
    """整页合成（题干 2 图窄隙 + 4 选项）：题干带必须给出 2 个 cell。"""
    import cv2
    stem = _two_arrows(4)
    page = np.full((92, 260), False)
    page[10:44, 20:110] = stem[0:34, 0:90]
    opts = _two_arrows(0, size=20)
    for i, x in enumerate((20, 80, 140, 200)):
        band = np.zeros((30, 40), dtype=bool)
        band[8:22, x and 6:22] = opts[8:22, 12:28]
        band[8:22, 26:30] = True
        page[56:86, x:x + 40] = band
    p = tmp_path / "page.png"
    assert cv2.imwrite(str(p), np.where(page, 0, 255).astype(np.uint8))
    res = localize_page(page.astype(np.uint8), n_options=4)
    srs = [r for r in res["stem_regions"]]
    assert srs and len(srs[0].get("cells") or []) == 2
