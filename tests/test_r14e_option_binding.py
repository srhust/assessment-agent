# -*- coding: utf-8 -*-
"""R14-E：选项证据消费绑定 + 标签锚点格盒（页级不变量，无 case-id）。"""
import numpy as np
import pytest

from assessment_agent.skills import SkillContext, SkillRegistry
from assessment_agent.skills.figure_reasoning import (
    FigureReasoningSkill, _rebuild_boxes, _tight_set, _inter_at_origin)
from assessment_agent.tools.figure_page_localizer import localize_page
from assessment_agent.tools.figure_tools import load_mask
from assessment_agent.tools.option_evidence import strip_label_from_crop

FIG = "data/benchmark_figshots/images/dev"


def _ctx(tmp_path):
    from tests.conftest import make_settings
    st = make_settings(tmp_path)
    st.r4g_layout = "pixel_only"
    return SkillContext(settings=st)


def test_anchor_cells_have_label_bbox_and_ink():
    """真实截图：锚点切分单元自带标签框、标签一一对应已验证。

    注：`label_bbox` 的 **高度是带高**（域分解的列投影范围，标签行以
    `_try_layer` 的行线门逐单元验证），y 向置零因此无害；x 向是标签域列。
    内容墨量 = 单元 ink（域分解已排除标签域）。
    """
    mask = load_mask(f"{FIG}/ft-overlay-12.png")
    res = localize_page(mask, n_options=4)
    cells = res["option_regions"]
    assert len(cells) == 4
    for c in cells:
        assert c["provenance"] == "localizer+label_anchor"
        lx0, ly0, lw_, lh_ = c["label_bbox"]
        assert 8 <= lw_ <= 18                       # 标签列宽=字母尺寸
        assert lx0 == c["bbox_page"][0]              # 标签在单元左端
        assert int(np.asarray(mask)[ly0:ly0 + lh_, lx0:lx0 + lw_].sum()) > 0
        assert c["ink"] >= 20                        # 内容非近空白


def test_rebuild_boxes_contain_ink_and_split_pairs():
    """格盒必须完整包含墨迹；同行双图必须分属两盒（不得一盒吞两图）。"""
    for pid, n in (("ft-overlay-12", 4), ("ft-rot90-01", 4)):
        mask = np.asarray(load_mask(f"{FIG}/{pid}.png"), dtype=bool)
        cells = localize_page(mask, n_options=n)["option_regions"]
        boxes = _rebuild_boxes(cells, mask)
        assert len(boxes) == n
        for c, (x0, y0, x1, y1) in zip(cells, boxes):
            bx, by, bw, bh = c["bbox_page"]
            assert x0 <= bx and x1 >= bx + bw        # 墨迹不被切
            assert y0 <= by and y1 >= by + bh
        for i in range(1, n):                          # 相邻盒不重叠
            assert boxes[i - 1][2] <= boxes[i][0]
        # 每盒 strip 标签后仍有实墨，且各盒墨集互不相同
        inks = []
        for c, (x0, y0, x1, y1) in zip(cells, boxes):
            crop = mask[y0:y1, x0:x1].copy()
            lx0, ly0, lw_, lh_ = c["label_bbox"]
            crop[max(0, ly0 - y0):ly0 - y0 + lh_,
                 max(0, lx0 - x0):lx0 - x0 + lw_] = False
            assert int(crop.sum()) >= 20
            inks.append(_tight_set(crop))
        for i in range(len(inks)):
            for j in range(i + 1, len(inks)):
                if inks[i].shape == inks[j].shape:
                    assert int((inks[i] ^ inks[j]).sum()) > 0


def test_tight_set_translation_invariant():
    a = np.zeros((20, 30), bool)
    a[4:12, 5:15] = True
    b = np.zeros((26, 40), bool)
    b[10:18, 20:30] = True
    assert np.array_equal(_tight_set(a), _tight_set(b))
    assert _inter_at_origin(_tight_set(a), _tight_set(b)) == int(a.sum())


