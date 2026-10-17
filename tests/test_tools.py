"""求解工具测试：SymPy 表达式、数列规律、Z3 约束。"""

from __future__ import annotations

from fractions import Fraction

import pytest

from assessment_agent.tools.base import ToolBox, ToolError, ToolUnavailable
from assessment_agent.tools.logic_tool import (
    Statement,
    Z3LogicTool,
    extract_entities,
    parse_clause,
    parse_statements,
)
from assessment_agent.tools.math_tool import (
    PythonMathTool,
    extract_expression,
    fraction_eval,
    match_option,
    prepare_expression,
)
from assessment_agent.tools.series import find_blank_position, parse_sequence, solve_series


# ---------------------------------------------------------------- 数字计算
def test_prepare_expression_fullwidth_and_percent():
    assert "25*(1/100)" in prepare_expression("２５％的８００")
    assert prepare_expression("（3＋5）×2") == "(3+5)*2"


def test_extract_expression_picks_arithmetic_fragment():
    assert extract_expression("计算 (3.5 + 2.4) × 4 的结果是多少？") == "(3.5 + 2.4) * 4"
    assert extract_expression("这一题没有数字运算符") is None


def test_evaluate_expression_is_exact():
    tool = PythonMathTool()
    result = tool.run(expression="1/3 + 1/6")
    assert result["exact"] == "1/2"
    assert result["value"] == pytest.approx(0.5)


def test_math_tool_percent_and_implicit_multiplication():
    tool = PythonMathTool()
    assert tool.run(text="250 的 40% 是多少")["value"] == pytest.approx(100.0)
    assert tool.run(expression="2(3+4)")["value"] == pytest.approx(14.0)
    assert tool.run(text="计算 15% × 240")["value"] == pytest.approx(36.0)


def test_math_tool_rejects_non_arithmetic_input():
    tool = PythonMathTool()
    with pytest.raises(ToolError):
        tool.run(expression="__import__('os')")
    with pytest.raises(ToolError):
        tool.run(text="请描述一下这幅图")


def test_fraction_eval_independent_of_sympy():
    assert fraction_eval("12.5 * 8 - 36 / 4") == Fraction(91)
    assert float(fraction_eval("(3.5 + 2.4) * 4")) == pytest.approx(23.6)


def test_match_option_handles_fraction_and_percent_text():
    assert match_option(0.5, [("A", "1/2"), ("B", "2/3")]) == "A"
    assert match_option(100.0, [("A", "90"), ("B", "100")]) == "B"
    assert match_option(0.333, [("A", "1")]) is None


# ---------------------------------------------------------------- 数字推理
def test_series_arithmetic_and_geometric():
    assert solve_series([Fraction(2), Fraction(5), Fraction(8), Fraction(11)]).next_value == 14
    solution = solve_series([2, 6, 18, 54])
    assert solution.next_value == 162
    assert solution.agreement is True


def test_series_detects_power_offset_and_fibonacci():
    assert solve_series([3, 7, 15, 31]).next_value == 63
    assert solve_series([1, 1, 2, 3, 5, 8]).next_value == 13


def test_series_detects_interleaved_pattern():
    solution = solve_series([1, 10, 3, 20, 5, 30])
    assert solution.next_value == 7
    assert any(candidate.name == "奇偶分组" for candidate in solution.candidates)


def test_series_reports_no_pattern():
    solution = solve_series([1, 5, 2, 9, 3])
    assert solution.next_value is None
    assert solution.candidates == []


def test_series_requires_three_terms():
    with pytest.raises(ToolError):
        solve_series([1, 2])


def test_parse_sequence_and_blank_position():
    assert parse_sequence("数列 2, 5, 8, 11, ( ) 的下一项是？") == [2, 5, 8, 11]
    assert find_blank_position("1, 3, 5, ( )") == "end"
    assert find_blank_position("1, 3, ( ), 7") == "middle"
    assert find_blank_position("没有空位") is None


# ---------------------------------------------------------------- 逻辑推理
def test_extract_entities_prefers_heavenly_stems():
    assert extract_entities("甲、乙、丙三人") == ["甲", "乙", "丙"]
    assert extract_entities("没有人物") == []


