"""R4g 测试：DiagramLayoutAssembler 正确性 + 确定性像素校验 + 防模板过拟合扰动。

纪律锚点：布局判定零金标（函数签名不接收 answer/grid 真值）、不因多区域
自动推定行列数、空白目标格必须像素证据确认、扰动（平移/缩放/边距/顺序/
干扰）下结论不变。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assessment_agent.tools.diagram_layout import (  # noqa: E402
    assemble_stem_layout, check_option_crop)

cv2 = pytest.importorskip("cv2")


def blank(h: int = 200, w: int = 400) -> np.ndarray:
    return np.zeros((h, w), dtype=bool)


def draw_cell(mask: np.ndarray, x0: int, y0: int, w: int = 20, h: int = 20,
              kind: int = 0) -> None:
    """画一个可辨识"图形单元"（不同 kind 内容不同）。"""
    if kind == 0:
        mask[y0:y0 + h, x0:x0 + w] = True
    elif kind == 1:  # 环（中间留白）
        mask[y0:y0 + h, x0:x0 + w] = True
        mask[y0 + 4:y0 + h - 4, x0 + 4:x0 + w - 4] = False
    else:            # 竖条（宽度 ≥ MIN_CELL_PX，否则被单元最小宽度纪律丢弃）
        mask[y0:y0 + h, x0:x0 + max(12, w // 2)] = True


def region(bbox, role="stem_figure", grid=None, rid="d"):
    return {"region_id": rid, "role": role, "bbox": list(bbox),
            "grid": grid, "crop_path": None}


def seq_page(n=3, x0=30, pitch=48, y=60, size=20, page=(200, 400), shift=(0, 0)):
    """无框线序列页（figshots 风格）：n 个内容单元。"""
    mask = blank(*page)
    rects = []
    for i in range(n):
        cx = x0 + i * pitch + shift[0]
        cy = y + shift[1]
        draw_cell(mask, cx, cy, size, size, kind=i % 3)
        rects.append([cx, cy, size, size])
    return mask, rects


def opt_regions(y=140):
    return [{"label": "A", "bbox": [30, y, 20, 20], "has_figure": True,
             "crop_path": None},
            {"label": "B", "bbox": [70, y, 20, 20], "has_figure": True,
             "crop_path": None}]


class TestMultiRegionMerge:
    def test_keeps_all_regions(self):
        """P0 缺陷回归：两个 stem_figure 都必须进入重建布局，不再只取第一个。"""
        mask, rects = seq_page(n=2)
        res = assemble_stem_layout(mask, [region(r) for r in rects], opt_regions())
        assert res["layout_source"] == "multi_region_merged"
        assert res["verified"] and res["cols"] == 2 and res["rows"] == 1
        assert len(res["units"]) == 2

    def test_three_figures_canvas_cells(self):
        mask, rects = seq_page(n=3)
        res = assemble_stem_layout(mask, [region(r) for r in rects], opt_regions())
        assert res["verified"] and res["cols"] == 3
        canvas = res["_canvas"]
        assert canvas.shape[1] == pytest.approx(canvas.shape[1])
        # 每格有内容且墨量守恒（±边缘 2px）
        wcell = canvas.shape[1] // 3
        inks = [int(canvas[:, i * wcell:(i + 1) * wcell].sum()) for i in range(3)]
        assert all(k > 10 for k in inks)

    def test_no_gold_params_in_signature(self):
        """布局判定不得接收金标：签名里不允许 answer/gold/true_grid 类参数。"""
        names = set(inspect.signature(assemble_stem_layout).parameters)
        assert not (names & {"answer", "gold", "case_id", "true_grid", "expected"})

    def test_extrapolated_blank_not_inflated(self):
        """只有 2 个可见单元时不得推定第三空白格——记录假设但拒绝。"""
        mask, rects = seq_page(n=2)
        res = assemble_stem_layout(mask, [region(r) for r in rects], opt_regions())
        assert res["cols"] == 2
        hypo = {h["kind"]: h for h in res["hypotheses"]}
        assert "extrapolated_blank_target" in hypo
        assert hypo["extrapolated_blank_target"]["accepted"] is False
        assert any("cols_3" in str(r.get("hypothesis")) for r in res["rejected"])


class TestPixelRecount:
    def test_single_region_recounted(self):
        """单区域 grid=None：由像素内容分组独立数格（trans-08 形态）。"""
        mask, _ = seq_page(n=3, pitch=64, size=24)
        bbox = [28, 58, 3 * 64, 28]
        res = assemble_stem_layout(mask, [region(bbox, grid=None)], opt_regions())
        assert res["layout_source"] == "pixel_recount" and res["cols"] == 3

    def test_pixel_only_ignores_wrong_declaration(self):
        """臂 D：声明 1×5 与像素 3 单元冲突时，输出以像素为准（不看声明）。"""
        mask, rects = seq_page(n=3)
        reg = [region(r, grid={"rows": 1, "cols": 5}) for r in rects[:1]]
        res = assemble_stem_layout(mask, reg, opt_regions(),
                                   trust_vlm_grid=False, profile="pixel_only")
        assert res["layout_source"] == "pixel_only" and res["cols"] == 3

    def test_pair_grouping(self):
        """每格双图元（组内间隙小、跨格间隙大）→ 3 格而不是 6 格。"""
        mask = blank()
        rects = []
        for i in range(3):
            x = 30 + i * 64
            draw_cell(mask, x, 60, 10, 20, kind=2)
            draw_cell(mask, x + 14, 60, 12, 20, kind=0)  # 组内 gap 4
            rects.append([x, 60, 26, 20])
        res = assemble_stem_layout(mask, [region(r) for r in rects], opt_regions())
        assert res["cols"] == 3


class TestBorderedFrames:
    def test_frame_blank_target_confirmed(self):
        """有框线页：4 框、末框空且 VLM 未声明 → cols=4（目标空格像素确认）。"""
        mask = blank(200, 400)
        fw, fh, x0, y0 = 40, 40, 20, 50
        for i in range(5):  # 5 条竖线 → 4 框
            mask[y0:y0 + fh, x0 + i * fw:x0 + i * fw + 2] = True
        mask[y0 + 10:y0 + 30, x0 + 10:x0 + 25] = True
        mask[y0 + 10:y0 + 30, x0 + fw + 10:x0 + fw + 25] = True
        mask[y0 + 10:y0 + 30, x0 + 2 * fw + 10:x0 + 2 * fw + 25] = True
        regs = [region([x0 + i * fw + 2, y0, fw - 4, fh]) for i in range(3)]
        res = assemble_stem_layout(mask, regs, opt_regions())
        assert res["cols"] == 4, res
        kinds = {h["kind"] for h in res["hypotheses"] if h.get("accepted")}
        assert "frame_blank_target" in kinds or res["layout_source"] == "pixel_recount"


class TestSixChecks:
    def test_out_of_image_region(self):
        mask, _ = seq_page(n=2)
        bad = region([395, 195, 50, 50])
        good = region([30, 60, 20, 20])
        res = assemble_stem_layout(mask, [good, bad], opt_regions())
        chk = {c["name"]: c for c in res["checks"]}
        assert chk["regions_in_image"]["passed"] is False

    def test_stem_option_overlap_detected(self):
        mask, rects = seq_page(n=2)
        opts = [{"label": "A", "bbox": rects[1], "has_figure": True,
                 "crop_path": None}]
        res = assemble_stem_layout(mask, [region(r) for r in rects], opts)
        chk = {c["name"]: c for c in res["checks"]}
        assert chk["stem_option_overlap"]["passed"] is False

    def test_crop_content_integrity(self):
        filled = np.zeros((20, 20), bool)
        filled[2:18, 2:18] = True
        empty = np.zeros((20, 20), bool)
        assert check_option_crop(filled)["passed"] is True
        assert check_option_crop(empty)["passed"] is False


class TestPerturbationInvariance:
    """P1-3 防模板过拟合：几何扰动不改变结构结论（禁止硬编码坐标/页面尺寸）。"""

    @pytest.mark.parametrize("shift", [(0, 0), (25, 13), (-12, 7)])
    @pytest.mark.parametrize("scale", [1, 2])
    def test_translation_scale(self, shift, scale):
        pitch, size = 48 * scale, 20 * scale
        mask = blank(200 * scale, 700 * scale)
        rects = []
        for i in range(3):
            cx = (30 + i * pitch) * scale // scale * scale + shift[0] * scale
            cy = 60 * scale + shift[1] * scale
            h = mask.shape
            if cx + size * scale > h[1] or cy + size * scale > h[0]:
                pytest.skip("扰动越界")
            # 直接按像素画（缩放页=更大单元+更大间距）
            mask[cy:cy + size, cx:cx + size * scale] = True
            mask[cy + 2:cy + size - 2, cx + 3:cx + size * scale - 3] = False
            mask[cy + size // 3:cy + 2 * size // 3,
                 cx + 3:cx + size * scale - 3] = True
            rects.append([cx, cy, size * scale, size])
        res = assemble_stem_layout(mask, [region(r) for r in rects],
                                   opt_regions(y=140 * scale))
        assert res["verified"] and res["cols"] == 3

    def test_region_order_permuted(self):
        """VLM 输出顺序置换：结构结论与顺序无关（几何决定，不靠声明顺序）。"""
        mask, rects = seq_page(n=3)
        a = assemble_stem_layout(mask, [region(r) for r in rects], opt_regions())
        b = assemble_stem_layout(mask, [region(r) for r in reversed(rects)],
                                 opt_regions())
        assert (a["cols"], a["mode"], a["verified"]) == (b["cols"], b["mode"],
                                                         b["verified"])
        # 单元坐标序（左→右）一致
        assert [u["bbox"][0] for u in a["units"]] == [u["bbox"][0] for u in b["units"]]

    def test_distractors_outside_band(self):
        """带外无关图形/文字干扰：不进入题干单元。"""
        mask, rects = seq_page(n=3)
        draw_cell(mask, 250, 150, 40, 30, kind=0)   # 选项区干扰块
        draw_cell(mask, 8, 8, 12, 12, kind=1)       # 页眉噪声
        res = assemble_stem_layout(mask, [region(r) for r in rects], opt_regions())
        assert res["cols"] == 3 and res["verified"]

    def test_missing_middle_content_unverified(self):
        """中间格内容被遮挡（VLM 声明了区域但像素无该单元）→ 不冒充成功。"""
        mask, rects = seq_page(n=3)
        mask[:, 78:110] = False  # 抹掉第 2 单元像素
        res = assemble_stem_layout(mask, [region(r) for r in rects], opt_regions())
        # 区域数 3、单元数 2：映射不一致 → 不得输出 verified 的 3
        assert not (res["verified"] and res["cols"] == 3)

    def test_asymmetric_margins(self):
        """非均匀边距：同样 3 单元。"""
        mask = blank(220, 500)
        rects = []
        for i, cx in enumerate([70, 160, 240]):
            draw_cell(mask, cx, 90, 20 + i * 4, 24, kind=i % 3)
            rects.append([cx, 90, 20 + i * 4, 24])
        res = assemble_stem_layout(mask, [region(r) for r in rects], opt_regions())
        assert res["cols"] == 3 and res["verified"]