def test_stale_crop_is_rejected_and_rebuilt(tmp_path):
    """陈旧（切半）裁剪不得被消费：solve 必须换成页面重建单元。"""
    import json
    from pathlib import Path
    from assessment_agent.schemas import Question

    src = Path("runs/r13g-ft-overlay-12/question.json")
    if not src.is_file():
        pytest.skip("R13G 真实截图产物不在库内")
    q = Question.model_validate(json.loads(src.read_text(encoding="utf-8")))
    ctx = _ctx(tmp_path)
    sol = FigureReasoningSkill().solve(q, ctx)
    prov = (sol.meta or {}).get("region_provenance") or {}
    cells = prov.get("option_cells") or []
    assert len(cells) == 4
    assert all(c.get("label_association_verified") for c in cells)
    # 无任何单元近空白（旧缺陷：C 只剩 16px）
    assert all(int(c.get("ink") or 0) >= 20 for c in cells)
    assert prov.get("consumption_binding") in ("verified", "rebuilt")


def test_binding_rejects_blank_crop_and_keeps_good_stem(tmp_path):
    """绑定只换坏掉的选项证据：题干输入原样保留（不牵动好证据）。"""
    import json
    from pathlib import Path
    from assessment_agent.schemas import Question

    src = Path("runs/r13g-ft-overlay-12/question.json")
    if not src.is_file():
        pytest.skip("R13G 真实截图产物不在库内")
    q = Question.model_validate(json.loads(src.read_text(encoding="utf-8")))
    st_ctx = _ctx(tmp_path)
    fr = SkillRegistry().require("figure_reasoning")
    ri = fr._region_inputs(q, st_ctx)
    if ri is None:
        pytest.skip("题面无 vision 区域输入")
    stem_before = ri.get("stem_crop")
    reb, notes = fr._rebind_option_evidence(q, st_ctx, dict(ri))
    if reb is None:
        pytest.skip("该页走整页重建路径")
    assert reb.get("stem_crop") == stem_before
    assert any("rebound_option_crops_from_page" in n for n in notes)
    for lab, path in reb["options"]:
        m = np.asarray(load_mask(path), dtype=bool)
        assert int(m.sum()) >= 20, f"{lab} 仍近空白"


def test_strip_label_row_line_rule():
    """行线互证第二路（小图版式）：首 run 是**字母全高**（高≤16、宽≤18、
    墨量<邻右图形），同一条垂直行线在带内其他列再现 ≥2 次，且首 run 是带
    最左段 → 剥除。主判据（R13-G：字母 ≤0.6×主高）另有既有用例覆盖。"""
    m = np.zeros((28, 40), bool)
    m[12:22, 2:10] = True                     # 标签列 ink 80（< 图形 128）
    m[4:12, 20:36] = True                     # 图形 ink 128
    band = np.zeros((28, 140), bool)
    band[:, 0:40] = m
    band[12:22, 62:70] = True                   # 同一条行线的其他字母
    band[4:12, 80:96] = True
    band[4:12, 100:116] = True
    band[12:22, 120:128] = True
    out = np.asarray(strip_label_from_crop(m, band_mask=band, band_x0=0))
    assert int(out.sum()) == 128               # 只剩图形
    assert out[4:12, 20:36].all()
    # 无互证（band 只此一单元）→ 不剥
    solo = np.zeros((28, 40), bool)
    solo[12:22, 2:10] = True
    solo[4:12, 20:36] = True
    out2 = np.asarray(strip_label_from_crop(solo, band_mask=solo, band_x0=0))
    assert int(out2.sum()) == int(solo.sum())
    # 首 run 左侧仍有墨（非带最左）→ 不剥
    shift = np.zeros((28, 80), bool)
    shift[12:22, 2:10] = True
    shift[4:12, 20:36] = True
    shift[12:22, 42:50] = True                  # 本单元首 run（左侧有墨）
    shift[4:12, 56:72] = True
    out3 = np.asarray(strip_label_from_crop(shift[:, 40:80], band_mask=shift,
                                            band_x0=40))
    assert int(out3.sum()) == int(shift[:, 40:80].sum())
