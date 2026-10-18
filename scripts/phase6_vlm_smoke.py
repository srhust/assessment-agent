"""R4d：真实 VLM image_only 冒烟（3 题）——以**分阶段调用审计凭证**判定执行状态。

纪律（修复 R4b 版 P0：进程退出码/run_ref 不能证明真实模型调用）：
- 状态五分为 NOT_CONFIGURED / CONFIGURED_NOT_CALLED / API_FAILED /
  PARTIAL_EXECUTED / EXECUTED，判定唯一依据是包内
  ``assessment_agent.evaluation.execution_status.classify_status``
  （本脚本与 run_real_vlm_experiment.py **共用同一实现**）：
  客户端分阶段审计（vision_parse/router/solver/verifier）+ 每题
  vision_http_success / vision_schema_valid / vision_extraction_valid 三段记录；
  **CLI 退出码 0、本地 run_ref 非空、HTTP 总数达标都不构成 EXECUTED 证据**
  ——API 成功但 JSON/结构化解析失败不算视觉解析成功。
- 真实执行的报告写入 ``results/real_vlm/<run_id>/smoke_report.json``；
  仓库内被 Git 跟踪的 ``data/benchmark_figshots/phase6_vlm_smoke.json``
  仅在**未配置**时更新（保持离线凭证不可变，真实运行绝不回写仓库文件）。
- 禁止用 mock/离线解析器冒充端到端结果；报告不落密钥。
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REPO_REPORT = ROOT / "data" / "benchmark_figshots" / "phase6_vlm_smoke.json"

# R4d：五态判定唯一实现挪进包内（smoke 与实验脚本共用，报告不再各说各话）
from assessment_agent.evaluation.execution_status import (  # noqa: E402
    STATUSES, classify_status, health_gate, vision_stage_summary,
)
STRICTNESS = ("image_only 模式 pipeline 仅接收原始完整页面图片：不提供题干文字/选项文本/"
              "区域坐标/grid/金标答案/layout 标签；金标 regions 只用于评测对照，"
              "不进入求解输入")
SHELL_QIDS = ("ft-rot90-01", "ft-grid-10", "ft-overlay-12")  # 三类变换形态代表题

STATUSES_LOCAL = STATUSES  # 兼容旧引用；判定实现见 execution_status.classify_status


def collect_case_rows(preds_path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in preds_path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        vision = r.get("vision") or {}
        rows.append({
            "case_id": r["case_id"], "run_ref": r.get("run_ref"),
            "usage": r.get("usage"), "latency_ms": r.get("latency_ms"),
            "parse_failed": bool(vision.get("parse_failed")),
            "vision_http_success": bool(vision.get("vision_http_success")),
            "vision_schema_valid": bool(vision.get("vision_schema_valid")),
            "vision_extraction_valid": bool(vision.get("vision_extraction_valid")),
            "evidence_complete": bool(vision.get("evidence_complete")),
            "extraction_accurate": r.get("extraction_accurate"),
            "region_iou": r.get("region_iou"), "option_map_ok": r.get("option_map_ok"),
            "status": r["status"], "predicted_option": r.get("predicted_option"),
            "predicted": r.get("predicted"), "expected": r.get("expected"),
            "correct": r["correct"],
            "verification_action": r.get("verification_action"),
        })
    return rows


def base_payload(env_view: Dict[str, Optional[str]], offline: bool) -> Dict[str, Any]:
    from assessment_agent.config import Settings
    st = Settings.from_env()
    return {
        "phase": "R4d real-VLM image_only smoke 3 题（分阶段审计 + 每题三段记录判定，"
                 "与 run_real_vlm_experiment 共用 execution_status）",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "provider": st.llm.base_url.split("//")[-1].split("/")[0],
        "vision_model": st.llm.vision_model,
        "text_model": st.llm.text_model,
        "temperature": st.llm.temperature,
        "max_tokens": st.llm.max_tokens,
        "api_key_present": bool(os.environ.get("LLM_API_KEY") or (st.llm.api_key or "")),
        "offline_flag": offline,
        "input_images": [f"data/benchmark_figshots/images/dev/{q}.png"
                         for q in SHELL_QIDS],
        "eval_mode": "image_only",
        "split": "figshots_dev",
        "strict_input_contract": STRICTNESS,
        "artifacts_per_case": ["原图", "extraction.json", "预测区域及裁剪", "router 结果",
                               "solver 候选规律与答案", "verifier 状态", "延迟与 token usage",
                               "最终错误归因", "llm_evidence.json（请求计数/状态码/回显模型）"],
    }


def write_repo_report(payload: Dict[str, Any]) -> None:
    """仅未配置路径写仓库报告；真实执行绝不回写（不可变纪律）。"""
    REPO_REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({k: payload.get(k) for k in ("status", "reason")}, ensure_ascii=False))


def main() -> None:
    offline = os.environ.get("ASSESSMENT_OFFLINE", "").strip() == "1"
    base = base_payload(
        {k: os.environ.get(k) for k in
         ("LLM_API_KEY", "LLM_MODEL", "LLM_VISION_MODEL", "LLM_BASE_URL")}, offline)
    from assessment_agent.config import Settings
    st = Settings.from_env()
    configured = bool(st.llm.vision_configured) and not offline
    if not configured:
        base.update({
            "status": "NOT_CONFIGURED",
            "reason": ("环境未配置 LLM_API_KEY / LLM_VISION_MODEL 或 ASSESSMENT_OFFLINE=1。"
                       "按纪律不得以 mock 或离线解析器冒充真实模型端到端结果；"
                       "真实执行报告将写入 results/real_vlm/<run_id>/，不回写本文件。"),
            "what_would_run": "python -m assessment_agent.cli eval --split "
                              "figshots_dev --eval-mode image_only（截图输入，"
                              "区域定位由 VisionParser→真实 VLM 完成），逐题记录 "
                              "extraction/区域 bbox/答案/验证状态/usage/latency，"
                              "并以 output_dir/llm_evidence.json 的请求计数判定状态",
            "status_semantics": {
                "NOT_CONFIGURED": "缺少真实模型配置（或离线开关）",
                "CONFIGURED_NOT_CALLED": "已配置但没有发出真实请求的证据",
                "API_FAILED": "已发起真实请求但全部失败（401/404/超时等）",
                "PARTIAL_EXECUTED": "部分样本完成真实视觉解析",
                "EXECUTED": "指定样本全部完成真实视觉解析（审计计数佐证）",
            },
            "report_fields_on_executed": [
                "audit.requests_sent/requests_succeeded", "audit.by_phase(vision_parse/router/…)",
                "audit.http_status_counts",
                "audit.error_types", "audit.echoed_models", "audit.request_ids_sample",
                "usage", "latency_ms P50/P95",
                "per-case vision_http_success/vision_schema_valid/vision_extraction_valid/"
                "evidence_complete",
                "run_dir（不写密钥与响应正文）"],
        })
        write_repo_report(base)
        return

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ROOT / "results" / "real_vlm" / f"smoke-{ts}"
    if run_dir.exists():  # R4d：唯一 experiment_id + 禁止静默覆盖历史凭证
        run_dir = ROOT / "results" / "real_vlm" / f"smoke-{ts}-{os.getpid()}"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "assessment_agent.cli", "eval",
         "--split", "figshots_dev", "--eval-mode", "image_only", "--json",
         "--case-ids", ",".join(SHELL_QIDS),
         "--no-overwrite",
         "--run-name", f"smoke_{ts}", "--eval-batch", f"smoke-{ts}"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    out_dir: Optional[Path] = None
    metrics_view: Dict[str, Any] = {}
    if proc.returncode == 0:
        d = json.loads(proc.stdout)
        out_dir = Path(d["output_dir"])
        metrics_view = {k: d["metrics"].get(k) for k in
                        ("overall_answer_accuracy", "answerable_coverage",
                         "selective_accuracy", "vision")}
    audit = None
    usage: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []
    if out_dir is not None:
        ev = out_dir / "llm_evidence.json"
        if ev.is_file():
            j = json.loads(ev.read_text(encoding="utf-8"))
            audit = j.get("audit")
            usage = j.get("usage") or {}
        preds = out_dir / "predictions.jsonl"
        if preds.is_file():
            rows = collect_case_rows(preds)
    expected = 3  # 冒烟阶段 = 三类形态代表题（运行前显式声明）
    rows_for_status = [r for r in rows if r["case_id"] in SHELL_QIDS]
    status, reason = classify_status(configured, offline, audit, rows_for_status, expected)
    summary = vision_stage_summary(rows_for_status, audit)
    gate_ok, gate_msg = health_gate(status, summary)
    lat = [r["latency_ms"] for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    report = {
        **base,
        "status": status,
        "reason": reason,
        "health_gate": {"ok": gate_ok, "message": gate_msg,
                        "next_stage_allowed": gate_ok},
        "audit": audit,
        "vision_stage_summary": summary,
        "usage_total": usage,
        "latency": ({"p50_ms": round(statistics.quantiles(lat, n=20)[9], 1),
                     "p95_ms": round(statistics.quantiles(lat, n=20)[18], 1)}
                    if len(lat) >= 5 else {"latencies_ms": lat}),
        "metrics": metrics_view,
        "cases": rows,
        "evaluation_output_dir": str(out_dir) if out_dir else None,
        "stderr_tail": proc.stderr[-400:] if proc.returncode != 0 else None,
        "note": "真实执行报告写入 results/real_vlm/；仓库 phase6_vlm_smoke.json 未被本路径修改",
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "smoke_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if out_dir is not None:  # 证据随 run_dir 归档（predictions/llm_evidence 副本）
        import shutil
        shutil.copytree(out_dir, run_dir / "eval_output", dirs_exist_ok=True)
    print(json.dumps({"status": status, "reason": reason,
                      "report": str(run_dir / "smoke_report.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
