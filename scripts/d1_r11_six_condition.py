# -*- coding: utf-8 -*-
"""R11-D：六条件实验（A金标 / B VLM题干+金标选项 / C 金标题干+VLM选项 / D 全VLM /
E 像素重建题干+金标选项 / F 像素重建题干+像素重建选项）。

零 API：B/C/D 用 R9/R10 已缓存的 VLM 解析（runs/e2e-r8* 提取件），
E/F 用 r11 像素格证据重建。条件名对应**实际裁剪内容**（不玩文本字段偷换）。
输出 evidence/R11_D_SIX_CONDITION.json：逐题释放/正确/弃权 + 格数正确性 +
目标位正确性 + 像素内容保留 + 求解-验证一致性。错误接受单列。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

FIG = ROOT / "data" / "benchmark_figshots"
BUDGET = {"llm_calls": 0}  # 本实验必须为 0


def load_items():
    items = []
    for l in (FIG / "dev" / "figshots.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            items.append(json.loads(l))
    return items


def build_question(item, vision: dict):
    from assessment_agent.schemas import Question, QuestionOption
    return Question(
        stem=item["stem"],
        options=[QuestionOption(label=o["label"], text="图") for o in item["options"]],
        image_path=str(FIG / "images" / "dev" / f"{item['id']}.png"),
        source="image",
        meta={"vision": vision})


def vision_from(ex) -> dict:
    """ExtractionResult(v2) → skill meta.vision 视图（crop 路径即条件语义）。"""
    d = next((x for x in (ex.diagram_regions or [])
              if getattr(x, "crop_path", None)), None)
    opts = [{"label": o.label, "bbox": o.bbox,
             "crop_path": getattr(o, "crop_path", None),
             "has_figure": o.has_figure}
            for o in (ex.option_regions or []) if o.has_figure]
    grid = None
    if d is not None and getattr(d, "grid", None):
        g = d.grid
        grid = [g.get("rows") if isinstance(g, dict) else getattr(g, "rows", None),
                g.get("cols") if isinstance(g, dict) else getattr(g, "cols", None)]
    return {"bbox_format": "pixel_xywh",
            "stem_matrix_crop": str(d.crop_path) if d is not None and d.crop_path else None,
            "stem_matrix_grid": grid,
            "option_regions": opts,
            "image_size": [ex.image_width, ex.image_height]}


def main() -> int:
    from assessment_agent.skills import SkillContext, SkillRegistry
    from assessment_agent.tools.diagram_grid_evidence import (
        build_grid_evidence, known_sequence, render_canvas)
    from assessment_agent.tools.figure_tools import load_mask
    from assessment_agent.tools.figure_page_localizer import localize_page
    from assessment_agent.verifier import Verifier
    from tests.conftest import make_settings
    import hashlib
    import numpy as np

    items = load_items()
    ab_flags = sorted({str(i.get("answerability") or i.get("expected_status")) for i in items})
    registry = SkillRegistry()
    skill = registry.require("figure_reasoning")
    out_rows = []
    ansb_key = (lambda it: (it.get("answerability") or it.get("expected_status") or "可作答"))
    for item in items:
        pid = item["id"]
        ansb = ansb_key(item)
        gold = item["answer"]
        row = {"id": pid, "answerability": ansb, "gold": gold, "conds": {},
               "design_abstain": pid in ("ft-sym-14", "ft-conflict-15")}
        tmp = Path(tempfile.mkdtemp())
        settings = make_settings(tmp)
        # ---- A-D：金标 + 缓存 VLM extraction（v2，含 crop 文件路径）
        sys.path.insert(0, str(ROOT / "scripts"))
        import importlib
        r9b = importlib.import_module("r9b_four_condition")
        gex = r9b.gold_extraction(item)
        vex = r9b.vlm_extraction(item)
        gv, vv = vision_from(gex), (vision_from(vex) if vex else None)
        combos = {
            "A": gv,
            "B": ({"bbox_format": "pixel_xywh", "stem_matrix_crop": vv["stem_matrix_crop"],
                    "stem_matrix_grid": vv["stem_matrix_grid"],
                    "option_regions": gv["option_regions"], "image_size": None} if vv else None),
            "C": ({"bbox_format": "pixel_xywh", "stem_matrix_crop": gv["stem_matrix_crop"],
                    "stem_matrix_grid": gv["stem_matrix_grid"],
                    "option_regions": vv["option_regions"], "image_size": None} if vv else None),
            "D": vv,
        }
        for cond, vis in combos.items():
            if vis is None:
                row["conds"][cond] = {"status": "cache_unavailable", "answer": None}
                continue
            # 条件语义防冒名：VLM 侧必须真的有落盘裁剪文件，否则记 cache_unavailable
            sc = vis.get("stem_matrix_crop")
            need_opts = cond in ("C",)
            if (cond in ("B", "D") and not (sc and Path(str(sc)).is_file())) or \
               (need_opts and not all(o.get("crop_path") and Path(str(o["crop_path"])).is_file()
                                      for o in vis["option_regions"])):
                row["conds"][cond] = {"status": "cache_unavailable", "answer": None}
                continue
            q = build_question(item, vis)
            try:
                sol = skill.solve(q, SkillContext(settings=settings))
                va = None
                if sol.status == "solved":
                    va = Verifier(settings, registry=SkillRegistry()).verify(q, sol).action
                row["conds"][cond] = {"status": sol.status, "answer": sol.answer,
                                      "verify": va}
            except Exception as exc:  # noqa: BLE001
                row["conds"][cond] = {"status": "error", "answer": None,
                                      "err": type(exc).__name__}
        # ---- E：像素重建题干 + 金标选项（走格证据，不挂 prov 门：选项为金标）
        try:
            mask = load_mask(str(FIG / "images" / "dev" / f"{pid}.png"))
            n_opts = len(item["options"])
            res = localize_page(mask, n_options=n_opts)
            from assessment_agent.tools.diagram_grid_evidence import merge_stem_regions
            sr_list = merge_stem_regions(res.get("stem_regions") or [])
            sr = (sr_list or [None])[0]
            if not sr:
                raise RuntimeError("no_stem_band")
            x, y, w, h = sr["bbox_page"]
            ev = build_grid_evidence(mask, "e", [x, y, x + w, y + h], 0, 0,
                                     expects_blank=True, cells_hint=sr.get("cells"),
                                     rows_hint=int(sr.get("sub_band_count") or 1))
            seq = known_sequence(ev)
            if seq.get("mode") in (None, "unsupported"):
                raise RuntimeError(f"evidence_{seq.get('reason')}")
            canvas = render_canvas(mask, ev)
            sp = tmp / "stem_evidence.png"
            import cv2
            cv2.imwrite(str(sp), np.where(canvas, 0, 255).astype(np.uint8))
            r = item["regions"]
            gx, gy, gw_, gh_ = r["stem_matrix_bbox"]
            tgt = ev.get("target_cell")
            gold_grid = r.get("stem_matrix_grid")
            row["conds"]["E_meta"] = {
                "grid": [ev["grid_rows"], ev["grid_cols"]], "gold_grid": gold_grid,
                "grid_correct": [ev["grid_rows"], ev["grid_cols"]] == list(gold_grid or []),
                "target": tgt, "target_rowmajor": seq.get("target_index_rowmajor")}
            vision = {"bbox_format": "pixel_xywh", "image_size": None,
                      "stem_matrix_crop": str(sp),
                      "stem_matrix_grid": [ev["grid_rows"], ev["grid_cols"]],
                      "option_regions": [{"label": o["label"], "bbox": o["bbox"]}
                                         for o in r.get("option_regions", [])]}
            q = build_question(item, vision)
            sol = skill.solve(q, SkillContext(settings=settings))
            va = None
            if sol.status == "solved":
                va = Verifier(settings, registry=SkillRegistry()).verify(q, sol).action
            row["conds"]["E"] = {"status": sol.status, "answer": sol.answer, "verify": va}
        except Exception as exc:  # noqa: BLE001
            row["conds"]["E"] = {"status": "abstain", "answer": None,
                                 "err": str(exc)[:80]}
        # ---- F：像素题干 + 像素选项（完整回退接线 + 验证器复核）
        vision = {"bbox_format": "pixel_xywh", "image_size": None,
                  "stem_matrix_crop": None, "option_regions": []}
        q = build_question(item, vision)
        ctx = SkillContext(settings=settings)
        sol = skill.solve(q, ctx)
        v_action = None
        if sol.status == "solved":
            v = Verifier(settings, registry=SkillRegistry()).verify(q, sol)
            v_action = v.action
        row["conds"]["F"] = {"status": sol.status, "answer": sol.answer,
                             "verify": v_action}
        out_rows.append(row)

    def summarize(cond):
        from assessment_agent.tools.release_scoring import summarize_rows
        return summarize_rows(out_rows, cond)

    ev_out = {"answerability_flags_seen": ab_flags, "conditions": {c: summarize(c) for c in "ABCDEF"},
              "llm_calls_used": BUDGET["llm_calls"],
              "per_item": out_rows,
              "note": "E/F 条件名对应实际像素重建裁剪内容；F 含验证器独立复核"}
    out_path = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv \
        else ROOT / "evidence/R12A_SIX_CONDITION_RESCORED.json"
    out_path.write_text(json.dumps(ev_out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(ev_out["conditions"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
