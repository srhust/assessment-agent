# -*- coding: utf-8 -*-
"""R11-G 受控真实视觉端到端（≤6 题，清单已在 evidence/R11G_FREEZE.json 冻结）。

止损规则内置：前 8 个请求中出现 3 次 ReadTimeout/连接失败 → 立即停止付费段。
HTTP 总预算 40（R11-A 已用 2）。失败原因用 F 阶段稳定分类桶。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.llm import build_client  # noqa: E402
from assessment_agent.tools.release_scoring import (  # noqa: E402
    audit_network_timeouts, audit_requests_sent)
from assessment_agent.pipeline import Pipeline  # noqa: E402
from assessment_agent.config import Settings  # noqa: E402

MAX_HTTP_TOTAL = 40
ALREADY_USED = 2


def classify(exc_text: str) -> str:
    s = exc_text.lower()
    if "timeout" in s or "read operation" in s or "connection" in s:
        return "sent_no_response"
    if "json" in s or "expecting value" in s:
        return "parse_or_protocol_fail"
    return "planned_but_exec_fail" if "plan" in s else "other"


def main() -> int:
    freeze = json.loads((ROOT / "evidence/R11G_FREEZE.json").read_text(encoding="utf-8"))
    settings = Settings.from_env()
    client = build_client(settings.llm)
    pipe = Pipeline(settings, llm=client)
    results = []
    timeouts = 0
    requests = 0
    aborted = None
    only = None
    for _a in list(sys.argv):
        if _a.startswith("--ids="):
            only = set(_a.split("=", 1)[1].split(","))
            sys.argv.remove(_a)
    for it in freeze["items"]:
        if only and it["id"] not in only:
            continue
        snap = client.audit_snapshot() if hasattr(client, "audit_snapshot") else {}
        sent = audit_requests_sent(snap)          # R12：真实计数（含每次重试）
        timeouts = audit_network_timeouts(snap)   # R12：HTTP 层 error_types，非外层异常
        if sent and sent <= 8 and timeouts >= 3:
            aborted = "stop_rule_3_timeouts_within_first_8_requests"
            break
        if ALREADY_USED + sent >= MAX_HTTP_TOTAL:
            aborted = "budget_http_cap"
            break
        if requests >= 8 and timeouts >= 3:
            aborted = "stop_rule_3_timeouts_in_first_8"
            break
        t0 = time.time()
        rec = {"id": it["id"], "family": it["family"], "gold": it["gold_answer"]}
        try:
            # R12：image_only 必须 text=None，让 VisionParser 真实解析（传文本会跳过视觉路径）
            res = pipe.run(text=None, image_path=str(ROOT / it["image"]),
                           skill="figure_reasoning", save=False,
                           hint="figure_reasoning")
            sol = getattr(res, "solution", None)
            ver = getattr(res, "verification", None)
            ext = getattr(res, "extraction", None)
            meta = (getattr(sol, "meta", None) or {}) if sol else {}
            prov = meta.get("region_provenance") or {}
            rec.update({
                "status": getattr(sol, "status", None),
                "solver_answer": getattr(sol, "answer", None),
                "verification_action": getattr(ver, "action", None),
                "final_answer": getattr(ver, "final_answer", None),
                "answer": (getattr(ver, "final_answer", None)
                           if getattr(ver, "action", None) in ("accept", "repair") else None),
                "vlm_stem_grid": (getattr(ext, "diagram_regions", None) and
                                  [getattr(d, "grid", None) for d in ext.diagram_regions][:3])
                                 if ext else None,
                "stem_evidence": prov.get("stem_evidence"),
                "stem_crop_source": prov.get("stem_crop_source"),
                "elapsed_s": round(time.time() - t0, 1),
            })
            rec["correct"] = rec.get("answer") == it["gold_answer"]
            parser = getattr(ext, "parser", None) if ext is not None else None
            rec["extraction_parser"] = parser
            rec["image_only_eligible"] = parser == "vision_parser"
            if parser != "vision_parser":
                rec.setdefault("note", "未走 VisionParser，不计入 image_only 合格样本")
            snap_after = client.audit_snapshot()
            rec["audit_after_item"] = {
                "requests_sent": audit_requests_sent(snap_after),
                "network_timeouts": audit_network_timeouts(snap_after)}
            if rec.get("final_answer") and rec["final_answer"] != it["gold_answer"]:
                rec["wrong_release"] = True
        except Exception as exc:  # noqa: BLE001
            txt = f"{type(exc).__name__}: {exc}"
            rec.update({"status": "error", "error": txt[:160],
                        "failure_bucket": classify(txt)})
            # R12：超时数以 audit.error_types 为准（循环顶部重取），不再外层自增
        requests += 1
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False, default=str)[:220], flush=True)
    snap = client.audit_snapshot() if hasattr(client, "audit_snapshot") else {}
    snap = client.audit_snapshot()
    out = {"stage": "R11-G (r12-audited)", "aborted": aborted,
           "requests_attempted": requests,
           "requests_sent_real": audit_requests_sent(snap),
           "network_timeouts_http_layer": audit_network_timeouts(snap),
           "eligible_image_only": sum(1 for r in results if r.get("image_only_eligible")),
           "usage_audit": snap, "per_item": results,
           "note": "Dev 集内受控真实视觉；非盲测、非泛化宣称"}
    (ROOT / "evidence/R11G_REAL_VISION.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    rel = [r for r in results if r.get("answer")]
    print("RELEASED", sum(1 for r in rel), "CORRECT", sum(1 for r in rel if r.get("correct")),
          "ABORTED", aborted, "TIMEOUTS", timeouts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
