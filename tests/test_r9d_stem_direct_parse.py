# -*- coding: utf-8 -*-
"""R9-D：题面直读独立求值通道（numeric.stem_direct_parse）回归。

背景：dv-expr-operators-03（-2^4+2^4）曾以 method=sympy_exact 把前导负号丢给
extract_expression，得到 2**4+2**4=32 并被"独立复核"放行——那是复算 Solver
自己的抽取式（同一计划算两遍），不是第二独立证据。新通道由 Verifier 直接从
题干原文再解析求值，mismatch 即阻断释放。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assessment_agent.config import Settings
from assessment_agent.schemas import Question, QuestionOption
from assessment_agent.skills.base import SkillContext
from assessment_agent.skills.numeric_calculation import NumericCalculationSkill
from assessment_agent.tools import default_toolbox


def _ctx():
    return SkillContext(settings=Settings.from_env(), llm=None, toolbox=default_toolbox())


def _q(stem, opts):
    return Question(id="t", skill="numeric_calculation", stem=stem,
                    options=[QuestionOption(label=l, text=t) for l, t in opts])


def test_sign_loss_is_caught_not_released():
    """-2^4+2^4=0。R10-C 后 AST-first：抽取失真在源头消除（AST 一元负号语义）；
    同时手工构造旧 sympy_exact 失真解，验证直读护栏仍在位。"""
    sk = NumericCalculationSkill()
    q = _q("计算：-2^4 + 2^4 = ?", [("A", "0"), ("B", "32"), ("C", "-32"), ("D", "16")])
    sol = sk.solve(q, _ctx())
    assert sol.method == "expr_ast" and sol.answer == "A"
    ver = sk.verify(q, sol, _ctx())
    assert ver.action == "accept"
    names = {c.name for c in (ver.checks or [])}
    assert "numeric.expr_ast_replay" in names
    # 护栏：旧失真抽取（丢前导负号得 32）走 sympy_exact 必须被 unverified
    from assessment_agent.schemas import Solution
    bad = Solution(status="solved", skill="numeric_calculation", answer="B",
                   method="sympy_exact",
                   meta={"expression": "2**4 + 2**4", "value": 32.0, "exact": "32",
                         "matched_label": "B"})
    ver2 = sk.verify(q, bad, _ctx())
    chk = next(c for c in (ver2.checks or []) if c.name == "numeric.stem_direct_parse")
    assert chk.passed is False and chk.severity == "blocking"
    assert ver2.action == "unverified" and ver2.final_answer is None


def test_faithful_arithmetic_stem_still_released():
    """题面 AST 三渠道互证一致时正常 accept（R10-C 起的证据形态）。"""
    sk = NumericCalculationSkill()
    q = _q("计算：7^2 - 4^2 = ?", [("A", "33"), ("B", "49"), ("C", "16"), ("D", "65")])
    sol = sk.solve(q, _ctx())
    ver = sk.verify(q, sol, _ctx())
    assert sol.status == "solved" and sol.answer == "A"
    chk = next(c for c in (ver.checks or []) if c.name == "numeric.expr_ast_replay")
    assert chk.passed is True
    assert ver.action == "accept"


def test_stem_reparse_exception_is_controlled_unverified():
    """R10-A6：独立复核执行异常必须落 unverified，不得静默跳过检查。"""
    from assessment_agent.schemas import Solution
    sk = NumericCalculationSkill()
    q = _q("计算：9//2 = ?", [("A", "4"), ("B", "4.5"), ("C", "5"), ("D", "9")])
    sol = Solution(status="solved", skill="numeric_calculation", answer="B",
                   method="sympy_exact",
                   meta={"expression": "9//2", "value": 4.5, "exact": "4.5",
                         "matched_label": "B"})
    ver = sk.verify(q, sol, _ctx())
    names = {c.name for c in (ver.checks or [])}
    assert "numeric.stem_direct_parse" in names
    chk = next(c for c in ver.checks if c.name == "numeric.stem_direct_parse")
    assert chk.passed is False and chk.severity == "blocking"
    assert ver.action == "unverified" and ver.final_answer is None


def test_natural_language_stem_not_applicable():
    """含中文实词的应用题不适用直读通道（不误伤模板路径）。"""
    sk = NumericCalculationSkill()
    q = _q("一件商品原价 100 元，先涨价 10% 再降价 10%，现价是多少元？",
           [("A", "99"), ("B", "100"), ("C", "101"), ("D", "98")])
    sol = sk.solve(q, _ctx())
    ver = sk.verify(q, sol, _ctx())
    names = {c.name for c in (ver.checks or [])}
    assert "numeric.stem_direct_parse" not in names
    assert sol.answer == "A" and ver.action == "accept"
