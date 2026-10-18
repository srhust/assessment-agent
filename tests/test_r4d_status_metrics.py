"""R4d：统一执行状态（P0-1）、最终答案口径（P0-2）、运行隔离与分阶段熔断（P1）。

评审反例必须逐条封死：
- HTTP 全部成功但 JSON/schema 失败 ≠ EXECUTED；
- 1/15 视觉成功 → PARTIAL_EXECUTED；
- Solver 提议 A + unverified + 无最终答案 → 不得记作已作答；
- 旧口径 5/12、新口径 5/14 → 主指标 value 必须与新分母一致。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from assessment_agent.evaluation.execution_status import (  # noqa: E402
    classify_status, health_gate, vision_stage_summary,
)


def _rows(n_ok: int, n_total: int) -> list:
    return [{"vision": {"vision_http_success": i < n_ok,
                        "vision_schema_valid": i < n_ok,
                        "vision_extraction_valid": i < n_ok}} for i in range(n_total)]


AUDIT_ALL_HTTP = {"requests_sent": 15, "requests_succeeded": 15,
                  "mock_or_injected_transport": False, "echoed_models": ["m"],
                  "by_phase": {"vision_parse": {"http_succeeded": 15,
                                                "json_output_valid": 15}}}


def test_smoke_and_experiment_share_one_status_function():
    """同源锁定：两个脚本都必须从包内导入，不许再各自维护第二套判定。"""
    smoke = (ROOT / "scripts/phase6_vlm_smoke.py").read_text(encoding="utf-8")
    exp = (ROOT / "scripts/run_real_vlm_experiment.py").read_text(encoding="utf-8")
    for src in (smoke, exp):
        assert "from assessment_agent.evaluation.execution_status import" in src
        assert "def classify_status" not in src  # 本地第二实现禁止存在
    assert "from phase6_vlm_smoke import classify_status" not in exp


def test_all_http_success_but_schema_invalid_is_not_executed():
    """评审原话：API 请求成功但 JSON 结构错误，不能算视觉解析成功。"""
    audit = {"requests_sent": 15, "requests_succeeded": 15,
             "mock_or_injected_transport": False, "echoed_models": ["m"],
             "by_phase": {"vision_parse": {"http_succeeded": 15,
                                           "json_output_valid": 0}}}
    rows = _rows(0, 15)
    st, reason = classify_status(True, False, audit, rows, 15)
    assert st == "PARTIAL_EXECUTED"
    assert "API 成功不等于视觉解析成功" in reason


def test_one_of_fifteen_vision_success_is_partial_not_full():
    st, reason = classify_status(True, False, AUDIT_ALL_HTTP, _rows(1, 15), 15)
    assert st == "PARTIAL_EXECUTED"
    assert "1" in reason


def test_executed_requires_every_case_structured():
    st, _ = classify_status(True, False, AUDIT_ALL_HTTP, _rows(15, 15), 15)
    assert st == "EXECUTED"
    # 14/15 结构化 → 未满额，不得 EXECUTED
    st2, _ = classify_status(True, False, AUDIT_ALL_HTTP, _rows(14, 15), 15)
    assert st2 == "PARTIAL_EXECUTED"


def test_health_gate_semantics():
    rows_ok = _rows(3, 3)
    audit = AUDIT_ALL_HTTP
    summary = vision_stage_summary(rows_ok, audit)
    st, _ = classify_status(True, False, audit, rows_ok, 3)
    ok, msg = health_gate(st, summary)
    assert ok and "允许扩大" in msg
    # 全 HTTP 成功、全 schema 失败 → 熔断
    audit_bad = {"requests_sent": 3, "requests_succeeded": 3,
                 "mock_or_injected_transport": False,
                 "by_phase": {"vision_parse": {"http_succeeded": 3,
                                               "json_output_valid": 0}}}
    st_bad, _ = classify_status(True, False, audit_bad, _rows(0, 3), 3)
    ok2, msg2 = health_gate(st_bad, vision_stage_summary(_rows(0, 3), audit_bad))
    assert not ok2 and "熔断" in msg2
    # NOT_CONFIGURED → 熔断（下一阶段不得称真实验证）
    ok3, _ = health_gate("NOT_CONFIGURED", vision_stage_summary(_rows(0, 3), None))
    assert not ok3


def test_stage_summary_numerator_denominator_and_nested_fields():
    rows = [{"vision": {"vision_http_success": True, "vision_schema_valid": True,
                        "vision_extraction_valid": True, "evidence_complete": False}},
            {"vision": {"vision_http_success": True, "vision_schema_valid": False,
                        "vision_extraction_valid": False}},
            {"vision": {}}]
    s = vision_stage_summary(rows, AUDIT_ALL_HTTP)
    assert s["vision_http_success"] == {"numerator": 2, "denominator": 3}
    assert s["vision_schema_valid"] == {"numerator": 1, "denominator": 3}
    assert s["vision_extraction_valid"] == {"numerator": 1, "denominator": 3}
    assert s["evidence_complete"]["numerator"] == 0  # schema 合法但证据不完整不算成功


def test_unverified_solver_proposal_not_counted_as_answered():
    """反例(a)：Solver 提议 A + Verifier unverified + 无最终答案 → 已作答=False。"""
    from run_real_vlm_experiment import attribute_case, stratified
    row = {"case_id": "c1", "skill": "figure_reasoning", "expected": "A",
           "predicted_option": "A", "predicted": None, "correct": False,
           "verification_action": "unverified", "failure_class": None,
           "input_mode": "oracle_solver", "routed_skill": None, "usage": {},
           "latency_ms": 1.0,
           "vision": {}}
    att = attribute_case(row)
    assert att["solver_proposed_answer"] == "A"
    assert att["final_answer"] is None
    assert att["answered_out_loud"] is False
    run = {"predictions": [row], "metrics": {
        "overall_answer_accuracy": {"value": 0.0, "hit": 0, "denominator": 1}},
        "audit": None}
    s = stratified(run, "oracle_solver")
    assert s["answered_out_loud"]["numerator"] == 0
    assert s["verified_answer_yield"]["numerator"] == 0


def test_objective_metric_uses_new_denominator_exclusively():
    """反例(b)：legacy 5/12 与新 5/14 并存时，value/分子/分母必须同取新口径。"""
    from run_real_vlm_experiment import stratified
    preds = [{"case_id": f"c{i}", "skill": "s", "correct": i < 5,
              "predicted": "A" if i < 5 else None,
              "verification_action": "accept" if i < 5 else "reject",
              "predicted_option": "A", "expected": "A", "usage": {},
              "latency_ms": 1.0, "failure_class": None,
              "input_mode": "oracle_solver", "vision": {}} for i in range(14)]
    run = {"predictions": preds, "audit": None, "metrics": {
        "answer_accuracy": round(5 / 12, 4),          # 旧口径 value
        "overall_answer_accuracy": {"value": round(5 / 14, 4),
                                     "hit": 5, "denominator": 14},
    }}
    s = stratified(run, "oracle_solver")
    e2e = s["end_to_end_objective_accuracy"]
    assert (e2e["numerator"], e2e["denominator"]) == (5, 14)
    assert e2e["value"] == round(5 / 14, 4) != round(5 / 12, 4)
    assert e2e["caliber"].startswith("overall_answer_accuracy")
    assert s["legacy_answer_accuracy_for_reference_only"] == round(5 / 12, 4)
    # 分母一致性：value == hit/denominator（不混用两套分子分母）
    assert abs(e2e["value"] - e2e["numerator"] / e2e["denominator"]) < 5e-4
    vy = s["verified_answer_yield"]
    assert (vy["numerator"], vy["denominator"]) == (5, 14)


def test_client_audit_by_phase_and_schema_counters():
    """分阶段审计：2xx 计入 http_succeeded；JSON 解析失败单独计入
    json_output_invalid——HTTP 成功 ≠ schema 成功。"""
    import httpx
    from assessment_agent.config import LLMSettings
    from assessment_agent.llm import OpenAICompatibleClient
    from assessment_agent.prompts import VISION_PARSE_SYSTEM, ROUTER_SYSTEM

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "vision-m", "id": "req-1",
            "choices": [{"message": {"content": "这不是一个 JSON 对象"}}]})

    settings = LLMSettings(api_key="sk-t", base_url="http://x/v1",
                           vision_model="vision-m", max_retries=0)
    client = OpenAICompatibleClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(Exception):
        client.complete_json(VISION_PARSE_SYSTEM, "看图", model="vision-m")
    with pytest.raises(Exception):
        client.complete_json(ROUTER_SYSTEM, "路由", model="t")  # 同样不是 JSON
    audit = client.audit_snapshot()
    bp = audit["by_phase"]
    assert bp["vision_parse"]["http_succeeded"] == 1   # vision 阶段 1 次 2xx
    assert bp["router"]["http_succeeded"] == 1         # router 阶段 1 次 2xx
    assert bp["vision_parse"]["json_output_invalid"] == 1
    assert bp["router"]["json_output_invalid"] == 1    # 阶段互不混计
    assert audit["requests_succeeded"] == 2            # 总数不是视觉成功数
    assert bp["vision_parse"]["http_succeeded"] != audit["requests_succeeded"]
    assert "sk-t" not in json.dumps(audit)


def test_no_overwrite_guard_and_cli_flag(tmp_path):
    """多次运行不得静默覆盖历史凭证；--no-overwrite 存在。"""
    from assessment_agent.config import Settings
    from assessment_agent.evaluation.runner import EvaluationRunner
    from assessment_agent.pipeline import Pipeline

    runner = EvaluationRunner(Pipeline(Settings.from_env({})),
                              data_dir=ROOT / "data", results_dir=tmp_path)
    runner.run("figshots_dev", eval_mode="oracle_solver", run_name="dup",
               save=True, limit=1, no_overwrite=True)
    with pytest.raises(FileExistsError):
        runner.run("figshots_dev", eval_mode="oracle_solver", run_name="dup",
                   save=True, limit=1, no_overwrite=True)
    # 不带保护时维持旧行为（不破坏既有回归）
    runner.run("figshots_dev", eval_mode="oracle_solver", run_name="dup",
               save=True, limit=1)
    cli_src = (ROOT / "src/assessment_agent/cli.py").read_text(encoding="utf-8")
    assert "--no-overwrite" in cli_src


def test_experiment_isolation_source_locks():
    exp = (ROOT / "scripts/run_real_vlm_experiment.py").read_text(encoding="utf-8")
    # experiment_id 贯穿每个子运行的 run_name 与 eval_batch（目录/runs 互不覆盖）
    import re
    assert re.search(r'run_eval\(\s*FIGSPLITS,\s*mode,\s*f"\{exp_id\}_smoke_\{mode\}"', exp)
    assert re.search(r'run_eval\(\s*FIGSPLITS,\s*mode,\s*f"\{exp_id\}_full_\{mode\}"', exp)
    assert re.search(r'run_eval\(\s*"bench_dev",\s*"image_only",\s*f"\{exp_id\}_cross_bench"', exp)
    assert exp.count('eval_batch=f"{exp_id}') >= 3
    # 覆盖保护 + 唯一目录
    assert "拒绝覆盖" in exp and "FileExistsError" in exp
    # 别名透传（父/子同一配置事实源）
    assert "OPENAI_API_KEY" in exp.split("def _keep_env")[1].split("def ")[0]
    assert "OPENAI_BASE_URL" in exp.split("def _keep_env")[1].split("def ")[0]
    # 分阶段熔断与显式越级
    assert "BLOCKED_BY_CIRCUIT_BREAKER" in exp
    assert "--force-continue" in exp
    assert "stage2(api_allowed=False)" in exp
    # 密钥只以"是否存在"入 manifest，绝不入值
    head = exp.split("def main")[0]
    assert "api_key_present\": bool(" in head
    assert "只记**哪些变量存在**，绝不记值" in head


def test_manifest_has_no_secret_but_records_code_hashes(monkeypatch, tmp_path):
    """配置一致性锁定：manifest 含代码/配置哈希，不含密钥值。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SUPER-SECRET-VALUE")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("LLM_VISION_MODEL", "qwen-vl-max")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    from run_real_vlm_experiment import lock_manifest
    m = lock_manifest("exp-test-1")
    blob = json.dumps(m, ensure_ascii=False)
    assert "sk-SUPER-SECRET-VALUE" not in blob
    assert "OPENAI_API_KEY" in m["config_env_names_present"]
    assert m["api_key_present"] is True
    assert m["experiment_id"] == "exp-test-1"
    assert len(m["code_sha256"]["src/assessment_agent/llm.py"]) == 64
    assert "stage1_smoke" in m["selection_rules_declared_before_run"]


