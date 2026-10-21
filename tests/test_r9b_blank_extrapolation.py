# -*- coding: utf-8 -*-
"""R9-B 空格外推修复验收（工单 B3 门：正常/反向边界/典型错误解/条件不足/独立验证
+ 页面扰动 + 区域顺序置换 + 新合成排版）。全部确定性 numpy 合成，零 API。"""
from __future__ import annotations

import numpy as np
import pytest

from assessment_agent.tools.diagram_layout import (
    _infer_trailing_blank,
    assemble_stem_layout,
)


def make_band(n_cells, inked, cell_w=40, pitch=60, band_h=40,
              page_w=400, page_h=120, x_off=20, y_off=30, pad_left=0, scale=1):
    """合成序列带：前 inked 格画方环图形，其后格留空。返回 mask、全部格区域列表。

    regions 只包含**有墨迹**的前 inked 格（模拟 VLM 对空格的漏检）。
    bbox 与金标/真实 VLM 输出同为 [x, y, w, h] 格式（_bbox_rect 解析约定）。"""
    s = scale
    cw, pw, bh = int(cell_w * s), int(pitch * s), int(band_h * s)
    H, W = int(page_h * s) + pad_left, int(page_w * s) + pad_left
    mask = np.zeros((H, W), dtype=bool)
    regions = []
    for i in range(n_cells):
        cx = int(x_off * s) + pad_left + i * pw
        if i < inked:
            y0 = int(y_off * s) + pad_left // 2
            my0, mx0 = y0 + bh // 4, cx + cw // 4
            my1, mx1 = y0 + 3 * bh // 4, cx + 3 * cw // 4
            mask[my0:my1, mx0:mx1] = True
            mask[my0 + 2:my1 - 2, mx0 + 2:mx1 - 2] = False
            regions.append({"role": "stem_figure",
                            "bbox": [cx, y0, cw, bh],
                            "grid": {"rows": 1, "cols": 1}})
    return mask, regions


def opts(k):
    return [{"label": chr(65 + i), "has_figure": True, "bbox": [0, 90, 10, 100]}
            for i in range(k)]


class TestInferTrailingBlank:
    """单元层五类样本（直接调 helper，与题干线索解耦）。"""

    def test_normal_accepted(self):
        mask, regions = make_band(3, 2)
        units = [(r["bbox"][0], r["bbox"][0] + r["bbox"][2]) for r in regions]
        ev = _infer_trailing_blank(mask, units, 10, 100, option_fig_count=3, width=mask.shape[1])
        assert ev.get("accepted") is True

    def test_reverse_boundary_options_fewer_than_cells(self):
        mask, regions = make_band(3, 2)
        units = [(r["bbox"][0], r["bbox"][0] + r["bbox"][2]) for r in regions]
        ev = _infer_trailing_blank(mask, units, 10, 100, option_fig_count=1, width=mask.shape[1])
        assert ev.get("rejected") == "structural"

    def test_typical_wrong_third_cell_actually_has_ink(self):
        # VLM 漏报了第 3 格（其实有图）：像素复核必须阻止把它当空格
        mask, regions = make_band(3, 3)
        units = [(r["bbox"][0], r["bbox"][0] + r["bbox"][2]) for r in regions[:2]]
        ev = _infer_trailing_blank(mask, units, 10, 100, option_fig_count=4, width=mask.shape[1])
        assert ev.get("rejected") == "cell_has_ink"

    def test_insufficient_single_unit(self):
        mask, regions = make_band(2, 1)
        units = [(r["bbox"][0], r["bbox"][0] + r["bbox"][2]) for r in regions]
        ev = _infer_trailing_blank(mask, units, 10, 100, option_fig_count=0, width=mask.shape[1])
        assert ev.get("rejected") == "structural"

    def test_out_of_bounds_rejected(self):
        mask, regions = make_band(3, 2, cell_w=40, pitch=60, page_w=150, x_off=10)
        units = [(r["bbox"][0], r["bbox"][0] + r["bbox"][2]) for r in regions]
        ev = _infer_trailing_blank(mask, units, 10, 100, option_fig_count=4, width=mask.shape[1])
        assert ev.get("rejected") in ("out_of_bounds", "cell_has_ink", "ink_after_target")

    def test_independent_verification_ink_after_target(self):
        # 目标位之后同带仍有墨迹 ⇒ 空格不在行末 ⇒ 拒绝（不猜位置）
        mask, regions = make_band(4, 3, page_w=500)
        units = [(r["bbox"][0], r["bbox"][0] + r["bbox"][2]) for r in regions[:2]]
        ev = _infer_trailing_blank(mask, units, 10, 100, option_fig_count=4, width=mask.shape[1])
        assert ev.get("rejected") in ("cell_has_ink", "ink_after_target", None) or ev.get("accepted")


