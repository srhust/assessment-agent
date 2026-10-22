# -*- coding: utf-8 -*-
"""R12-E：零 API 六条件三层重跑 + Shape/Position 消融 + 失败归因 + 30 页扰动回归。

前置：d1_r11_six_condition.py 已重跑（R12A_SIX_CONDITION_RESCORED.json，含 E_meta）。
本脚本补：
  1) E2 消融 = 与 E 完全同链路，但消费 Position-preserving 画布；
  2) 逐题失败归因（tools.failure_attribution，四分类，无 case-id）；
  3) 冻结 15 页网格契约率/目标位判据分布；
  4) R10D 30 页扰动语料定位回归（同源金标网格对比）。
输出 evidence/R12E_SIX_CONDITION.{json}。不写任何 R11 原件。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from d1_r11_six_condition import FIG, build_question, load_items  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(ROOT))
from tests.conftest import make_settings  # noqa: E402
from assessment_agent.schemas import Solution  # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry  # noqa: E402
from assessment_agent.tools.diagram_grid_evidence import (  # noqa: E402
    build_grid_evidence, known_sequence, merge_stem_regions, render_canvas)
from assessment_agent.tools.failure_attribution import classify_failure  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402
from assessment_agent.tools.figure_page_localizer import localize_page  # noqa: E402
from assessment_agent.tools.release_scoring import summarize_rows  # noqa: E402
from assessment_agent.verifier import Verifier  # noqa: E402

DESIGN_AMBIGUOUS = {"ft-sym-14", "ft-conflict-15"}


def evidence_for(mask, pid, res=None, n_opts=4):
    res = res or localize_page(mask, n_options=n_opts)
    srs = merge_stem_regions(res.get("stem_regions") or [])
    if not srs:
        return None, res
    sr = srs[0]
    x, y, w, h = sr["bbox_page"]
    ev = build_grid_evidence(mask, pid, [x, y, x + w, y + h], 0, 0,
                             expects_blank=True, cells_hint=sr.get("cells"),
                             rows_hint=int(sr.get("sub_band_count") or 1),
                             stem_continuation_suspect=bool(
                                 sr.get("continuation_suspect")))
    return ev, res


def solve_with_canvas(item, settings, canvas_png, grid):
    vision = {"bbox_format": "pixel_xywh", "image_size": None,
              "stem_matrix_crop": str(canvas_png),
              "stem_matrix_grid": grid,
              "option_regions": [
                  {"label": o["label"], "bbox": o["bbox"]}
                  for o in item["regions"].get("option_regions", [])]}
    q = build_question(item, vision)
    sol = SkillRegistry().require("figure_reasoning").solve(
        q, SkillContext(settings=settings))
    va = None
    if sol.status == "solved":
        va = Verifier(settings, registry=SkillRegistry()).verify(q, sol).action
    return sol, va, q


def main() -> int:
    _tmp = tempfile.TemporaryDirectory()
    settings = make_settings(Path(_tmp.name))
    items = {it["id"]: it for it in load_items()}
    base = json.loads((ROOT / "evidence/R12A_SIX_CONDITION_RESCORED.json")
                      .read_text(encoding="utf-8"))
    rows = {r["id"]: dict(r) for r in base["per_item"]}

    audit = {}
    e2_rows = []
    for pid, item in items.items():
        mask = load_mask(str(FIG / "images" / "dev" / f"{pid}.png"))
        ev, res = evidence_for(mask, pid, n_opts=len(item["options"]))
        a = {"grid": [ev["grid_rows"], ev["grid_cols"]] if ev else None,
             "gold": list(item["regions"].get("stem_matrix_grid") or []),
             "status": ev["verification_status"] if ev else "no_band",
             "target": ev.get("target_cell") if ev else None,
             "target_basis": ev.get("target_basis") if ev else None,
             "deltas": (ev or {}).get("ink_deltas") or [],
             "cells_identical": len({c.get("cell_pixel_sha") for c in (ev or {}).get("cells", []) if c.get("cell_kind") == "visible"}) < max(1, len([c for c in (ev or {}).get("cells", []) if c.get("cell_kind") == "visible"]))}
        a["match"] = a["grid"] == a["gold"]
        audit[pid] = a

        # E2 消融：同一证据，消费 Position-preserving 画布
        row_out = {"id": pid}
        if ev and known_sequence(ev).get("mode") in ("sequence", "grid3"):
            import cv2
            with tempfile.TemporaryDirectory() as td:
                pos = render_canvas(mask, ev, mode="position")
                pp = Path(td) / "stem_position.png"
                cv2.imwrite(str(pp), np.where(pos, 0, 255).astype(np.uint8))
                try:
                    sol, va, _ = solve_with_canvas(item, settings, pp,
                                                   [ev["grid_rows"], ev["grid_cols"]])
                    row_out["E2"] = {"status": sol.status, "answer": sol.answer,
                                     "verify": va}
                except Exception as exc:  # noqa: BLE001
                    row_out["E2"] = {"status": "error", "answer": None,
                                     "err": f"{type(exc).__name__}:{str(exc)[:60]}"}
            # shape 对照（同 solve_with_canvas 链路）
            with tempfile.TemporaryDirectory() as td:
                sh = render_canvas(mask, ev, mode="shape")
                sp = Path(td) / "stem_shape.png"
                import cv2
                cv2.imwrite(str(sp), np.where(sh, 0, 255).astype(np.uint8))
                try:
                    sol2, va2, _ = solve_with_canvas(item, settings, sp,
                                                     [ev["grid_rows"], ev["grid_cols"]])
                    row_out["S1"] = {"status": sol2.status, "answer": sol2.answer,
                                     "verify": va2}
                except Exception as exc:  # noqa: BLE001
                    row_out["S1"] = {"status": "error", "answer": None,
                                     "err": str(exc)[:60]}
        e2_rows.append(row_out)

    # 失败归因（对可答题且 E/F 未释放的逐条分类）
    attrib = {}
    for pid, r in rows.items():
        if r.get("answerability") != "solved" or pid in DESIGN_AMBIGUOUS:
            continue
        for cond in ("E", "F"):
            rec = (r.get("conds") or {}).get(cond) or {}
            if rec.get("status") == "solved" and rec.get("verify") in ("accept",
                                                                       "repair"):
                continue
            lab, ev = classify_failure(
                solved=rec.get("status") == "solved",
                verdict=rec.get("status"),
                stem_deltas=(audit.get(pid) or {}).get("deltas"),
                cells_identical=(audit.get(pid) or {}).get("cells_identical", False),
                option_frames_known=False,   # 页面选项带为墨迹紧框（D 实测结论）
                verify_action=rec.get("verify"),
                recheck_note=rec.get("err"))
            # 修正语义：cells_identical 需数据而非 id；上面仅传结构信号
            attrib[f"{pid}|{cond}"] = {"class": lab, "evidence": ev,
                                       "notes": rec.get("err") or rec.get("verify")}

    # 30 页扰动语料定位回归
    pert = json.loads((ROOT / "evidence/R10D_PERTURB_CORPUS.json")
                      .read_text(encoding="utf-8"))
    pert_rows = []
    for it in pert["items"]:
        src = items.get(it["source_item"]) or {}
        gold = list((src.get("regions") or {}).get("stem_matrix_grid") or [])
        m2 = load_mask(str(ROOT / "data" / it["image"]))
        ev2, _ = evidence_for(m2, it["id"], n_opts=len(src.get("options") or []))
        pert_rows.append({
            "id": it["id"], "source": it["source_item"], "scale": it.get("scale"),
            "transform": it.get("transform"), "gold": gold,
            "ev": [ev2["grid_rows"], ev2["grid_cols"]] if ev2 else None,
            "match": bool(ev2) and [ev2["grid_rows"], ev2["grid_cols"]] == gold,
            "status": ev2["verification_status"] if ev2 else "no_band"})

    out = {
        "stage": "R12-E",
        "three_layer_from": "evidence/R12A_SIX_CONDITION_RESCORED.json",
        "conditions_three_layer": base["conditions"],
        "fifteen_page_grid_contract": {
            "matches": sum(1 for a in audit.values() if a["match"]),
            "n": len(audit), "per_item": audit},
        "ablation_position_vs_shape": {r["id"]: {k: v for k, v in r.items()
                                                 if k != "id"} for r in e2_rows},
        "failure_attribution": attrib,
        "perturbation_regression": {
            "matches": sum(1 for r in pert_rows if r["match"]),
            "n": len(pert_rows), "by_status": {},
            "items": pert_rows},
    }
    from collections import Counter
    out["perturbation_regression"]["by_status"] = dict(
        Counter(r["status"] for r in pert_rows))
    (ROOT / "evidence/R12E_SIX_CONDITION.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "contract": f"{out['fifteen_page_grid_contract']['matches']}/15",
        "perturb": f"{out['perturbation_regression']['matches']}/{len(pert_rows)}",
        "perturb_status": out["perturbation_regression"]["by_status"],
        "E": out["conditions_three_layer"]["E"],
        "F": out["conditions_three_layer"]["F"]},
        ensure_ascii=False, default=str)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
