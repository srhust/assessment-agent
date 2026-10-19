"""R4e 同输入离线回放：对冻结的真实 VLM 产物重跑求解/验证链路（零 API）。

背景（评审裁决 P1-3/评测要求）：旧码与新码必须在**完全相同的视觉输入**上对照。
视觉输入 = `evidence/real_vlm_*/<smoke-run>/runs/<run_ref>/` 里 VisionParser
落盘的裁剪（diagram/选项 crop）+ question.json 的网格几何。本脚本不发起任何
模型调用，仅重放 figure 适配层（六类通道 + 变换通道）。

R4f 审计升级（评审阶段A）：
- run_ref ↔ 基准题号（ft-*）可靠映射：run 目录名以 `--case-ids` 的基准题号
  结尾，内部 q_* 仅作为 question.id 留档；expected/failure 字段按映射取自
  原始 predictions，不再为 null。
- 自动代码溯源：实际 git SHA（含 dirty 标记）+ 关键模块 SHA-256 + Python 版本
  写入报告，**禁止空 code_sha**。
- 每个裁剪文件内容 SHA-256 随报告发布：干净克隆可逐字节校验输入同一性。

用法：
    $env:PYTHONPATH="src"
    python scripts/r4e_replay_first_run.py --label post_fix --publish
    python scripts/r4e_replay_first_run.py --label second --publish \
        --evidence evidence/real_vlm_second_run/smoke-20261019T051724Z

产物：
- results/r4e_replay/<label>-<ts>/replay_report.json（全部明细，不覆盖历史）
- --publish 时把报告同步一份到 evidence/r4e_replay/<label>/（入库供评审复核；
  首跑/二跑 evidence 目录只读，绝不写入）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.figure_adapter import (  # noqa: E402
    solve_figure_regions, solve_figure_transform_regions,
)

EVIDENCE = ROOT / "evidence" / "real_vlm_first_run" / "smoke-20261019T022717Z"
KEY_MODULES = (
    "src/assessment_agent/tools/figure_transform.py",
    "src/assessment_agent/tools/figure_transform_tools.py",
    "src/assessment_agent/tools/figure_adapter.py",
    "src/assessment_agent/evaluation/vision_metrics.py",
    "src/assessment_agent/skills/figure_reasoning.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_provenance() -> dict:
    """实际 git SHA + dirty 标记 + 关键模块内容哈希（禁止空 code_sha）。"""
    def git(*args):
        try:
            p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                               text=True, timeout=20)
            return (p.stdout or "").strip() if p.returncode == 0 else ""
        except Exception:  # noqa: BLE001
            return ""
    sha = git("rev-parse", "HEAD")
    dirty_files = git("status", "--porcelain", "--", "src", "scripts", "tests")
    prov = {
        "git_sha": sha or "UNAVAILABLE",
        "worktree_dirty_src": bool(dirty_files),
        "dirty_files": [ln[3:] for ln in dirty_files.splitlines()][:20],
        "python": sys.version.split()[0],
        "module_sha256": {m: _sha256(ROOT / m) for m in KEY_MODULES
                          if (ROOT / m).is_file()},
    }
    prov["effective_code_sha"] = (
        f"{prov['git_sha']}{'-dirty' if prov['worktree_dirty_src'] else ''}")
    return prov


def _relocate(p: str, run_dir: Path) -> str:
    """把首跑机器上的绝对裁剪路径映射到 evidence 冻结副本内的同名文件。"""
    name = Path(str(p)).name
    for cand in ((run_dir / "regions" / name), (run_dir / "image" / name),
                 (run_dir / "regions" / "image" / name)):
        if cand.is_file():
            return str(cand)
    return str(p)


def _short(text, limit: int = 240) -> str:
    s = str(text or "")
    return s[:limit]


def replay_case(run_dir: Path, out_dir: Path, gold: dict, cid: str) -> dict:
    question = json.loads((run_dir / "question.json").read_text(encoding="utf-8"))
    vision = (question.get("meta") or {}).get("vision") or {}
    stem = _relocate(vision.get("stem_matrix_crop") or "", run_dir)
    options = [(str(r.get("label")), _relocate(r.get("crop_path"), run_dir))
               for r in (vision.get("option_regions") or [])
               if r.get("has_figure") and r.get("crop_path")]
    grid_raw = vision.get("stem_matrix_grid")
    grid = tuple(int(x) for x in grid_raw) if grid_raw else None
    g = gold.get(cid, {})
    row: dict = {
        "case_id": cid,                       # 基准题号（predictions 同域）
        "question_id": question["id"],        # 内部 q_*（仅留档）
        "run_ref": run_dir.name,
        "expected": g.get("expected"),
        "first_run": {
            "solver_proposed": g.get("solver_proposed_answer"),
            "predicted_option": g.get("predicted_option"),
            "predicted": g.get("predicted"),
            "failure_class": g.get("failure_class"),
            "failure_reason": _short(g.get("failure_reason")),
            "first_detected_error": g.get("first_detected_error"),
            "blocking_failure": g.get("blocking_failure"),
            "grid_vlm": list(grid_raw) if grid_raw else None,
            "grid_gold": g.get("gold_grid"),
        },
        "replay_inputs": {
            "stem_crop": str(Path(stem).resolve().relative_to(ROOT)).replace("\\", "/"),
            "stem_crop_sha256": _sha256(Path(stem)),
            "grid": list(grid) if grid else None,
            "option_labels": [label for label, _ in options],
            "option_crops": {label: {"path": str(Path(cp).resolve().relative_to(ROOT)).replace("\\", "/"),
                                     "sha256": _sha256(Path(cp))}
                             for label, cp in options if Path(cp).is_file()},
        },
    }
    canvas_path = out_dir / f"{cid}_adapter_canvas.png"
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
    ap.add_argument("--code-sha", default="",
                    help="（可选）人工说明，如 pre_fix 对应历史基线 1e96958；"
                         "实际溯源始终另记 code_provenance，code_sha 不为空")
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

    run_dirs = sorted(rd for rd in (evidence / "runs").iterdir()
                      if rd.is_dir() and (rd / "question.json").is_file())
    # run_ref ↔ 基准题号：run 目录名以 `--case-ids` 传入的 predictions 题号结尾
    def resolve_cid(run_dir: Path) -> str:
        hits = [cid for cid in gold if run_dir.name.endswith("-" + cid)]
        if len(hits) != 1:
            raise RuntimeError(f"run_ref 无法唯一映射基准题号：{run_dir.name} -> {hits}")
        return hits[0]

    prov = code_provenance()
    rows = [replay_case(rd, out_dir, gold, resolve_cid(rd)) for rd in run_dirs]
    report = {
        "kind": "r4e_same_input_replay",
        "label": args.label,
        "generated_at": ts,
        "code_sha": prov["effective_code_sha"],
        "code_provenance": prov,
        "code_sha_override_note": args.code_sha or "",
        "evidence_run": evidence.name,
        "api_calls_made": 0,
        "note": ("视觉输入=真实跑 VisionParser 落盘裁剪与网格几何，完全相同"
                 "（逐文件 sha256 随报告）；回放只重跑六类+变换求解/验证链路，"
                 "零模型调用。真实跑产物只读。"),
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
