# -*- coding: utf-8 -*-
"""R13-D 三通道对照：shape 归一 / position 保序 / 规则驱动联合（零 API）。

通道定义
  CH1 shape-normalized：求解器现协议——紧墨裁剪 + 规则变换后的 IoU 型残差。
  CH2 position-preserving：保留格内偏移的比较——题干画布(position) 与
      选项 geometry_frame 等比框渲染；对**平移族**用"上一图按格距平移"预测。
  CH3 joint：规则驱动仲裁——
      * rotate/flip/overlay 族（位置不变规则）→ CH1 权威；
      * translate 族（位置敏感规则）→ CH2 权威，CH1 仅参考；
      * 两通道都有权威判定且结论冲突 → 弃权（绝不平均分数）。
输出：evidence/R13D_CHANNEL_AUDIT.json（每题×通道：残差/次序/margin/gold 名次）。
"""
from __future__ import annotations

import hashlib
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
from assessment_agent.tools.option_evidence import (  # noqa: E402
    label_anchor_frames, option_evidence)

FIG = ROOT / "data/benchmark_figshots"
IDS = ["ft-rot90-01", "ft-mirror-04", "ft-trans-07", "ft-trans-08",
       "ft-overlay-12"]


def _crop(mask, box):
    x0, y0, x1, y1 = box
    return np.asarray(mask[max(0, y0):y1, max(0, x0):x1], dtype=np.uint8)


def _resize_bin(a, w, h):
    import cv2
    if a.size == 0:
        return np.zeros((h, w), np.uint8)
    return cv2.resize(a, (max(1, w), max(1, h)), interpolation=cv2.INTER_NEAREST)


def _residual(pred, tgt):
    """IoU 残差：1 - |∩|/|∪|，两者先放到同尺寸画布居中。"""
    H = max(pred.shape[0], tgt.shape[0])
    W = max(pred.shape[1], tgt.shape[1])
    P = np.zeros((H, W), np.uint8)
    T = np.zeros((H, W), np.uint8)
    P[0:pred.shape[0], 0:pred.shape[1]] = pred > 0
    T[0:tgt.shape[0], 0:tgt.shape[1]] = tgt > 0
    inter = int((P & T).sum())
    union = int((P | T).sum())
    return 1.0 - inter / union if union else 1.0


def _shifted(pred_crop, slot_box, opt_frame_box):
    """把预测图形放回帧内相对位置（与选项帧渲染同协议）。"""
    fw = opt_frame_box[2] - opt_frame_box[0]
    fh = opt_frame_box[3] - opt_frame_box[1]
    sw = slot_box[2] - slot_box[0]
    sh = slot_box[3] - slot_box[1]
    canvas = np.zeros((max(1, fh), max(1, fw)), np.uint8)
    px = min(fw - 1, max(0, int(round(fw * 0.0))))
    canvas[0:min(sh, fh), 0:min(sw, fw)] = pred_crop[:min(sh, fh), :min(sw, fw)]
    return canvas


