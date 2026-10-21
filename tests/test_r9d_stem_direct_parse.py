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
    """-2^4+2^4=0；若 Solver 抽取丢负号得 32，必须被阻断而非 accept。"""
    sk = NumericCalculationSkill()
    q = _q("计算：-2^4 + 2^4 = ?", [("A", "0"), ("B", "32"), ("C", "-32"), ("D", "16")])
    sol = sk.solve(q, _ctx())
    ver = sk.verify(q, sol, _ctx())
    names = {c.name for c in (ver.checks or [])}
    if sol.meta.get("expression") == "2**4 + 2**4":
        # 抽取失真确实存在的路径：必须 unverified
        assert "numeric.stem_direct_parse" in names
        assert ver.action != "accept"
        assert ver.final_answer is None
    else:
        # 抽取正确（=-16+16=0）：直读通道应给出通过的独立证据
        assert "numeric.stem_direct_parse" in names
        assert ver.final_answer == "A"


def test_faithful_arithmetic_stem_still_released():
    """题面直读与 Solver 一致时正常 accept，且新检查在列。"""
    sk = NumericCalculationSkill()
    q = _q("计算：7^2 - 4^2 = ?", [("A", "33"), ("B", "49"), ("C", "16"), ("D", "65")])
    sol = sk.solve(q, _ctx())
    ver = sk.verify(q, sol, _ctx())
    assert sol.status == "solved" and sol.answer == "A"
    chk = next(c for c in (ver.checks or []) if c.name == "numeric.stem_direct_parse")
    assert chk.passed is True
    assert ver.action == "accept"


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
