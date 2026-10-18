"""Router / Verifier / Skill 注册表测试。"""

from __future__ import annotations

import pytest

from assessment_agent.config import Settings
from assessment_agent.router import Router, score_rules
from assessment_agent.schemas import Question, QuestionOption, Solution
from assessment_agent.skills import SkillRegistry
from assessment_agent.verifier import Verifier
from tests.conftest import FakeChatModel, make_settings


def _question(stem: str, options=None):
    return Question(stem=stem, options=[QuestionOption(label=label, text=text) for label, text in (options or [])])


# ---------------------------------------------------------------- Router
@pytest.mark.parametrize(
    "stem,expected",
    [
        ("计算 12 × 8 的结果", "numeric_calculation"),
        ("数列 2, 4, 8, ( ) 的下一项是？", "number_series"),
        ("甲乙丙三人参加比赛，以下哪项一定为真？", "logical_reasoning"),
        ("依次填入下列横线处最恰当的一项是（ ）", "verbal_understanding"),
        ("下列图形中，符合规律的下一个图形是（ ）", "figure_reasoning"),
        ("根据以下资料，2023 年的同比增长率是多少？", "data_analysis"),
        ("苹果和水果的关系，与下列哪项最为相似？", "language_reasoning"),
    ],
)
def test_router_rules_route_each_skill(stem, expected, settings):
    router = Router(settings, llm=None)
    decision = router.route(_question(stem, [("A", "选项一"), ("B", "选项二")]))
    assert decision.skill == expected
    assert decision.source == "rules"


def test_router_explicit_skill_wins(settings):
    router = Router(settings, llm=None)
    decision = router.route(_question("随便什么题干"), explicit_skill="personality")
    assert decision.skill == "personality"
    assert decision.source == "explicit"


def test_router_falls_back_when_no_signal_and_offline(settings):
    router = Router(settings, llm=None)
    decision = router.route(_question("请帮我看看这个"))
    assert decision.source == "fallback"
    assert decision.ambiguous is True


def test_router_uses_llm_when_rules_are_weak(tmp_path):
    settings = make_settings(
        tmp_path, {"LLM_API_KEY": "sk-test", "LLM_TEXT_MODEL": "m", "ASSESSMENT_OFFLINE": "false"}
    )
    llm = FakeChatModel(payload={"skill": "data_analysis", "confidence": 0.82, "reason": "含图表"})
    registry = SkillRegistry()
    router = Router(settings, llm=llm, registry=registry)
    decision = router.route(_question("请帮我看看这个"))
    assert decision.skill == "data_analysis"
    assert decision.source == "llm"
    assert decision.confidence == pytest.approx(0.82)


def test_router_rejects_unknown_llm_skill(tmp_path):
    settings = make_settings(tmp_path, {"LLM_API_KEY": "sk-test"})
    llm = FakeChatModel(payload={"skill": "not_a_skill", "confidence": 0.9})
    router = Router(settings, llm=llm, registry=SkillRegistry())
    decision = router.route(_question("请帮我看看这个"))
    assert decision.source == "fallback"


# ---------------------------------------------------------------- Verifier
def test_verifier_accepts_correct_numeric_solution(tmp_path):
    settings = make_settings(tmp_path)
    registry = SkillRegistry()
    verifier = Verifier(settings, registry=registry)
    question = _question("计算 (3.5 + 2.4) × 4 的结果是多少？", [("A", "23.6"), ("B", "22.6")])
    solution = Solution(
        status="solved",
        skill="numeric_calculation",
        answer="A",
        answer_text="23.6",
        confidence=0.95,
        method="sympy_exact",
        meta={"expression": "(3.5 + 2.4) * 4", "value": 23.6, "exact": "59/2"},
    )
    verification = verifier.verify(question, solution)
    assert verification.passed is True
    assert verification.action == "accept"
    assert verification.final_answer == "A"


