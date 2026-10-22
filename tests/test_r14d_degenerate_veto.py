# -*- coding: utf-8 -*-
"""R14-D：退化常数序列弃权 + 跨协议尺度 + 复核坐标契约（零 API 反例组）。

锁定三条因果链（全部来自 R13G 真实解析件的确定性归因）：
1. trans-07 式"题干逐像素相同"的序列，旋转/镜像族必须弃权——旧行为是
   I/FH/FV/R180 四条退化候选**共同**假证错误选项并被独立验证放行。
2. 题干 pitch 槽 vs 选项贴边裁剪：尺度惩罚走绝对像素面积，不再饱和到 1.0。
3. 复核显式网格与节距外推格数一致时用同一外推槽（第二类虚假漂移）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from assessment_agent.schemas import Question  # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry  # noqa: E402
from assessment_agent.tools.diagram_grid_evidence import (  # noqa: E402
    build_grid_evidence, merge_stem_regions)
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402
from assessment_agent.tools.figure_transform import (  # noqa: E402
    analyze_transform)
from assessment_agent.verifier import Verifier  # noqa: E402
from assessment_agent.tools.release_scoring import release_status  # noqa: E402


def _settings(tmp):
    from tests.conftest import make_settings
    st = make_settings(Path(tmp))
    st.r4g_layout = "pixel_only"
    return st


def test_constant_sequence_vetoed_on_real_trans07():
    """真实解析件：trans-07 不再被任何通道释放（旧行为=错误 accept B）。"""
    q = Question.model_validate(json.loads(
        (ROOT / "runs/r13g-ft-trans-07/question.json").read_text(encoding="utf-8")))
    with tempfile.TemporaryDirectory() as td:
        st = _settings(td)
        reg = SkillRegistry()
        sol = reg.require("figure_reasoning").solve(q, SkillContext(settings=st))
        act = fa = None
        if sol.status == "solved":
            vr = Verifier(st, registry=reg).verify(q, sol)
            act, fa = vr.action, vr.final_answer
        rs = release_status(act, fa, "A")
        assert rs["released"] is False, (act, fa)
        assert rs["wrong_accept"] is False


def test_degenerate_identity_emits_no_shape_candidates():
    sys.path.insert(0, str(ROOT / "tests"))
    from test_r4a_figure_transform import box
    bar = box(12, 17, 16, 6)
    r = analyze_transform([bar, bar, bar],
                          {"A": bar, "B": box(18, 2, 4, 8)})
    kinds = {c["rule_type"] for c in r["candidates"]}
    assert "rotate_flip_seq" not in kinds      # I/F 族整体弃权
    assert r["verdict"].get("answer") is None  # 不作答


def test_protocol_aware_abs_area_resolves_pitch_vs_tight():
    """pitch 宽槽题干 + 贴边窄选项：同形状残差 ≈0（旧饱和 1.0 的反例）。"""
    page = load_mask(str(ROOT / "data/benchmark_figshots/images/dev/"
                         "ft-mirror-04.png"))
    cells = [page[58:101, 20:72], page[58:101, 72:124]]   # 52 宽 pitch 槽
    opts = {L: load_mask(str(ROOT / f"runs/r13g-ft-mirror-04/regions/option_{L}.png"))
            for L in "ABC"}
    r = analyze_transform(cells, opts, mode="sequence",
                          cell_geometry=[[20, 58, 72, 101], [72, 58, 124, 101]],
                          stem_canvas=(43, 156))
    sc = [c["candidate_scores"] for c in r["candidates"] if c["transformation"] == "FH"]
    assert sc, "FH 律应存在（非恒定序列不受 veto）"
    assert sc[0]["A"] <= 0.15 and sc[0]["B"] > 0.5       # 判别恢复
    assert r["verdict"].get("answer") == "A"


def test_pitch_slot_unified_under_matching_explicit_grid():
    """显式 [1,4] 与外推格数一致 → ok（旧：均分槽 vs 外推槽虚假 override）。"""
    q = Question.model_validate(json.loads(
        (ROOT / "data/benchmark_xingce_dev/r10gen/xingce_dev_r10gen.jsonl")
        .read_text(encoding="utf-8").splitlines()[0] or "{}")) if False else None
    from tests.conftest import make_settings  # noqa: F401  统一 import 副作用
    img = ROOT / "runs/r13g-ft-rot90-01/image/original.png"
    if not img.exists():
        img = ROOT / "data/benchmark_figshots/images/dev/ft-rot90-01.png"
    mask = load_mask(str(img))
    from assessment_agent.tools.figure_page_localizer import localize_page
    res = localize_page(mask, n_options=4)
    sr = merge_stem_regions(res.get("stem_regions") or [])
    b = sr[0]["bbox_page"]
    sb = [b[0], b[1], b[0] + b[2], b[1] + b[3]]
    ev = build_grid_evidence(mask, "", sb, 0, 0, expects_blank=True,
                             cells_hint=sr[0].get("cells"),
                             rows_hint=int(sr[0].get("sub_band_count") or 1))
    auto_grid = ev.get("grid") or [1, len(sr[0].get("cells") or []) + 1]
    ev2 = build_grid_evidence(mask, "", sb, int(auto_grid[0]), int(auto_grid[1]),
                              expects_blank=True, cells_hint=sr[0].get("cells"),
                              rows_hint=int(sr[0].get("sub_band_count") or 1))
    assert ev2["verification_status"] == "ok", ev2.get("notes")
    assert ev2.get("release_eligible", True) is True
    # 与外推**不一致**的显式网格保持降级（安全语义不放松）
    ev3 = build_grid_evidence(mask, "", sb, 1, 2, expects_blank=True,
                              cells_hint=sr[0].get("cells"),
                              rows_hint=int(sr[0].get("sub_band_count") or 1))
    assert ev3["verification_status"] != "ok" or not ev3.get("release_eligible", True)
