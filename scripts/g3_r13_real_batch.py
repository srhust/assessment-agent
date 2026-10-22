# -*- coding: utf-8 -*-
"""R13-G 真实视觉批（预注册 6 项，HTTP ≤6 **原子闸**）。

与 R12-G 的差别：预算不再逐题检查，而是 OpenAICompatibleClient(max_requests=6)
在每次 socket 发出前（含重试）原子拒绝；本轮同时验证 R13-E textless 流程。
停止规则：任何 accept 且 final_answer≠gold → 立即停止。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.config import Settings  # noqa: E402
from assessment_agent.llm import build_client  # noqa: E402
from assessment_agent.pipeline import Pipeline  # noqa: E402
from assessment_agent.tools.release_scoring import row_layer  # noqa: E402

MAX_HTTP_TOTAL = 6
ORDER = [
    ("ft-mirror-04", "data/benchmark_figshots/images/dev/ft-mirror-04.png", "A"),
    ("ft-rot90-01", "data/benchmark_figshots/images/dev/ft-rot90-01.png", "A"),
    ("ft-trans-07", "data/benchmark_figshots/images/dev/ft-trans-07.png", "A"),
    ("ft-overlay-12", "data/benchmark_figshots/images/dev/ft-overlay-12.png", "A"),
    ("ft-mirror-04--tight",
     "data/benchmark_figshots_perturb/images/ft-mirror-04--tight.png", "A"),
    ("ft-rot90-01--small",
     "data/benchmark_figshots_perturb/images/ft-rot90-01--small.png", "A"),
]


def main() -> int:
    dry = "--dry" in sys.argv
    settings = Settings.from_env()
    client = build_client(settings.llm, max_requests=MAX_HTTP_TOTAL)
    pipe = Pipeline(settings, llm=client)
    if dry:
        req = [{"item": pid, "image": p} for pid, p, _ in ORDER]
        est = sum(len(json.dumps(r).encode("utf-8")) for r in req)
        print(json.dumps({"stage": "R13-G pre-launch estimate (zero HTTP)",
                          "items": len(req), "cap": client.max_requests,
                          "requests": 0, "tokens": 0,
                          "note": "真实 token 只能发送后可知；本轮以原子闸=6 为硬上限",
                          "est_payload_bytes": est, "image_sha": {}},
                         ensure_ascii=False, indent=1))
        return 0
    results = []
    stop_reason = None
    for pid, rel, gold in ORDER:
        if client._requests_sent >= MAX_HTTP_TOTAL:
            stop_reason = f"atomic cap reached before {pid}"
            results.append({"item": pid, "skipped": stop_reason})
            break
        path = ROOT / rel
        sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        t0 = time.perf_counter()
        rec = {"item": pid, "image": rel, "image_sha16": sha, "gold": gold}
        try:
            res = pipe.run(image_path=str(path), text=None, save=True,
                           run_id=f"r13g-{pid}")
            v = res.verification
            sol = res.solution
            checks = [getattr(c, "name", None) for c in (v.checks or [])] \
                if v is not None else []
            fa = getattr(v, "final_answer", None) if v else None
            row = {"status": sol.status, "answer": sol.answer, "gold": gold,
                   "verify": getattr(v, "action", None) if v else None,
                   "final_answer": fa}
            rec.update({
                "run_dir": str(res.run_dir), "route": res.route.skill,
                "extraction_stem_len": len(res.question.stem or ""),
                "extraction_opts": len(res.question.options),
                "solution": row,
                "failed_checks": [getattr(c, "name", None)
                                  for c in (v.checks or [])
                                  if not getattr(c, "passed", True)]
                if v else [],
                "layer": row_layer(row),
                "requests_sent_after": client._requests_sent,
                "elapsed_s": round(time.perf_counter() - t0, 2),
            })
            if rec["layer"] == 3 and fa != gold:
                stop_reason = f"WRONG ACCEPT on {pid} ({fa}≠{gold}) — batch halted"
                rec["release"] = "final"
            elif rec["layer"] == 3:
                rec["release"] = "final"
        except Exception as exc:  # noqa: BLE001
            rec["exception"] = str(exc)[:220]
            rec["requests_sent_after"] = client._requests_sent
        results.append(rec)
        if stop_reason:
            break
    usage = {}
    audit = {}
    try:
        usage = client.usage_snapshot()
        audit = client.audit_snapshot()
    except Exception:  # noqa: BLE001
        pass
    out = {"stage": "R13-G real-vision batch (atomic cap)",
           "cap": MAX_HTTP_TOTAL,
           "requests_sent": audit.get("requests_sent"),
           "error_types": audit.get("error_types"),
           "token_usage": usage,
           "stop_reason": stop_reason,
           "release_rule": "layer3 需 verifier 实返 final_answer 且==gold",
           "results": results}
    (ROOT / "evidence/R13G_REAL_VISION.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
