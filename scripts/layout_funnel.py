# -*- coding: utf-8 -*-
"""R6 任务D：布局证据八项漏斗（runtime 与 gold 严格分离）。

背景（评审裁定）：R4g 的 runtime 布局自检是**自洽性**检查，
不等于金标网格核对通过；两类指标必须分开报告，
不允许用 runtime self-consistency 宣称"视觉结构完整"。

八项（逐题）：
1. runtime_layout_verified        运行时几何自检一致（金标无关）
2. gold_grid_correct              检测 rows/cols == 金标 grid（**离线-only**）
3. complete_cell_recall           检测单元数 / 金标单元数（r*c，含空白占位）
4. target_blank_localized         金标网格正确且自检一致（空白在行末，列数即定位）
5. geometric_evidence_usable      区域证据齐 + 裁剪内容自检通过
6. solver_proposed                Solver 给出候选答案
7. independently_verified         验证 accept/repair
8. final_released                 实际释放答案

用法：python scripts/layout_funnel.py --predictions <jsonl> --dataset <jsonl> --out <md>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def funnel_rows(pred_path: Path, ds_path: Path) -> list:
    gold = {}
    for line in ds_path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            d = json.loads(line)
            gold[d["id"]] = d.get("grid")
    rows = []
    for line in pred_path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        p = {**p, **(p.get("vision") or {})}  # 运行时布局字段嵌套在 vision 组
        cid = p["case_id"]
        if p.get("skill") != "figure_reasoning" and "fig" not in cid and "ft-" not in cid:
            continue
        g = gold.get(cid)
        d_rows, d_cols = p.get("detected_rows"), p.get("detected_cols")
        gold_ok = None
        if g and d_rows is not None:
            gold_ok = bool(int(g[0]) == int(d_rows) and int(g[1]) == int(d_cols))
        units = p.get("detected_units")
        recall = None
        if g and isinstance(units, int) and units >= 0:
            recall = round(units / (int(g[0]) * int(g[1])), 3)
        blank_loc = None
        if gold_ok is not None:
            blank_loc = bool(gold_ok and p.get("runtime_layout_consistent"))
        rows.append({
            "id": cid,
            "runtime_layout_verified": p.get("runtime_layout_consistent"),
            "gold_grid": g, "detected_grid": [d_rows, d_cols] if d_rows is not None else None,
            "gold_grid_correct": gold_ok,
            "complete_cell_recall": recall,
            "target_blank_localized": blank_loc,
            "geometric_evidence_usable": bool(p.get("region_evidence_complete"))
                and bool(p.get("crop_content_integrity", True)),
            "solver_proposed": p.get("predicted") is not None,
            "independently_verified": p.get("verification_action") in ("accept", "repair"),
            "final_released": p.get("verification_action") in ("accept", "repair")
                and p.get("predicted") is not None,
            "layout_source": p.get("layout_source"),
            "ambiguity": p.get("layout_ambiguity_reason"),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = funnel_rows(Path(a.predictions), Path(a.dataset))
    keys = ["runtime_layout_verified", "gold_grid_correct", "complete_cell_recall",
            "target_blank_localized", "geometric_evidence_usable", "solver_proposed",
            "independently_verified", "final_released"]
    n = len(rows)
    lines = ["# 布局证据八项漏斗（runtime ≠ gold）", "",
             f"样本：{n} 题（figure 类）", "",
             "| 项 | True | False | None/不适用 |", "|---|---|---|---|"]
    for k in keys:
        t = sum(1 for r in rows if r.get(k) is True)
        f = sum(1 for r in rows if r.get(k) is False)
        lines.append(f"| {k} | {t}/{n} | {f} | {n - t - f} |")
    lines += ["", "## 逐题", "",
              "| id | runtime | 金标→检测 grid | gold_ok | recall | blank定位 | 证据可用 | proposed | verified | released | 来源/备注 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['runtime_layout_verified']} | {r['gold_grid']}→{r['detected_grid']} "
            f"| {r['gold_grid_correct']} | {r['complete_cell_recall']} | {r['target_blank_localized']} "
            f"| {r['geometric_evidence_usable']} | {r['solver_proposed']} | {r['independently_verified']} "
            f"| {r['final_released']} | {r['layout_source']} {('；' + str(r['ambiguity'])) if r['ambiguity'] else ''} |")
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    mism = [r["id"] for r in rows if r["gold_grid_correct"] is False and r["runtime_layout_verified"] is True]
    print(f"[funnel] rows={n} -> {a.out}")
    print("[runtime通过但金标网格不符]", mism if mism else "无")
    return 0


if __name__ == "__main__":
    sys.exit(main())