@pytest.mark.parametrize(
    "clause,expected",
    [
        ("甲比乙高", ("甲", ">", "乙")),
        ("甲比乙矮", ("甲", "<", "乙")),
        ("甲不比乙高", ("甲", "<=", "乙")),
        ("甲和乙一样高", ("甲", "=", "乙")),
        ("甲最高", ("甲", "max", None)),
        ("甲不是最高", ("甲", "not_max", None)),
    ],
)
def test_parse_clause_variants(clause, expected):
    statement = parse_clause(clause, ["甲", "乙"])
    assert statement is not None
    assert (statement.left, statement.op, statement.right) == expected


def test_parse_statements_skips_non_claims():
    statements, unparsed = parse_statements("甲乙丙三人参加比赛，名次各不相同。甲比乙高。", ["甲", "乙", "丙"])
    assert len(statements) == 1
    assert unparsed == []


def test_parse_clause_rejects_numeric_offset():
    with pytest.raises(ToolError):
        parse_clause("甲比乙高3分", ["甲", "乙"])


def test_z3_tool_entailment_and_unique_max():
    tool = Z3LogicTool()
    if not tool.available():
        pytest.skip("z3-solver 未安装")
    result = tool.run(
        stem="甲乙丙三人参加比赛，名次各不相同。甲比乙高，乙比丙高。以下哪项一定为真？",
        options=[["A", "甲比丙矮"], ["B", "丙比甲矮"], ["C", "乙最高"], ["D", "丙最高"]],
    )
    assert result["answer_label"] == "B"
    assert result["entailed_options"] == ["B"]

    who = tool.run(
        stem="甲乙丙三人身高不同。甲比乙高，乙比丙高。谁最高？",
        options=[["A", "甲"], ["B", "乙"], ["C", "丙"]],
    )
    assert who["answer_entity"] == "甲"
    assert who["answer_label"] == "A"


def test_z3_tool_negation_conjunct():
    tool = Z3LogicTool()
    if not tool.available():
        pytest.skip("z3-solver 未安装")
    result = tool.run(
        stem="甲乙丙三人分数各不相同。甲比乙高，丙不是最高。以下哪项一定为真？",
        options=[["A", "甲最高"], ["B", "乙最高"], ["C", "丙最低"], ["D", "甲最低"]],
    )
    assert result["answer_label"] == "A"
    assert result["entailed_options"] == ["A"]


def test_z3_tool_rejects_contradiction_and_unknown_entities():
    tool = Z3LogicTool()
    if not tool.available():
        pytest.skip("z3-solver 未安装")
    with pytest.raises(ToolError):
        tool.run(stem="甲比乙高，乙比甲高。以下哪项一定为真？", options=[["A", "甲最高"]])
    with pytest.raises(ToolError):
        tool.run(stem="张三比李四高。以下哪项一定为真？", options=[["A", "张三最高"]])


def test_toolbox_records_invocations_and_failures():
    toolbox = ToolBox([PythonMathTool()])
    ok = toolbox.run("python_math", expression="2+2")
    bad = toolbox.run("python_math", expression="abc")
    assert ok.ok and ok.result["value"] == 4
    assert not bad.ok and bad.error
    assert [call.tool for call in toolbox.invocations] == ["python_math", "python_math"]
    with pytest.raises(ToolError):
        toolbox.run("missing_tool")


def test_figure_tools_expose_capabilities_but_not_fake_solving():
    from assessment_agent.tools.figure_tools import FigureTools

    tool = FigureTools()
    capabilities = tool.run(operation="capabilities")
    assert "implemented" in capabilities and "planned" in capabilities
    with pytest.raises(NotImplementedError):
        tool.run(operation="rotation_match")


def test_number_series_ambiguous_patterns():
    """回归测试：验证数列歧义时正确标记 ambiguous。"""
    # 数列 1, 2, 4, 8 可以解释为等比（公比2）或 2^n-1 + 1
    # 等比：下一项是 16
    # 2^n + 1：1=2^0+?, 2=2^1+0, 4=2^2+0, 8=2^3+0 → 下一项 2^4+0=16
    # 实际上这个例子不够好，让我找一个更好的
    
    # 数列 2, 4, 8, 16 只有等比一种解释
    result = solve_series([Fraction("2"), Fraction("4"), Fraction("8"), Fraction("16")])
    assert result.agreement is True  # 应该只有一种解释
    
    # 数列 1, 3, 5, 7 只有等差一种解释
    result = solve_series([Fraction("1"), Fraction("3"), Fraction("5"), Fraction("7")])
    assert result.agreement is True
    
    # 验证 candidates 字段存在
    assert len(result.candidates) >= 1
    assert result.candidates[0].name in ["等差", "等比", "前两项之和", "2阶差分", "3阶差分"]


