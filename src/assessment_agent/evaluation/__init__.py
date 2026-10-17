"""数据集与评测程序。

目录约定（默认 ``data/``，可用 ``ASSESSMENT_DATA_DIR`` 覆盖）::

    data/train/<skill>.jsonl
    data/dev/<skill>.jsonl
    data/test/<skill>.jsonl

每条样本（JSONL）字段：
- id          样本唯一 ID
- skill       期望题型（强制路由到该 Skill，便于按题型统计）
- stem        题干
- options     可选，{"A": "...", "B": "..."} 或 ["...", "..."]
- answer      期望答案（选项标号或数值文本）
- expected_status  可选，"solved"（默认）/ "unsupported"
- tags        可选，标签数组，用于失败归因
"""

from .metrics import CaseResult, MetricsAccumulator, SkillMetrics, compute_metrics
from .runner import Case, EvaluationRunner, EvaluationSummary, load_cases

__all__ = [
    "Case",
    "CaseResult",
    "EvaluationRunner",
    "EvaluationSummary",
    "MetricsAccumulator",
    "SkillMetrics",
    "compute_metrics",
    "load_cases",
]
