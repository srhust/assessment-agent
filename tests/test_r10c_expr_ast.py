# -*- coding: utf-8 -*-
"""R10-C：结构化表达式 AST 测试——正负例、优先级、嵌套、非法格式、等值结果。

工单：弃"抽取最长数字运算片段"；候选片段以可解析为合法 AST 为准；
degenerate_copy 仅在缺结构化计算证据时阻断。
"""
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from assessment_agent.tools.expr_ast import (computation_evidence,
                                             parse_expr, parse_stem_expression,
                                             replay_postfix)


def test_basic_ops_values():
    assert parse_expr("7^2 - 4^2").eval() == Fraction(33)
    assert parse_expr("C(6,2) + P(3,2)").eval() == Fraction(15 + 6)
    assert parse_expr("3! + √16").eval() == Fraction(10)


def test_unary_minus_precedence_right_assoc():
    assert parse_expr("-2^4 + 2^4").eval() == Fraction(0)      # (-16)+16
    assert parse_expr("(-2)^4").eval() == Fraction(16)
    assert parse_expr("2^3^2").eval() == Fraction(512)         # 右结合
    assert parse_expr("2^-2").eval() == Fraction(1, 4)


def test_nesting_and_percent():
    assert parse_expr("(1+2+4)!").eval() == Fraction(5040)
    assert parse_expr("10% + 20%").eval() == Fraction(3, 10)
    assert parse_expr("sqrt(9) + 1").eval() == Fraction(4)


def test_spans_map_to_stem():
    stem = "计算：C(4,2)+2! = ?"
    node = parse_stem_expression(stem)
    assert node is not None and node.eval() == Fraction(6 + 2)
    spans = node.number_spans()
    for s, e, v in spans:
        seg = stem[s:e]
        assert seg.replace("(", "").isdigit() or seg in ("4", "2"), (s, e, seg)


def test_illegal_forms_rejected():
    bad = ["3^0.5", "√2", "C(4)", "(1+2", "2 + 汉字", "1..2", "sqrt(7)"]
    for b in bad:
        with pytest.raises((ValueError, ZeroDivisionError)):
            node = parse_expr(b)
            node.eval()


def test_double_unary_plus_matches_python_semantics():
    """复核一致性优先于风格：1++2 在 fraction_eval/Python 亦为 3，AST 不另立语义。"""
    from assessment_agent.tools.math_tool import fraction_eval
    assert parse_expr("1++2").eval() == fraction_eval("1++2")


def test_stem_not_pure_arithmetic_returns_none():
    assert parse_stem_expression("涨价 10% 后再降 10%，现价是原价的百分之几？") is None
    assert parse_stem_expression("3^4÷9=9 中的 9 是什么") is None


def test_replay_postfix_matches_eval():
    node = parse_expr("C(5,2) * 3! - 2^4")
    v1 = node.eval()
    v2 = replay_postfix(node.postfix())
    assert v1 == v2 == Fraction(10 * 6 - 16)


def test_computation_evidence_legit_equal_value():
    """工单场景：目标=题干已知数值（3^4÷9=9），有强算子依赖链→证据成立。"""
    node = parse_stem_expression("3^4÷9 = ?")
    assert node is not None and node.eval() == Fraction(9)
    ev = computation_evidence(node)
    assert ev["ok"] and ev["has_strong_operator"]


def test_degenerate_copy_no_evidence():
    node = parse_expr("5-5")
    ev = computation_evidence(node)
    assert not ev["has_strong_operator"]
    assert ev["combines_distinct_values"] is False  # 左右同值：退化
    node2 = parse_expr("9")
    assert computation_evidence(node2)["ok"] is False
