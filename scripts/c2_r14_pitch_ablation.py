# -*- coding: utf-8 -*-
"""R14-C：pitch 因果消融——同一解析件/选项/求解器/验证器/阈值，**只换题干画布**。

画布组：
  A_old_maxink  旧协议：格宽=max_ink+2（不吸收间距）——R13-G 失败现场
  B_center_pitch 新协议：CellGeometry 中心距 pitch 槽（R14-B/C 修复）
  C_gold        金标画布（dev15 regions/stem.png，离线诊断天花板，不进部署）

逐件报告（ticket 要求的数字）：格中心坐标、墨宽、估计 pitch、槽边界、重建内容
（画布尺寸/墨量/sha）、每候选残差、best-second margin、Solver 结果、Verifier
真实结果、释放层。归因标签：pitch_causal_fixed / option_crop_error /
rule_not_applicable / rule_conflict / verifier_mismatch / unchanged。零 API。
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from assessment_agent.schemas import Question  # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry  # noqa: E402
from assessment_agent.tools import diagram_layout  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402
from assessment_agent.tools.release_scoring import release_status  # noqa: E402
from assessment_agent.verifier import Verifier  # noqa: E402
from tests.conftest import make_settings  # noqa: E402

PIDS = ["ft-mirror-04", "ft-rot90-01", "ft-trans-07", "ft-overlay-12",
        "ft-mirror-04--tight"]
GOLD = {p: "A" for p in PIDS}
FIG = ROOT / "data" / "benchmark_figshots"


def _canvas_stats(cv: np.ndarray, cols: int):
    cvb = (cv > 0).astype(np.uint8)
    w = cvb.shape[1] // max(1, cols)
    cells = []
    for i in range(cols):
        sub = cvb[:, i * w:(i + 1) * w]
        ys, xs = np.nonzero(sub)
        ink = int(sub.sum())
        cells.append({
            "slot_bbox_px": [i * w, 0, (i + 1) * w, cvb.shape[0]],
            "ink": ink,
            "ink_width": int(xs.max() - xs.min() + 1) if ink else 0,
            "center_x": round(float(xs.mean()) + i * w, 2) if ink else None,
            "sha": hashlib.sha256(np.ascontiguousarray(sub).tobytes()).hexdigest()[:12],
        })
    return {"canvas_shape": list(cvb.shape),
            "canvas_sha": hashlib.sha256(np.ascontiguousarray(cvb).tobytes()).hexdigest()[:12],
            "cells": cells}


def _stem_data_for(q: Question):
    v = (q.meta or {}).get("vision") or {}
    sd = []
    g = v.get("stem_matrix_grid") or [1, 0]
    if isinstance(g, list) and len(g) == 2 and int(g[1]) >= 2:
        cols = int(g[1])
        img = np.asarray(load_mask(str(q.image_path)))
        # 题干带：取上半幅找最密墨行（与 localizer 同思路的轻量版）
        band_top = 0
        if v.get("stem_band_bbox"):
            band_top = None
        sd = [{"role": "stem_figure", "bbox": list(v.get("stem_band_bbox") or []),
               "grid": {"rows": int(g[0]), "cols": cols}} for _ in range(1)]
    return v, sd


def build_canvas(q: Question, mode: str, tmp: Path, pid: str):
    """返回 (canvas_path, grid, geometry_report)。"""
    v = (q.meta or {}).get("vision") or {}
    grid = v.get("stem_matrix_grid") or [1, 3]
    opts = [{"label": o["label"], "bbox": list(o.get("bbox") or o.get("bbox_page") or [0, 0, 1, 1]),
             "has_figure": True}
            for o in (v.get("option_regions") or [])]
    mask = load_mask(str(q.image_path))
    if mode == "C_gold":
        base = FIG / "regions" / "dev" / pid.replace("--tight", "")
        p = base / "stem.png"
        if p.exists():
            return str(p), grid, {"source": "gold_regions_png"}
        return None, grid, {"source": "gold_missing"}
    stem_data = [{"role": "stem_figure",
                  "bbox": list((v.get("stem_matrix_bbox") or v.get("stem_band_bbox") or [])),
                  "grid": {"rows": int(grid[0]), "cols": int(grid[1])}}]
    # 解析件里无 bbox 时用整幅上半带兜底（与生产链路一致的策略由
    # assemble_stem_layout 内部完成；这里直接复刻调用）
    if not stem_data[0]["bbox"]:
        h = mask.shape[0]
        stem_data[0]["bbox"] = [0, 0, mask.shape[1], int(h * 0.62)]
    orig = diagram_layout._canvas_pitch
    if mode == "A_old_maxink":
        diagram_layout._canvas_pitch = lambda units: None   # 旧协议
    try:
        res = diagram_layout.assemble_stem_layout(
            mask, stem_data, opts, profile="pixel_only",
            trust_vlm_grid=False, expects_blank_target=True)
    finally:
        diagram_layout._canvas_pitch = orig
    cv = res.get("_canvas")
    if cv is None:
        return None, grid, {"source": "no_canvas",
                            "notes": res.get("_fallback_notes") or res.get("notes")}
    cv = (cv > 0).astype(np.uint8) * 255
    p = tmp / f"stem_{mode}.png"
    cv2.imwrite(str(p), cv)
    geo = res.get("cell_geometry") or {}
    return (str(p), [int(grid[0]), len(res.get("stem_cells") or []) or int(grid[1])],
            {"source": "assembled", "pitch_x": geo.get("cell_pitch_x"),
             "pitch_y": geo.get("cell_pitch_y"), "slots": geo.get("cell_slot_bbox"),
             "confidence": geo.get("geometry_confidence"),
             "stats": _canvas_stats(cv, max(1, len(res.get("stem_cells") or [])))})


def run_one(q: Question, canvas: str, grid, tmp: Path, pid: str,
            legacy_scale_protocol: bool = False):
    from copy import deepcopy
    qq = deepcopy(q)
    v = qq.meta["vision"]
    v["stem_matrix_crop"] = canvas
    v["stem_matrix_grid"] = grid
    st = make_settings(tmp)
    st.r4g_layout = "pixel_only"
    reg = SkillRegistry()
    from assessment_agent.tools import figure_transform as _ft
    _old_tol = _ft.CANVAS_ALIGN_TOL_PX
    if legacy_scale_protocol:
        _ft.CANVAS_ALIGN_TOL_PX = 10 ** 9   # 强制相对尺度惩罚=修复前协议
    try:
        sol = reg.require("figure_reasoning").solve(qq, SkillContext(settings=st))
    finally:
        _ft.CANVAS_ALIGN_TOL_PX = _old_tol
    act = fa = None
    cands = []
    if sol.status == "solved":
        vr = Verifier(st, registry=reg).verify(qq, sol)
        act, fa = vr.action, vr.final_answer
    m = sol.meta or {}
    tp = m.get("transform") or {}
    for c in tp.get("candidates") or []:
        sc = c.get("candidate_scores") or {}
        vals = sorted(sc.values())
        cands.append({"rule": f"{c['rule_type']}/{c['transformation']}",
                      "scores": {k: round(float(x), 4) for k, x in sc.items()},
                      "margin": (round(float(vals[1] - vals[0]), 4)
                                 if len(vals) >= 2 else None)})
    rs = release_status(act, fa, GOLD[pid])
    return {"solver_status": sol.status, "solver_answer": sol.answer,
            "solver_error": (sol.error or "")[:160],
            "verifier_action": act, "final_answer": fa,
            "candidates": cands[:8],
            "release": rs}, sol


def label(a: dict, b: dict, c: dict, pid: str):
    def rel_ok(r):
        return r["release"]["released"] and r["final_answer"] == GOLD[pid]
    if rel_ok(b) and not rel_ok(a):
        return "pitch_causal_fixed"
    if rel_ok(b) and rel_ok(a):
        return "unchanged"
    errA = (a["solver_error"] + b["solver_error"]).lower()
    if "几乎空白" in (a["solver_error"] + b["solver_error"]):
        return "option_crop_error"
    if "不唯一" in (a["solver_error"] + b["solver_error"]) or "无法唯一" in errA:
        return ("rule_conflict" if (b["release"]["released"] or a["release"]["released"])
                else "rule_conflict")
    if c.get("solver_status") == "solved" and b["solver_status"] != "solved":
        return "option_crop_error"   # 金标能解而真实槽不能 → 裁剪/槽质量
    if b["solver_answer"] is not None and not b["release"]["released"]:
        return "verifier_mismatch"
    if b["solver_status"] != "solved":
        return "rule_not_applicable"
    return "pitch_not_sufficient"


def main() -> int:
    out = {"stage": "R14-C pitch causal ablation (0 HTTP; fixed text/options/solver/verifier/thresholds)",
           "canvas_groups": ["A_old_maxink", "B_center_pitch", "C_gold(off diagnostic only)"],
           "items": {}}
    for pid in PIDS:
        fp = ROOT / "runs" / f"r13g-{pid}" / "question.json"
        if not fp.exists():
            out["items"][pid] = {"missing": True}
            continue
        q = Question.model_validate(json.loads(fp.read_text(encoding="utf-8")))
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = {}
            for mode in ("A_old_maxink", "B_center_pitch", "C_gold"):
                canvas, grid, geo = build_canvas(q, mode, tmp, pid)
                if not canvas:
                    rows[mode] = {"no_canvas": geo}
                    continue
                r, _ = run_one(q, canvas, grid, tmp, pid)
                r["geometry"] = geo
                rows[mode] = r
                # 因果第二轴：同一画布回退到修复前相对尺度惩罚
                rl, _ = run_one(q, canvas, grid, tmp, pid, legacy_scale_protocol=True)
                rows[mode + "__legacy_scale"] = rl
            lab = None
            need = ("A_old_maxink", "B_center_pitch")
            if all(m in rows and "solver_status" in rows[m] for m in need):
                cg = rows.get("C_gold") or {}
                if "solver_status" not in cg:
                    cg = {"solver_status": "no_canvas", "solver_error": ""}
                lab = label(rows["A_old_maxink"], rows["B_center_pitch"], cg, pid)
            out["items"][pid] = {"label": lab, "rows": rows}
            a = rows.get("A_old_maxink", {}); b = rows.get("B_center_pitch", {})
            print(pid, "->", lab,
                  "| A:", a.get("solver_status"), a.get("final_answer"),
                  "| B:", b.get("solver_status"), b.get("final_answer"),
                  "| gold:", rows.get("C_gold", {}).get("solver_status"))
    (ROOT / "evidence/R14C_PITCH_ABLATION.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
