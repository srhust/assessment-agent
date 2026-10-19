"""R4d 收尾：HTTP 错误诊断脱敏（评审建议——不落响应正文原文，只落状态码/错误类型）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from assessment_agent.config import LLMSettings  # noqa: E402
from assessment_agent.llm import LLMError, OpenAICompatibleClient, redact_secrets  # noqa: E402

# 测试密钥运行时拼装，避免仓库源码出现可被安全扫描命中的字面量
SECRET = "sk-" + "SUP3Rsecre" + "tDOnot" + "store0987"


def _client(handler, **kw):
    settings = LLMSettings(api_key="sk-client-key-should-never-appear",
                           base_url="http://x/v1", vision_model="probe-v",
                           max_retries=kw.get("max_retries", 0),
                           **kw.get("extra", {}))
    return OpenAICompatibleClient(settings, transport=httpx.MockTransport(handler))


def test_redact_masks_key_shapes():
    assert SECRET not in redact_secrets(f"bad key {SECRET} provided")
    assert SECRET not in redact_secrets(f"Authorization: Bearer {SECRET}")
    assert SECRET not in redact_secrets(f'{{"api_key": "{SECRET}"}}')
    # 正常诊断文本不被误伤
    assert redact_secrets("model_not_found") == "model_not_found"


def test_http_error_summary_has_type_code_but_not_raw_body():
    """4xx 异常字符串：状态码 + error type/code 保留；正文原文与密钥不落。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={
            "error": {"type": "authentication_error", "code": "invalid_api_key",
                      "message": f"Incorrect API key provided: {SECRET}"},
            "request_id": "req-42"})

    client = _client(handler)
    with pytest.raises(LLMError) as ei:
        client.chat([{"role": "user", "content": "hi"}], model="m")
    msg = str(ei.value)
    assert SECRET not in msg
    assert "authentication_error" in msg and "invalid_api_key" in msg
    assert "401" in msg and "req-42" in msg
    # 审计本来就不含正文；异常字符串是唯一错误出口，同样必须干净
    assert SECRET not in json.dumps(client.audit_snapshot())


def test_nonjson_error_body_is_redacted_snippet():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, text=f'upstream gateway refused key={SECRET} <html>trace…</html>')

    client = _client(handler)
    with pytest.raises(LLMError) as ei:
        client.chat([{"role": "user", "content": "hi"}], model="m", json_mode=True)
    msg = str(ei.value)
    assert SECRET not in msg
    assert "400" in msg


def test_5xx_retry_final_error_sanitized():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": "EngineOverloaded",
                                                   "message": f"busy {SECRET}"}})

    client = _client(handler, max_retries=1)
    with pytest.raises(LLMError) as ei:
        client.chat([{"role": "user", "content": "hi"}], model="m")
    assert SECRET not in str(ei.value)
    assert client.audit_snapshot()["error_types"].get("server_5xx") == 2


def test_json_mode_fallback_only_on_explicit_format_rejection():
    """脱敏摘要仍保留 response_format/json_object 判据（降级不破），
    普通 400 不得再猜测性去掉 json 模式重试（旧逻辑会把鉴权错误误当格式问题）。"""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(400, json={
            "error": {"code": "invalid_parameter",
                      "message": "Unknown parameter: response_format"}})

    client = _client(handler)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}], model="m")
    assert len(seen) == 2 and "response_format" not in seen[1]

    seen.clear()

    def handler2(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"code": "rate_limit", "message": "no"}})

    client2 = _client(handler2)
    with pytest.raises(LLMError):
        client2.chat([{"role": "user", "content": "hi"}], model="m")
    assert len(seen) == 1 and "response_format" in seen[0]  # 无格式证据不猜


def test_runner_failure_reason_has_no_secret(tmp_path):
    """端到端：注入 transport 的错误正文密钥不得流入 predictions 的
    failure_reason（诊断链路 = 异常字符串 → 报告）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={
            "error": {"type": "authentication_error", "code": "invalid_api_key",
                      "message": f"key {SECRET} rejected"}})

    from assessment_agent.config import Settings
    from assessment_agent.evaluation.runner import EvaluationRunner
    from assessment_agent.pipeline import Pipeline

    llm = _client(handler)
    st = Settings(llm=LLMSettings(api_key="sk-x", base_url="http://x/v1",
                                  vision_model="probe-v", max_retries=0),
                  offline=False)
    p = Pipeline(st)
    p.llm = llm
    runner = EvaluationRunner(p, data_dir=ROOT / "data", results_dir=tmp_path)
    summary = runner.run("figshots_dev", eval_mode="image_only",
                         run_name="sec", save=True, limit=1,
                         eval_batch="sec-batch")
    blob = (Path(summary.output_dir) / "predictions.jsonl").read_text(encoding="utf-8")
    assert SECRET not in blob
    row = json.loads(blob.splitlines()[0])
    assert row["failure_class"] == "model_api_error"
    assert "authentication_error" in row["failure_reason"]  # 诊断信息仍可用
    assert json.loads((Path(summary.output_dir) / "llm_evidence.json")
                      .read_text(encoding="utf-8"))["audit"]["mock_or_injected_transport"] is True