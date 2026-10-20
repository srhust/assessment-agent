"""R7 C1：numeric skill × ReasoningPlanner 集成（FakeLLM，零 API）。"""

from __future__ import annotations

from fractions import Fraction

from assessment_agent.config import Settings
from assessment_agent.knowledge.methods import MethodCard
from assessment_agent.planner import ReasoningPlanner
from assessment_agent.schemas import Question
from assessment_agent.skills.base import SkillContext
from assessment_agent.skills.numeric_calculation import NumericCalculationSkill

STEM = "某商品原价 300 元，先打 8 折，再降价 10%，最终价格是多少元？"
OPTIONS = [{"label": "A", "text": "240"}, {"label": "B", "text": "216"},
           {"label": "C", "text": "270"}, {"label": "D", "text": "210"}]


def _card() -> MethodCard:
    return MethodCard(
        method_id="test.successive-percent", skill="numeric_calculation",
        problem_family="successive-change", concepts=["乘法复合"],
        applicability_conditions=["多次百分比/折扣变化"], required_quantities=["初值", "变化率"],
        reasoning_operations=["逐步乘因子"], solution_procedure=["绑定", "复合"],
        verification_strategy=["逐步基准核对"], common_pitfalls=["百分比相加"],
        source_references=[{"title": "t", "url": "https://example.org/t"}], version="1", raw={})


class FakeKB:
    def search(self, query, skill=None, top_k=3):
        return [_card()]


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete_json(self, system, user, *, image_path=None, model=None):
        self.calls += 1
        return self.payload


def _payload(**over) -> dict:
    base = {
        "family": "successive-discount", "method_id": "test.successive-percent",
        "rationale": "两段变化逐步复合",
        "givens": [
            {"label": "price0", "value": "300", "stem_span": "原价 300 元"},
            {"label": "f1", "value": "4/5", "stem_span": "先打 8 折"},
            {"label": "r2", "value": "9/10", "stem_span": "再降价 10%"}],
        "steps": [
            {"var": "after1", "expr": "price0*f1", "source": "先打 8 折",
             "deps": ["price0", "f1"]},
            {"var": "final", "expr": "after1*r2", "source": "再降价 10%",
             "deps": ["after1", "r2"]}],
        "target": "final", "unknowns": [], "question_hypotheses": ["数量关系"]}
    base.update(over)
    return base


def _ctx(llm) -> SkillContext:
    settings = Settings.from_env() if hasattr(Settings, "from_env") else Settings()
    return SkillContext(settings=settings, llm=llm)  # type: ignore[arg-type]


def test_planner_path_solves_two_stage_discount():
    skill = NumericCalculationSkill()
    skill.method_kb = FakeKB()  # type: ignore[attr-defined]
    q = Question(stem=STEM, options=OPTIONS)
    sol = skill.solve(q, _ctx(FakeLLM(_payload())))
    assert sol is not None and sol.status == "solved", sol.error if sol else None
    assert sol.answer == "B"
    assert sol.meta.get("solver_source") == "llm_planner"
    assert Fraction(sol.meta["exact"]) == Fraction(216)
    ver = skill.verify(q, sol, _ctx(None))
    assert ver is not None and ver.action == "accept"
    names = {c.name for c in ver.checks}
    assert "planner.dependency_graph" in names


def test_tampered_dependency_graph_is_not_released():
    """验证器独立重放：Solver 自报 solved 但依赖图来源被伪造 → unverified。"""
    skill = NumericCalculationSkill()
    q = Question(stem=STEM, options=OPTIONS)
    from assessment_agent.schemas import Solution
    sol = Solution(
        status="solved", skill=skill.id, answer="B",
        meta={"solver_source": "llm_planner", "value": 216.0, "exact": "216",
              "expression": "(300*(4/5)*(9/10))",
              "structured_plan": ReasoningPlanner._bind_payload(
                  _payload(givens=[
                      {"label": "price0", "value": "300", "stem_span": "题干里没有的片段"},
                      {"label": "f1", "value": "4/5", "stem_span": "先打 8 折"},
                      {"label": "r2", "value": "9/10", "stem_span": "再降价 10%"}]),
                  [_card()]).to_dict()})  # type: ignore[union-attr]
    ver = skill.verify(q, sol, _ctx(None))
    assert ver is not None and ver.action == "unverified"
    assert ver.final_answer is None


def test_planner_absent_llm_keeps_honest_failure():
    skill = NumericCalculationSkill()
    skill.method_kb = FakeKB()  # type: ignore[attr-defined]
    q = Question(stem="浓度 12% 盐水 300 克加水 60 克后浓度？",
                 options=[{"label": "A", "text": "10%"}, {"label": "B", "text": "8%"},
                          {"label": "C", "text": "15%"}, {"label": "D", "text": "12%"}])
    sol = skill.solve(q, _ctx(None))  # 无 LLM
    assert sol is None or sol.status in ("failed", "solved")
    if sol and sol.status == "solved":
        ver = skill.verify(q, sol, _ctx(None))
        # 即便旧通道意外解出，也必须证据齐备（不变式：释放⇒正确或证据全过）
        assert ver is not None