def audit(pid, item):
    mask = load_mask(str(FIG / "images/dev" / f"{pid}.png"))
    n_opts = len(item["options"])
    res = localize_page(mask, n_options=n_opts)
    srs = merge_stem_regions(res["stem_regions"])
    if not srs or not srs[0].get("cells"):
        return {"skip": "no_band"}
    sr = srs[0]
    x, y, w, h = sr["bbox_page"]
    ev = build_grid_evidence(mask, pid, [x, y, x + w, y + h], 0, 0,
                             expects_blank=True, cells_hint=sr["cells"],
                             rows_hint=int(sr.get("sub_band_count") or 1),
                             stem_continuation_suspect=bool(
                                 sr.get("continuation_suspect")))
    band = res["bands"][-1]
    frs = label_anchor_frames(mask, band["y0"], band["y1"],
                              band["y1"] - band["y0"])
    oev = option_evidence(mask, (band["y0"], band["y1"]), frs)
    labels = [o["label"] for o in item["options"]][:len(oev)]
    # CH1：紧墨形状残差（当前协议）——对每个候选规则做同款"最后图变换"
    cells = ev["cells"]
    visible = [c for c in cells if c.get("cell_kind") == "visible"]
    tgt_rc = tuple(ev.get("target_cell") or [])
    target = next((c for c in cells if (c["row"], c["col"]) == tgt_rc), None)
    out = {"grid": [ev["grid_rows"], ev["grid_cols"]], "status": ev["verification_status"],
           "options_evidenced": len(oev), "labels": labels, "gold": item["answer"],
           "option_frames": [list(map(int, e["geometry_frame"])) for e in oev],
           "option_fig_assoc": [e["label_association_verified"] for e in oev]}
    if not visible or not target:
        out["skip"] = "no_target"
        return out
    # 取最后一个可见格的墨图
    def cell_crop(c, mode="ink"):
        b = c["ink_bbox_page"] if mode == "ink" else c["cell_bbox_page"]
        return _crop(mask, b)
    vcells = visible
    f_last = cell_crop(vcells[-1], "ink")
    ops = {
        "identity": lambda a: a,
        "rot90cw": lambda a: np.rot90(a, -1),
        "rot180": lambda a: np.rot90(a, 2),
        "rot90ccw": lambda a: np.rot90(a, 1),
        "flip_h": lambda a: np.fliplr(a),
        "flip_v": lambda a: np.flipud(a),
    }
    ch1 = {}
    for name, fn in ops.items():
        pred = fn(f_last)
        resid = {}
        for lab, e in zip(labels, oev):
            fig = _crop(mask, [e["bbox_page"][0], e["bbox_page"][1],
                               e["bbox_page"][0] + e["bbox_page"][2],
                               e["bbox_page"][1] + e["bbox_page"][3]])
            resid[lab] = round(_residual(pred, fig), 6)
        srt = sorted(resid.items(), key=lambda kv: kv[1])
        margin = round(srt[1][1] - srt[0][1], 6) if len(srt) > 1 else 0.0
        ch1[name] = {"best": srt[0][0], "residual": srt[0][1], "margin": margin,
                     "gold_rank": [l for l, _ in srt].index(out["gold"]) + 1
                     if out["gold"] in resid else None}
    out["CH1_shape_tight"] = ch1
    # CH2：保序——帧框渲染（含标签侧偏移）对 identity 规则（平移族核心）
    fr_crops = []
    for e in oev:
        fx0, fy0, fx1, fy1 = e["geometry_frame"]
        fr_crops.append(_crop(mask, [fx0, fy0, fx1, fy1]))
    slot0 = vcells[-1]["cell_bbox_page"]
    pitch = None
    if len(vcells) >= 2:
        a = vcells[-2]["cell_bbox_page"]
        b = vcells[-1]["cell_bbox_page"]
        pitch = b[0] - a[0]
    pred_pos = None
    if pitch:
        tw = target["cell_bbox_page"][2] - target["cell_bbox_page"][0]
        canvas_w = tw + pitch  # 目标槽内预测落位（上一图整体前移一格距）
        cc = np.zeros((fr_crops[0].shape[0], max(1, canvas_w)), np.uint8)
        sub = f_last
        cc[0:min(sub.shape[0], cc.shape[0]), 0:min(sub.shape[1], cc.shape[1])] = sub
        pred_pos = cc
    if pred_pos is not None:
        ch2 = {}
        for lab, fc in zip(labels, fr_crops):
            fc_r = _resize_bin(fc, pred_pos.shape[1], pred_pos.shape[0])
            ch2[lab] = round(_residual(pred_pos, fc_r), 6)
        srt = sorted(ch2.items(), key=lambda kv: kv[1])
        out["CH2_position_identity_shift"] = {
            "best": srt[0][0], "residual": srt[0][1],
            "margin": round(srt[1][1] - srt[0][1], 6) if len(srt) > 1 else 0.0,
            "gold_rank": [l for l, _ in srt].index(out["gold"]) + 1
            if out["gold"] in ch2 else None,
            "scores": ch2}
    # CH3 仲裁说明（规则驱动）
    out["CH3_arbitration"] = {
        "rotate/flip/overlay 族": "CH1 权威（位置不变）",
        "translate 族": "CH2 权威（CH1 丢失偏移不可判）",
        "冲突": "两通道权威结论不一致 → 弃权", "平均分数": "禁止"}
    return out


def main():
    items = {json.loads(l)["id"]: json.loads(l)
             for l in (FIG / "dev" / "figshots.jsonl")
             .read_text(encoding="utf-8").splitlines() if l.strip()}
    gold_opts = {pid: [o["label"] for o in it["options"]]
                 for pid, it in items.items()}
    rows = {}
    for pid in IDS:
        it = items[pid]
        rows[pid] = audit(pid, it)
    (ROOT / "evidence/R13D_CHANNEL_AUDIT.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    main()
