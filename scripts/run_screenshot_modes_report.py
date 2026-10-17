"""三模式截图评测对照报告（oracle_solver / auto_router / image_only）。

- oracle_solver、auto_router：完全离线，注入金标准题干/区域，只测 Solver、Router+Solver；
- image_only（真实模型）：仅当配置 LLM_API_KEY + LLM_VISION_MODEL 才运行；
- image_only（mock 视觉后端）：合成截图 + 由金标准区域构造的 mock VLM payload，
  只验证 VisionParser→Router→Adapter→Verifier 的**链路机制**，
  不代表真实模型识别能力，产物目录与报告均显式标注。

运行：``python -X utf8 scripts/run_screenshot_modes_report.py``
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from assessment_agent.config import LLMSettings, Settings  # noqa: E402
from assessment_agent.evaluation.runner import EvaluationRunner  # noqa: E402
from assessment_agent.pipeline import Pipeline  # noqa: E402
from assessment_agent.tools.base import ToolUnavailable  # noqa: E402  # noqa: F401  - 保持错误类型可用
import test_screens_benchmark as bench  # noqa: E402


def _mode_rows(summary) -> dict:
    m = summary.metrics
    return {
        "eval_mode": summary.eval_mode,
        "total": m["total"],
        "answer_accuracy": m["answer_accuracy"],
        "answerable_coverage": m["answerable_coverage"],
        "selective_accuracy": m["selective_accuracy"],
        "route_accuracy": m["route_accuracy"],
        "abstention_accuracy": m["abstention_accuracy"],
        "mean_latency_ms": m["mean_latency_ms"],
        "p95_latency_ms": m["p95_latency_ms"],
        "failure_reasons": m["failure_reasons"],
        "e2e": m.get("e2e") or {},
    }


def main() -> int:
    data_dir = ROOT / "data"
    results_dir = ROOT / "results"
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = results_dir / "screens" / f"modes_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    rows = []

    offline = Settings(offline=True, data_dir=data_dir, results_dir=out / "offline")
    for mode in ("oracle_solver", "auto_router"):
        runner = EvaluationRunner(Pipeline(offline), data_dir=data_dir, results_dir=out / "offline")
        summary = runner.run("screens", eval_mode=mode, save=True, run_name=mode)
        rows.append(_mode_rows(summary))

    # image_only（真实模型）——未配置则明确记录未执行
    live = Settings.from_env()
    real_status = "NOT EXECUTED: 未配置 LLM_API_KEY/LLM_VISION_MODEL（不得以 mock 结果冒充）"
    if live.llm.vision_configured and not live.offline:
        runner = EvaluationRunner(Pipeline(live), data_dir=data_dir, results_dir=out / "real_vlm")
        summary = runner.run("screens", eval_mode="image_only", save=True, run_name="image_only_real_vlm")
        row = _mode_rows(summary)
        row["vision_backend"] = "real-model"
        rows.append(row)
        real_status = "EXECUTED"

    # image_only（mock 视觉后端）：只测链路机制
    cases = bench._cases()
    payloads = {Path(c["image"]).stem: bench._payload_from_case(c) for c in cases}
    fake = bench._MultiImageFake(payloads)
    mock_settings = Settings(
        offline=True,
        llm=LLMSettings(api_key="sk-offline-fake", vision_model="mock-vlm"),
        data_dir=data_dir,
        results_dir=out / "mock_vlm",
        runs_dir=out / "mock_vlm_runs",
    )
    runner = EvaluationRunner(Pipeline(mock_settings, llm=fake), data_dir=data_dir, results_dir=out / "mock_vlm")
    summary = runner.run("screens", eval_mode="image_only", save=True, run_name="image_only_mock_vlm")
    row = _mode_rows(summary)
    row["vision_backend"] = "mock-vlm (synthetic payload from gold regions; mechanism check only)"
    rows.append(row)

    comparison = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_provenance": "全部样本为合成截图（scripts/make_screenshot_benchmark.py 生成），非真实招聘平台数据",
        "real_vision_model": real_status,
        "warning": "样本量 9，oracle/auto 满分只说明合成回归基线通过，不代表真实场景准确率",
        "modes": rows,
    }
    (out / "mode_comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# R2a 三模式截图评测对照",
        "",
        f"- 生成时间：{comparison['generated_at']}",
        f"- 数据来源：{comparison['data_provenance']}",
        f"- 真实视觉模型端到端：**{real_status}**",
        f"- ⚠️ {comparison['warning']}",
        "",
        "| 模式 | 视觉后端 | 答案准确率 | 可答题覆盖率 | 选择性准确率 | 路由准确率 | 拒答处理 | E2E题干解析 | 区域IoU | 选项映射 | E2E准确率 | 平均耗时ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in rows:
        e2e = item["e2e"]
        route = "N/A(oracle)" if item["eval_mode"] == "oracle_solver" else f"{item['route_accuracy']:.2%}"
        lines.append(
            f"| {item['eval_mode']} | {item.get('vision_backend', '无（不经过 Vision）')} | {item['answer_accuracy']:.2%} | "
            f"{item['answerable_coverage']:.2%} | {item['selective_accuracy']:.2%} | {route} | "
            f"{item['abstention_accuracy']:.2%} | {e2e.get('extraction_accuracy', '—')} | {e2e.get('region_iou_mean', '—')} | "
            f"{e2e.get('option_mapping_accuracy', '—')} | {e2e.get('end_to_end_accuracy', '—')} | {item['mean_latency_ms']:.1f} |"
        )
    lines += ["", "## 失败归因（按模式）", ""]
    for item in rows:
        reasons = item["failure_reasons"] or {"（无失败）": 0}
        lines.append(f"- **{item['eval_mode']}**：{json.dumps(reasons, ensure_ascii=False)}")
    lines.append("")
    lines.append("说明：mock-vlm 行仅验证解析→适配→求解→验证的链路机制；真实模型识别能力必须在配置 LLM_API_KEY/LLM_VISION_MODEL 后重跑 image_only 才有结论。")
    (out / "mode_comparison.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 对照报告 → {out / 'mode_comparison.md'}")
    print("\n".join(lines[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
