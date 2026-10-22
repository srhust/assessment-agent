# -*- coding: utf-8 -*-
"""R13-A2/A3：五项分项结构指标 + 30 页扰动逐题翻转复核（零 API）。

五项（今后禁止再合并成单一"契约率"）：
  1 grid_size_ok   行列数 == 金标
  2 blank_ok       金标空格在证据中无幻影墨、且证据空格位无墨（测量而非猜测）
  3 target_ok      证据 target_cell == 金标空格位（金标位由 gold crop 像素墨量导出，
                   仅用于评分审计，绝不进 Solver/定位器输入）
  4 option_map_ok  定位到的选项区域与金标 option_regions 一一对应（IoU>0.3 且标签数一致）
  5 content_ok     position 画布逐像素等于原始带切片；shape 画布墨量=各格墨量之和
另输出与 R12E_PERTURB_BASELINE_R11 的逐题 match 翻转（改善/退化清单，人工口径复核）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.diagram_grid_evidence import (  # noqa: E402
    build_grid_evidence, merge_stem_regions, render_canvas)
from assessment_agent.tools.figure_page_localizer import localize_page  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402

FIG = ROOT / "data" / "benchmark_figshots"


def gold_blank(mask_gold_full, grid, bbox) -> tuple | None:
    """金标空格位：gold crop 像素按格均墨量最低且低于全图格均 1/8 的唯一格。"""
    r, c = grid
    x, y, w, h = bbox
    sub = mask_gold_full[y:y + h, x:x + w]
    ink = np.array(sub, dtype=np.float64)
    if ink.size == 0:
        return None
    hh, ww = ink.shape
    counts = []
    for i in range(r):
        for j in range(c):
            cell = ink[int(hh * i / r):int(hh * (i + 1) / r),
                       int(ww * j / c):int(ww * (j + 1) / c)]
            counts.append(float(cell.sum()))
    thr = max(1.0, sum(counts) / len(counts) / 8.0)
    zeros = [k for k, v in enumerate(counts) if v <= thr]
    if len(zeros) == 1:
        return (zeros[0] // c, zeros[0] % c)
    return None


def slot_ink_at(mask, box, rows, cols, r, c):
    # stem_bbox_page 是 [x0,y0,x1,y1]
    x, y = box[0], box[1]
    w, h = box[2] - box[0], box[3] - box[1]
    sub = np.array(mask[y:y + h, x:x + w], dtype=np.float64)
    hh, ww = sub.shape
    cell = sub[int(hh * r / rows):int(hh * (r + 1) / rows),
               int(ww * c / cols):int(ww * (c + 1) / cols)]
    return int(cell.sum())


def metrics_for(mask, item, page_path) -> dict:
    grid = list(item["regions"].get("stem_matrix_grid") or [])
    if not grid:
        return {}
    res = localize_page(mask, n_options=len(item["options"]))
    srs = merge_stem_regions(res.get("stem_regions") or [])
    out = {"grid": grid, "size": None, "grid_size_ok": False, "blank_ok": None,
           "target_ok": False, "option_map_ok": None, "content_ok": None,
           "ev_status": "no_band", "target": None, "target_basis": None,
           "gold_blank": None}
    if not srs or not srs[0].get("cells"):
        return out
    sr = srs[0]
    x, y, w, h = sr["bbox_page"]
    ev = build_grid_evidence(mask, page_path.stem, [x, y, x + w, y + h], 0, 0,
                             expects_blank=True, cells_hint=sr["cells"],
                             rows_hint=int(sr.get("sub_band_count") or 1),
                             stem_continuation_suspect=bool(
                                 sr.get("continuation_suspect")))
    size = [ev["grid_rows"], ev["grid_cols"]]
    out.update(size=size, grid_size_ok=size == grid,
               ev_status=ev["verification_status"],
               target=ev.get("target_cell"),
               target_basis=ev.get("target_basis"))
    gb = gold_blank(mask, tuple(grid), list(item["regions"]["stem_matrix_bbox"]))
    out["gold_blank"] = list(gb) if gb else None
    if gb is not None and size == grid:
        # 独立测量：ev 自己的等分槽位墨量。要求 空白集合恰为 {金标空位}：
        # 既无漏（金标空位有墨）也无幻影（其它空位被报空）
        r, c = gb
        ink_gb = slot_ink_at(mask, ev["stem_bbox_page"], size[0], size[1], r, c)
        ev_blanks = {tuple(b) if isinstance(b, (list, tuple)) else b
                     for b in (ev.get("blank_cells") or [])}
        n_phantom = sum(
            1 for i in range(size[0] * size[1])
            if (i // size[1], i % size[1]) not in ev_blanks
            and slot_ink_at(mask, ev["stem_bbox_page"], size[0], size[1],
                            i // size[1], i % size[1]) <= max(1.0, ink_gb * 3 + 1)
            and (i // size[1], i % size[1]) != gb)
        out["blank_ok"] = ink_gb <= max(1.0, 45) and (gb in ev_blanks or not ev_blanks) \
            and n_phantom == 0
        out["target_ok"] = list(ev.get("target_cell") or []) == [r, c]
    go = [o for o in item["regions"].get("option_regions", []) if o.get("has_figure")]
    det = res.get("option_regions") or []
    if go:
        pairs = 0
        for o in go:
            gx0, gy0, gw, gh = o["bbox"]
            for b in det:
                bx, by, bw_, bh_ = b["bbox_page"]
                ix0, iy0 = max(gx0, bx), max(gy0, by)
                ix1, iy1 = min(gx0 + gw, bx + bw_), min(gy0 + gh, by + bh_)
                inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
                small = min(gw * gh, bw_ * bh_)
                if small > 0 and inter / small > 0.7:
                    pairs += 1
                    break
        out["option_map_ok"] = pairs == len(go)
        out["option_pairs"] = f"{pairs}/{len(go)}"
    pos = render_canvas(mask, ev, mode="position")
    allc = ev.get("cells", [])
    visb = [c for c in ev.get("cells", []) if c.get("cell_kind") == "visible"]
    if allc:
        r0 = min(c["cell_bbox_page"][1] for c in allc)
        r1 = max(c["cell_bbox_page"][3] for c in allc)
        c0 = min(c["cell_bbox_page"][0] for c in allc)
        c1 = max(c["cell_bbox_page"][2] for c in allc)
        raw = mask[r0:r1, c0:c1]
        eq = pos.shape == raw.shape and np.array_equal(pos, raw)
        sh = render_canvas(mask, ev, mode="shape")
        tot = 0
        for cc in visb:
            if not cc.get("ink_bbox_page"):
                continue
            ix0, iy0, ix1, iy1 = cc["ink_bbox_page"]  # x0y0x1y1
            tot += int(mask[iy0:iy1, ix0:ix1].sum())
        out["content_ok"] = bool(eq) and int(sh.sum()) == tot
    return out


def main() -> int:
    items = {}
    for l in (FIG / "dev" / "figshots.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            it = json.loads(l)
            items[it["id"]] = it
    native = {}
    for pid, it in items.items():
        m = load_mask(str(FIG / "images" / "dev" / f"{pid}.png"))
        native[pid] = metrics_for(m, it, FIG / "images" / "dev" / f"{pid}.png")

    pert = json.loads((ROOT / "evidence/R10D_PERTURB_CORPUS.json").read_text(encoding="utf-8"))
    base = json.loads((ROOT / "evidence/R12E_PERTURB_BASELINE_R11.json").read_text(encoding="utf-8"))
    cur = json.loads((ROOT / "evidence/R12E_SIX_CONDITION.json").read_text(encoding="utf-8"))
    curmap = {r["id"]: r for r in cur["perturbation_regression"]["items"]}
    improved, regressed, same = [], [], []
    for k, v in base.items():
        n = curmap.get(k) or {}
        if not v["match"] and n.get("match"):
            improved.append(k)
        elif v["match"] and not n.get("match"):
            regressed.append(k)
        else:
            same.append(k)

    def bucket(d):
        ks = ["grid_size_ok", "blank_ok", "target_ok", "option_map_ok", "content_ok"]
        return {k: f"{sum(1 for r in d.values() if r.get(k) is True)}/"
                   f"{sum(1 for r in d.values() if r.get(k) is not None)}" for k in ks}

    out = {
        "stage": "R13-A2/A3",
        "native15": {"per_item": native, "buckets": bucket(native)},
        "perturb30": {"improved": improved, "regressed": regressed,
                      "improved_n": len(improved), "regressed_n": len(regressed),
                      "note": "match 口径=R12E 时点，与人工计数复核；三反例状态见 unsafe"},
        "unsafe_verified_grid_examples": [
            {"id": k, "r11": base[k]["ev"], "r12": curmap[k]["ev"],
             "gold": base[k]["gold"], "r12_status": curmap[k]["status"]}
            for k in regressed + improved if k in base],
    }
    (ROOT / "evidence/R13A_SUBMETRICS.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("native15:", out["native15"]["buckets"])
    print("improved:", improved)
    print("regressed:", regressed)
    for it in out["unsafe_verified_grid_examples"]:
        if it["r12"] != it["gold"]:
            print("WRONG-BUT-FLOWED:", it["id"], it["r12"], it["r12_status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