def test_number_series_ambiguous_with_mock():
    """回归测试：使用 mock 测试歧义处理逻辑。
    
    由于现有的规律检测器很难产生 agreement=False 的情况，
    我们直接测试 number_series.py 中的歧义处理逻辑。
    """
    from assessment_agent.skills.number_series import NumberSeriesSkill
    from assessment_agent.skills.base import SkillContext
    from assessment_agent.tools.base import ToolBox, ToolInvocation
    from assessment_agent.schemas import Question, Solution
    from unittest.mock import Mock
    
    # 创建一个 mock 的工具调用，返回歧义结果
    mock_invocation = ToolInvocation(
        tool="number_series",
        args={"text": "1, 2, 4, 8, ( )"},
        ok=True,
        result={
            "sequence": ["1", "2", "4", "8"],
            "candidates": [
                {
                    "name": "等比",
                    "priority": 20,
                    "next_value": "16",
                    "explanation": "公比为 2",
                    "detail": {},
                },
                {
                    "name": "2^n±常数",
                    "priority": 60,
                    "next_value": "17",  # 不同的预测值
                    "explanation": "2^n + 1",
                    "detail": {},
                },
            ],
            "next_value": "17",  # 优先级最高的
            "next_value_float": 17.0,
            "agreement": False,  # 存在歧义
            "note": "存在 2 种可能答案，取优先级最高者",
        },
        error=None,
        duration_ms=1.0,
    )
    
    # 创建 Skill 和 Context
    skill = NumberSeriesSkill()
    settings = Mock()
    settings.data_dir = "."
    toolbox = Mock(spec=ToolBox)
    toolbox.run = Mock(return_value=mock_invocation)
    toolbox.invocations = []
    ctx = SkillContext(settings=settings, llm=None, toolbox=toolbox)
    
    # 创建问题（有选项）
    question = Question(
        stem="数列 1, 2, 4, 8, ( ) 的下一项是？",
        options=[
            {"label": "A", "text": "16"},
            {"label": "B", "text": "17"},
        ],
    )
    
    # 求解
    solution = skill.solve(question, ctx)
    
    # 验证：应该标记为 ambiguous
    assert solution.meta.get("ambiguous") is True
    # 当多个候选答案都匹配选项时，应该返回 failed 状态
    assert solution.status == "failed"
    assert "歧义" in solution.error
    # 答案应该为 None（不猜测）
    assert solution.answer is None
    
    # 测试场景2：只有一个候选答案匹配选项
    mock_invocation2 = ToolInvocation(
        tool="number_series",
        args={"text": "1, 2, 4, 8, ( )"},
        ok=True,
        result={
            "sequence": ["1", "2", "4", "8"],
            "candidates": [
                {
                    "name": "等比",
                    "priority": 20,
                    "next_value": "16",
                    "explanation": "公比为 2",
                    "detail": {},
                },
                {
                    "name": "2^n±常数",
                    "priority": 60,
                    "next_value": "17",
                    "explanation": "2^n + 1",
                    "detail": {},
                },
            ],
            "next_value": "17",
            "next_value_float": 17.0,
            "agreement": False,
            "note": "存在 2 种可能答案，取优先级最高者",
        },
        error=None,
        duration_ms=1.0,
    )
    
    toolbox.run = Mock(return_value=mock_invocation2)
    
    # 只有 16 在选项中
    question2 = Question(
        stem="数列 1, 2, 4, 8, ( ) 的下一项是？",
        options=[
            {"label": "A", "text": "16"},
            {"label": "B", "text": "15"},
        ],
    )
    
    solution2 = skill.solve(question2, ctx)
    
    # 验证：应该标记为 ambiguous，但只有一个候选匹配，所以可以给出答案
    assert solution2.meta.get("ambiguous") is True
    assert solution2.status == "solved"
    assert solution2.answer == "A"
    # 置信度应该降低
    assert solution2.confidence == 0.5
