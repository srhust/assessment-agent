"""R14-E tight 排版**覆盖缺口的成因诊断**（零 API、零改动生产逻辑）。

背景：`R14E_NEWLAYOUT_REGRESSION_V2` 记录 7 张 tight 页中 3 页拿到完整真实
选项证据、**4 页消费 0 单元**（grid-10 / overlay-13 / scale-09 / trans-07
的 --tight 变体）。STOPGATE 裁决点 5 要人工定"是否纳入 R14 范围"。人工要的是
**逐页因果**，不是"它们失败了"这句话。本脚本只做测量：

  1. 生产侧**权威**原因：跑真实 Solver，摘出它自己写的 note
     （`option_cells_not_label_anchored` / `option_band_not_last` /
     `labels_from_row_order_no_letter_evidence` / 完整性未过 idx[k] …）；
  2. 局注器侧实测：末带位置、每个聚类单元的 bbox/ink/尺寸、完整性
     **具体子判据**（`min(h,w) >= MIN_CELL_PX` 还是 `ratio >= MIN_INK_RATIO`）；
  3. 页面真值（独立测量，不用任何金标）：末带列段宽度/高度/墨/是否实心，
     以及"该段是否可能是**真图形**"的中性描述（长宽比、墨占比）；
  4. 两条候选路径为何不走：锚点路径（有无字母层）与**行序无字母**路径
     （Solver 同一套谓词：单元数/单行/等距/宽高一致/墨一致）逐项给值；
  5. 对照件：证据完整的 3 页 + 真实截图 4 页，用同一张表测量。

本脚本**不改阈值、不加规则、不硬编码 case-id**（页名只来自语料目录，
判据全部页面像素导出）。结论一律写成"哪一gate在哪个数值上拒绝"，是否
**该**覆盖这些排版由人工裁决（STOPGATE 5）。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import numpy as np                                          # noqa: E402
from assessment_agent.artifacts import RunArtifacts          # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry  # noqa: E402
from assessment_agent.tools.figure_page_localizer import (   # noqa: E402
    localize_page, row_bands, col_segments, cluster_cells, _option_band_cells)
from assessment_agent.tools.figure_tools import load_mask    # noqa: E402
from assessment_agent.tools.diagram_layout import MIN_CELL_PX, MIN_INK_RATIO  # noqa: E402
from tests.conftest import make_settings                    # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from e2e_r14_newlayout_regression import build_pages, make_question  # noqa: E402

AUTHORITATIVE_NOTES = ("option_cells_not_label_anchored", "option_band_not_last",
                       "labels_from_row_order_no_letter_evidence",
                       "options_band_not_detected",
                       "stem_evidence_ambiguous_layout",
                       "选项裁剪完整性未过", "定位到 0 个选项区")


def completeness_subreason(cell, mask):
    """把 `completeness.passed=False` 归到**具体子判据**（整幅基语义）。"""
    bb = cell["bbox_page"]
    w, h = int(bb[2]), int(bb[3])
    ratio = float(cell["completeness"].get("ink_ratio") or 0.0)
    fails = []
    if ratio < MIN_INK_RATIO:
        fails.append(f"ink_ratio {ratio:.5f} < MIN_INK_RATIO {MIN_INK_RATIO}")
    if min(h, w) < MIN_CELL_PX:
        fails.append(f"min(h,w)={min(h, w)} < MIN_CELL_PX={MIN_CELL_PX}")
    return fails


def bottom_band_runs(mask):
    """末带列段的**中性**测量：宽度/高度/墨/实心度（不判定它"是不是选项"）。"""
    bands = row_bands(mask)
    if not bands:
        return None
    y0, y1 = bands[-1]
    segs = col_segments(mask, y0, y1, min_col_ink=1, gap=2, min_ink=1)
    out = []
    for a, b in segs:
        sub = mask[y0:y1, a:b]
        rows = np.nonzero(sub.any(axis=1))[0]
        h = int(rows[-1] - rows[0] + 1) if len(rows) else 0
        w = b - a
        ink = int(sub.sum())
        out.append({"x": [a, b], "w": w, "h": h, "ink": ink,
                    "solid_ratio": round(ink / max(1, w * h), 3),
                    "aspect_w_over_h": round(w / max(1, h), 3)})
    return {"band": [int(y0), int(y1)], "n_runs": len(out), "runs": out}


def anchor_mirror(mask, bands, expect):
    """锚点路径**为何不走**：只报告真实 `_option_band_cells` 的返回值 +
    HEAD 候选集（末带 / 末两带）的带高，与字母层是否存在（同一判据 8-16/5-18）。
    不重新实现判据链，避免复算与生产漂移。"""
    res = {}
    for tag, idx in (("last_band", [len(bands) - 1]),
                     ("last_two_bands", [len(bands) - 2, len(bands) - 1])):
        if len(idx) > 1 and len(bands) < 2:
            res[tag] = "skip:不足两带"
            continue
        y0, y1 = bands[idx[0]][0], bands[idx[-1]][1]
        if y1 - y0 > 40:
            res[tag] = f"skip:带高 {y1 - y0} > 40"
            continue
        got = _option_band_cells(mask, y0, y1, expect)
        # 字母层存在性（同一尺寸判据，纯测量）
        from scipy import ndimage as ndi
        lab, nc = ndi.label(mask[y0:y1, :], structure=np.ones((3, 3), int))
        heights = []
        for s in range(1, nc + 1):
            ys, xs = np.nonzero(lab == s)
            if len(ys) == 0:
                continue
            heights.append((int(ys.max() - ys.min() + 1),
                            int(xs.max() - xs.min() + 1)))
        letter_layers = {}
        for h, w in heights:
            if 8 <= h <= 16 and 5 <= w <= 18:
                letter_layers[h] = letter_layers.get(h, 0) + 1
        res[tag] = {"band_h": int(y1 - y0), "nc": int(nc),
                    "anchored_cells": None if got is None else len(got["cells"]),
                    "letter_layers_h_to_count": letter_layers,
                    "layer_counts_matching_expect": sorted(
                        h for h, c in letter_layers.items() if c == expect)}
    return res


def row_order_mirror(mask, declared):
    """Solver 的"无标签字母版式"候选路径（cand_n 2..6）**同谓词**逐项给值：
    只测量、不改变判定；谓词与 figure_reasoning.py 消费处一一对应。"""
    out = []
    for cand_n in range(2, 7):
        trial = localize_page(mask, n_options=cand_n)
        tc = trial.get("option_regions") or []
        item = {"cand_n": cand_n, "n_cells": len(tc),
                "notes": trial.get("notes") or []}
        if len(tc) != cand_n:
            item["stop_at"] = "单元数 != cand_n（选项带未定位到该数量）"
            out.append(item)
            continue
        all_letterless = all(c.get("has_letter_labels") is False for c in tc)
        all_complete = all((c.get("completeness") or {}).get("passed") for c in tc)
        all_ink20 = all(int(c.get("ink") or 0) >= 20 for c in tc)
        ws = [c["bbox_page"][2] for c in tc]
        hs = [c["bbox_page"][3] for c in tc]
        st = [c["bbox_page"][0] for c in tc]
        pits = [st[i + 1] - st[i] for i in range(len(st) - 1)]
        med_w = float(np.median(ws)) if ws else 0
        med_p = float(np.median(pits)) if pits else 0
        med_i = float(np.median([int(c["ink"]) for c in tc]))
        same_row = len({tuple(c["band"]) for c in tc}) == 1
        pitch_ok = bool(pits) and med_p > 0 and (max(pits) - min(pits)) <= max(
            4.0, 0.15 * med_p)
        width_ok = all(0.75 <= w / med_w <= 1.35 for w in ws) if med_w else False
        height_ok = (max(hs) - min(hs)) <= 4
        ink_ok = all(abs(int(c["ink"]) - med_i) <= 0.25 * med_i for c in tc)
        item.update({
            "has_letter_labels": [c.get("has_letter_labels") for c in tc],
            "all_letterless": all_letterless,
            "all_complete": all_complete,
            "completeness_fails": {int(c["index"]): completeness_subreason(c, mask)
                                   for c in tc
                                   if not (c.get("completeness") or {}).get("passed")},
            "all_ink_ge_20": all_ink20,
            "same_row": same_row, "pitch": pits, "widths": ws, "heights": hs,
            "inks": [int(c["ink"]) for c in tc],
            "predicates": {"letterless": all_letterless, "complete": all_complete,
                           "ink20": all_ink20, "same_row": same_row,
                           "pitch_uniform": pitch_ok, "width_uniform": width_ok,
                           "height_uniform": height_ok, "ink_uniform": ink_ok},
        })
        fails = [k for k, v in item["predicates"].items() if not v]
        item["stop_at"] = "全部通过（该路径会给出真实选项证据）" if not fails \
            else "不通过谓词：" + ",".join(fails)
        out.append(item)
    return out


def main() -> int:
    pages = build_pages()
    reg = SkillRegistry()
    fr = reg.require("figure_reasoning")
    wanted = [p for p in pages if "--tight" in p["pid"]]
    controls = [p for p in pages if p["kind"] == "real"
                and "--tight" not in p["pid"]]
    report = {}
    for pg in wanted + controls:
        rid = pg["pid"]
        if rid in report:
            rid = f"{rid}[{pg['kind']}]"
        m = np.asarray(load_mask(pg["img"]), dtype=bool)
        bands = row_bands(m)
        ink_rows = np.nonzero(m.any(axis=1))[0]
        q = make_question(pg)
        st = make_settings(Path(tempfile.mkdtemp()))
        st.r4g_layout = "pixel_only"
        ctx = SkillContext(settings=st, artifacts=RunArtifacts(
            st.runs_dir, run_id=f"gap-{rid}"))
        sol = fr.solve(q, ctx)
        err = sol.error or ""
        notes_hit = [n for n in AUTHORITATIVE_NOTES if n in err]
        # **两个量分开**（V2 曾把它们混同，V3 已勘误）：
        #   saved_option_crops  = 实际落盘的 option_*.png 份数
        #   evidence_cells      = Solver 证据链里的选项单元数
        saved = []
        for d in (ctx.artifacts.dir / "regions_pixel", ctx.artifacts.dir / "regions"):
            if d.is_dir():
                for p in sorted(d.glob("option_*.png")):
                    saved.append({"file": p.name,
                                  "ink": int(load_mask(str(p)).sum())})
        prod = localize_page(m, n_options=len(q.options or []) or 2)
        cells = prod.get("option_regions") or []
        evidence_cells = ((sol.meta or {}).get("region_provenance") or {}
                          ).get("option_cells") or []
        report[rid] = {
            "kind": pg["kind"], "declared_options": len(q.options or []),
            "saved_option_crops": len(saved),
            "saved_crop_inks": [s["ink"] for s in saved],
            "evidence_cells": len(evidence_cells),
            "cause_class": None,
            "mask_hw": [int(m.shape[0]), int(m.shape[1])],
            "page_last_ink_row": int(ink_rows[-1]) if len(ink_rows) else None,
            "n_bands": len(bands), "bands_tail": [[int(a), int(b)] for a, b in bands[-3:]],
            "solver_status": sol.status,
            "authoritative_production_notes": notes_hit,
            "solver_error_excerpt": err[:260],
            "localizer_at_declared": {
                "n_cells": len(cells),
                "notes": prod.get("notes") or [],
                "cells": [{"index": c["index"], "bbox": c["bbox_page"],
                           "ink": c["ink"],
                           "completeness_passed": bool(
                               (c.get("completeness") or {}).get("passed")),
                           "completeness_subreason_if_fail":
                               completeness_subreason(c, m),
                           "has_letter_labels": c.get("has_letter_labels")}
                          for c in cells]},
            "bottom_band_runs_measured": bottom_band_runs(m),
            "anchor_path_mirror": anchor_mirror(m, bands, len(q.options or []) or 2),
            "row_order_path_mirror": row_order_mirror(m, len(q.options or []) or 2),
        }
        # ---- 成因分类（由上面测到的量导出，不引入新判定）
        fails = [c for c in report[rid]["localizer_at_declared"]["cells"]
                 if not c["completeness_passed"]]
        if saved and not evidence_cells:
            report[rid]["cause_class"] = (
                "C 裁剪已落盘但证据链为空：随后在**题干**侧弃权"
                "（" + ("、".join(notes_hit) or err[:60]) + "）；"
                "注意这不是\"选项证据缺失\"，而是选项证据未进入可核对链路")
        elif not saved and not cells:
            report[rid]["cause_class"] = (
                "A 选项带根本未定位：localizer 在声明数下给 0 单元"
                "（notes=" + str(prod.get("notes") or []) + "）")
        elif not saved and cells and fails:
            report[rid]["cause_class"] = (
                "B 单元定位到但有格裁剪完整性未过：" +
                ";".join(f"idx{f['index']}:" + ",".join(
                    report[rid]["localizer_at_declared"]["cells"][n]
                    ["completeness_subreason_if_fail"])
                    for n, f in enumerate(cells)
                    if not f["completeness"]["passed"]))
        elif saved and evidence_cells:
            report[rid]["cause_class"] = "D 证据完整（对照件）"
        else:
            report[rid]["cause_class"] = "其他（见明细）"
    gap = {k: v for k, v in report.items()
           if "--tight" in k and v["saved_option_crops"] == 0}
    gap_aborted = {k: v for k, v in report.items()
                   if "--tight" in k and v["saved_option_crops"] > 0
                   and v["evidence_cells"] == 0}
    merged = {k: [i for i in v["saved_crop_inks"] if i > 250]
              for k, v in report.items() if "--tight" in v or True}
    merged = {k: v for k, v in merged.items() if v}
    out = {"pages": report, "gap_pages_no_crop_at_all": sorted(gap),
           "gap_pages_crop_saved_but_not_in_evidence": sorted(gap_aborted),
           "crops_with_suspicious_high_ink_gt_250": merged,
           "quantity_definitions": {
               "saved_option_crops": "落盘 option_*.png 份数（Solver 中途弃权也可能已写盘）",
               "evidence_cells": "region_provenance.option_cells 单元数（可核对证据链）",
               "V2_erratum": "V2 的 tight 汇总把 evidence_cells 标成\"消费单元\"，"
                             "故\"4 页选项 0 份\"表述过强：实测其中 2 页有 4 份裁剪落盘"
                             "（grid-10--tight、overlay-13--tight），另 2 页确为 0 份"},
            "gate_constants": {"MIN_CELL_PX": MIN_CELL_PX,
                              "MIN_INK_RATIO": MIN_INK_RATIO},
           "no_changes_made": "本脚本不调阈值、不加规则、不改 src；仅测量。",
           "llm_calls_used": 0, "http_requests_made": 0}
    dst = ROOT / "evidence" / "R14E_TIGHT_GAP_DIAGNOSIS.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"gap_no_crop": sorted(gap),
                      "gap_crop_but_not_evidence": sorted(gap_aborted),
                      "suspicious_ink": merged,
                      "n_pages": len(report),
                      "per_page_cause": {
                          k: {"cause": v["cause_class"],
                              "saved_crops": v["saved_option_crops"],
                              "crop_inks": v["saved_crop_inks"],
                              "evidence_cells": v["evidence_cells"],
                              "prod_notes": v["authoritative_production_notes"]}
                          for k, v in report.items() if "--tight" in k}},
                     ensure_ascii=False, indent=1))
    print("written", dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
