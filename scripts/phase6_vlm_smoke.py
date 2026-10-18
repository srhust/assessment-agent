"""R4a Phase6：真实 VLM image_only 冒烟（图形变换题截图 → 视觉解析 → 求解 → 验证）。

纪律：只有发生**真实模型调用**才标 EXECUTED，并记录模型名/输入图像/run 引用/
解析区域/答案/验证状态；环境未配置 LLM_API_KEY/LLM_VISION_MODEL 时输出显式
NOT_EXECUTED 报告——**禁止用 mock/离线替身冒充端到端结果**。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "benchmark_figshots" / "phase6_vlm_smoke.json"
STRICTNESS = ("image_only 模式 pipeline 仅接收原始完整页面图片：不提供题干文字/选项文本/"
              "区域坐标/grid/金标答案/layout 标签；金标 regions 只用于评测对照，"
              "不进入求解输入")
SHELL_QIDS = ("ft-rot90-01", "ft-grid-10", "ft-overlay-12")  # 三类变换形态代表题


def configured() -> tuple[bool, dict]:
    env = {k: os.environ.get(k) for k in
           ("LLM_API_KEY", "LLM_MODEL", "LLM_VISION_MODEL", "LLM_BASE_URL")}
    ok = bool(env.get("LLM_API_KEY")) and bool(env.get("LLM_VISION_MODEL"))
    return ok, env


def write_report(payload: dict) -> None:
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(json.dumps({k: payload.get(k) for k in ("status", "reason")},
                     ensure_ascii=False))


def main() -> None:
    ok, env = configured()
    base = {
        "phase": "R4a Phase6 real-VLM image_only smoke",
        "generated_at": "2026-10-18",
        "vision_model": env.get("LLM_VISION_MODEL"),
        "llm_model": env.get("LLM_MODEL"),
        "base_url_configured": bool(env.get("LLM_BASE_URL")),
        "input_images": [f"data/benchmark_figshots/images/dev/{q}.png"
                         for q in SHELL_QIDS],
        "eval_mode": "image_only",
        "split": "figshots_dev",
        "strict_input_contract": STRICTNESS,
        "artifacts_per_case": ["原图", "extraction.json", "预测区域及裁剪", "router 结果",
                               "solver 候选规律与答案", "verifier 状态", "延迟与 token usage",
                               "最终错误归因"],
    }
    if not ok:
        base.update({
            "status": "NOT_EXECUTED",
            "reason": "环境未配置 LLM_API_KEY / LLM_VISION_MODEL（离线评测环境）。"
                      "按纪律不得以 mock 或离线解析器冒充真实模型端到端结果。",
            "what_would_run": "python -m assessment_agent.cli eval --split "
                              "figtrans_dev --eval-mode image_only（截图输入，"
                              "区域定位由 VisionParser→LLM 完成），"
                              "逐题记录 parsed_regions/predicted_option/"
                              "verification_action/usage(run_ref)",
            "report_fields_on_executed": [
                "model", "run_ref", "usage_tokens", "input_image",
                "parsed_regions(bbox/labels)", "answer", "verification_status"],
        })
        write_report(base)
        return

    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "assessment_agent.cli", "eval",
         "--split", "figshots_dev", "--eval-mode", "image_only", "--json"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    if proc.returncode != 0:
        base.update({"status": "EXECUTION_FAILED", "stderr_tail": proc.stderr[-400:]})
        write_report(base)
        return
    d = json.loads(proc.stdout)
    preds_path = Path(d["output_dir"]) / "predictions.jsonl"
    cases = []
    for line in preds_path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        cases.append({
            "case_id": r["case_id"], "run_ref": r.get("run_ref"),
            "usage": r.get("usage"), "extraction_accurate": r.get("extraction_accurate"),
            "region_iou": r.get("region_iou"), "option_map_ok": r.get("option_map_ok"),
            "status": r["status"], "predicted_option": r.get("predicted_option"),
            "expected": r.get("expected"), "correct": r["correct"],
            "verification_action": r.get("verification_action"),
        })
    base.update({
        "status": "EXECUTED",
        "evidence": "真实 LLM 调用发生（predictions 携带 run_ref/usage 非空）",
        "all_have_run_ref": all(c["run_ref"] for c in cases),
        "usage_total": sum((c.get("usage") or {}).get("total_tokens", 0) for c in cases),
        "metrics": {k: (d["metrics"].get(k)) for k in
                    ("overall_answer_accuracy", "answerable_coverage",
                     "selective_accuracy")},
        "vision": d["metrics"].get("vision"),
        "cases": cases,
    })
    if not base["all_have_run_ref"]:
        base["warning"] = "存在 run_ref 缺失的用例：无法证明真实调用，需人工核查"
    write_report(base)


if __name__ == "__main__":
    main()
