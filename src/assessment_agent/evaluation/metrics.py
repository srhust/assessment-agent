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
    # 评测输入模式与 E2E（image-only）字段
    input_mode: str = "oracle_solver"  # oracle_solver / auto_router / image_only
    extraction_accurate: Optional[bool] = None  # 解析题干是否与金标准一致
    region_iou: Optional[float] = None  # 题干矩阵区域 IoU（vs 标注）
    option_map_ok: Optional[bool] = None  # 选项标号集合是否与标注一致
    run_ref: Optional[str] = None  # image_only 模式对应的 runs/ 产物目录（可追溯证据）
    failure_class: Optional[str] = None  # R2b 失败归因类别（diagnosis.FAILURE_CLASSES）
    # R2b：逐题视觉内容评测明细（CER / 选项文本 / 逐选项 IoU / 区域召回 / 表格单元格 …），
    # 仅 image_only 且存在金标准时填充；分母字段一律显式写出
    vision: Dict[str, Any] = field(default_factory=dict)
    # R2b：真实模型用量（tokens / 成本，客户端可获取时记录，否则 None）
    usage: Dict[str, Any] = field(default_factory=dict)
    # R2c：标注双维度（客观可答性 × 当前支持能力），从数据派生或显式标注
    answerability: str = ""  # has_unique_answer / undecidable / subjective / objective_unlabeled
    capability_status: str = ""  # supported / unsupported
    # R2c：归因拆分——最初出错位置 与 验证结果
    root_cause: Optional[str] = None
    verification_outcome: Optional[str] = None
    # R4e P1-2：错误归因三分——"最上游偏差"不等于"阻断原因"。首次检出偏差、
    # 真正阻断作答的直接原因、其余次要偏差分开记录（image_only 全链路审计）。
    first_detected_error: Optional[str] = None
    blocking_failure: Optional[str] = None
    secondary_errors: List[Dict[str, Any]] = field(default_factory=list)

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
    answerable_coverage: float = 0.0  # covered_answerable / answerable（分母分子都限定在可答题样本内，≤100%）
    selective_accuracy: float = 0.0  # coverage 中真正正确的比例
    abstention_total: int = 0  # 应当拒答的样本数（expected_status in unsupported/failed）
    abstention_correct: int = 0  # 正确拒答的样本数
    abstention_accuracy: float = 0.0  # abstention_correct / abstention_total
    # 路由指标
    route_total: int = 0  # 有路由信息的样本数
    route_correct: int = 0  # 路由正确的样本数（routed_skill == skill）
    route_accuracy: float = 0.0  # route_correct / route_total
    route_mode: str = "oracle"  # 路由模式（oracle/auto）
    # R2c：客观可答口径（分母含当前 Skill 未实现但有标准答案的样本）
    objective_total: int = 0
    objective_answered_correct: int = 0
    overall_answer_accuracy: float = 0.0
    false_accept_total: int = 0
    false_reject_total: int = 0
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


ANSWERED_OUTCOMES = {"correct_accept", "false_accept", "answered_unlabeled"}


def _rate(items: List["CaseResult"], predicate) -> Dict[str, Any]:
    hit = sum(1 for item in items if predicate(item))
    return {
        "value": round(hit / len(items), 4) if items else None,
        "hit": hit,
        "denominator": len(items),
    }


