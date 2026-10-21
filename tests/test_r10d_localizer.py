# -*- coding: utf-8 -*-
"""R10-D：页面区域定位器——真实定位（槽位指派）与自洽分离的回归测试。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.figure_page_localizer import (  # noqa: E402
    _containment, grid_blank_cells, localize_page)
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402

FIG = ROOT / "data" / "benchmark_figshots"


def _item(item_id):
    for l in (FIG / "dev" / "figshots.jsonl").read_text(encoding="utf-8").splitlines():
        it = json.loads(l)
        if it["id"] == item_id:
            return it
    raise KeyError(item_id)


def _perturb(item_id):
    cor = json.loads((ROOT / "evidence/R10D_PERTURB_CORPUS.json").read_text(encoding="utf-8"))
    for it in cor["items"]:
        if it["id"] == item_id:
            return it
    raise KeyError(item_id)


def _slot_score(res, gold_regions, mask):
    det = res["option_regions"]
    go = gold_regions["option_regions"]
    assigned = {}
    for di, d in enumerate(det):
        bx, by, bw, bh = d["bbox_page"]
        cx, cy = bx + bw / 2, by + bh / 2
        for gi, g in enumerate(go):
            gx, gy, gw, gh = g["bbox"]
            if gx <= cx < gx + gw and gy <= cy < gy + gh:
                if _containment(d["bbox_page"], g["bbox"]) >= 0.5 and gi not in assigned:
                    assigned[gi] = di
                break
    return sum(1 for gi in range(len(go)) if assigned.get(gi) == gi), len(go)


def test_figshots_native_slot_assignment():
    it = _item("ft-rot90-01")
    m = load_mask(str(ROOT / "data" / it["image"]))
    res = localize_page(m, n_options=len(it["options"]))
    ok, tot = _slot_score(res, it["regions"], m)
    assert (ok, tot) == (4, 4)
    assert all(d["completeness"]["passed"] for d in res["option_regions"])
    assert all(d["pixel_sha"] for d in res["option_regions"])


def test_figshots_blank_cell_localization():
    it = _item("ft-grid-10")
    m = load_mask(str(ROOT / "data" / it["image"]))
    gr = it["regions"]["stem_matrix_grid"]
    found = {(b["row"], b["col"]) for b in grid_blank_cells(
        m, it["regions"]["stem_matrix_bbox"], int(gr[0]), int(gr[1]))}
    assert (2, 2) in found  # 金标右下角空格


def test_perturb_new_layouts_scale_and_vertical():
    for pid in ("ft-rot90-01--large", "ft-rot90-01--vertical"):
        it = _perturb(pid)
        m = load_mask(str(ROOT / "data" / it["image"]))
        res = localize_page(m, n_options=len(it["options"]))
        ok, tot = _slot_score(res, it["regions"], m)
        assert ok == tot, f"{pid}: {ok}/{tot}"


def test_self_consistency_is_not_localization():
    """自洽（重复运行 bbox/pixel_sha 一致）单列报告，不作定位正确证据。"""
    it = _item("ft-mirror-04")
    m = load_mask(str(ROOT / "data" / it["image"]))
    a = localize_page(m, n_options=len(it["options"]))
    b = localize_page(m, n_options=len(it["options"]))
    fa = [(d["bbox_page"], d["pixel_sha"]) for d in a["option_regions"]]
    fb = [(d["bbox_page"], d["pixel_sha"]) for d in b["option_regions"]]
    assert fa == fb
    if not fa:
        pytest.skip("no option band located (honest degradation path)")


def test_vlm_reconciliation_fields():
    """与 VLM 声明对账：confirmed / disagreed 标注必须落字段。"""
    it = _item("ft-rot90-01")
    m = load_mask(str(ROOT / "data" / it["image"]))
    vlm = [{"bbox": [50, 136, 48, 48]}]  # 与金标 A 一致，其余缺失
    res = localize_page(m, n_options=4, vlm_options=vlm,
                        vlm_stem=[24, 64, 192, 48])
    sources = {d["source"] for d in res["option_regions"]}
    assert "vlm+pixel_confirmed" in sources
    assert any("pixel_localizer" in s for s in sources)
    assert res["stem_regions"][0]["source"] == "vlm+pixel_confirmed"