def test_verifier_repairs_tampered_numeric_answer(tmp_path):
    settings = make_settings(tmp_path)
    verifier = Verifier(settings, registry=SkillRegistry())
    question = _question("计算 (3.5 + 2.4) × 4 的结果是多少？", [("A", "23.6"), ("B", "22.6")])
    solution = Solution(
        status="solved",
        skill="numeric_calculation",
        answer="B",
        confidence=0.95,
        method="sympy_exact",
        meta={"expression": "(3.5 + 2.4) * 4", "value": 22.6, "exact": "113/5"},
    )
    verification = verifier.verify(question, solution)
    assert verification.passed is False
    assert verification.action == "repair"
    assert verification.repaired_answer == "A"


def test_verifier_repairs_tampered_series_answer(tmp_path):
    settings = make_settings(tmp_path)
    verifier = Verifier(settings, registry=SkillRegistry())
    question = _question("数列 2, 5, 8, 11, ( ) 的下一项是？", [("A", "13"), ("B", "14")])
    solution = Solution(
        status="solved",
        skill="number_series",
        answer="A",
        confidence=0.9,
        method="series_pattern",
        meta={"sequence": ["2", "5", "8", "11"], "next_value": "13", "agreement": True},
    )
    verification = verifier.verify(question, solution)
    assert verification.passed is False
    assert verification.action == "repair"
    assert verification.repaired_answer == "B"


def test_verifier_accepts_correct_logic_solution(tmp_path):
    settings = make_settings(tmp_path)
    verifier = Verifier(settings, registry=SkillRegistry())
    question = _question(
        "甲乙丙三人参加比赛，名次各不相同。甲比乙高，乙比丙高。以下哪项一定为真？",
        [("A", "甲比丙矮"), ("B", "丙比甲矮")],
    )
    solution = Solution(
        status="solved",
        skill="logical_reasoning",
        answer="B",
        confidence=0.92,
        method="z3_constraints",
        meta={
            "entities": ["甲", "乙", "丙"],
            "intent": "entailment",
            "statements": [
                {"op": ">", "left": "甲", "right": "乙", "source": "甲比乙高"},
                {"op": ">", "left": "乙", "right": "丙", "source": "乙比丙高"},
            ],
            "entailed_options": ["B"],
        },
    )
    verification = verifier.verify(question, solution)
    assert verification.passed is True, verification.checks


def test_verifier_rejects_unsupported_solution(tmp_path):
    settings = make_settings(tmp_path)
    verifier = Verifier(settings, registry=SkillRegistry())
    solution = Solution(status="unsupported", skill="verbal_understanding", error="未实现")
    verification = verifier.verify(_question("依次填入最恰当的一项"), solution)
    assert verification.passed is False
    assert verification.action == "reject"
    assert any(check.name == "solution.status" and not check.passed for check in verification.checks)


# ---------------------------------------------------------------- Skills
def test_registry_has_eight_skills_and_five_supported():
    registry = SkillRegistry()
    assert len(registry.ids()) == 8
    supported = {skill.id for skill in registry.supported()}
    assert supported == {
        "numeric_calculation",
        "number_series",
        "logical_reasoning",
        "figure_reasoning",
        "data_analysis",  # R3a：资料分析从声明接口升级为已实现能力
    }


@pytest.mark.parametrize(
    "skill_id",
    ["verbal_understanding", "language_reasoning", "personality"],
)
def test_declared_skills_never_fake_answers(skill_id, settings, fake_llm):
    from assessment_agent.skills import SkillContext

    registry = SkillRegistry()
    ctx = SkillContext(settings=settings, llm=fake_llm)
    solution = registry.require(skill_id).solve(_question("任意题干", [("A", "甲"), ("B", "乙")]), ctx)
    assert solution.status == "unsupported"
    assert solution.answer is None
    assert solution.meta["planned_capabilities"]
    assert solution.meta["stage"] == 2


def test_data_analysis_honest_without_data_regions(settings):
    """R3a：data_analysis 已实现——但缺数据区域证据时必须如实失败，绝不凭空作答。"""
    from assessment_agent.skills import SkillContext

    registry = SkillRegistry()
    ctx = SkillContext(settings=settings)
    solution = registry.require("data_analysis").solve(
        _question("根据下表，2023 年销售额合计是多少万元？", [("A", "930"), ("B", "900")]), ctx)
    assert solution.status == "failed"
    assert solution.answer is None
    assert (solution.meta.get("failure") or {}).get("code") == "no_data_regions"


