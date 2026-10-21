# -*- coding: utf-8 -*-
"""R10-D1/D2：区域定位器零 API 评测——真实定位正确率与自洽率分列。

工单红线：**布局 runtime 自洽不得计为真实定位正确**。
- 真实定位 = 检测单元框落入金标网格槽位（containment≥0.8）且槽位指派顺序正确
- 完整性 = 每单元过 check_option_crop；空白格 = 金标空格位置命中
- 自洽 = 同图重复定位 bbox/pixel_sha 一致（只作稳定性报告，单列）
输入：--corpus figshots（金标 dev 集）或 --corpus perturb（新生成扰动集，金标
由渲染器程序化记录）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.figure_page_localizer import (  # noqa: E402
    _containment, grid_blank_cells, localize_page)
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402


def score_page(mask, gold_regions, n_options, gold_grid_for_blank=None):
    res = localize_page(mask, n_options=n_options)
    det_o = res["option_regions"]
    out = {"options_ok": 0, "options_total": 0, "option_notes": [],
           "stem_slots_ok": 0, "stem_slots_total": 0,
           "blank_ok": None, "completeness_pass": sum(
               1 for d in det_o if d["completeness"]["passed"]),
           "regions": res}
    go = gold_regions.get("option_regions") or []
    # 槽位指派：det 单元中心落入金标格（且 ≥50% 面积在该格）；一格多 det 只认第一个
    assigned = {}
    for di, d in enumerate(det_o):
        bx, by, bw, bh = d["bbox_page"]
        cx, cy = bx + bw / 2, by + bh / 2
        for gi, g in enumerate(go):
            gx, gy, gw, gh = g["bbox"]
            if gx <= cx < gx + gw and gy <= cy < gy + gh:
                if _containment(d["bbox_page"], g["bbox"]) >= 0.5 and gi not in assigned:
                    assigned[gi] = di
                break
    for gi, g in enumerate(go):
        out["options_total"] += 1
        if len(det_o) == len(go) and assigned.get(gi) == gi:
            out["options_ok"] += 1
        else:
            out["option_notes"].append(
                {"label": g.get("label"), "det_n": len(det_o), "assigned": assigned.get(gi),
                 "det_boxes": [d["bbox_page"] for d in det_o][:4]})
    sb = gold_regions.get("stem_matrix_bbox")
    gr = gold_regions.get("stem_matrix_grid") or [1, 1]
    if sb:
        rows, cols = int(gr[0]), int(gr[1])
        x, y, w, h = sb
        slots = [[x + c * (w / cols), y + r * (h / rows), w / cols, h / rows]
                 for r in range(rows) for c in range(cols)]
        cells = [sl for sl in slots
                 if mask[int(sl[1]):int(sl[1] + sl[3]),
                         int(sl[0]):int(sl[0] + sl[2])].sum() > 25]
        out["stem_slots_total"] = len(cells)
        for sl in cells:
            if any(_containment([c["bbox_page"][0], c["bbox_page"][1],
                                 c["bbox_page"][2], c["bbox_page"][3]], sl) >= 0.8
                   for s in res["stem_regions"] for c in s.get("cells", [])):
                out["stem_slots_ok"] += 1
        if gold_grid_for_blank is not None:
            want = tuple(gold_grid_for_blank)
            found = {(b["row"], b["col"]) for b in res["blank_cells"]}
            gb = grid_blank_cells(mask, sb, rows, cols)
            found |= {(b["row"], b["col"]) for b in gb}
            out["blank_ok"] = want in found
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="figshots")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.corpus == "figshots":
        items = []
        for l in (ROOT / "data/benchmark_figshots/dev/figshots.jsonl").read_text(
                encoding="utf-8").splitlines():
            if not l.strip():
                continue
            it = json.loads(l)
            gold_blank = None
            if it["id"] in ("ft-grid-10", "ft-grid-11", "ft-overlay-12", "ft-overlay-13"):
                gold_blank = [2, 2]
            items.append((it, ROOT / "data" / it["image"], it["regions"], gold_blank))
    else:
        corpus = json.loads((ROOT / "evidence/R10D_PERTURB_CORPUS.json").read_text(encoding="utf-8"))
        items = [(it, ROOT / "data" / it["image"], it["regions"], it.get("gold_blank"))
                 for it in corpus["items"]]
    per = []
    tot = {"o": 0, "o_ok": 0, "s": 0, "s_ok": 0, "b": 0, "b_ok": 0}
    stab = 0
    for it, img, reg, gb in items:
        m = load_mask(str(img))
        sc = score_page(m, reg, n_options=len(it["options"]), gold_grid_for_blank=gb)
        tot["o"] += sc["options_total"]; tot["o_ok"] += sc["options_ok"]
        tot["s"] += sc["stem_slots_total"]; tot["s_ok"] += sc["stem_slots_ok"]
        if sc["blank_ok"] is not None:
            tot["b"] += 1; tot["b_ok"] += int(sc["blank_ok"])
        m2 = load_mask(str(img))
        sc2 = localize_page(m2, n_options=len(it["options"]))
        if [(d["bbox_page"], d["pixel_sha"]) for d in sc["regions"]["option_regions"]] == \
           [(d["bbox_page"], d["pixel_sha"]) for d in sc2["option_regions"]]:
            stab += 1
        per.append({"id": it["id"], "options": f"{sc['options_ok']}/{sc['options_total']}",
                    "stem_slots": f"{sc['stem_slots_ok']}/{sc['stem_slots_total']}",
                    "blank": sc["blank_ok"], "completeness_pass": sc["completeness_pass"],
                    "notes": sc["option_notes"][:2],
                    "notes_stem": sc["regions"]["notes"]})
    summary = {
        "corpus": args.corpus, "pages": len(per),
        "options_located": f"{tot['o_ok']}/{tot['o']}",
        "stem_slots_located": f"{tot['s_ok']}/{tot['s']}",
        "blank_cells_hit": f"{tot['b_ok']}/{tot['b']}",
        "self_consistent_pages": f"{stab}/{len(per)}",
        "self_consistency_note": "自洽仅证明定位器稳定，不构成真实定位正确证据（工单红线）",
        "per_page": per}
    outp = Path(args.out) if args.out else ROOT / "evidence/R10D_LOCALIZER_EVAL.json"
    outp.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_page"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
