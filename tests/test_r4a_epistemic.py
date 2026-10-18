"""R4a：预报/传闻/假设/断言最小对比（Forecast(P) ⊬ P），全机制通用、不针对样本。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.config import Settings  # noqa: E402
from assessment_agent.schemas import Question, QuestionOption  # noqa: E402
from assessment_agent.skills.base import SkillContext  # noqa: E402
from assessment_agent.skills.verbal_understanding import VerbalUnderstandingSkill  # noqa: E402
from assessment_agent.tools.verbal_tool import (classify_inference_option,  # noqa: E402
                                                 epistemics, evidence_units)

P = "明天有暴雨"


def _ctx():
    return SkillContext(settings=Settings.from_env(
        env={"ASSESSMENT_OFFLINE": "1"}), llm=None)


# ---------------------------------------------------------------- 六状态

@pytest.mark.parametrize("text,want", [
    ("明日有暴雨，出行请注意", "asserted"),
    ("有报道称，明日有暴雨", "reported"),
    ("部分专家认为明日有暴雨", "believed"),
    ("如果明日有暴雨，演出将取消", "hypothetical"),
    ("气象预报显示，明日有暴雨", "predicted"),
    ("预计明日有暴雨", "predicted"),
    ("官方否认了明日有暴雨的说法", "denied_attribution"),
])
def test_epistemic_six_statuses(text, want):
    assert epistemics(text)["status"] == want


# ---------------------------------------------------------------- 最小对比

def _classify(option_text: str, stem: str):
    units = evidence_units(stem[:24])
    return classify_inference_option("X", option_text, units, stem[:24])


def test_minimal_contrast_same_P_predicate_swaps_behavior():
    """同一 P：断言外层支持裸断言；预报外层阻断裸断言、支持预报复述。"""
    stem_assert = "实测记录显示：明日将有暴雨。根据这段文字，以下哪项可以推出？"
    stem_forecast = "气象预报显示，明日有暴雨。根据这段文字，以下哪项可以推出？"
    a1 = _classify("明日将有暴雨", stem_assert)
    a2 = _classify("明日将有暴雨", stem_forecast)
    assert a1.relation == "supported"
    assert a2.relation == "overreach" and "裸断言" in (a2.cue_diffs[0] if a2.cue_diffs else a2.notes)
    b2 = _classify("气象预报显示明日有暴雨", stem_forecast)
    assert b2.relation == "supported"      # 归属层复述仍合法（P1 规则保留）
    # 假设外层：裸断言同样被阻断
    stem_hypo = "如果明日有暴雨，演出取消。根据这段文字，以下哪项可以推出？"
    assert _classify("明日有暴雨", stem_hypo).relation in {"overreach", "insufficient"}


def test_forecast_bare_assertion_end_to_end_not_accepted():
    sk = VerbalUnderstandingSkill()
    q = Question(stem="气象预报显示，明日有暴雨，户外演出因此可能取消。根据这段文字，以下哪项可以推出？",
                 options=[QuestionOption(label="A", text="明日有暴雨"),
                          QuestionOption(label="B", text="户外演出将取消")],
                 question_type="single_choice")
    sol = sk.solve(q, _ctx())
    assert sol.status == "failed"          # 两个裸断言都被阻断 → 无合法候选，诚实失败


def test_forecast_attribution_echo_accepted():
    sk = VerbalUnderstandingSkill()
    q = Question(stem="气象预报显示，明日有暴雨，户外演出因此可能取消。根据这段文字，以下哪项可以推出？",
                 options=[QuestionOption(label="A", text="明日有暴雨"),
                          QuestionOption(label="B", text="气象预报显示明日有暴雨")],
                 question_type="single_choice")
    sol = sk.solve(q, _ctx())
    assert sol.status == "solved" and sol.answer == "B"
    ver = sk.verify(q, sol, _ctx())
    assert ver.action == "accept" and ver.final_answer == "B"


def test_audit_layer_blocks_forecast_as_fact_independently():
    """LLM 通道伪造 supported 时，audit 的独立词表（含预报）必须拦截归属越权。"""
    from assessment_agent.tools.verbal_tool import audit
    stem = "气象预报显示，明日有暴雨，户外演出因此可能取消。根据这段文字，以下哪项可以推出？"
    opts = [("A", "明日有暴雨"), ("B", "气象预报显示，明日有暴雨")]
    spans = ["气象预报显示，明日有暴雨"]
    res = audit(stem, opts, "A", "inference", claim_relation="supported",
                span_texts=spans, epistemic=True)
    assert res["status"] != "confirmed"
    res2 = audit(stem, opts, "B", "inference", claim_relation="supported",
                 span_texts=spans, epistemic=True)
    assert res2["status"] == "confirmed"
