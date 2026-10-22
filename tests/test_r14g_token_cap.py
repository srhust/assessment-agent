# -*- coding: utf-8 -*-
"""R14-G 前置：token 硬顶闸——累计 total_tokens 达顶后下一次请求（含重试）
在**发出前**拒绝；已在途的响应如实计入，不允许"最后一次超发"。"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.config import LLMSettings  # noqa: E402
from assessment_agent.llm import (  # noqa: E402
    LLMRequestBudgetExhausted, OpenAICompatibleClient as LLMClient)


def _settings():
    return LLMSettings(api_key="sk-test", base_url="https://mock.local/v1",
                       model="m", max_retries=0, timeout_s=2)


def _ok_handler(tokens):
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            "usage": {"prompt_tokens": tokens - 100,
                      "completion_tokens": 100, "total_tokens": tokens}})
    return handler, state


def test_token_cap_refuses_next_request_before_send():
    h, st = _ok_handler(60000)
    c = LLMClient(_settings(), transport=httpx.MockTransport(h),
                  max_requests=6, max_tokens=100000)
    c.chat([{"role": "user", "content": "a"}])
    c.chat([{"role": "user", "content": "b"}])          # 120000 在途，如实计入
    assert c.usage_snapshot()["total_tokens"] == 120000
    assert st["n"] == 2
    with pytest.raises(LLMRequestBudgetExhausted):
        c.chat([{"role": "user", "content": "c"}])
    assert st["n"] == 2                                  # 第三次从未触网
    assert c._requests_sent == 2


def test_token_cap_none_keeps_legacy_behaviour():
    h, st = _ok_handler(999999)
    c = LLMClient(_settings(), transport=httpx.MockTransport(h), max_requests=3)
    for _ in range(3):
        c.chat([{"role": "user", "content": "a"}])
    with pytest.raises(LLMRequestBudgetExhausted):
        c.chat([{"role": "user", "content": "a"}])
    assert st["n"] == 3


def test_env_token_cap(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_LLM_MAX_TOKENS", "5000")
    h, st = _ok_handler(6000)
    c = LLMClient(_settings(), transport=httpx.MockTransport(h), max_requests=5)
    c.chat([{"role": "user", "content": "a"}])           # 6000 ≥ 5000 → 顶满
    with pytest.raises(LLMRequestBudgetExhausted):
        c.chat([{"role": "user", "content": "b"}])
    assert st["n"] == 1
