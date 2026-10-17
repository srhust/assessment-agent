"""评测执行器：跑 train/dev/test 数据集，输出准确率、耗时与失败原因。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..pipeline import Pipeline, PipelineError
from ..schemas import QuestionOption
from .metrics import CaseResult, MetricsAccumulator, answers_match

SPLITS = ("train", "dev", "test")


@dataclass
class Case:
    id: str
    skill: str
    stem: str
    options: List[QuestionOption] = field(default_factory=list)
    answer: Optional[str] = None
    expected_status: str = "solved"
    tags: List[str] = field(default_factory=list)
    source_file: str = ""
    image: Optional[str] = None
    grid: Optional[List[int]] = None


@dataclass
class EvaluationSummary:
    split: str
    total: int
    accuracy: float
    metrics: Dict[str, Any]
    output_dir: Optional[str]
    duration_s: float
    results: List[CaseResult] = field(default_factory=list)


def _load_options(raw: Any) -> List[QuestionOption]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [
            QuestionOption(label=str(label).upper(), text=str(text))
            for label, text in raw.items()
        ]
    if isinstance(raw, list):
        options: List[QuestionOption] = []
        for index, item in enumerate(raw):
            if isinstance(item, dict):
                options.append(
                    QuestionOption(
                        label=str(item.get("label") or chr(ord("A") + index)).upper(),
                        text=str(item.get("text") or ""),
                    )
                )
            else:
                options.append(QuestionOption(label=chr(ord("A") + index), text=str(item)))
        return options
    return []


def _load_answer(payload: Dict[str, Any]) -> Optional[str]:
    """标注答案：支持 ``answer`` 与 ``expected_answer`` 两种写法。"""
    for key in ("answer", "expected_answer"):
        value = payload.get(key)
        if value is not None and str(value) != "":
            return str(value)
    return None


def _resolve_image(data_dir: Path, raw: Any, source: Path, line_number: int) -> Optional[str]:
    """题图路径：相对路径按 data/ 目录解析；缺失时直接报错，而不是静默跳过。"""
    if not raw:
        return None
    candidate = Path(str(raw))
    resolved = candidate if candidate.is_absolute() else Path(data_dir) / candidate
    if not resolved.is_file():
        raise FileNotFoundError(f"{source.name}:{line_number} 题图不存在: {resolved}")
    return str(resolved)


def _load_grid(raw: Any) -> Optional[List[int]]:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            rows, cols = int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            return None
        if rows > 0 and cols > 0:
            return [rows, cols]
    return None


def load_cases(data_dir: Path, split: str, skills: Optional[Sequence[str]] = None) -> List[Case]:
    if split not in SPLITS:
        raise ValueError(f"split 必须是 {SPLITS} 之一，收到 {split!r}")
    split_dir = Path(data_dir) / split
    if not split_dir.is_dir():
        raise FileNotFoundError(f"数据集目录不存在: {split_dir}")
    cases: List[Case] = []
    for path in sorted(split_dir.glob("*.jsonl")):
        if skills and path.stem not in skills:
            continue
        # utf-8-sig：兼容 Windows PowerShell 写出的 BOM 文件
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} JSON 解析失败: {exc}") from exc
            cases.append(
                Case(
                    id=str(payload.get("id") or f"{path.stem}-{line_number}"),
                    skill=str(payload.get("skill") or path.stem),
                    stem=str(payload.get("stem") or ""),
                    options=_load_options(payload.get("options")),
                    answer=_load_answer(payload),
                    expected_status=str(payload.get("expected_status") or "solved"),
                    tags=[str(tag) for tag in payload.get("tags") or []],
                    source_file=str(path.name),
                    image=_resolve_image(data_dir, payload.get("image"), path, line_number),
                    grid=_load_grid(payload.get("grid")),
                )
            )
    if not cases:
        raise ValueError(f"{split_dir} 中没有可用样本")
    return cases


def _failure_reason(status: str, action: str, error: Optional[str]) -> Optional[str]:
    if status == "unsupported":
        return "unsupported_skill"
    if status == "failed":
        return error or "solve_failed"
    if action == "reject":
        return "verification_rejected"
    return "wrong_answer"


class EvaluationRunner:
    def __init__(self, pipeline: Pipeline, data_dir: Optional[Path] = None, results_dir: Optional[Path] = None):
        self.pipeline = pipeline
        self.data_dir = Path(data_dir or pipeline.settings.data_dir)
        self.results_dir = Path(results_dir or pipeline.settings.results_dir)

    def run(
        self,
        split: str = "test",
        *,
        save: bool = True,
        run_name: Optional[str] = None,
        skills: Optional[Sequence[str]] = None,
        route_mode: str = "oracle",
    ) -> EvaluationSummary:
        cases = load_cases(self.data_dir, split, skills=skills)
        accumulator = MetricsAccumulator()
        started = time.perf_counter()
        for case in cases:
            result = self._run_case(case, route_mode=route_mode)
            accumulator.add(result)
        duration = time.perf_counter() - started

        metrics = accumulator.summary()
        output_dir: Optional[Path] = None
        if save:
            output_dir = self.results_dir / split / (run_name or datetime.now().strftime("%Y%m%dT%H%M%S"))
            output_dir.mkdir(parents=True, exist_ok=True)
            self._write_outputs(output_dir, split, cases, accumulator.results, metrics, duration)
        return EvaluationSummary(
            split=split,
            total=metrics["total"],
            accuracy=metrics["accuracy"],
            metrics=metrics,
            output_dir=str(output_dir) if output_dir else None,
            duration_s=round(duration, 3),
            results=accumulator.results,
        )

    # ------------------------------------------------------------------ 内部
    def _run_case(self, case: Case, route_mode: str = "oracle") -> CaseResult:
        started = time.perf_counter()
        try:
            # oracle 模式使用标注题型，auto 模式由 Router 自动判断
            skill_for_pipeline = case.skill if route_mode == "oracle" else None
            run = self.pipeline.run(
                text=case.stem,
                image_path=case.image,
                options=case.options,
                skill=skill_for_pipeline,
                save=False,
                meta={"grid": case.grid} if case.grid else None,
            )
            solution = run.solution
            verification = run.verification
            route = run.route
            # 只在 Verifier 接受或修复答案时使用 final_answer，拒绝时不输出未验证答案
            predicted = (
                verification.final_answer
                if verification.action in {"accept", "repair"}
                else None
            )
            if case.expected_status == "unsupported":
                correct = solution.status == "unsupported"
            elif case.expected_status == "failed":
                # 合规性样本：必须严格匹配 failed 状态（不猜答案）才算正确
                correct = solution.status == "failed"
            else:
                correct = solution.status == "solved" and answers_match(predicted, case.answer)
            latency = (time.perf_counter() - started) * 1000.0
            return CaseResult(
                case_id=case.id,
                skill=case.skill,
                expected=case.answer,
                predicted=predicted,
                correct=correct,
                status=solution.status,
                verification_action=verification.action,
                latency_ms=round(latency, 3),
                expected_status=case.expected_status,
                failure_reason=None if correct else _failure_reason(solution.status, verification.action, solution.error),
                predicted_option=solution.answer,
                tags=list(case.tags),
                routed_skill=route.skill,
                route_source=route.source,
                route_confidence=route.confidence,
            )
        except PipelineError as exc:
            latency = (time.perf_counter() - started) * 1000.0
            return CaseResult(
                case_id=case.id,
                skill=case.skill,
                expected=case.answer,
                predicted=None,
                correct=False,
                status="failed",
                verification_action="reject",
                latency_ms=round(latency, 3),
                expected_status=case.expected_status,
                failure_reason=f"pipeline_error: {exc}",
                tags=list(case.tags),
            )
        except Exception as exc:  # noqa: BLE001 - 评测需要记录单题崩溃而不是中断
            latency = (time.perf_counter() - started) * 1000.0
            return CaseResult(
                case_id=case.id,
                skill=case.skill,
                expected=case.answer,
                predicted=None,
                correct=False,
                status="failed",
                verification_action="reject",
                latency_ms=round(latency, 3),
                expected_status=case.expected_status,
                failure_reason=f"exception: {type(exc).__name__}: {exc}",
                tags=list(case.tags),
            )

    def _write_outputs(
        self,
        output_dir: Path,
        split: str,
        cases: List[Case],
        results: List[CaseResult],
        metrics: Dict[str, Any],
        duration: float,
    ) -> None:
        (output_dir / "predictions.jsonl").write_text(
            "\n".join(json.dumps(result.as_dict(), ensure_ascii=False) for result in results) + "\n",
            encoding="utf-8",
        )
        (output_dir / "metrics.json").write_text(
            json.dumps(
                {
                    "split": split,
                    "sample_count": len(cases),
                    "duration_s": round(duration, 3),
                    "settings": self.pipeline.settings.describe(),
                    **metrics,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (output_dir / "summary.md").write_text(
            self._render_summary(split, len(cases), metrics, duration),
            encoding="utf-8",
        )

    @staticmethod
    def _render_summary(split: str, sample_count: int, metrics: Dict[str, Any], duration: float) -> str:
        lines = [
            f"# 评测报告 · split={split}",
            "",
            f"- 样本数：{sample_count}",
            f"- 总体准确率：{metrics['accuracy']:.2%}（{metrics['correct']}/{metrics['total']}）",
            f"- 未实现（unsupported）：{metrics['unsupported']}；求解失败：{metrics['failed']}",
            f"- 耗时：总 {duration:.2f}s，平均 {metrics['mean_latency_ms']:.1f} ms，"
            f"p50 {metrics['p50_latency_ms']:.1f} ms，p95 {metrics['p95_latency_ms']:.1f} ms",
            "",
            "| 题型 | 正确/总数 | 准确率 | 平均耗时(ms) | p95(ms) | unsupported | failed | 主要失败原因 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for skill, stats in metrics["per_skill"].items():
            reasons = ", ".join(f"{key}×{value}" for key, value in stats["failure_reasons"].items()) or "-"
            lines.append(
                f"| {skill} | {stats['correct']}/{stats['total']} | {stats['accuracy']:.2%} | "
                f"{stats['mean_latency_ms']:.1f} | {stats['p95_latency_ms']:.1f} | "
                f"{stats['unsupported']} | {stats['failed']} | {reasons} |"
            )
        lines.append("")
        return "\n".join(lines)