def test_numeric_skill_fails_honestly_on_unparseable_text(settings):
    from assessment_agent.skills import SkillContext

    registry = SkillRegistry()
    ctx = SkillContext(settings=settings)
    solution = registry.require("numeric_calculation").solve(_question("这段文字里没有算式"), ctx)
    assert solution.status == "failed"
    assert "解析" in (solution.error or "")


def test_verifier_skill_exception_blocks_acceptance(tmp_path):
    """回归测试：Skill 复核异常时应标记为 blocking 失败，而不是 warning 放行。"""
    from assessment_agent.skills.base import BaseSkill, SkillContext
    
    settings = make_settings(tmp_path)
    
    # 创建一个会抛出异常的 Skill
    class BrokenSkill(BaseSkill):
        id = "broken_skill"
        name_zh = "故障技能"
        stage = 1
        
        def solve(self, question, ctx):
            return Solution(status="solved", skill=self.id, answer="A", confidence=0.9)
        
        def verify(self, question, solution, ctx):
            raise RuntimeError("模拟复核异常")
    
    # 注册故障 Skill
    from assessment_agent.skills import SkillRegistry
    registry = SkillRegistry()
    registry._skills["broken_skill"] = BrokenSkill()
    
    verifier = Verifier(settings, registry=registry)
    question = _question("测试题目", [("A", "选项A")])
    solution = Solution(status="solved", skill="broken_skill", answer="A", confidence=0.9)
    
    verification = verifier.verify(question, solution)
    
    # 应该因为复核异常而失败
    assert verification.passed is False
    # 应该有 blocking 级别的失败检查
    blocking_failures = [c for c in verification.checks if c.severity == "blocking" and not c.passed]
    assert len(blocking_failures) > 0
    assert any("复核异常" in c.detail for c in blocking_failures)


# ---------------------------------------------------------------- R2a 回归（R1c 遗留边界）
def test_number_series_no_pattern_returns_structured_failure(tmp_path, monkeypatch):
    """P0 回归：没有匹配规律时不得 float(None) 抛 TypeError，必须返回结构化失败。"""
    from assessment_agent.skills import SkillContext
    from assessment_agent.tools.base import ToolInvocation

    settings = make_settings(tmp_path)
    ctx = SkillContext(settings=settings, llm=None)
    no_pattern_invocation = ToolInvocation(
        tool="number_series",
        args={"text": "数列 1, 3, 4, 9, ( )"},
        ok=True,
        result={
            "sequence": ["1", "3", "4", "9"],
            "candidates": [],
            "next_value": None,
            "next_value_float": None,
            "agreement": False,
            "note": "没有匹配到任何已知规律",
        },
    )
    monkeypatch.setattr(ctx.toolbox, "run", lambda *args, **kwargs: no_pattern_invocation)

    question = _question("数列 1, 3, 4, 9, ( ) 的下一项是？", [("A", "10"), ("B", "12")])
    solution = SkillRegistry().require("number_series").solve(question, ctx)

    assert solution.status == "failed"
    assert solution.answer is None
    assert "没有匹配到任何已知规律" in (solution.error or "")
    assert solution.meta.get("no_pattern") is True


def _fake_ambiguous_solve_series():
    """构造一个真实会产生**不同预测值**的歧义复算环境（候选 16 与 17）。"""
    from fractions import Fraction

    from assessment_agent.tools.series import SeriesCandidate, SeriesSolution

    def fake_solve(sequence):
        values = [Fraction(str(item)) for item in sequence]
        return SeriesSolution(
            values,
            [
                SeriesCandidate("等比", 10, Fraction(16), "公比为 2", {}),
                SeriesCandidate("2ⁿ±常数", 20, Fraction(17), "近似 2ⁿ+1", {}),
            ],
            next_value=Fraction(16),
            agreement=False,
            note="存在 2 种可能答案，取优先级最高者",
        )

    return fake_solve


