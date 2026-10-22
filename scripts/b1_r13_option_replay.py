# -*- coding: utf-8 -*-
"""R13-B：真实 VLM 选项证据链零 API 复盘（缓存解析件，非本轮新 HTTP）。

来源声明（红线）：
  * R12-G 首跑原始 VLM 解析件**未落盘**（save=False）→ artifact_unavailable；
  * 本复盘使用 runs/e2e-dev15-20261019T065016Z-* 的**历史真实 VLM 输出**，
    明确标注为缓存件重放，不冒充 R12 模型原始输出；
  * Solver 消费像素与验证拦截原因为**当前代码**在同一输入上的确定性重放。

产物：每题 1) 原图 2) 金标选项 3) VLM 选项区域(缓存) 4) 定位器选项区域
5) Solver 消费裁剪 6) 题干双画布 7) 候选/残差/margin 8) 验证器拦截原因。
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.schemas import Question, QuestionOption  # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry  # noqa: E402
from assessment_agent.tools.diagram_grid_evidence import (  # noqa: E402
    build_grid_evidence, merge_stem_regions, render_canvas)
from assessment_agent.tools.figure_page_localizer import localize_page  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402
from assessment_agent.verifier import Verifier  # noqa: E402
from tests.conftest import make_settings  # noqa: E402

FIG = ROOT / "data" / "benchmark_figshots"
RUNS = ROOT / "runs"
IDS = ["ft-rot90-01", "ft-mirror-04", "ft-trans-07", "ft-overlay-12"]


def sha(arr) -> str:
    if arr is None:
        return ""
    return hashlib.sha256(
        np.ascontiguousarray(np.asarray(arr)).tobytes()).hexdigest()[:16]


def band_img(mask, box, s=3):
    x0, y0, x1, y1 = box
    crop = np.where(mask[y0:y1, x0:x1], 0, 255).astype(np.uint8)
    return cv2.resize(crop, (crop.shape[1] * s, crop.shape[0] * s),
                      interpolation=cv2.INTER_NEAREST)


def row_of(imgs, pad=6):
    hs = [im.shape[0] for im in imgs]
    h = max(hs)
    canvas = np.full((h, sum(im.shape[1] for im in imgs) + pad * (len(imgs) - 1)),
                     200, np.uint8)
    x = 0
    for im in imgs:
        canvas[:im.shape[0], x:x + im.shape[1]] = im
        x += im.shape[1] + pad
    return canvas


def main() -> int:
    items = {json.loads(l)["id"]: json.loads(l)
             for l in (FIG / "dev" / "figshots.jsonl").read_text(encoding="utf-8")
             .splitlines() if l.strip()}
    out = {"stage": "R13-B option evidence replay",
           "provenance": {"r12g_original_parse": "artifact_unavailable (save=False)",
                          "vlm_source": "runs/e2e-dev15-20261019T065016Z-* (历史真实输出，缓存重放)"},
           "items": {}}
    tmp = Path(tempfile.mkdtemp())
    settings = make_settings(tmp)
    reg = SkillRegistry()
    for pid in IDS:
        run = RUNS / f"e2e-dev15-20261019T065016Z-{pid}"
        ex = json.loads((run / "extraction.json").read_text(encoding="utf-8"))
        item = items[pid]
        mask = load_mask(str(FIG / "images" / "dev" / f"{pid}.png"))
        n_opts = len(item["options"])
        # 3) VLM 选项区域（缓存）
        vlm_opts = []
        for o in (ex.get("option_regions") or []):
            if not o.get("label"):
                continue
            cp = run / "regions" / f"option_{o['label']}.png"
            vlm_opts.append({"label": o.get("label"), "bbox": o.get("bbox"),
                             "has_figure": o.get("has_figure"),
                             "crop_sha16": sha(cv2.imread(str(cp),
                                            cv2.IMREAD_GRAYSCALE))})
        # 4) 定位器选项区域
        res = localize_page(mask, n_options=n_opts)
        loc_opts = res.get("option_regions") or []
        # 5)+7)+8) 当前代码重放：用缓存 vision 构造题面
        vision = {"bbox_format": "pixel_xywh",
                  "stem_matrix_crop": None, "stem_matrix_grid": None,
                  "option_regions": [{"label": o["label"], "bbox": o["bbox"]}
                                     for o in vlm_opts],
                  "image_size": [ex.get("image_width"), ex.get("image_height")]}
        q = Question(stem=item["stem"],
                     options=[QuestionOption(label=o["label"], text="图")
                              for o in item["options"]],
                     image_path=str(FIG / "images" / "dev" / f"{pid}.png"),
                     source="image", meta={"vision": vision})
        skill = reg.require("figure_reasoning")
        sol = skill.solve(q, SkillContext(settings=settings))
        prov = (sol.meta or {}).get("region_provenance") or {}
        vr = None
        checks = []
        if sol.status == "solved":
            vr = Verifier(settings, registry=reg).verify(q, sol)
            checks = [{"name": c.get("name"), "passed": c.get("passed"),
                       "message": c.get("message")}
                      for c in (vr.to_dict().get("checks") or [])] \
                if hasattr(vr, "to_dict") else []
        tp = (sol.meta or {}).get("transform") or {}
        cands = []
        for c in (tp.get("candidates") or [])[:6]:
            if isinstance(c, dict):
                cands.append({"rule": c.get("rule_type"),
                              "residual": c.get("geometric_residual"),
                              "margin": c.get("discriminative_margin"),
                              "scores": c.get("candidate_scores")})
        ev_out = {"grid": [sol.status, sol.answer],
                  "verify_action": getattr(vr, "action", None) if vr else None,
                  "verify_final": getattr(vr, "final_answer", None) if vr else None,
                  "stem_crop_source": prov.get("stem_crop_source"),
                  "stem_evidence": prov.get("stem_evidence"),
                  "option_cells_consumed": [
                      {"label": c.get("label"), "bbox": c.get("bbox"),
                       "pixel_sha": c.get("pixel_sha"),
                       "completeness": c.get("completeness")}
                      for c in (prov.get("option_cells") or [])],
                  "candidates_residuals": cands,
                  "verifier_checks": checks,
                  "solver_notes": (sol.meta or {}).get("notes")}
        # 图板
        panels = {}
        cv2.imwrite(str(ROOT / f"evidence/R13B_{pid}_original.png"),
                    band_img(mask, [0, 0, mask.shape[1], mask.shape[0]], 2))
        panels["original"] = f"evidence/R13B_{pid}_original.png"
        go_crops = []
        for o in item["regions"].get("option_regions", []):
            if not o.get("has_figure"):
                continue
            gp = (FIG / o["crop_path"]).resolve()
            im = cv2.imread(str(gp), cv2.IMREAD_GRAYSCALE)
            if im is not None:
                go_crops.append(cv2.resize(im, (max(8, im.shape[1]) * 3,
                                                max(8, im.shape[0]) * 3),
                                           interpolation=cv2.INTER_NEAREST))
        if go_crops:
            p = ROOT / f"evidence/R13B_{pid}_gold_options.png"
            cv2.imwrite(str(p), row_of(go_crops))
            panels["gold_options"] = str(p.relative_to(ROOT))
        vv = [cv2.imread(str(run / "regions" / f"option_{o['label']}.png"),
                         cv2.IMREAD_GRAYSCALE) for o in vlm_opts]
        vv = [cv2.resize(x, (x.shape[1] * 3, x.shape[0] * 3),
                         interpolation=cv2.INTER_NEAREST) for x in vv if x is not None]
        if vv:
            p = ROOT / f"evidence/R13B_{pid}_vlm_options.png"
            cv2.imwrite(str(p), row_of(vv))
            panels["vlm_options"] = str(p.relative_to(ROOT))
        if loc_opts:
            imgs = [band_img(mask, [b["bbox_page"][0], b["bbox_page"][1],
                                    b["bbox_page"][0] + b["bbox_page"][2],
                                    b["bbox_page"][1] + b["bbox_page"][3]], 3)
                    for b in loc_opts]
            p = ROOT / f"evidence/R13B_{pid}_localizer_options.png"
            cv2.imwrite(str(p), row_of(imgs))
            panels["localizer_options"] = str(p.relative_to(ROOT))
        sr = (merge_stem_regions(res.get("stem_regions") or []) or [{}])[0]
        if sr.get("cells"):
            b = sr["bbox_page"]
            ev = build_grid_evidence(mask, pid, [b[0], b[1], b[0] + b[2], b[1] + b[3]],
                                     0, 0, expects_blank=True,
                                     cells_hint=sr["cells"],
                                     rows_hint=int(sr.get("sub_band_count") or 1),
                                     stem_continuation_suspect=bool(
                                         sr.get("continuation_suspect")))
            for mode, tag in (("shape", "shape_canvas"), ("position", "pos_canvas")):
                im = np.where(render_canvas(mask, ev, mode=mode), 0, 255).astype(np.uint8)
                p = ROOT / f"evidence/R13B_{pid}_{tag}.png"
                cv2.imwrite(str(p), cv2.resize(im, (im.shape[1] * 3, im.shape[0] * 3),
                                               interpolation=cv2.INTER_NEAREST))
                panels[tag] = str(p.relative_to(ROOT))
            ev_out["fresh_evidence"] = {
                "grid": [ev["grid_rows"], ev["grid_cols"]],
                "status": ev["verification_status"],
                "release_eligible": ev.get("release_eligible"),
                "safety_notes": ev.get("safety_notes")}
        out["items"][pid] = {"panels": panels, "replay": ev_out,
                             "vlm_options_cached": vlm_opts,
                             "localizer_options": [
                                 {k: o[k] for k in ("bbox_page", "ink", "pixel_sha")}
                                 for o in loc_opts]}
    (ROOT / "evidence/R13B_OPTION_REPLAY.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: {"status_answer": v["replay"]["grid"],
                          "verify": v["replay"]["verify_action"],
                          "notes": str(v["replay"]["solver_notes"])[:90]}
                      for k, v in out["items"].items()}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
