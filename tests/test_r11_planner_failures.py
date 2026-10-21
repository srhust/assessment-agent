# -*- coding: utf-8 -*-
"""R11-F：planner 失败原因分类 + 声明性弃权（unknowns）。

关键要求：条件不足弃权必须走**模型成功返回**的路径（非超时诱导），
分类桶稳定可审计：not_sent / sent_no_response / parse_or_protocol_fail /
planned_but_exec_fail / declared_insufficient。全部零 API（Fake LLM）。"""
from __future__ import annotations

from assessment_agent.schemas import Question

from assessment_agent.skills.numeric_calculation import NumericCalculationSkill
from tests.test_numeric_planner_integration import (  # noqa: F401
    OPTIONS, STEM, FakeKB, FakeLLM, _ctx, _payload)


class RaisingLLM:
    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    def complete_json(self, system, user, *, image_path=None, model=None):
        self.calls += 1
        raise self.exc


def _skill():
    sk = NumericCalculationSkill()
    sk.method_kb = FakeKB()  # type: ignore[attr-defined]
    return sk


def test_declared_unknowns_abstain_with_successful_response():
    """模型正常返回完整合法 JSON，但 unknowns 指向目标 → 弃权不猜（非超时诱导）。"""
    pay = _payload(unknowns=["final 需要的再降价幅度题面未给出"])
    sol = _skill().solve(Question(stem=STEM, options=OPTIONS),
                         _ctx(FakeLLM(pay), word_problem=False))
    assert sol is not None and sol.status == "failed"
    assert sol.answer is None
    assert sol.meta.get("failure_bucket") == "declared_insufficient"
    assert "未给出" in str(sol.meta.get("declared_unknown"))


def test_timeout_buckets_as_sent_no_response():
    sol = _skill().solve(Question(stem=STEM, options=OPTIONS),
                         _ctx(RaisingLLM(TimeoutError("The read operation timed out")),
                              word_problem=False))
    assert sol is not None and sol.status == "failed"
    assert sol.meta.get("failure_bucket") == "sent_no_response"


def test_json_parse_failure_buckets_as_parse_or_protocol_fail():
    sol = _skill().solve(Question(stem=STEM, options=OPTIONS),
                         _ctx(RaisingLLM(ValueError("Expecting value: line 1 column 1")),
                              word_problem=False))
    assert sol is not None and sol.status == "failed"
    assert sol.meta.get("failure_bucket") == "parse_or_protocol_fail"


def test_harmless_unknowns_do_not_block_release():
    """卡面约定：干扰量可写 unknowns——不含缺失指向词且未指向目标时正常释放。"""
    pay = _payload(unknowns=["原价的历史成本价（干扰信息，不参与计算）"])
    sol = _skill().solve(Question(stem=STEM, options=OPTIONS),
                         _ctx(FakeLLM(pay), word_problem=False))
    assert sol is not None and sol.status == "solved", sol.error
    assert sol.answer == "B"
