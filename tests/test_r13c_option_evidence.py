# -*- coding: utf-8 -*-
"""R13-C OptionEvidence 门：正/反例 + 验证器封顶行为（零 API）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.option_evidence import (  # noqa: E402
    label_anchor_frames, option_evidence, options_release_ok)


def _fig():
    # 与真实页同比例：图形墨高 18，标签墨高 9（1:2）
    a = np.zeros((24, 22), np.uint8)
    a[3:21, 3:6] = 1
    a[18:21, 3:20] = 1
    return a


def _letter():
    a = np.zeros((12, 10), np.uint8)
    a[2:11, 3:6] = 1
    a[2:5, 1:9] = 1
    return a


def _band(items, gap=8, y0=100, y1=124, w=360):
    m = np.zeros((y1 + 40, w), np.uint8)
    x = 20
    for kind, im in items:
        h, wd = im.shape
        yy = y0 + 3 if kind == "fig" else y0 + 9
        m[yy:yy + h, x:x + wd] = im
        x += wd + gap
    return m


def test_positive_label_fig_frames_all_associated():
    m = _band([("lab", _letter()), ("fig", _fig()),
               ("lab", _letter()), ("fig", _fig())])
    frs = label_anchor_frames(m, 100, 124, 24)
    ev = option_evidence(m, (100, 124), frs)
    assert len(ev) == 2
    assert all(e["label_association_verified"] is True for e in ev)
    assert all(e["clipping_detected"] is False for e in ev)
    ok, reasons = options_release_ok(ev, 2)
    assert ok, reasons


def test_negative_clipped_figure_flagged():
    # 图形真实画出带尾（带外仍有墨）→ clipping_detected 必须为 True
    m = _band([("lab", _letter()), ("fig", _fig())])
    frs = label_anchor_frames(m, 100, 118, 18)   # 谎报带尾=118，图形 103..124 越界
    ev = option_evidence(m, (100, 118), frs)
    figs = [e for e in ev if (e["bbox_page"][1] + e["bbox_page"][3]) >= 117]
    assert figs, ev
    assert any(e["clipping_detected"] for e in figs)


def test_negative_detached_label_not_verified():
    m = _band([("lab", _letter()), ("fig", _fig())], gap=120)  # 标签离图形过远
    frs = label_anchor_frames(m, 100, 124, 24)
    ev = option_evidence(m, (100, 124), frs)
    assert all(e["label_association_verified"] is not True for e in ev)
    ok, _ = options_release_ok(ev, len(ev))
    assert not ok  # 未证实关联不得放行


def test_real_page_mirror04_frames_pass_gate():
    from assessment_agent.tools.figure_tools import load_mask
    from assessment_agent.tools.figure_page_localizer import localize_page
    m = load_mask(str(ROOT / "data/benchmark_figshots/images/dev/ft-mirror-04.png"))
    res = localize_page(m, n_options=3)
    b = res["bands"][-1]
    frs = label_anchor_frames(m, b["y0"], b["y1"], b["y1"] - b["y0"])
    ev = option_evidence(m, (b["y0"], b["y1"]), frs)
    assert len(ev) == 3
    assert all(e["label_association_verified"] is True for e in ev)
    assert all(not e["clipping_detected"] for e in ev)


def test_skill_verify_gates_on_bad_label_association():
    # 直接测 gate：构造 prov（含 label False 的 cell）→ skill.verify → unverified
    import tempfile
    from assessment_agent.schemas import Question, Solution, QuestionOption
    from assessment_agent.skills import SkillContext, SkillRegistry
    from tests.conftest import make_settings
    img = str(ROOT / "data/benchmark_figshots/images/dev/ft-mirror-04.png")
    with tempfile.TemporaryDirectory() as td:
        settings = make_settings(Path(td))
        reg = SkillRegistry()
        skill = reg.require("figure_reasoning")
        import json
        item = [json.loads(l) for l in (ROOT / "data/benchmark_figshots/dev/figshots.jsonl")
                .read_text(encoding="utf-8").splitlines() if l.strip()
                if json.loads(l)["id"] == "ft-mirror-04"][0]
        run = (ROOT / "runs/e2e-dev15-20261019T065016Z-ft-mirror-04" / "extraction.json")
        if not run.exists():
            return  # 无缓存件则跳过（不发 HTTP、不造假）
        ex = json.loads(run.read_text(encoding="utf-8"))
        vision = {"bbox_format": "pixel_xywh", "stem_matrix_crop": None,
                  "stem_matrix_grid": None,
                  "option_regions": [{"label": o["label"], "bbox": o["bbox"]}
                                     for o in (ex.get("option_regions") or [])
                                     if o.get("label")],
                  "image_size": [ex.get("image_width"), ex.get("image_height")]}
        q = Question(stem=item["stem"],
                     options=[QuestionOption(label=o["label"], text="图")
                              for o in item["options"]],
                     image_path=img, source="image", meta={"vision": vision})
        sol = skill.solve(q, SkillContext(settings=settings))
        if sol.status != "solved":
            return
        prov = dict(sol.meta["region_provenance"])
        cells = [dict(c) for c in prov["option_cells"]]
        assert all(c.get("label_association_verified") is True for c in cells)
        cells[0]["label_association_verified"] = False
        prov["option_cells"] = cells
        sol2 = Solution(status="solved", answer=sol.answer, confidence=sol.confidence,
                        meta={**sol.meta, "region_provenance": prov})
        v = skill.verify(q, sol2, SkillContext(settings=settings))
        assert v is not None and v.action == "unverified"
        assert any(c.name == "figure.option_evidence_gate" for c in v.checks)
