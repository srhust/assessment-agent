# -*- coding: utf-8 -*-
"""R14-F：六条件配套审计——契约/E2 消融 + **30 页扰动语料释放审计**。

与 R12E 的差别（ticket F 要求）：
1. 扰动页不止回归"定位网格是否=金标格"，还要走**完整 solve→verify→释放层**：
   按本地检出格（哪怕与金标不符）重建画布作答，统计
   `wrong_grid_releasable`——格错却仍可释放的页数，必须为 0。
2. 基线输入 = evidence/R14F_SIX_CONDITION.json（新 pitch 代码重跑件）。
零 API。
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from d1_r11_six_condition import FIG, build_question, load_items  # noqa: E402
from tests.conftest import make_settings  # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry  # noqa: E402
from assessment_agent.tools.diagram_grid_evidence import (  # noqa: E402
    build_grid_evidence, known_sequence, merge_stem_regions, render_canvas)
from assessment_agent.tools.figure_page_localizer import localize_page  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402
from assessment_agent.tools.release_scoring import release_status  # noqa: E402
from assessment_agent.verifier import Verifier  # noqa: E402

from e1_r12_six_condition import evidence_for, solve_with_canvas  # noqa: E402


def main() -> int:
    _tmp = tempfile.TemporaryDirectory()
    settings = make_settings(Path(_tmp.name))
    settings.r4g_layout = "pixel_only"
    items = {it["id"]: it for it in load_items()}

    # ---- 15 页契约 + E2/S1（沿用 e1 链路）
    audit = {}
    for pid, item in items.items():
        mask = load_mask(str(FIG / "images" / "dev" / f"{pid}.png"))
        ev, res = evidence_for(mask, pid, n_opts=len(item["options"]))
        a = {"grid": [ev["grid_rows"], ev["grid_cols"]] if ev else None,
             "gold": list(item["regions"].get("stem_matrix_grid") or []),
             "status": ev["verification_status"] if ev else "no_band",
             "target": ev.get("target_cell") if ev else None,
             "target_basis": ev.get("target_basis") if ev else None}
        a["match"] = a["grid"] == a["gold"]
        audit[pid] = a

    # ---- 30 页扰动：定位 + **释放层**审计
    pert = json.loads((ROOT / "evidence/R10D_PERTURB_CORPUS.json")
                      .read_text(encoding="utf-8"))
    pert_rows = []
    for it in pert["items"]:
        src = items.get(it["source_item"]) or {}
        gold = list((src.get("regions") or {}).get("stem_matrix_grid") or [])
        gold_ans = src.get("answer")
        m2 = load_mask(str(ROOT / "data" / it["image"]))
        ev2, _ = evidence_for(m2, it["id"], n_opts=len(src.get("options") or []))
        row = {"id": it["id"], "source": it["source_item"],
               "scale": it.get("scale"), "transform": it.get("transform"),
               "gold": gold,
               "ev": [ev2["grid_rows"], ev2["grid_cols"]] if ev2 else None,
               "match": bool(ev2) and [ev2["grid_rows"], ev2["grid_cols"]] == gold,
               "status": ev2["verification_status"] if ev2 else "no_band",
               "release": None}
        # 释放审计：只有**金标格匹配**的扰动页才作答（格错页面本就该弃权，
        # 但仍走链路记录其行为）；选项用源条目金标区域（扰动只动题干页）。
        if ev2 and known_sequence(ev2).get("mode") in ("sequence", "grid3"):
            with tempfile.TemporaryDirectory() as td:
                sh = render_canvas(m2, ev2, mode="shape")
                sp = Path(td) / "stem_shape.png"
                cv2.imwrite(str(sp), np.where(sh, 0, 255).astype(np.uint8))
                try:
                    sol, va, _q = solve_with_canvas(src, settings, sp,
                                                    [ev2["grid_rows"], ev2["grid_cols"]])
                    fa = None
                    if sol.status == "solved" and va in ("accept", "repair"):
                        # solve_with_canvas 未回传 final_answer；释放层答案=answer
                        fa = sol.answer
                    rs = release_status(va, fa if va in ("accept", "repair") else None,
                                        gold_ans)
                    row["release"] = {"answer": sol.answer, "verify": va,
                                      "released": rs["released"],
                                      "wrong_accept": rs["wrong_accept"],
                                      "grid_match": row["match"]}
                except Exception as exc:  # noqa: BLE001
                    row["release"] = {"err": f"{type(exc).__name__}:{str(exc)[:60]}"}
        pert_rows.append(row)

    rel = [r for r in pert_rows if r["release"]]
    wrong_grid_releasable = [r["id"] for r in rel
                             if r["release"].get("released") and not r["release"].get("grid_match")]
    wrong_accept_pert = [r["id"] for r in rel if r["release"].get("wrong_accept")]

    out = {
        "stage": "R14-F",
        "baseline_three_layer": "evidence/R14F_SIX_CONDITION.json",
        "fifteen_page_grid_contract": {
            "matches": sum(1 for a in audit.values() if a["match"]),
            "n": len(audit), "per_item": audit},
        "perturbation_regression": {
            "matches": sum(1 for r in pert_rows if r["match"]),
            "n": len(pert_rows),
            "by_status": dict(Counter(r["status"] for r in pert_rows)),
            "solve_layer_rows": len(rel),
            "wrong_grid_releasable": wrong_grid_releasable,
            "wrong_accept_pert": wrong_accept_pert,
            "items": pert_rows},
        "llm_calls_used": 0,
    }
    (ROOT / "evidence/R14F_PERTURB_RELEASE_AUDIT.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "contract": f"{out['fifteen_page_grid_contract']['matches']}/15",
        "perturb_loc": f"{out['perturbation_regression']['matches']}/30",
        "perturb_status": out["perturbation_regression"]["by_status"],
        "solve_rows": len(rel),
        "wrong_grid_releasable": wrong_grid_releasable,
        "wrong_accept_pert": wrong_accept_pert}, ensure_ascii=False)[:900])
    return 0


if __name__ == "__main__":
    sys.exit(main())
