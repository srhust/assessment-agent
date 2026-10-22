# -*- coding: utf-8 -*-
"""R12-G 受控真实视觉复测（清单已在 evidence/R12G_FREEZE.json 先于任何 HTTP 冻结）。

硬约束：≤8 个 HTTP 请求**含重试**（audit.requests_sent）；前 8 请求内 HTTP 层
超时≥3 即停；token 优先真实 usage_snapshot，缺回显时保守估算硬顶 240k；
image_only = text=None；任一错误答案被接受释放 → 立即停止并归因；
只写 evidence/R12G_REAL_VISION.json，不触碰 R11 原件。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.llm import build_client  # noqa: E402
from assessment_agent.pipeline import Pipeline  # noqa: E402
from assessment_agent.config import Settings  # noqa: E402
from assessment_agent.tools.release_scoring import (  # noqa: E402
    audit_network_timeouts, audit_requests_sent)

MAX_HTTP_TOTAL = 8
TOKEN_CAP = 240_000
PER_REQUEST_OVERHEAD_EST = 2_000      # prompt+response 保守估算（字符级不确定时）


def classify(exc_text: str) -> str:
    s = exc_text.lower()
    if "timeout" in s or "read operation" in s or "connection" in s:
        return "sent_no_response"
    if "json" in s or "expecting value" in s:
        return "parse_or_protocol_fail"
    return "planned_but_exec_fail" if "plan" in s else "other"


def token_state(client, done_bytes: int, n_req: int):
    usage = {}
    try:
        usage = client.usage_snapshot() if hasattr(client, "usage_snapshot") else {}
    except Exception:  # noqa: BLE001
        usage = {}
    real = int(usage.get("total_tokens") or 0)
    est = int(done_bytes / 4) + n_req * PER_REQUEST_OVERHEAD_EST
    return real, est, (real if real > 0 else est), ("usage_snapshot"
                                                    if real > 0 else "conservative_estimate")


def main() -> int:
    freeze = json.loads((ROOT / "evidence/R12G_FREEZE.json").read_text(encoding="utf-8"))
    settings = Settings.from_env()
    client = build_client(settings.llm)
    pipe = Pipeline(settings, llm=client)
    results = []
    aborted = None
    requests = 0
    done_bytes = 0
    only = None
    for _a in list(sys.argv):
        if _a.startswith("--ids="):
            only = set(_a.split("=", 1)[1].split(","))
            sys.argv.remove(_a)
    if "--dry" in sys.argv:
        print("freeze ok:", freeze["frozen_at_head"][:12],
              "items:", [i["id"] for i in freeze["items"]])
        return 0
    for it in freeze["items"]:
        if only and it["id"] not in only:
            continue
        snap = client.audit_snapshot()
        sent = audit_requests_sent(snap)
        timeouts = audit_network_timeouts(snap)
        real_tok, est_tok, tok_gate, tok_mode = token_state(client, done_bytes, requests)
        if sent and sent <= MAX_HTTP_TOTAL and timeouts >= 3:
            aborted = "stop_rule_3_timeouts_within_first_8_requests"
            break
        if sent >= MAX_HTTP_TOTAL:
            aborted = "budget_http_cap_8"
            break
        if tok_gate >= TOKEN_CAP:
            aborted = f"token_cap_{tok_mode}"
            break
        t0 = time.time()
        rec = {"id": it["id"], "family": it["family"], "gold": it["gold_answer"]}
        img = ROOT / it["image"]
        try:
            res = pipe.run(text=None, image_path=str(img),
                           skill="figure_reasoning", save=True,
                           hint="figure_reasoning")  # R12-G 复盘：解析件必须落盘
            sol = getattr(res, "solution", None)
            ver = getattr(res, "verification", None)
            ext = getattr(res, "extraction", None)
            meta = (getattr(sol, "meta", None) or {}) if sol else {}
            prov = meta.get("region_provenance") or {}
            action = getattr(ver, "action", None)
            fa = getattr(ver, "final_answer", None)
            released = action in ("accept", "repair") and bool(fa)
            rec.update({
                "status": getattr(sol, "status", None),
                "solver_answer": getattr(sol, "answer", None),
                "verification_action": action,
                "final_answer": fa,
                "released": released,
                "answer": fa if released else None,
                "stem_evidence": prov.get("stem_evidence"),
                "stem_crop_source": prov.get("stem_crop_source"),
                "elapsed_s": round(time.time() - t0, 1),
            })
            parser = getattr(ext, "parser", None) if ext is not None else None
            rec["extraction_parser"] = parser
            rec["image_only_eligible"] = parser == "vision_parser"
            rec["vlm_stem_grid"] = ([getattr(d, "grid", None)
                                      for d in (ext.diagram_regions or [])][:2]
                                     if ext is not None else None)
            rec["vlm_option_regions"] = (
                [[o.label, o.bbox, bool(o.has_figure)]
                 for o in (ext.option_regions or [])][:6] if ext is not None else None)
            rec["correct"] = bool(released) and fa == it["gold_answer"]
            if released and fa != it["gold_answer"]:
                rec["wrong_release"] = True
        except Exception as exc:  # noqa: BLE001
            txt = f"{type(exc).__name__}: {exc}"
            rec.update({"status": "error", "error": txt[:160],
                        "failure_bucket": classify(txt), "released": False,
                        "correct": False, "image_only_eligible": False})
        done_bytes += img.stat().st_size
        requests += 1
        snap_after = client.audit_snapshot()
        rec["audit_after_item"] = {
            "requests_sent": audit_requests_sent(snap_after),
            "network_timeouts": audit_network_timeouts(snap_after)}
        real_tok, est_tok, tok_gate, tok_mode = token_state(
            client, done_bytes, requests)
        rec["token_state"] = {"usage_snapshot": real_tok, "estimate": est_tok,
                              "gate_used": tok_mode}
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False, default=str)[:260], flush=True)
        if rec.get("wrong_release"):
            aborted = "stop_rule_wrong_answer_accepted"
            break
        if audit_requests_sent(snap_after) <= MAX_HTTP_TOTAL and \
                audit_network_timeouts(snap_after) >= 3:
            aborted = "stop_rule_3_timeouts_within_first_8_requests"
            break
    snap = client.audit_snapshot()
    rel = [r for r in results if r.get("released")]
    out = {
        "stage": "R12-G", "frozen_at_head": freeze["frozen_at_head"],
        "aborted": aborted,
        "requests_sent_real_incl_retries": audit_requests_sent(snap),
        "network_timeouts_http_layer": audit_network_timeouts(snap),
        "tokens": {"usage_snapshot_total": (
                       client.usage_snapshot().get("total_tokens")
                       if hasattr(client, "usage_snapshot") else None),
                   "conservative_estimate": int(done_bytes / 4) + requests * PER_REQUEST_OVERHEAD_EST,
                   "cap": TOKEN_CAP},
        "eligible_image_only": sum(1 for r in results if r.get("image_only_eligible")),
        "three_layer": {
            "correct_candidates": sum(1 for r in results
                                      if r.get("solver_answer") == r.get("gold")),
            "accepted": len(rel),
            "final_released_correct": sum(1 for r in rel if r.get("correct")),
            "wrong_releases": [r["id"] for r in results if r.get("wrong_release")],
        },
        "usage_audit": snap, "per_item": results,
        "note": "Dev 集受控真实视觉小批量；非盲测、非泛化宣称；预算/超时/token 均来自真实审计或保守估算",
    }
    (ROOT / "evidence/R12G_REAL_VISION.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(out["three_layer"], ensure_ascii=False),
          "SENT", out["requests_sent_real_incl_retries"],
          "TIMEOUTS", out["network_timeouts_http_layer"], "ABORTED", aborted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
