"""数字推理工具：数列规律识别（纯 Python，可独立测试）。

只承认能被全部已知项验证的规律；多条规律给出不同答案时如实上报“存在歧义”，
不臆造唯一解。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence

from .base import Tool, ToolError

_PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59,
           61, 67, 71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113]


@dataclass
class SeriesCandidate:
    name: str
    priority: int
    next_value: Fraction
    explanation: str
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SeriesSolution:
    sequence: List[Fraction]
    candidates: List[SeriesCandidate]
    next_value: Optional[Fraction] = None
    agreement: bool = False
    note: str = ""


def _fmt(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _diff(sequence: Sequence[Fraction]) -> List[Fraction]:
    return [b - a for a, b in zip(sequence, sequence[1:])]


def _is_constant(values: Sequence[Fraction]) -> bool:
    return bool(values) and all(value == values[0] for value in values)


# ---------------------------------------------------------------- 候选规律
def _constant_series(sequence: Sequence[Fraction]) -> Optional[SeriesCandidate]:
    if len(sequence) < 3 or not _is_constant(sequence):
        return None
    return SeriesCandidate("常数", 5, sequence[-1], f"每一项都等于 {_fmt(sequence[0])}")


def _arithmetic(sequence: Sequence[Fraction]) -> Optional[SeriesCandidate]:
    if len(sequence) < 3:
        return None
    diffs = _diff(sequence)
    if len(diffs) < 2 or not _is_constant(diffs):
        return None
    step = diffs[0]
    return SeriesCandidate(
        "等差",
        10,
        sequence[-1] + step,
        f"相邻两项公差为 {_fmt(step)}，下一项 = {_fmt(sequence[-1])} + {_fmt(step)}",
        {"diff": str(step)},
    )


def _geometric(sequence: Sequence[Fraction]) -> Optional[SeriesCandidate]:
    if len(sequence) < 3:
        return None
    ratios = set()
    for left, right in zip(sequence, sequence[1:]):
        if left == 0:
            return None
        ratios.add(right / left)
    if len(ratios) != 1:
        return None
    ratio = ratios.pop()
    if ratio == 1:
        return None
    return SeriesCandidate(
        "等比",
        20,
        sequence[-1] * ratio,
        f"公比为 {_fmt(ratio)}，下一项 = {_fmt(sequence[-1])} × {_fmt(ratio)}",
        {"ratio": str(ratio)},
    )


def _polynomial(sequence: Sequence[Fraction], order: int) -> Optional[SeriesCandidate]:
    """order 阶差分恒定（order>=2）。要求足够长的序列以避免过拟合。"""
    if order < 2 or len(sequence) < order + 3:
        return None
    levels: List[List[Fraction]] = [list(sequence)]
    for _ in range(order):
        levels.append(_diff(levels[-1]))
    if not _is_constant(levels[order]):
        return None
    value = levels[order][-1]
    for level in reversed(levels[:-1]):
        value = level[-1] + value
    return SeriesCandidate(
        f"{order}阶差分",
        30 + order,
        value,
        f"{order} 阶差分恒为 {_fmt(levels[order][0])}，外推下一项",
        {"order": order},
    )


def _fibonacci_like(sequence: Sequence[Fraction]) -> Optional[SeriesCandidate]:
    if len(sequence) < 4:
        return None
    if not all(sequence[i] == sequence[i - 1] + sequence[i - 2] for i in range(2, len(sequence))):
        return None
    return SeriesCandidate(
        "前两项之和",
        50,
        sequence[-1] + sequence[-2],
        "从第三项起每项等于前两项之和",
    )


def _power_offset(sequence: Sequence[Fraction]) -> Optional[SeriesCandidate]:
    if len(sequence) < 4:
        return None
    bases = {
        "n": lambda index: Fraction(index),
        "n²": lambda index: Fraction(index * index),
        "n³": lambda index: Fraction(index**3),
        "2ⁿ": lambda index: Fraction(2**index),
        "3ⁿ": lambda index: Fraction(3**index),
        "n!": lambda index: Fraction(math.factorial(index)),
    }
    for shift in (0, 1, 2):
        for name, basis in bases.items():
            offsets = {sequence[i] - basis(i + 1 + shift) for i in range(len(sequence))}
            if len(offsets) != 1:
                continue
            offset = offsets.pop()
            sign = "+" if offset >= 0 else "-"
            return SeriesCandidate(
                f"{name}±常数",
                60 + shift,
                basis(len(sequence) + 1 + shift) + offset,
                f"通项为 {name}（第 {1 + shift} 项起）{sign} {_fmt(abs(offset))}",
                {"shift": shift, "offset": _fmt(offset)},
            )
    return None


def _interleaved(sequence: Sequence[Fraction]) -> Optional[SeriesCandidate]:
    if len(sequence) < 6:
        return None
    first = list(sequence[0::2])
    second = list(sequence[1::2])
    if len(first) < 3 or len(second) < 3:
        return None
    fit_first = _arithmetic(first) or _geometric(first) or _polynomial(first, 2)
    fit_second = _arithmetic(second) or _geometric(second) or _polynomial(second, 2)
    if fit_first is None or fit_second is None:
        return None
    next_value = fit_first.next_value if len(sequence) % 2 == 0 else fit_second.next_value
    return SeriesCandidate(
        "奇偶分组",
        15,
        next_value,
        f"奇偶项各成规律（奇：{fit_first.name}，偶：{fit_second.name}）",
        {"odd": fit_first.name, "even": fit_second.name},
    )


def _prime_run(sequence: Sequence[Fraction]) -> Optional[SeriesCandidate]:
    """素数序列（行测常考）：连续整数恰为连续素数 → 下一素数。"""
    if len(sequence) < 3 or any(v.denominator != 1 or v <= 1 for v in sequence):
        return None
    ints = [int(v) for v in sequence]
    for start in range(max(1, len(_PRIMES) - len(ints))):
        window = _PRIMES[start:start + len(ints)]
        if window == ints and start + len(ints) < len(_PRIMES):
            nxt = _PRIMES[start + len(ints)]
            return SeriesCandidate("素数", 12, Fraction(nxt),
                                   f"连续素数列 {_fmt(sequence[0])}… 下一素数 {nxt}",
                                   {"start_index": start})
    return None


def _fraction_components(sequence: Sequence[Fraction]) -> Optional[SeriesCandidate]:
    """分数数列：分子、分母各自成规律（如 1/2,3/4,5/8,7/16 → 9/32）。"""
    if len(sequence) < 4 or all(v.denominator == 1 for v in sequence):
        return None
    num_c = _run_simple([Fraction(v.numerator) for v in sequence])
    den_c = _run_simple([Fraction(v.denominator) for v in sequence])
    if num_c is None or den_c is None:
        return None
    nxt = Fraction(num_c.next_value) / Fraction(den_c.next_value)
    return SeriesCandidate(
        "分子分母复合", 14, nxt,
        f"分子按{num_c.name}→{_fmt(num_c.next_value)}，分母按{den_c.name}→{_fmt(den_c.next_value)}",
        {"numerator_rule": num_c.name, "denominator_rule": den_c.name},
    )


def _run_simple(sequence: Sequence[Fraction]) -> Optional[SeriesCandidate]:
    """给分子/分母序列找一条简单规律（素数/等差/等比/幂/平方偏移）。"""
    for detector in (_arithmetic, _geometric, _prime_run, _power_offset):
        candidate = detector(sequence)
        if candidate is not None:
            return candidate
    return _polynomial(sequence, 2)


def _detect(sequence: Sequence[Fraction]) -> List[SeriesCandidate]:
    candidates: List[SeriesCandidate] = []
    for detector in (_constant_series, _arithmetic, _geometric, _fibonacci_like,
                     _power_offset, _prime_run, _interleaved, _fraction_components):
        candidate = detector(sequence)
        if candidate is not None:
            candidates.append(candidate)
    for order in (2, 3, 4):
        candidate = _polynomial(sequence, order)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (item.priority, item.name))
    return candidates


def solve_series(sequence: Sequence[Fraction]) -> SeriesSolution:
    values = [Fraction(value) for value in sequence]
    if len(values) < 3:
        raise ToolError("数列至少需要 3 项才能识别规律")
    candidates = _detect(values)
    unique_answers = {candidate.next_value for candidate in candidates}
    if not candidates:
        return SeriesSolution(values, [], None, False, "没有匹配到任何已知规律")
    chosen = candidates[0]
    return SeriesSolution(
        sequence=values,
        candidates=candidates,
        next_value=chosen.next_value,
        agreement=len(unique_answers) == 1,
        note="唯一规律" if len(unique_answers) == 1 else f"存在 {len(unique_answers)} 种可能答案，取优先级最高者",
    )


# ---------------------------------------------------------------- 文本解析
_SEQUENCE_CUT_MARKERS = ("选项", "A.", "A、", "A)", "A）", "（A）", "(A)")


def parse_sequence(text: str) -> List[Fraction]:
    """从题干中解析数列项；结尾括号空位会被忽略。"""
    cleaned = text
    for marker in _SEQUENCE_CUT_MARKERS:
        index = cleaned.find(marker)
        if index > 2:
            cleaned = cleaned[:index]
    cleaned = re.sub(r"[（(]\s*[)）]", " ", cleaned)
    cleaned = cleaned.replace("，", ",").replace("、", ",").replace("；", " ")
    numbers: List[Fraction] = []
    for token in re.findall(r"-?\d+(?:/\d+)?(?:\.\d+)?", cleaned):
        try:
            numbers.append(Fraction(token))
        except (ValueError, ZeroDivisionError):
            continue
    return numbers


def find_blank_position(text: str) -> Optional[str]:
    """判断空位位置：'end' 表示末尾待求项，'middle' 表示中间缺项。"""
    match = re.search(r"[（(]\s*[)）]", text)
    if not match:
        return None
    return "middle" if re.search(r"\d", text[match.end() :]) else "end"


class NumberSeriesTool(Tool):
    id = "number_series"
    description = "数列规律识别：等差 / 等比 / 差分 / 奇偶分组 / 递推 / 幂次偏移"
    requires = ()

    def run(
        self,
        text: Optional[str] = None,
        sequence: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        if sequence is None:
            if not text:
                raise ToolError("需要 text 或 sequence 参数")
            sequence = parse_sequence(text)
        if len(sequence) < 3:
            raise ToolError(f"只解析到 {len(sequence)} 项，不足以识别规律")
        solution = solve_series([Fraction(str(item)) for item in sequence])
        return {
            "sequence": [_fmt(value) for value in solution.sequence],
            "candidates": [
                {
                    "name": candidate.name,
                    "priority": candidate.priority,
                    "next_value": _fmt(candidate.next_value),
                    "explanation": candidate.explanation,
                    "detail": candidate.detail,
                }
                for candidate in solution.candidates
            ],
            "next_value": _fmt(solution.next_value) if solution.next_value is not None else None,
            "next_value_float": float(solution.next_value) if solution.next_value is not None else None,
            "agreement": solution.agreement,
            "note": solution.note,
        }
