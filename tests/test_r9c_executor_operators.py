# -*- coding: utf-8 -*-
"""R9-C 阶段 C：执行器扩充算子（C/P/!/整数幂/sqrt/ceil/floor/pow/abs）五类样本验收。

类1 normal        正常题面用法，Fraction 精确值
类2 reverse-boundary 反向边界（k=0/k=n/0!/负指数/分数完全平方）
类3 typical-wrong  典型错误对照（C≠P、^右结合、一元负号优先级）
类4 insufficient   不足条件/真不支持算子：失败必须点名，禁止静默
类5 independent   独立复核：展开式二次执行与原值一致（同计划两次运行不算独立证据）
"""
from __future__ import annotations

from fractions import Fraction

import pytest

from assessment_agent.planner import (Given, PlanStep, StructuredPlan,
                                      execute_plan)


def _plan(expr: str, givens):
    return StructuredPlan(
        family="t", method_id="nc.t",
        givens=[Given(label=a, value=str(b), stem_span="") for a, b in givens],
        steps=[PlanStep(var="out", expr=expr, source="", deps=[])],
        target="out", rationale="")


def _ev(expr: str, givens=(("n", "7"), ("k", "2"), ("x", "16"))):
    ex = execute_plan(_plan(expr, givens))
    assert ex.error is None, ex.error
    return ex


# ---------------------------------------------------------------- 类1 正常
def test_normal_exact_values():
    assert _ev("C(n,k)").final == Fraction(21)
    assert _ev("P(n,k)").final == Fraction(42)
    assert _ev("k!").final == Fraction(2)
    assert _ev("n^2").final == Fraction(49)
    assert _ev("sqrt(x)").final == Fraction(4)
    assert _ev("ceil(n/k)").final == Fraction(4)
    assert _ev("floor(n/k)").final == Fraction(3)
    assert _ev("pow(2,10)").final == Fraction(1024)
    assert _ev("abs(0-k)+1").final == Fraction(3)


# ---------------------------------------------------------------- 类2 反向边界
def test_reverse_boundaries():
    assert _ev("C(n,0)").final == Fraction(1)
    assert _ev("C(n,n)").final == Fraction(1)
    assert _ev("0!").final == Fraction(1)
    assert _ev("2^-1").final == Fraction(1, 2)
    assert _ev("sqrt(1/4)").final == Fraction(1, 2)   # 分数完全平方
    assert _ev("n^0").final == Fraction(1)
    assert _ev("0^3").final == Fraction(0)


# ---------------------------------------------------------------- 类3 典型错误对照
def test_typical_wrong_distinguished():
    # C 与 P 混用（典型错答）：值必不同
    assert _ev("C(5,2)").final == Fraction(10)
    assert _ev("P(5,2)").final == Fraction(20)
    # ^ 右结合：2^3^2 = 2^9 = 512（左结合会得 64——典型错算）
    assert _ev("2^3^2").final == Fraction(512)
    # 一元负号优先级低于幂：-2^2 = -4（当作 (-2)^2 得 4 是典型错）
    assert _ev("-2^2").final == Fraction(-4)
    # 乘除先于加减：1+2*3^2 = 19
    assert _ev("1+2*3^2").final == Fraction(19)


# ---------------------------------------------------------------- 类4 不足条件必须点名
def test_insufficient_fails_named():
    cases = [
        ("C(2,5)", "参数非法"),
        ("P(-1,2)", "参数非法"),
        ("sqrt(2)", "仅支持完全平方"),
        ("sqrt(0-k)", "负数"),
        ("2^0.5", "整数指数"),
        ("n^(1/2)", "整数指数"),
        ("(-2)^0.5", "整数指数"),
        ("1001!", "0..1000"),
        ("0^-1", "负幂"),
    ]
    for expr, needle in cases:
        ex = execute_plan(_plan(expr, [("n", "7"), ("k", "2")]))
        assert ex.final is None, (expr, "应失败却给出值")
        assert needle in (ex.error or ""), (expr, ex.error)


# ---------------------------------------------------------------- 类5 独立复核
def test_expanded_expression_reexecutes():
    """执行器给出的展开表达式必须能被独立重放（复核通道的基础）。"""
    expr = "C(6,2) * 2^3 - sqrt(16)/ceil(3/2)"   # 15*8-4/2=118
    ex = _ev(expr, givens=())
    assert ex.final == Fraction(118)
    replay = execute_plan(_plan(ex.expression, givens=()))
    assert replay.error is None, replay.error
    assert replay.final == ex.final
    # 展开式只含数字/算子/函数名，不再含步骤变量（防自指）
    assert "out" not in ex.expression


def test_same_plan_twice_not_independent():
    """同一计划重复执行不构成独立证据——只有展开式重放才是（记录语义，防误用）。"""
    ex1 = _ev("P(n,2)", givens=(("n", "6"),))
    ex2 = _ev("P(n,2)", givens=(("n", "6"),))
    assert ex1.final == ex2.final == Fraction(30)