def compute_capability_metrics(results: List["CaseResult"]) -> Dict[str, Any]:
    """R2c 双维度口径：客观可答性（answerability）× 当前支持能力（capability_status）。

    - ``overall_answer_accuracy``：分母 = **所有有唯一客观标准答案**的样本——包括
      当前 Skill 未实现（capability_status=unsupported）但题目本身有答案的样本。
      系统未实现就答不出，如实计为未答对（能力缺口），不再被排除在分母之外。
    - ``supported_answer_accuracy``：分母 = 客观有答案 **且** 当前已支持题型的样本
      （≈ 旧 answer_accuracy 的口径，历史指标继续保留）。
    - ``capability_coverage``：客观有答案样本中当前题型已支持的比例（能力覆盖）。
    - 诚实弃权（undecidable/subjective）与"有答案未实现的安全拒答"分开统计，
      不再混为同一个 abstention 指标。
    - 误放/误杀/unverified/over-answer 逐题 verification_outcome 汇总计数。
    """
    objective = [r for r in results if r.answerability == "has_unique_answer"]
    supported_objective = [r for r in objective if r.capability_status == "supported"]
    unsupported_objective = [r for r in objective if r.capability_status != "supported"]
    honest = [r for r in results if r.answerability in {"undecidable", "subjective"}]
    undecidable = [r for r in results if r.answerability == "undecidable"]
    subjective = [r for r in results if r.answerability == "subjective"]
    unlabeled = [r for r in results if r.answerability == "objective_unlabeled"]

    outcomes: Dict[str, int] = {}
    root_causes: Dict[str, int] = {}
    for item in results:
        if item.verification_outcome:
            outcomes[item.verification_outcome] = outcomes.get(item.verification_outcome, 0) + 1
        if item.root_cause:
            root_causes[item.root_cause] = root_causes.get(item.root_cause, 0) + 1

    def _is_correct_accept(item: "CaseResult") -> bool:
        return item.verification_outcome == "correct_accept" or (
            item.verification_outcome is None  # 兼容手构造用例：按字段直判
            and item.expected is not None
            and item.predicted is not None
            and item.verification_action in {"accept", "repair"}
            and answers_match(item.predicted, item.expected)
        )

    def _is_answered(item: "CaseResult") -> bool:
        if item.verification_outcome:
            return item.verification_outcome in ANSWERED_OUTCOMES
        return item.predicted is not None and item.verification_action in {"accept", "repair"}

    def _is_honest_abstention(item: "CaseResult") -> bool:
        return item.verification_outcome in {"correct_abstention", "unverified_abstention"} or (
            item.verification_outcome is None and not _is_answered(item)
        )

    overall = _rate(objective, _is_correct_accept)
    supported = _rate(supported_objective, _is_correct_accept)
    answered = _rate(objective, _is_answered)
    honest_rate = _rate(honest, _is_honest_abstention)
    capability_coverage_value = round(len(supported_objective) / len(objective), 4) if objective else None
    return {
        "objective_total": overall["denominator"],
        "overall_answer_accuracy": overall,
        "supported_objective_total": supported["denominator"],
        "supported_answer_accuracy": supported,
        "capability_coverage": {"value": capability_coverage_value,
                                "hit": len(supported_objective), "denominator": len(objective)},
        "overall_answered_coverage": answered,
        "honest_abstention": honest_rate,
        "undecidable_abstention": _rate(undecidable, _is_honest_abstention),
        "subjective_abstention": _rate(subjective, _is_honest_abstention),
        "objective_unlabeled": {"total": len(unlabeled),
                                "answered": sum(1 for r in unlabeled if _is_answered(r))},
        "unsupported_answerable_refusals": sum(
            1 for r in unsupported_objective if not _is_answered(r)
        ),  # 有标准答案但未实现题型的合规安全拒答——计入能力缺口，不计入诚实弃权
        "verification_outcomes": dict(sorted(outcomes.items(), key=lambda x: -x[1])),
        "root_causes": dict(sorted(root_causes.items(), key=lambda x: -x[1])),
        "false_accept_total": outcomes.get("false_accept", 0),
        "false_reject_total": outcomes.get("false_reject", 0),
        # R4g P1-2：提议=金标但证据不足未释放（纪律正确的扣留）单列，
        # **不并入** false_reject_total（后者=证据完整仍被拒的真误杀口径）
        "evidence_insufficient_correct_withheld_total": outcomes.get(
            "evidence_insufficient_correct_withheld", 0),
        "unverified_answerable_total": outcomes.get("unverified_answerable", 0),
        "over_answer_total": outcomes.get("over_answer", 0),
    }


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
            abstention_items = [item for item in items if item.expected_status in {"unsupported", "failed", "undecidable"}]
            route_items = [item for item in items if item.routed_skill is not None]

            answerable_correct = sum(1 for item in answerable if item.correct)
            # 使用 answers_match() 判断 coverage 中的答案是否正确
            coverage_correct = sum(1 for item in coverage_items if answers_match(item.predicted, item.expected))
            abstention_correct = sum(1 for item in abstention_items if item.correct)
            route_correct = sum(1 for item in route_items if item.routed_skill == item.skill)

            # 计算 answerable_coverage：分子只统计 **可答题样本中** 给出有效答案的数量，
            # 避免错误接受了应拒答样本时分子混入分母之外导致 >100%
            covered_answerable = sum(
                1
                for item in answerable
                if item.predicted is not None and item.verification_action in {"accept", "repair"}
            )
            answerable_coverage = round(covered_answerable / len(answerable), 4) if answerable else 0.0
            cap = compute_capability_metrics(items)

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
                # R2c 客观可答口径（分母含未实现但有标准答案的样本）
                objective_total=cap["objective_total"],
                overall_answer_accuracy=cap["overall_answer_accuracy"]["value"] or 0.0,
                false_accept_total=cap["false_accept_total"],
                false_reject_total=cap["false_reject_total"],
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
        abstention_items = [item for item in self._results if item.expected_status in {"unsupported", "failed", "undecidable"}]
        route_items = [item for item in self._results if item.routed_skill is not None]

        answerable_correct = sum(1 for item in answerable if item.correct)
        # 使用 answers_match() 判断 coverage 中的答案是否正确
        coverage_correct = sum(1 for item in coverage_items if answers_match(item.predicted, item.expected))
        abstention_correct = sum(1 for item in abstention_items if item.correct)
        route_correct = sum(1 for item in route_items if item.routed_skill == item.skill)

        # 计算 answerable_coverage：分子只统计可答题样本中获得有效答案的数量
        covered_answerable = sum(
            1
            for item in answerable
            if item.predicted is not None and item.verification_action in {"accept", "repair"}
        )
        answerable_coverage = round(covered_answerable / len(answerable), 4) if answerable else 0.0

        # E2E（image-only）模块级归因指标（R2b 口径：可答题与拒答分开，不再混合）
        e2e_items = [item for item in self._results if item.input_mode == "image_only"]
        e2e: Dict[str, Any] = {}
        if e2e_items:
            extraction_flags = [item.extraction_accurate for item in e2e_items if item.extraction_accurate is not None]
            region_ious = [item.region_iou for item in e2e_items if item.region_iou is not None]
            option_flags = [item.option_map_ok for item in e2e_items if item.option_map_ok is not None]
            e2e_answerable = [
                item for item in e2e_items if item.expected is not None and item.expected_status == "solved"
            ]
            e2e_abstention = [item for item in e2e_items if item.expected_status in {"unsupported", "failed", "undecidable"}]
            e2e_capability = compute_capability_metrics(e2e_items)
            e2e = {
                "cases": len(e2e_items),
                "extraction_accuracy": round(sum(1 for flag in extraction_flags if flag) / len(extraction_flags), 4) if extraction_flags else None,
                "extraction_scored": len(extraction_flags),
                "region_iou_mean": round(mean(region_ious), 4) if region_ious else None,
                "region_iou_scored": len(region_ious),  # 分母=带题干矩阵金标准的样本数（漏检按 0 计入）
                "option_mapping_accuracy": round(sum(1 for flag in option_flags if flag) / len(option_flags), 4) if option_flags else None,
                # ---- R3a：主指标（answerability 客观可答口径，与其他 capability 块同源）----
                "primary_answer_accuracy": e2e_capability["overall_answer_accuracy"],
                "primary_answered_coverage": e2e_capability["overall_answered_coverage"],
                "primary_honest_abstention": e2e_capability["honest_abstention"],
                "primary_false_accept": e2e_capability["false_accept_total"],
                "primary_unverified": e2e_capability["unverified_answerable_total"],
                # ---- 以下均为 R2b expected_status 口径（legacy，勿与新口径混读）----
                "legacy_fields": ["answer_accuracy", "answer_scored", "abstention_accuracy",
                                   "abstention_scored", "answerable_coverage",
                                   "end_to_end_accuracy_mixed"],
                "legacy_note": "answer_accuracy 等字段分母=标注 expected_status==solved 的样本，"
                "与 answerability 口径不同源；报告与对比一律以 primary_*/capability 为准",
                # R2b 口径：分母只含有标准答案的样本
                "answer_accuracy": round(sum(1 for item in e2e_answerable if item.correct) / len(e2e_answerable), 4) if e2e_answerable else None,
                "answer_scored": len(e2e_answerable),
                # 拒答口径独立
                "abstention_accuracy": round(sum(1 for item in e2e_abstention if item.correct) / len(e2e_abstention), 4) if e2e_abstention else None,
                "abstention_scored": len(e2e_abstention),
                # 覆盖率：image_only 下给出通过校验答案的可答题比例
                "answerable_coverage": round(
                    sum(
                        1
                        for item in e2e_answerable
                        if item.predicted is not None and item.verification_action in {"accept", "repair"}
                    )
                    / len(e2e_answerable),
                    4,
                )
                if e2e_answerable
                else None,
                # 旧混合口径（可答题+拒答一起算）——保留但显式标注
                "end_to_end_accuracy_mixed": round(sum(1 for item in e2e_items if item.correct) / len(e2e_items), 4),
                "mixed_note": "旧口径：分母含应拒答样本，仅作历史对照；R2b 起以 answer_accuracy/abstention_accuracy 为准，R3a 起以 primary_* 为准",
                # R2c：image_only 子集的双维度口径
                "capability": e2e_capability,
            }

        # 失败归因类别分布（R2b）
        failure_class_totals: Dict[str, int] = {}
        for item in self._results:
            if not item.correct and item.failure_class:
                failure_class_totals[item.failure_class] = failure_class_totals.get(item.failure_class, 0) + 1

        # R2c：双维度口径（客观可答性 × 支持能力）
        capability = compute_capability_metrics(self._results)

        return {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 4) if total else 0.0,  # legacy: 混合准确率
            "route_mode": route_mode,
            # R2c 顶层口径（overall = 分母含未实现但有客观答案的样本）——主指标
            "capability": capability,
            "primary_answer_accuracy": capability["overall_answer_accuracy"],
            "primary_capability_coverage": capability["capability_coverage"],
            "primary_answered_coverage": capability["overall_answered_coverage"],
            "primary_honest_abstention": capability["honest_abstention"],
            "overall_answer_accuracy": capability["overall_answer_accuracy"],
            "capability_coverage": capability["capability_coverage"],
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
            "e2e": e2e,
            # 原有指标
            "unsupported": sum(1 for item in self._results if item.status == "unsupported"),
            "failed": sum(1 for item in self._results if item.status == "failed"),
            "mean_latency_ms": round(mean(latencies), 3) if latencies else 0.0,
            "p50_latency_ms": percentile(latencies, 0.5),
            "p95_latency_ms": percentile(latencies, 0.95),
            "failure_reasons": dict(sorted(reason_totals.items(), key=lambda item: item[1], reverse=True)),
            "failure_classes": dict(sorted(failure_class_totals.items(), key=lambda item: item[1], reverse=True)),
            "per_skill": {skill: metrics.as_dict() for skill, metrics in per_skill.items()},
        }


def compute_metrics(results: List[CaseResult]) -> Dict[str, Any]:
    accumulator = MetricsAccumulator()
    for result in results:
        accumulator.add(result)
    return accumulator.summary()
