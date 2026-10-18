"""R4c：真实 VLM image_only 冒烟——以**调用审计凭证**判定执行状态。

纪律（修复 R4b 版 P0：进程退出码/run_ref 不能证明真实模型调用）：
- 状态五分为 NOT_CONFIGURED / CONFIGURED_NOT_CALLED / API_FAILED /
  PARTIAL_EXECUTED / EXECUTED，判定唯一依据是 OpenAICompatibleClient
  的 ``audit_snapshot()``（HTTP 尝试计数、状态码分布、错误类型、服务端回显
  模型、request id、是否注入 transport/mock）与逐题 VisionParser 解析状态；
  **CLI 退出码 0、本地 run_ref 非空都不构成 EXECUTED 证据**。
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
STRICTNESS = ("image_only 模式 pipeline 仅接收原始完整页面图片：不提供题干文字/选项文本/"
              "区域坐标/grid/金标答案/layout 标签；金标 regions 只用于评测对照，"
              "不进入求解输入")
SHELL_QIDS = ("ft-rot90-01", "ft-grid-10", "ft-overlay-12")  # 三类变换形态代表题

STATUSES = ("NOT_CONFIGURED", "CONFIGURED_NOT_CALLED", "API_FAILED",
            "PARTIAL_EXECUTED", "EXECUTED")


def classify_status(configured: bool, offline: bool, audit: Optional[Dict[str, Any]],
                    parse_ok: int, expected_cases: int) -> Tuple[str, str]:
    """五状态判定（纯函数，可离线单测）。返回 (status, reason)。"""
    if not configured or offline:
        return ("NOT_CONFIGURED",
                "未配置 LLM_API_KEY/LLM_VISION_MODEL，或 ASSESSMENT_OFFLINE=1 关闭了网络")
    if audit is None:
        return ("CONFIGURED_NOT_CALLED", "评测未产出 llm_evidence.json：无任何调用凭证")
    if audit.get("mock_or_injected_transport"):
        return ("CONFIGURED_NOT_CALLED",
                "检测到注入 transport/mock 客户端：不构成真实模型执行证据")
    sent = int(audit.get("requests_sent", 0))
    succ = int(audit.get("requests_succeeded", 0))
    if sent == 0:
        return ("CONFIGURED_NOT_CALLED", "配置存在但本次评测未发出任何 HTTP 请求")
    if succ == 0:
        return ("API_FAILED",
                f"已发起 {sent} 次真实请求但 0 次成功（错误类型 "
                f"{audit.get('error_types')}，状态码 {audit.get('http_status_counts')}）")
    if succ < expected_cases:
        return ("PARTIAL_EXECUTED",
                f"成功响应 {succ}/{expected_cases} 题下限，解析成功 {parse_ok} 题")
    if parse_ok < expected_cases:
        return ("PARTIAL_EXECUTED",
                f"API 成功 {succ} 次，但视觉解析仅 {parse_ok}/{expected_cases} 题可结构化")
    return ("EXECUTED",
            f"{expected_cases}/{expected_cases} 题发出真实请求且全部解析成功"
            f"（成功响应 {succ}，回显模型 {audit.get('echoed_models')}）")


def collect_case_rows(preds_path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in preds_path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        vision = r.get("vision") or {}
        rows.append({
            "case_id": r["case_id"], "run_ref": r.get("run_ref"),
            "usage": r.get("usage"), "latency_ms": r.get("latency_ms"),
            "parse_failed": bool(vision.get("parse_failed")),
            "extraction_accurate": r.get("extraction_accurate"),
            "region_iou": r.get("region_iou"), "option_map_ok": r.get("option_map_ok"),
            "status": r["status"], "predicted_option": r.get("predicted_option"),
            "expected": r.get("expected"), "correct": r["correct"],
            "verification_action": r.get("verification_action"),
        })
    return rows


def base_payload(env_view: Dict[str, Optional[str]], offline: bool) -> Dict[str, Any]:
    from assessment_agent.config import Settings
    st = Settings.from_env()
    return {
        "phase": "R4c real-VLM image_only smoke（调用审计凭证判定）",
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
                "audit.requests_sent/requests_succeeded", "audit.http_status_counts",
                "audit.error_types", "audit.echoed_models", "audit.request_ids_sample",
                "usage", "latency_ms P50/P95", "per-case parse_failed",
                "run_dir（不写密钥与响应正文）"],
        })
        write_repo_report(base)
        return

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ROOT / "results" / "real_vlm" / ts
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "assessment_agent.cli", "eval",
         "--split", "figshots_dev", "--eval-mode", "image_only", "--json",
         "--run-name", f"smoke_{ts}"],
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
    expected = 15  # figshots_dev 全量
    parse_ok = sum(1 for r in rows if not r["parse_failed"])
    status, reason = classify_status(configured, offline, audit, parse_ok, expected)
    lat = [r["latency_ms"] for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    report = {
        **base,
        "status": status,
        "reason": reason,
        "audit": audit,
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
