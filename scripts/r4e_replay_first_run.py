"""R4e 同输入离线回放：对冻结的首次真实 VLM 产物重跑求解/验证链路（零 API）。

背景（评审裁决 P1-3/评测要求）：旧码与新码必须在**完全相同的视觉输入**上对照。
视觉输入 = 首跑 `evidence/real_vlm_first_run/<smoke-run>/runs/<run_ref>/` 里
VisionParser 落盘的裁剪（diagram/选项 crop）+ extraction.json 的网格几何。
本脚本不发起任何模型调用，仅重放 figure 适配层（六类通道 + 变换通道），
输出与首跑 predictions 可并排的对照表。

用法：
    $env:PYTHONPATH="src"
    python scripts/r4e_replay_first_run.py --label pre_fix --publish
    python scripts/r4e_replay_first_run.py --label post_fix --publish

产物：
- results/r4e_replay/<label>-<ts>/replay_report.json（全部明细，不覆盖历史）
- --publish 时把报告同步一份到 evidence/r4e_replay/<label>/（入库供评审复核；
  首跑 evidence 目录只读，绝不写入）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.figure_adapter import (  # noqa: E402
    solve_figure_regions, solve_figure_transform_regions,
)

EVIDENCE = ROOT / "evidence" / "real_vlm_first_run" / "smoke-20261019T022717Z"


def _relocate(p: str, run_dir: Path) -> str:
    """把首跑机器上的绝对裁剪路径映射到 evidence 冻结副本内的同名文件。"""
    name = Path(str(p)).name
    for cand in ((run_dir / "regions" / name), (run_dir / "image" / name)):
        if cand.is_file():
            return str(cand)
    return str(p)


def _short(text, limit: int = 240) -> str:
    s = str(text or "")
    return s[:limit]


def replay_case(run_dir: Path, out_dir: Path, gold: dict) -> dict:
    question = json.loads((run_dir / "question.json").read_text(encoding="utf-8"))
    vision = (question.get("meta") or {}).get("vision") or {}
    stem = _relocate(vision.get("stem_matrix_crop") or "", run_dir)
    options = [(str(r.get("label")), _relocate(r.get("crop_path"), run_dir))
               for r in (vision.get("option_regions") or [])
               if r.get("has_figure") and r.get("crop_path")]
    grid_raw = vision.get("stem_matrix_grid")
    grid = tuple(int(x) for x in grid_raw) if grid_raw else None
    case_id = question["id"]
    row: dict = {
        "case_id": case_id,
        "expected": gold.get(case_id, {}).get("expected"),
        "first_run": {
            "solver_proposed": gold.get(case_id, {}).get("solver_proposed_answer"),
            "predicted_option": gold.get(case_id, {}).get("predicted_option"),
            "predicted": gold.get(case_id, {}).get("predicted"),
            "failure_class": gold.get(case_id, {}).get("failure_class"),
            "failure_reason": _short(gold.get(case_id, {}).get("failure_reason")),
            "grid_vlm": list(grid_raw) if grid_raw else None,
            "grid_gold": gold.get(case_id, {}).get("gold_grid"),
        },
        "replay_inputs": {
            "stem_crop": str(Path(stem).resolve().relative_to(ROOT)),
            "grid": list(grid) if grid else None,
            "option_labels": [label for label, _ in options],
        },
    }
    canvas_path = out_dir / f"{case_id}_adapter_canvas.png"
    try:
        six = solve_figure_regions(stem, options, grid=grid,
                                   canvas_path=str(canvas_path))
        row["six_rules"] = {"status": six.get("status"), "answer": six.get("answer"),
                             "rule": six.get("rule"), "reason": _short(six.get("reason"))}
    except Exception as exc:  # noqa: BLE001
        row["six_rules"] = {"status": "error",
                            "reason": f"{type(exc).__name__}: {_short(exc)}"}
    try:
        tr = solve_figure_transform_regions(stem, options, grid=grid)
        v = tr.get("verification") or {}
        row["transform"] = {
            "status": tr.get("status"), "answer": tr.get("answer"),
            "mode": tr.get("mode"),
            "verdict": (tr.get("verdict") or {}).get("verdict"),
            "reason": _short(tr.get("reason")),
            "n_candidates": len(tr.get("candidates") or []),
            "parameter_stable": tr.get("parameter_stable"),
            "independently_verified": tr.get("independently_verified"),
            "verifier_action": v.get("action"),
            "blockers": tr.get("blockers"),
            "overlay_eval": tr.get("overlay_eval"),
            "coord_contract": tr.get("coord_contract"),
            "stem_fingerprint": tr.get("stem_fingerprint"),
        }
    except Exception as exc:  # noqa: BLE001
        row["transform"] = {"status": "error",
                            "reason": f"{type(exc).__name__}: {_short(exc)}"}
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="首跑真实裁剪的离线同输入回放")
    ap.add_argument("--label", required=True,
                    help="代码版本标签（pre_fix=修复前基线 / post_fix=修复后）")
    ap.add_argument("--evidence", default=str(EVIDENCE))
    ap.add_argument("--results", default=str(ROOT / "results" / "r4e_replay"))
    ap.add_argument("--publish", action="store_true",
                    help="复制报告到 evidence/r4e_replay/<label>/（入库）")
    ap.add_argument("--code-sha", default="")
    args = ap.parse_args(argv)

    evidence = Path(args.evidence)
    if not evidence.is_dir():
        print(f"首跑冻结产物不存在：{evidence}", file=sys.stderr)
        return 1
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = Path(args.results) / f"{args.label}-{ts}"
    if out_dir.exists():
        raise FileExistsError(f"回放输出目录已存在（拒绝覆盖）：{out_dir}")
    out_dir.mkdir(parents=True)

    gold = {}
    preds = evidence / "eval_output" / "predictions.jsonl"
    for line in preds.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            gold[r["case_id"]] = r
    # 金标网格从 cases/*.json 读（对比 VLM 网格）
    for case_file in (evidence / "eval_output" / "cases").glob("*.json"):
        c = json.loads(case_file.read_text(encoding="utf-8"))
        cid = c.get("case_id") or case_file.stem
        if cid in gold:
            gold[cid]["gold_grid"] = c.get("gold", {}).get("grid")

    rows = [replay_case(rd, out_dir, gold) for rd in sorted((evidence / "runs").iterdir())
            if rd.is_dir() and (rd / "question.json").is_file()]
    report = {
        "kind": "r4e_same_input_replay",
        "label": args.label,
        "generated_at": ts,
        "code_sha": args.code_sha,
        "evidence_run": evidence.name,
        "api_calls_made": 0,
        "note": ("视觉输入=首跑 VisionParser 落盘裁剪与网格几何，完全相同；"
                 "回放只重跑六类+变换求解/验证链路，零模型调用。首跑产物只读。"),
        "cases": rows,
    }
    (out_dir / "replay_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{args.label}] 同输入回放 {len(rows)} 题 → {out_dir / 'replay_report.json'}")
    for r in rows:
        t = r.get("transform", {})
        print("-" * 68)
        print(f"{r['case_id']}: 首跑 solver={r['first_run']['predicted_option']} "
              f"final={r['first_run']['predicted']} ({r['first_run']['failure_class']})")
        print(f"  six_rules: {r.get('six_rules', {}).get('status')} "
              f"answer={r.get('six_rules', {}).get('answer')} | "
              f"{_short(r.get('six_rules', {}).get('reason'), 90)}")
        print(f"  transform: {t.get('status')} answer={t.get('answer')} "
              f"verdict={t.get('verdict')} cands={t.get('n_candidates')} "
              f"stable={t.get('parameter_stable')} indep={t.get('independently_verified')}")
        print(f"  reason: {_short(t.get('reason'), 140)}")
        if t.get("blockers") is not None:
            print(f"  blockers: {json.dumps(t.get('blockers'), ensure_ascii=False)[:200]}")
        if t.get("coord_contract") is not None:
            print(f"  coord_contract: "
                  f"{json.dumps(t.get('coord_contract'), ensure_ascii=False)[:200]}")

    if args.publish:
        import shutil
        pub = ROOT / "evidence" / "r4e_replay" / args.label
        pub.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out_dir / "replay_report.json",
                        pub / "replay_report.json")
        print(f"已发布评审副本：{pub / 'replay_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
