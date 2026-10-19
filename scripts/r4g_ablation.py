"""R4g P0-3：四臂离线同输入消融（零 API）。

对 R4f dev15 的 15 题**真实入库输入**（VLM extraction payload + 原图）
分别以四档重跑同一 Solver/Verifier/阈值：

  A off          原始 VLM 输出 + 原始适配器（冻结基线，重跑校验一致性）
  B merge        原始 VLM 输出 + 多区域直接合并（接口修复，无像素复核）
  C merge_pixel  B + 确定性像素校验（单元切分/映射/间距/pitch 门槛）
  D pixel_only   原始整图 + 不依赖 VLM 网格结论的确定性布局候选（诊断用）

金标只用于评分对照，绝不进入布局判定（D 臂同样只用 VLM bbox 并集定位题带）。
输出：results/r4g_ablation/<ts>/ablation_report.json + 逐题 CSV + 布局可视化。
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np  # noqa: E402

from assessment_agent.config import Settings  # noqa: E402
from assessment_agent.pipeline import Pipeline  # noqa: E402
from assessment_agent.tools.diagram_layout import (  # noqa: E402
    assemble_stem_layout, check_option_crop)
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARMS = {"A": "off", "B": "merge", "C": "merge_pixel", "D": "pixel_only"}


def load_cases(batch: str) -> list[dict]:
    base = ROOT / "evidence" / "real_vlm_dev15" / batch / "runs"
    dev = {json.loads(l)["id"]: json.loads(l)
           for l in (ROOT / "data/benchmark_figshots/dev/figshots.jsonl")
           .read_text(encoding="utf-8").splitlines() if l.strip()}
    cases = []
    for rd in sorted(base.glob("e2e-*")):
        cid = rd.name.split("Z-")[-1]
        q = json.loads((rd / "question.json").read_text(encoding="utf-8"))
        vision = dict(q.get("meta") or {})
        payload = dict(vision.get("vision") or {})

        def rel(p):
            if not p:
                return None
            src = rd / Path(str(p)).name
            cand = [rd / "regions" / Path(str(p)).name, rd / "image" / Path(str(p)).name]
            for c in cand:
                if c.is_file():
                    return str(c)
            return str(src) if src.is_file() else None

        for d in payload.get("diagram_regions") or []:
            d["crop_path"] = rel(d.get("crop_path"))
        for o in payload.get("option_regions") or []:
            o["crop_path"] = rel(o.get("crop_path"))
        cases.append({
            "id": cid, "stem": q.get("stem") or "", "options": q.get("options") or [],
            "image": str(rd / "image" / "original.png"),
            "meta": {"vision": payload, "grid": vision.get("grid")},
            "gold": dev.get(cid, {}).get("answer"),
            "expected_status": dev.get(cid, {}).get("expected_status", "solved"),
        })
    return cases


def run_arm(arm: str, profile: str, cases: list[dict], out_dir: Path) -> list[dict]:
    settings = Settings(offline=True, r4g_layout=profile)
    pipe = Pipeline(settings)
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        try:
            run = pipe.run(text=c["stem"], image_path=c["image"],
                           options=c["options"], skill="figure_reasoning",
                           save=False, meta=dict(c["meta"]))
            sol, ver = run.solution, run.verification
            released = (ver.final_answer if ver.action in ("accept", "repair") else None)
            layout = (sol.meta or {}).get("diagram_layout") or {}
            rows.append({
                "arm": arm, "case_id": c["id"], "gold": c["gold"],
                "solver_status": sol.status,
                "proposal": sol.answer, "released": released,
                "correct": released == c["gold"] and c["gold"] is not None,
                "false_accept": released is not None and c["gold"] is not None
                                and released != c["gold"],
                "verification_action": ver.action,
                "failure_reason": (sol.error or "")[:220],
                "layout_source": layout.get("layout_source"),
                "layout_verified": layout.get("verified"),
                "layout_mode": layout.get("mode"),
                "layout_cols": layout.get("cols"),
                "declared_cols": layout.get("declared_cols"),
                "ambiguity_reason": layout.get("ambiguity_reason"),
                "channel": (sol.meta or {}).get("channel"),
                "rule": (sol.meta or {}).get("rule"),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({"arm": arm, "case_id": c["id"], "gold": c["gold"],
                         "solver_status": "error", "proposal": None, "released": None,
                         "correct": False, "false_accept": False,
                         "verification_action": "reject",
                         "failure_reason": f"harness:{type(exc).__name__}: {exc}"[:220],
                         "ms": round((time.perf_counter() - t0) * 1000, 1)})
    return rows


def crop_integrity(cases: list[dict]) -> dict:
    """校验 5 的逐题实测：每个有图选项裁剪的墨迹完整性。"""
    out = {}
    for c in cases:
        items = {}
        for o in (c["meta"]["vision"].get("option_regions") or []):
            p = o.get("crop_path")
            if o.get("has_figure") and p and Path(p).is_file():
                items[str(o.get("label"))] = check_option_crop(load_mask(str(p)))
        out[c["id"]] = items
    return out


def visualize(case: dict, profile: str, out_png: Path) -> None:
    """把重建证据画到原图：绿=像素单元边界，红=VLM 区域，蓝=目标空格假设。"""
    import cv2

    img = cv2.imread(case["image"])
    if img is None:
        return
    mask = load_mask(case["image"])
    payload = case["meta"]["vision"]
    stem_regions = [d for d in (payload.get("diagram_regions") or [])
                    if str(d.get("role")) in ("stem_figure", "stem_matrix")]
    res = assemble_stem_layout(mask, stem_regions, payload.get("option_regions") or [],
                               profile=profile, trust_vlm_grid=(profile != "pixel_only"))
    res.pop("_canvas", None)
    h, w = img.shape[:2]
    pad = np.zeros((140, w, 3), np.uint8)
    img = np.vstack([img, pad])
    for u in res.get("units") or []:
        bb = u.get("bbox")
        if not bb:
            continue
        cv2.rectangle(img, (bb[0], bb[1]), (bb[0] + bb[2], bb[1] + bb[3]), (0, 200, 0), 1)
    for d in stem_regions:
        x, y, bw, bh = d["bbox"]
        cv2.rectangle(img, (x, y), (x + bw, y + bh), (0, 0, 220), 1)
    txt = (f"{case['id']} src={res['layout_source']} mode={res['mode']} "
           f"{res['rows']}x{res['cols']} verified={res['verified']} "
           f"{res['ambiguity_reason'] or ''}")
    cv2.putText(img, txt[:110], (4, h + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (30, 30, 30), 1)
    cv2.putText(img, f"declared_cols={res.get('declared_cols')} units={len(res.get('units') or [])}",
                (4, h + 44), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (30, 30, 30), 1)
    rej = " | ".join(str(r.get("reason", ""))[:60] for r in (res.get("rejected") or [])[:2])
    cv2.putText(img, f"rejected: {rej[:110]}", (4, h + 66),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 0, 160), 1)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_png), img)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="dev15-20261019T065016Z")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else ROOT / "results" / "r4g_ablation" / f"r4g-{ts}"
    if out_dir.exists():
        raise FileExistsError(f"拒绝覆盖：{out_dir}")
    out_dir.mkdir(parents=True)

    cases = load_cases(args.batch)
    assert len(cases) == 15, f"期望 15 题，得到 {len(cases)}"
    (out_dir / "input_hashes.json").write_text(json.dumps({
        "code_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                                   capture_output=True, text=True).stdout.strip(),
        "algorithm": "r4g-diagram-layout-v1",
        "batch": args.batch,
        "images": {c["id"]: __import__("hashlib").sha256(
            Path(c["image"]).read_bytes()).hexdigest() for c in cases},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    all_rows, per_arm = [], {}
    for arm, profile in ARMS.items():
        rows = run_arm(arm, profile, cases, out_dir)
        per_arm[arm] = rows
        all_rows.extend(rows)
        # C 臂可视化全部 + 其余臂只画差异题
        for c in cases:
            if arm == "C" or (arm == "B" and c["id"] in {
                    r["case_id"] for r in rows} - {r["case_id"] for r in per_arm["A"]}):
                visualize(c, profile, out_dir / "viz" / f"{arm}_{c['id']}.png")

    summary = {}
    for arm, rows in per_arm.items():
        summary[arm] = {
            "profile": ARMS[arm],
            "layout_verified": sum(1 for r in rows if r.get("layout_verified")),
            "proposal_coverage": sum(1 for r in rows if r.get("proposal")),
            "released": sum(1 for r in rows if r.get("released")),
            "correct": sum(1 for r in rows if r.get("correct")),
            "false_accept": sum(1 for r in rows if r.get("false_accept")),
            "abstain_with_gold": sum(1 for r in rows
                                     if r.get("gold") is not None
                                     and r.get("proposal") is None),
            "refuse_expected": sum(1 for r in rows
                                   if r.get("gold") is None
                                   and not r.get("released")),
            "refusal_reasons": sorted({str(r.get("failure_reason"))[:110]
                                       for r in rows if not r.get("proposal")}),
        }
    report = {
        "kind": "r4g_ablation", "batch": args.batch, "arms": ARMS,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "discipline": ("同输入（15 题真实 VLM payload+原图）/同 Solver/同 Verifier/"
                       "同阈值；金标只用于评分，不进入布局判定；D 臂仅用 VLM bbox "
                       "并集定位题带。A 臂为 off 档重跑，须与 dev15 冻结结果一致。"),
        "summary": summary,
        "rows": all_rows,
    }
    (out_dir / "ablation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out_dir / "ablation_rows.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)

    for arm in ARMS:
        print(arm, ARMS[arm].ljust(11),
              "verified:", summary[arm]["layout_verified"],
              "proposal:", summary[arm]["proposal_coverage"],
              "released:", summary[arm]["released"],
              "correct:", summary[arm]["correct"],
              "FA:", summary[arm]["false_accept"])
    print("输出：", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
