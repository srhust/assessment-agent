"""评测指标：每类题型的准确率、耗时分布与失败原因统计。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any, Dict, List, Optional


def answers_match(predicted: Optional[str], expected: Optional[str]) -> bool:
    """判断预测答案与期望答案是否匹配（支持字符串和数值比较）。"""
    if predicted is None or expected is None:
        return False
    left = str(predicted).strip()
    right = str(expected).strip()
    if left == right:
        return True
    if left.upper() == right.upper():
        return True
    try:
        return abs(float(left) - float(right)) <= max(1e-9, abs(float(right)) * 1e-6)
    except ValueError:
        return False


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
    expected_status: str = "solved"  # solved / unsupported / failed
    failure_reason: Optional[str] = None
    predicted_option: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    # 路由信息
    routed_skill: Optional[str] = None  # Router 预测的题型
    route_source: Optional[str] = None  # 路由来源（rules/llm/explicit）
    route_confidence: Optional[float] = None  # 路由置信度

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillMetrics:
    skill: str
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0  # legacy: 混合准确率（包含拒答样本）
    # 新指标：分离准确率与拒答率
    answerable: int = 0  # 有标准答案的题目数
    answer_accuracy: float = 0.0  # 正确解答数 / answerable
    covered_count: int = 0  # 提供通过校验答案的题目数（predicted 不为 None 且 action in accept/repair）
    coverage_ratio: float = 0.0  # covered_count / total (overall_answer_yield)
    answerable_coverage: float = 0.0  # covered_count / answerable (answerable_coverage)
    selective_accuracy: float = 0.0  # coverage 中真正正确的比例
    abstention_total: int = 0  # 应当拒答的样本数（expected_status in unsupported/failed）
    abstention_correct: int = 0  # 正确拒答的样本数
    abstention_accuracy: float = 0.0  # abstention_correct / abstention_total
    # 路由指标
    route_total: int = 0  # 有路由信息的样本数
    route_correct: int = 0  # 路由正确的样本数（routed_skill == skill）
    route_accuracy: float = 0.0  # route_correct / route_total
    route_mode: str = "oracle"  # 路由模式（oracle/auto）
    # 原有指标
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

    def skill_metrics(self, route_mode: str = "oracle") -> Dict[str, SkillMetrics]:
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

            # 计算新指标
            answerable = [item for item in items if item.expected is not None and item.expected_status == "solved"]
            coverage_items = [
                item for item in items
                if item.predicted is not None and item.verification_action in {"accept", "repair"}
            ]
            abstention_items = [item for item in items if item.expected_status in {"unsupported", "failed"}]
            route_items = [item for item in items if item.routed_skill is not None]

            answerable_correct = sum(1 for item in answerable if item.correct)
            # 使用 answers_match() 判断 coverage 中的答案是否正确
            coverage_correct = sum(1 for item in coverage_items if answers_match(item.predicted, item.expected))
            abstention_correct = sum(1 for item in abstention_items if item.correct)
            route_correct = sum(1 for item in route_items if item.routed_skill == item.skill)

            # 计算 answerable_coverage
            answerable_coverage = (
                round(len(coverage_items) / len(answerable), 4) if answerable else 0.0
            )

            metrics[skill] = SkillMetrics(
                skill=skill,
                total=len(items),
                correct=sum(1 for item in items if item.correct),
                accuracy=round(sum(1 for item in items if item.correct) / len(items), 4) if items else 0.0,
                # 新指标
                answerable=len(answerable),
                answer_accuracy=round(answerable_correct / len(answerable), 4) if answerable else 0.0,
                covered_count=len(coverage_items),
                coverage_ratio=round(len(coverage_items) / len(items), 4) if items else 0.0,
                answerable_coverage=answerable_coverage,
                selective_accuracy=round(coverage_correct / len(coverage_items), 4) if coverage_items else 0.0,
                abstention_total=len(abstention_items),
                abstention_correct=abstention_correct,
                abstention_accuracy=round(abstention_correct / len(abstention_items), 4) if abstention_items else 0.0,
                route_total=len(route_items),
                route_correct=route_correct,
                route_accuracy=round(route_correct / len(route_items), 4) if route_items else 0.0,
                route_mode=route_mode,
                # 原有指标
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

    def summary(self, route_mode: str = "oracle") -> Dict[str, Any]:
        per_skill = self.skill_metrics(route_mode=route_mode)
        total = len(self._results)
        correct = sum(1 for item in self._results if item.correct)
        latencies = [item.latency_ms for item in self._results]
        reason_totals: Dict[str, int] = {}
        for item in self._results:
            if item.failure_reason:
                reason_totals[item.failure_reason] = reason_totals.get(item.failure_reason, 0) + 1

        # 全局新指标
        answerable = [item for item in self._results if item.expected is not None and item.expected_status == "solved"]
        coverage_items = [
            item for item in self._results
            if item.predicted is not None and item.verification_action in {"accept", "repair"}
        ]
        abstention_items = [item for item in self._results if item.expected_status in {"unsupported", "failed"}]
        route_items = [item for item in self._results if item.routed_skill is not None]

        answerable_correct = sum(1 for item in answerable if item.correct)
        # 使用 answers_match() 判断 coverage 中的答案是否正确
        coverage_correct = sum(1 for item in coverage_items if answers_match(item.predicted, item.expected))
        abstention_correct = sum(1 for item in abstention_items if item.correct)
        route_correct = sum(1 for item in route_items if item.routed_skill == item.skill)

        # 计算 answerable_coverage
        answerable_coverage = (
            round(len(coverage_items) / len(answerable), 4) if answerable else 0.0
        )

        return {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 4) if total else 0.0,  # legacy: 混合准确率
            "route_mode": route_mode,
            # 新指标
            "answerable": len(answerable),
            "answer_accuracy": round(answerable_correct / len(answerable), 4) if answerable else 0.0,
            "covered_count": len(coverage_items),
            "coverage_ratio": round(len(coverage_items) / total, 4) if total else 0.0,  # overall_answer_yield
            "answerable_coverage": answerable_coverage,
            "selective_accuracy": round(coverage_correct / len(coverage_items), 4) if coverage_items else 0.0,
            "abstention_total": len(abstention_items),
            "abstention_correct": abstention_correct,
            "abstention_accuracy": round(abstention_correct / len(abstention_items), 4) if abstention_items else 0.0,
            "route_total": len(route_items),
            "route_correct": route_correct,
            "route_accuracy": round(route_correct / len(route_items), 4) if route_items else 0.0,
            # 原有指标
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