def test_runner_records_vision_stages_per_case(tmp_path):
    """离线 image_only：predictions 的 vision 明细必须带三段标志（全 False，
    诚实表示未执行），EXECUTED 判定原料齐备。"""
    from assessment_agent.config import Settings
    from assessment_agent.evaluation.runner import EvaluationRunner
    from assessment_agent.pipeline import Pipeline

    runner = EvaluationRunner(Pipeline(Settings.from_env({})),
                              data_dir=ROOT / "data", results_dir=tmp_path)
    summary = runner.run("figshots_dev", eval_mode="image_only",
                         run_name="stages", save=True, limit=2)
    out = Path(summary.output_dir)
    preds = [json.loads(l) for l in
             (out / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(preds) == 2
    for p in preds:
        v = p["vision"]
        for key in ("vision_http_success", "vision_schema_valid",
                    "vision_extraction_valid", "evidence_complete"):
            assert key in v, (key, sorted(v))
            assert v[key] is False  # 离线：真实链路任何一段都没成功
    st, _ = classify_status(True, False,
                            {"requests_sent": 0, "requests_succeeded": 0,
                             "mock_or_injected_transport": False}, preds, 2)
    assert st == "CONFIGURED_NOT_CALLED"


def test_injected_transport_probe_distinguishes_three_layers(tmp_path):
    """判定链探针（**注入 transport，非真实模型证据**——classify_status 对它
    一律拒绝 EXECUTED）：三种链路形态必须落到不同层级——
      合法 v2 区域输出 → 三段 True
      HTTP 200 但输出非 JSON → schema False
      旧版纯文本 payload → 结构化 False + legacy_payload_fallback True
    结构化层级要求 v2 区域数组**真实非空**：schemas.ExtractionResult 的
    parser 默认值就是 vision_parser，拿字段值当证据会把 v1 降级冒充成成功。"""
    import contextlib
    import io

    import httpx
    from assessment_agent.config import LLMSettings, Settings
    from assessment_agent.evaluation.runner import EvaluationRunner
    from assessment_agent.llm import OpenAICompatibleClient
    from assessment_agent.pipeline import Pipeline

    v2 = {"stem_text": "把图1顺时针旋转90°后得到的图形是？",
          "text_regions": [{"id": "t1", "bbox": [10, 8, 300, 40],
                            "text": "把图1顺时针旋转90°后得到的图形是？"}],
          "diagram_regions": [{"id": "d1", "bbox": [30, 60, 260, 200],
                               "role": "stem_figure",
                               "grid": {"rows": 3, "cols": 3}}],
          "option_regions": [
              {"label": "A", "text": "", "bbox": [40, 260, 90, 90]},
              {"label": "B", "text": "", "bbox": [130, 260, 90, 90]},
              {"label": "C", "text": "", "bbox": [220, 260, 90, 90]},
              {"label": "D", "text": "", "bbox": [310, 260, 90, 90]}],
          "table_regions": []}
    legacy = {"stem": "把图1顺时针旋转90°后得到的图形是？",
              "options": [{"label": "A", "text": ""}], "answer": "A"}

    def run_case(name, content):
        n = {"i": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            n["i"] += 1
            body = content if isinstance(content, str) else json.dumps(content)
            return httpx.Response(200, json={"model": "probe-vision", "id": f"p{n['i']}",
                                             "choices": [{"message": {"content": body}}]})

        llm = OpenAICompatibleClient(
            LLMSettings(api_key="sk-probe", base_url="http://x/v1",
                        vision_model="probe-vision", max_retries=0),
            transport=httpx.MockTransport(handler))
        st = Settings(llm=LLMSettings(api_key="sk-probe", base_url="http://x/v1",
                                      vision_model="probe-vision", max_retries=0),
                      offline=False)
        p = Pipeline(st)
        p.llm = llm  # 注入客户端：审计如实标 mock_or_injected_transport=True
        runner = EvaluationRunner(p, data_dir=ROOT / "data", results_dir=tmp_path / name)
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run("figshots_dev", eval_mode="image_only", run_name="p",
                       limit=1, eval_batch=f"probe-{name}")
        preds = [json.loads(l) for l in
                 (tmp_path / name / "figshots_dev" / "p" / "predictions.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        return preds[0]["vision"], llm.audit_snapshot()

    v, audit = run_case("v2", v2)
    assert (v["vision_http_success"], v["vision_schema_valid"],
            v["vision_extraction_valid"]) == (True, True, True)
    assert audit["mock_or_injected_transport"] is True
    st_v2, _ = classify_status(True, False, audit, [ {"vision": v}], 1)
    assert st_v2 == "CONFIGURED_NOT_CALLED"  # 探针绝不冒充真实执行

    v, _ = run_case("nonjson", "这不是 JSON")
    assert (v["vision_http_success"], v["vision_schema_valid"],
            v["vision_extraction_valid"]) == (True, False, False)

    v, _ = run_case("legacy", legacy)
    assert (v["vision_http_success"], v["vision_schema_valid"],
            v["vision_extraction_valid"]) == (True, True, False)
    assert v["legacy_payload_fallback"] is True
