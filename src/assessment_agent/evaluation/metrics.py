"""评测指标：每类题型的准确率、耗时分布与失败原因统计。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any, Dict, List, Optional


@dataclass
class CaseResult:
    case_id: str
    skill: str
    expected: Optional[str]
    predicted: Optional[str]
    correct: bool
    status: str
    verification_action: str
    latency_ms: float
    failure_reason: Optional[str] = None
    predicted_option: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillMetrics:
    skill: str
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0
    solved: int = 0
    unsupported: int = 0
    failed: int = 0
    repaired: int = 0
    rejected: int = 0
    mean_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    failure_reasons: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    value = ordered[lower] * (1 - fraction) + ordered[upper] * fraction
    return round(value, 3)


class MetricsAccumulator:
    """按题型累积 CaseResult，输出结构化指标。"""

    def __init__(self) -> None:
        self._results: List[CaseResult] = []

    def add(self, result: CaseResult) -> None:
        self._results.append(result)

    @property
    def results(self) -> List[CaseResult]:
        return list(self._results)

    def skill_metrics(self) -> Dict[str, SkillMetrics]:
        buckets: Dict[str, List[CaseResult]] = {}
        for result in self._results:
            buckets.setdefault(result.skill, []).append(result)
        metrics: Dict[str, SkillMetrics] = {}
        for skill, items in sorted(buckets.items()):
            latencies = [item.latency_ms for item in items]
            reasons: Dict[str, int] = {}
            for item in items:
                if item.failure_reason:
                    reasons[item.failure_reason] = reasons.get(item.failure_reason, 0) + 1
            metrics[skill] = SkillMetrics(
                skill=skill,
                total=len(items),
                correct=sum(1 for item in items if item.correct),
                accuracy=round(sum(1 for item in items if item.correct) / len(items), 4),
                solved=sum(1 for item in items if item.status == "solved"),
                unsupported=sum(1 for item in items if item.status == "unsupported"),
                failed=sum(1 for item in items if item.status == "failed"),
                repaired=sum(1 for item in items if item.verification_action == "repair"),
                rejected=sum(1 for item in items if item.verification_action == "reject"),
                mean_latency_ms=round(mean(latencies), 3) if latencies else 0.0,
                p50_latency_ms=percentile(latencies, 0.5),
                p95_latency_ms=percentile(latencies, 0.95),
                failure_reasons=dict(sorted(reasons.items(), key=lambda item: item[1], reverse=True)),
            )
        return metrics

    def summary(self) -> Dict[str, Any]:
        per_skill = self.skill_metrics()
        total = len(self._results)
        correct = sum(1 for item in self._results if item.correct)
        latencies = [item.latency_ms for item in self._results]
        reason_totals: Dict[str, int] = {}
        for item in self._results:
            if item.failure_reason:
                reason_totals[item.failure_reason] = reason_totals.get(item.failure_reason, 0) + 1
        return {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 4) if total else 0.0,
            "unsupported": sum(1 for item in self._results if item.status == "unsupported"),
            "failed": sum(1 for item in self._results if item.status == "failed"),
            "mean_latency_ms": round(mean(latencies), 3) if latencies else 0.0,
            "p50_latency_ms": percentile(latencies, 0.5),
            "p95_latency_ms": percentile(latencies, 0.95),
            "failure_reasons": dict(sorted(reason_totals.items(), key=lambda item: item[1], reverse=True)),
            "per_skill": {skill: metrics.as_dict() for skill, metrics in per_skill.items()},
        }


def compute_metrics(results: List[CaseResult]) -> Dict[str, Any]:
    accumulator = MetricsAccumulator()
    for result in results:
        accumulator.add(result)
    return accumulator.summary()
