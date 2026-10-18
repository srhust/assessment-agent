"""R4c：真实 VLM 端到端冒烟五状态判定 + 凭证基础设施锁定。"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from phase6_vlm_smoke import STATUSES, classify_status  # noqa: E402


def _rows(k: int, n: int, *, http: bool = True, schema: bool = True,
          extraction: bool = True) -> list:
    """每题三段记录（vision 明细形状）：k 题达到指定真实解析层级、其余失败。"""
    out = []
    for i in range(n):
        ok = i < k
        out.append({"vision_http_success": ok and http,
                    "vision_schema_valid": ok and schema,
                    "vision_extraction_valid": ok and extraction})
    return out


AUDIT_OK = {"requests_sent": 15, "requests_succeeded": 15, "http_status_counts": {"200": 15},
            "error_types": {}, "echoed_models": ["qwen-vl-max"],
            "mock_or_injected_transport": False,
            "by_phase": {"vision_parse": {"http_succeeded": 15, "json_output_valid": 15}}}
AUDIT_ALL_FAIL = {"requests_sent": 15, "requests_succeeded": 0,
                  "http_status_counts": {"401 (error)": 15},
                  "error_types": {"auth_401_403": 15}, "echoed_models": [],
                  "mock_or_injected_transport": False,
                  "by_phase": {"vision_parse": {"http_succeeded": 0, "json_output_valid": 0}}}


def test_five_status_not_configured_wins_over_everything():
    st, _ = classify_status(False, False, AUDIT_OK, _rows(15, 15), 15)
    assert st == "NOT_CONFIGURED"
    st, _ = classify_status(True, True, AUDIT_OK, _rows(15, 15), 15)
    assert st == "NOT_CONFIGURED"  # ASSESSMENT_OFFLINE=1 时配置存在也不算执行


def test_configured_but_zero_requests_not_executed():
    # 关键洞封堵①：CLI 退出码 0、run_ref 非空都不是证据；审计 0 请求 → 未调用
    for audit in (None, {}, {"requests_sent": 0, "requests_succeeded": 0,
                             "mock_or_injected_transport": False}):
        st, reason = classify_status(True, False, audit, _rows(15, 15), 15)
        assert st == "CONFIGURED_NOT_CALLED", (audit, reason)


def test_all_api_failures_never_executed():
    # 关键洞封堵②：全部 401/超时也不得报 EXECUTED（哪怕 predictions 全有 run_ref）
    st, reason = classify_status(True, False, AUDIT_ALL_FAIL, _rows(0, 15), 15)
    assert st == "API_FAILED"
    assert "auth_401_403" in reason or "401" in reason


def test_mock_transport_refused():
    audit = dict(AUDIT_OK, mock_or_injected_transport=True)
    st, _ = classify_status(True, False, audit, _rows(15, 15), 15)
    assert st == "CONFIGURED_NOT_CALLED"


def test_partial_and_executed():
    audit7 = {"requests_sent": 15, "requests_succeeded": 7,
              "mock_or_injected_transport": False, "echoed_models": ["m"],
              "by_phase": {"vision_parse": {"http_succeeded": 7, "json_output_valid": 7}}}
    st, _ = classify_status(True, False, audit7, _rows(7, 15), 15)
    assert st == "PARTIAL_EXECUTED"
    st, _ = classify_status(True, False, AUDIT_OK, _rows(12, 15), 15)
    assert st == "PARTIAL_EXECUTED"  # API 全成功但解析未满额
    st, _ = classify_status(True, False, AUDIT_OK, _rows(15, 15), 15)
    assert st == "EXECUTED"
    assert len(STATUSES) == 5


def test_llm_client_audit_snapshot_shape():
    import httpx
    from assessment_agent.config import LLMSettings
    from assessment_agent.llm import OpenAICompatibleClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    settings = LLMSettings(api_key="sk-test", vision_model="v", max_retries=0)
    client = OpenAICompatibleClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(Exception):
        client.chat([{"role": "user", "content": "hi"}], model="v")
    audit = client.audit_snapshot()
    assert audit["requests_sent"] == 1
    assert audit["requests_succeeded"] == 0
    assert audit["error_types"].get("auth_401_403") == 1
    assert audit["mock_or_injected_transport"] is True  # 注入 transport 必须披露
    assert "sk-test" not in json.dumps(audit)           # 凭证不落审计


def test_runner_persists_llm_evidence(tmp_path, monkeypatch):
    """评测输出目录必须含 llm_evidence.json（EXECUTED 判定的原料）。离线模式下审计
    报告 0 请求 + offline_mode True——即 CLI 退出码为 0 也不会被脚本误判 EXECUTED。"""
    from assessment_agent.config import Settings
    from assessment_agent.evaluation.runner import EvaluationRunner
    from assessment_agent.pipeline import Pipeline

    settings = Settings.from_env({})
    runner = EvaluationRunner(Pipeline(settings),
                              data_dir=ROOT / "data", results_dir=tmp_path)
    summary = runner.run("figshots_dev", eval_mode="image_only",
                         run_name="t", save=True)
    out = Path(summary.output_dir)
    ev = out / "llm_evidence.json"
    assert ev.is_file()
    j = json.loads(ev.read_text(encoding="utf-8"))
    audit = j["audit"]
    assert audit["requests_sent"] == 0            # 离线环境一条 HTTP 都没发出
    # 关键：退出码 0 + 全部 model_api_error 时，classify 绝不给 EXECUTED
    st, _ = classify_status(True, True, audit, [], 15)
    assert st == "NOT_CONFIGURED"
    preds = [json.loads(line) for line in
             (out / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert all(p["status"] == "failed" and p["failure_class"] == "model_api_error"
               for p in preds)
    # EXECUTED 判定只看审计计数，不被 run_ref/退出码欺骗
    st2, _ = classify_status(True, False, {"requests_sent": 0, "requests_succeeded": 0,
                                           "mock_or_injected_transport": False}, [], 15)
    assert st2 == "CONFIGURED_NOT_CALLED"


def test_repo_smoke_report_immutable_after_real_run():
    """脚本源码层面锁定：配置存在（真实执行）分支的所有写盘都指向
    results/real_vlm/<run_id>/，绝不触碰仓库内被 Git 跟踪的报告。"""
    src = (ROOT / "scripts/phase6_vlm_smoke.py").read_text(encoding="utf-8")
    marker = 'ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")'
    assert src.count(marker) == 1
    real_branch = src.split(marker, 1)[1]  # 真实执行之后的全部代码
    assert "write_repo_report(" not in real_branch
    assert "REPO_REPORT.write_text" not in real_branch
    assert 'run_dir / "smoke_report.json"' in real_branch
    assert "results" in real_branch.split("run_dir =")[1].splitlines()[0]


def test_case_ids_and_limit_filters_declared_before_run():
    """冒烟子集选择：显式 case_ids、缺题报错、limit 取行序前 N——不按结果筛题。"""
    from assessment_agent.config import Settings
    from assessment_agent.evaluation.runner import EvaluationRunner
    from assessment_agent.pipeline import Pipeline

    monkey_runner = EvaluationRunner(Pipeline(Settings.from_env({})),
                                     data_dir=ROOT / "data")
    s = monkey_runner.run("figshots_dev", eval_mode="oracle_solver",
                          run_name="lim", save=False, limit=3)
    assert s.total == 3
    s2 = monkey_runner.run("figshots_dev", eval_mode="oracle_solver",
                           run_name="ids", save=False,
                           case_ids=["ft-rot90-01", "ft-grid-10"])
    assert s2.total == 2
    with pytest.raises(ValueError):
        monkey_runner.run("figshots_dev", eval_mode="oracle_solver",
                          run_name="bad", save=False, case_ids=["不存在-99"])


def test_attribution_fields_from_vision_metrics():
    """compare_extraction_detail 新增 grid/裁剪字段，归因脚本按真实字段消费。"""
    from assessment_agent.evaluation.vision_metrics import compare_extraction_detail

    gold = {"stem_text": "请选择符合规律的图形。", "stem_matrix_bbox": [10, 40, 96, 96],
            "stem_matrix_grid": {"rows": 3, "cols": 3},
            "option_regions": [{"label": "A", "text": "", "bbox": [140, 40, 48, 48]}]}

    class Opt:
        label, text, bbox, crop_path = "A", "", [140, 40, 48, 48], None

    class Reg:
        def __init__(self, grid):
            self.bbox, self.grid, self.crop_path = [10, 40, 96, 96], grid, None

    class G:
        rows, cols = 3, 3

    class Ext:
        def __init__(self):
            self.stem = "请选择符合规律的图形。"
            self.options = []
            self.option_regions = [Opt()]
            self.diagram_regions = [Reg(G())]
            self.table_regions = []

    d = compare_extraction_detail(gold, Ext())
    assert d["grid_parse_correct"] is True
    assert d["crops_complete"] is False and d["crops_scored"] == 2
    d0 = compare_extraction_detail(gold, None)
    assert d0["grid_parse_correct"] is False and d0["crops_complete"] is False

    sys.path.insert(0, str(ROOT / "scripts"))
    from run_real_vlm_experiment import attribute_case
    row = {"case_id": "x", "expected": "A", "predicted_option": None,
           "correct": False, "verification_action": "reject",
           "failure_class": None, "input_mode": "image_only", "skill": "figure_reasoning",
           "routed_skill": "figure_reasoning",
           "vision": {"parse_failed": False, "stem_exact": True, "stem_cer": 0.0,
                      "option_label_recall": 1.0, "option_text_accuracy": None,
                      "diagram_iou_mean": 1.0, "option_iou_mean": 0.9,
                      "grid_parse_correct": True, "crops_complete": True}}
    att = attribute_case(row)
    assert att["error_type"] in ("solver_error", "verifier_error")
    assert att["stem_ocr_correct"] is True and att["grid_parse_correct"] is True
    row_api = dict(row, failure_class="model_api_error",
                   vision={"parse_failed": True})
    att_api = attribute_case(row_api)
    assert att_api["error_type"] == "api_failure"
    assert att_api["stem_ocr_correct"] is None  # 未执行 ≠ 零值


def test_frozen_offline_report_status_stays_not_executed():
    """仓库 phase6_vlm_smoke.json 在离线环境永保持未执行语义；真实执行不回写它。"""
    p = ROOT / "data/benchmark_figshots/phase6_vlm_smoke.json"
    j = json.loads(p.read_text(encoding="utf-8"))
    assert j["status"] in ("NOT_CONFIGURED", "NOT_EXECUTED")
    assert j.get("split") == "figshots_dev"
    assert "figtrans_dev" not in json.dumps(j, ensure_ascii=False)