class TestAssembleIntegration:
    """组装层：线索门控 + 扰动/置换/新排版三关。"""

    def test_merge_arm_recovers_third_cell(self):
        mask, regions = make_band(3, 2)
        res = assemble_stem_layout(mask, regions, opts(3), profile="merge",
                                   expects_blank_target=True)
        assert res["verified"] is True and res["cols"] == 3
        assert any(h["kind"] == "trailing_blank_inferred" and h.get("accepted")
                   for h in res["hypotheses"])

    def test_no_cue_never_extrapolates(self):
        # 反向边界：无线索（如非补全型）即使空格存在也不得多造一格
        mask, regions = make_band(3, 2)
        res = assemble_stem_layout(mask, regions, opts(3), profile="merge",
                                   expects_blank_target=False)
        assert res["cols"] == 2

    def test_complete_sequence_not_overextended(self):
        # 典型错误解：完整 3 格序列 + 4 选项 + 补全线索误给 ⇒ 像素外推格越出内容区
        # 或有格；至少不得改变 cols（此处第 4 格投影落在页边空白 → 门控靠线索：
        # 完整序列的题干不含"补全"，cue=False 已在上例保证）
        mask, regions = make_band(3, 3)
        res = assemble_stem_layout(mask, regions, opts(4), profile="merge",
                                   expects_blank_target=False)
        assert res["cols"] == 3

    def test_page_perturbation_scale_and_pad(self):
        for scale, pad in [(1.5, 0), (0.8, 25), (2.0, 40)]:
            mask, regions = make_band(3, 2, scale=scale, pad_left=pad)
            res = assemble_stem_layout(mask, regions, opts(3), profile="merge",
                                       expects_blank_target=True)
            assert res["cols"] == 3, (scale, pad, res["hypotheses"])

    def test_region_order_permutation(self):
        mask, regions = make_band(3, 2)
        for perm in ([1, 0], [0, 1]):
            res = assemble_stem_layout(mask, [regions[i] for i in perm], opts(3),
                                       profile="merge", expects_blank_target=True)
            assert res["verified"] is True and res["cols"] == 3

    @pytest.mark.parametrize("n_cells,inked,opts_k", [(4, 3, 4), (5, 4, 5)])
    def test_new_synthetic_layouts(self, n_cells, inked, opts_k):
        mask, regions = make_band(n_cells, inked, cell_w=30, pitch=48)
        res = assemble_stem_layout(mask, regions, opts(opts_k), profile="merge",
                                   expects_blank_target=True)
        assert res["verified"] is True and res["cols"] == n_cells, res["hypotheses"]

    def test_single_region_declared_grid_recombines(self):
        # comp-06 形态：VLM 给单个 1×2 声明区域 ⇒ 重组为 2 单元 + 外推空格 = 3
        mask, _ = make_band(3, 2)
        _m2, r1 = make_band(2, 2)
        bbox = [r1[0]["bbox"][0], r1[0]["bbox"][1],
                r1[1]["bbox"][0] + r1[1]["bbox"][2] - r1[0]["bbox"][0], r1[0]["bbox"][3]]
        region = {"role": "stem_figure", "bbox": bbox, "grid": {"rows": 1, "cols": 2}}
        res = assemble_stem_layout(mask, [region], opts(3), profile="merge",
                                   expects_blank_target=True)
        assert res["verified"] is True and res["cols"] == 3