def test_number_series_ambiguous_solver_verifier_full_chain(tmp_path, monkeypatch):
    """P1 回归：完整 Solver→Verifier 链路。

    歧义数列中 Solver 基于选项匹配选择**非最高优先级**候选（17），
    Verifier 必须按候选集合一致性接受它，而不是要求等于最高优先级结果（16）。
    """
    from assessment_agent.skills import SkillContext
    from assessment_agent.tools.base import ToolInvocation

    monkeypatch.setattr(
        "assessment_agent.skills.number_series.solve_series", _fake_ambiguous_solve_series()
    )
    settings = make_settings(tmp_path)
    ctx = SkillContext(settings=settings, llm=None)
    invocation = ToolInvocation(
        tool="number_series",
        args={"text": "数列 1, 2, 4, 8, ( )"},
        ok=True,
        result={
            "sequence": ["1", "2", "4", "8"],
            "candidates": [
                {"name": "等比", "priority": 10, "next_value": "16", "explanation": "公比为 2", "detail": {}},
                {"name": "2ⁿ±常数", "priority": 20, "next_value": "17", "explanation": "近似 2ⁿ+1", "detail": {}},
            ],
            "next_value": "16",
            "next_value_float": 16.0,
            "agreement": False,
            "note": "存在 2 种可能答案，取优先级最高者",
        },
    )
    monkeypatch.setattr(ctx.toolbox, "run", lambda *args, **kwargs: invocation)

    # 只有候选 17 出现在选项中 → Solver 选择 B
    question = _question("数列 1, 2, 4, 8, ( ) 的下一项是？", [("A", "15"), ("B", "17"), ("C", "18"), ("D", "19")])
    solution = SkillRegistry().require("number_series").solve(question, ctx)
    assert solution.status == "solved"
    assert solution.answer == "B"
    assert solution.meta.get("ambiguous") is True
    assert str(solution.meta.get("next_value")) == "17"

    verifier = Verifier(settings, registry=SkillRegistry())
    verification = verifier.verify(question, solution)
    recompute = next(check for check in verification.checks if check.name == "series.recompute")
    assert recompute.passed, recompute.detail  # 关键断言：17 ∈ 候选集合 → 不能按“≠最高优先级 16”拒绝
    assert verification.passed is True
    assert verification.action == "accept"
    assert verification.final_answer == "B"


def test_number_series_ambiguous_invalid_candidate_rejected(tmp_path, monkeypatch):
    """P1 回归：歧义数列中不在候选集合内的答案必须被 Verifier 拒绝。"""
    from assessment_agent.skills import SkillContext
    from assessment_agent.tools.base import ToolInvocation

    monkeypatch.setattr(
        "assessment_agent.skills.number_series.solve_series", _fake_ambiguous_solve_series()
    )
    settings = make_settings(tmp_path)
    ctx = SkillContext(settings=settings, llm=None)
    invocation = ToolInvocation(
        tool="number_series",
        args={"text": "数列 1, 2, 4, 8, ( )"},
        ok=True,
        result={
            "sequence": ["1", "2", "4", "8"],
            "candidates": [
                {"name": "等比", "priority": 10, "next_value": "16", "explanation": "公比为 2", "detail": {}},
                {"name": "2ⁿ±常数", "priority": 20, "next_value": "17", "explanation": "近似 2ⁿ+1", "detail": {}},
            ],
            "next_value": "16",
            "next_value_float": 16.0,
            "agreement": False,
            "note": "存在 2 种可能答案，取优先级最高者",
        },
    )
    monkeypatch.setattr(ctx.toolbox, "run", lambda *args, **kwargs: invocation)
    question = _question("数列 1, 2, 4, 8, ( ) 的下一项是？", [("A", "15"), ("B", "17"), ("C", "18"), ("D", "19")])
    solution = SkillRegistry().require("number_series").solve(question, ctx)

    # 人为把复算值改成不在候选集合内的 19（模拟 Solver 记录被篡改）
    tampered = solution.model_copy(deep=True)
    tampered.meta["next_value"] = "19"

    verifier = Verifier(settings, registry=SkillRegistry())
    verification = verifier.verify(question, tampered)
    assert verification.passed is False
    assert verification.action == "reject"
    assert verification.final_answer is None
    recompute = next(check for check in verification.checks if check.name == "series.recompute")
    assert not recompute.passed
