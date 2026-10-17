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
def test_registry_has_eight_skills_and_four_mvp():
    registry = SkillRegistry()
    assert len(registry.ids()) == 8
    supported = {skill.id for skill in registry.supported()}
    assert supported == {
        "numeric_calculation",
        "number_series",
        "logical_reasoning",
        "figure_reasoning",
    }


@pytest.mark.parametrize(
    "skill_id",
    ["verbal_understanding", "language_reasoning", "data_analysis", "personality"],
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


def test_numeric_skill_fails_honestly_on_unparseable_text(settings):
    from assessment_agent.skills import SkillContext

    registry = SkillRegistry()
    ctx = SkillContext(settings=settings)
    solution = registry.require("numeric_calculation").solve(_question("这段文字里没有算式"), ctx)
    assert solution.status == "failed"
    assert "解析" in (solution.error or "")
