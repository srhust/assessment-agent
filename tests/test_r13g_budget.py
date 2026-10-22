# -*- coding: utf-8 -*-
"""R13-G 原子预算闸：发送前拒绝，重试不得穿透 cap（离线 mock transport）。"""
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
                       model="m", max_retries=5, timeout_s=2)


def _client(cap, status=429):
    sent = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["n"] += 1
        return httpx.Response(status, json={"error": "boom"})

    c = LLMClient(_settings(), transport=httpx.MockTransport(handler),
                  max_requests=cap)
    return c, sent


def test_retries_cannot_penetrate_cap():
    c, sent = _client(cap=3)
    with pytest.raises(LLMRequestBudgetExhausted):
        c.complete("hi") if hasattr(c, "complete") else c.chat(
            [{"role": "user", "content": "hi"}])
    assert sent["n"] == 3                 # 恰发 3 次，第 4 次被闸拒绝
    assert c._requests_sent == 3


def test_cap_exactly_allows_n_sends_success():
    c, sent = _client(cap=1, status=200)
    # 成功路径：响应 200 但 envelope 无 choices → 解析异常不关心，只验证发出=1
    try:
        c.chat([{"role": "user", "content": "hi"}])
    except Exception:  # noqa: BLE001
        pass
    assert sent["n"] == 1


def test_env_cap_fallback(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_LLM_MAX_REQUESTS", "2")
    c, sent = _client(cap=None)
    assert c.max_requests == 2
    with pytest.raises(LLMRequestBudgetExhausted):
        c.chat([{"role": "user", "content": "hi"}])
    assert sent["n"] == 2


def test_no_cap_unbounded_behaves_unchanged():
    c, sent = _client(cap=None)
    c.max_requests = None
    with pytest.raises(Exception):  # noqa: BLE001
        c.chat([{"role": "user", "content": "hi"}])
    assert sent["n"] == 6                 # max_retries=5 → 6 次

