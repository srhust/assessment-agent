"""R2b 三模式评测对照报告：regression（screens）+ 独立基准（benchmark_real dev/holdout）。

- oracle_solver / auto_router：完全离线，注入金标准题干/区域，只测 Solver、Router+Solver；
- image_only（真实模型）：仅当配置 LLM_API_KEY + LLM_VISION_MODEL 才运行；未配置则**明确报告未执行**；
- image_only（mock 视觉后端）：payload 由金标准区域构造——只验证
  VisionParser→Router→Adapter→Verifier 的**链路机制**，永远不代表真实识别能力。

运行：``python -X utf8 scripts/run_screenshot_modes_report.py``
产物：results/modes_<stamp>/mode_comparison.{json,md}
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.config import LLMSettings, Settings  # noqa: E402
from assessment_agent.evaluation.mock_vision import MockVisionClient, payload_from_record  # noqa: E402
from assessment_agent.evaluation.runner import EvaluationRunner  # noqa: E402
from assessment_agent.pipeline import Pipeline  # noqa: E402

CORPORA = (
    ("screens", "screens（regression 合成截图集）", "R2a 合成回归集"),
    ("bench_dev", "benchmark_real/dev（独立基准·开发集）", "独立合成集，非真实平台截图"),
    ("bench_holdout", "benchmark_real/holdout（独立基准·冻结集）", "独立合成集，非真实平台截图"),
)


def _mode_rows(summary) -> dict:
    m = summary.metrics
    cap = m.get("capability") or {}
    return {
        "eval_mode": summary.eval_mode,
        "eval_batch": summary.eval_batch,
        "total": m["total"],
        "answer_accuracy": m["answer_accuracy"],
        "answer_scored": m.get("answerable"),
        "abstention_scored": m.get("abstention_total"),
        "answerable_coverage": m["answerable_coverage"],
        "selective_accuracy": m["selective_accuracy"],
        "route_accuracy": m["route_accuracy"],
        "abstention_accuracy": m["abstention_accuracy"],
        "mean_latency_ms": m["mean_latency_ms"],
        "p95_latency_ms": m["p95_latency_ms"],
        "failure_reasons": m["failure_reasons"],
        "failure_classes": m.get("failure_classes") or {},
        "e2e": m.get("e2e") or {},
        "vision": m.get("vision") or {},
        "usage_total": m.get("llm_usage") or {},
        # R2c 双维度口径
        "capability": {
            "overall_answer_accuracy": (cap.get("overall_answer_accuracy") or {}).get("value"),
            "overall_hit": (cap.get("overall_answer_accuracy") or {}).get("hit"),
            "objective_total": cap.get("objective_total"),
            "supported_answer_accuracy": (cap.get("supported_answer_accuracy") or {}).get("value"),
            "capability_coverage": (cap.get("capability_coverage") or {}).get("value"),
            "answered_coverage": (cap.get("overall_answered_coverage") or {}).get("value"),
            "honest_abstention": (cap.get("honest_abstention") or {}).get("value"),
            "false_accept": cap.get("false_accept_total", 0),
            "false_reject": cap.get("false_reject_total", 0),
            "unverified_answerable": cap.get("unverified_answerable_total", 0),
            "over_answer": cap.get("over_answer_total", 0),
            "outcomes": cap.get("verification_outcomes") or {},
            "root_causes": cap.get("root_causes") or {},
        },
    }


def _pct(value, digits: int = 1) -> str:
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def main() -> int:
    data_dir = ROOT / "data"
    results_dir = ROOT / "results"
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = results_dir / f"modes_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    report: Dict = {}

    offline = Settings(offline=True, data_dir=data_dir, results_dir=out / "offline")
    live = Settings.from_env()
    real_status = "NOT EXECUTED：本机未配置 LLM_API_KEY/LLM_VISION_MODEL，真实模型实验未执行（不得以 mock 结果冒充）"

    for split, label, provenance in CORPORA:
        corpora_rows = []
        for mode in ("oracle_solver", "auto_router"):
            runner = EvaluationRunner(Pipeline(offline), data_dir=data_dir, results_dir=out / "offline")
            summary = runner.run(split, eval_mode=mode, save=True, run_name=f"{split}_{mode}", eval_batch=f"{split}-offline-{stamp}")
            row = _mode_rows(summary)
            row["vision_backend"] = "无（不经过视觉解析）"
            corpora_rows.append(row)

        if live.llm.vision_configured and not live.offline:
            runner = EvaluationRunner(Pipeline(live), data_dir=data_dir, results_dir=out / "real_vlm")
            summary = runner.run(split, eval_mode="image_only", save=True, run_name=f"{split}_image_only_real_vlm", eval_batch=f"{split}-realvlm-{stamp}")
            row = _mode_rows(summary)
            row["vision_backend"] = "real-model"
            corpora_rows.append(row)
            real_status = "EXECUTED（见 real_vlm 各行）"

        records = [
            json.loads(line)
            for json_file in sorted((data_dir / {"bench_dev": "benchmark_real/dev", "bench_holdout": "benchmark_real/holdout"}.get(split, split)).glob("*.jsonl"))
            for line in json_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        payloads = {Path(r["image"]).stem: payload_from_record(r) for r in records if r.get("image")}
        fake = MockVisionClient(payloads)
        mock_settings = Settings(
            offline=True,
            llm=LLMSettings(api_key="sk-offline-mock", vision_model="mock-vlm"),
            data_dir=data_dir,
            results_dir=out / "mock_vlm",
            runs_dir=out / "mock_vlm_runs",
        )
        runner = EvaluationRunner(Pipeline(mock_settings, llm=fake), data_dir=data_dir, results_dir=out / "mock_vlm")
        summary = runner.run(split, eval_mode="image_only", save=True, run_name=f"{split}_image_only_mock_vlm", eval_batch=f"{split}-mockvlm-{stamp}")
        row = _mode_rows(summary)
        row["vision_backend"] = "mock-vlm（金标准构造 payload：仅机制检查，不代表真实识别能力）"
        corpora_rows.append(row)

        failures = [
            {
                "case_id": result.case_id,
                "expected": result.expected,
                "expected_status": result.expected_status,
                "predicted": result.predicted,
                "status": result.status,
                "failure_class": result.failure_class,
                "failure_reason": result.failure_reason,
                "routed_skill": result.routed_skill,
                "answerability": result.answerability,
                "capability_status": result.capability_status,
                "root_cause": result.root_cause,
                "verification_outcome": result.verification_outcome,
            }
            for result in summary.results
            if not result.correct
        ]
        report[split] = {
            "label": label,
            "provenance": provenance,
            "sample_count": len(records),
            "modes": corpora_rows,
            "image_only_mock_failures": failures,
        }

    comparison = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "real_vision_model": real_status,
        "data_policy": (
            "screens=regression（允许自校验/调参）；benchmark_real=独立合成基准（不自校验，"
            "holdout 冻结）；两者均非真实招聘平台截图。本轮无授权真实截图。"
        ),
        "note": "mock-vlm 各行仅验证链路机制；真实模型实验状态见 real_vision_model 字段",
        "corpora": report,
    }
    (out / "mode_comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# R2c 三模式评测对照（regression + 独立基准，双维度口径）",
        "",
        f"- 生成时间：{comparison['generated_at']}",
        f"- 真实视觉模型端到端：**{real_status}**",
        f"- 数据政策：{comparison['data_policy']}",
        "",
        "口径说明（R2c）：**总体答题准确率（overall）**分母=所有有唯一客观标准答案的样本，"
        "含当前 Skill 未实现但客观有答案的题型（答不出如实计错，不再混入\"诚实弃权\"）；"
        "**已支持准确率（supported）**≈旧 answer_accuracy 口径；能力覆盖=已支持/客观可答；"
        "误放 false_accept=接受的答案与金标准不一致，误杀 false_reject=Solver 曾给对却被拒。",
        "",
    ]
    for split in report:
        info = report[split]
        lines += [
            f"## {info['label']}（{info['sample_count']} 样本 · {info['provenance']}）",
            "",
            "| 模式 | 视觉后端 | 可答题准确率 | 覆盖率 | 拒答准确率 | 路由准确率 | 混合口径* | E2E题干解析 | 选项映射 | 平均耗时ms |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in info["modes"]:
            e2e = item["e2e"]
            route = "N/A(oracle)" if item["eval_mode"] == "oracle_solver" else _pct(item["route_accuracy"])
            ans_den = e2e.get("answer_scored", item["answer_scored"])
            abst_den = e2e.get("abstention_scored", item["abstention_scored"])
            lines.append(
                f"| {item['eval_mode']} | {item['vision_backend']} | "
                f"{_pct(item['answer_accuracy'])}（分母 {ans_den}） | "
                f"{_pct(item['answerable_coverage'])} | {_pct(item['abstention_accuracy'])}（分母 {abst_den}） | {route} | "
                f"{_pct(e2e.get('end_to_end_accuracy_mixed'))} | {e2e.get('extraction_accuracy', '—') if e2e else '—'} | "
                f"{e2e.get('option_mapping_accuracy', '—') if e2e else '—'} | {item['mean_latency_ms']:.1f} |"
            )
        lines.append("")
        lines.append("*混合口径=旧 R2a 定义（可答+弃权混分母），仅作历史对照。")
        lines += [
            "",
            "R2c 双维度口径（objective=有唯一客观答案样本，含未实现题型）：",
            "",
            "| 模式 | 总体准确率 overall | 已支持准确率 supported | 能力覆盖 | 作答覆盖 | 诚实弃权 | 误放 | 误杀 | unverified | 越权 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in info["modes"]:
            cap = item["capability"]
            lines.append(
                f"| {item['eval_mode']} | {_pct(cap['overall_answer_accuracy'])}"
                f"（{cap['overall_hit']}/{cap['objective_total']}） "
                f"| {_pct(cap['supported_answer_accuracy'])} | {_pct(cap['capability_coverage'])} "
                f"| {_pct(cap['answered_coverage'])} | {_pct(cap['honest_abstention'])} "
                f"| {cap['false_accept']} | {cap['false_reject']} | {cap['unverified_answerable']} | {cap['over_answer']} |"
            )
        lines.append("")
        mock_row = next(i for i in info["modes"] if i["e2e"])
        if mock_row["vision"]:
            v = mock_row["vision"]
            lines += [
                "",
                "image_only(mock) 视觉内容指标（全部注明分母，漏检计入）：",
                "",
                f"- 题干精确匹配 {_pct(v['stem_exact_match']['value'])}（{v['stem_exact_match']['denominator']}） · 题干 CER {v['stem_cer']['value']}（{v['stem_cer']['denominator']}）",
                f"- 选项标号召回 {_pct(v['option_label_recall']['value'])} · 选项文本精确 {_pct(v['option_text_accuracy']['value'])}（{v['option_text_accuracy']['denominator']}）",
                f"- 选项区域 IoU 均值 {_pct(v['option_region_iou_mean']['value'])}（题数 {v['option_region_iou_mean']['denominator']}；框数 {v['option_region_iou_pooled']['denominator']}） · 图形区域 Recall {_pct(v['diagram_region_recall']['value'])}（{v['diagram_region_recall']['denominator']}）",
                f"- 表格单元格准确率 {_pct(v['table_cell_accuracy']['value'])}（{v['table_cell_accuracy']['denominator']}） · 区域检测失败率 {_pct(v['region_detection_failure_rate']['value'])}（{v['region_detection_failure_rate']['denominator']}）",
            ]
        lines += ["", "失败归因分布（按最上游失效环节 / R2c 验证结果与根因）：", ""]
        for item in info["modes"]:
            if item["failure_classes"]:
                lines.append(f"- {item['eval_mode']} failure_class：`{json.dumps(item['failure_classes'], ensure_ascii=False)}`")
            cap = item["capability"]
            if cap["outcomes"]:
                lines.append(f"- {item['eval_mode']} verification_outcome：`{json.dumps(cap['outcomes'], ensure_ascii=False)}`")
            if cap["root_causes"]:
                lines.append(f"- {item['eval_mode']} root_cause：`{json.dumps(cap['root_causes'], ensure_ascii=False)}`")
        lines.append("")
        lines.append(f"失败样本清单（image_only mock，共 {len(info['image_only_mock_failures'])} 条）：见 mode_comparison.json `{split}.image_only_mock_failures`")
        lines.append("")

    lines.append("说明：mock-vlm 行只验证解析→适配→求解→验证的链路机制；真实模型识别能力必须在配置 LLM_API_KEY/LLM_VISION_MODEL 后重跑 image_only 才有结论。")
    (out / "mode_comparison.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 对照报告 → {out / 'mode_comparison.md'}")
    print(f"     真实视觉模型：{real_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
