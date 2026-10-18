"""R4c：固定配置的真实 VLM 端到端实验运行器。

流程（选择标准运行前声明、运行后不改）：
  1. 锁定实验配置：git commit（含 dirty 凭证）、provider/model/temperature/
     max_tokens、VisionParser prompt sha256、图片 sha256、时间、模式矩阵。
  2. figshots_dev：冒烟（3 题，运行前显式指定题号）→ 全量 15 题，三模式
     （oracle_solver / auto_router / image_only）。
  3. 跨题型感知：bench_dev 的 data_analysis / numeric_calculation /
     figure_reasoning / verbal_understanding 子集 image_only——**不按真实
     VLM 是否解析成功筛样本**。
  4. 每题感知归因（11 字段 + 错误类型优先级分类）与分层指标（15 项，
     全部带分子/分母，区分 未执行/缺失/零值）。
  5. 执行状态用 llm_evidence.json 审计计数判定（scripts.phase6_vlm_smoke
     .classify_status）——退出码与 run_ref 不构成证据。

未配置真实 API 时：基础设施与离线回归照常产出，状态 NOT_CONFIGURED，
绝不声称真实 VLM 已验证。报告写 results/real_vlm/<run_id>/，不改仓库文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

# R4d：执行状态判定与 smoke **同源**（禁止两套脚本对 EXECUTED 各说各话）
from assessment_agent.evaluation.execution_status import (  # noqa: E402
    classify_status, health_gate, vision_stage_summary,
)

SMOKE_IDS = ["ft-rot90-01", "ft-grid-10", "ft-overlay-12"]
FIGSPLITS = "figshots_dev"
CROSS_SKILLS = ("data_analysis", "numeric_calculation", "figure_reasoning",
                "verbal_understanding")
ERROR_ORDER = ["api_failure", "ocr_error", "bbox_error", "option_map_error",
               "grid_error", "router_error", "solver_error", "verifier_error"]
FALSE_ACCEPT_PRIORITY = {"api_failure": 0, "ocr_error": 1, "bbox_error": 2,
                         "option_map_error": 3, "grid_error": 4, "router_error": 5,
                         "solver_error": 6, "verifier_error": 7}


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", cwd=str(ROOT)).stdout.strip()


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def lock_manifest(exp_id: str) -> Dict[str, Any]:
    from assessment_agent.config import Settings
    from assessment_agent.prompts import VISION_PARSE_SYSTEM
    st = Settings.from_env()
    images = sorted((ROOT / "data/benchmark_figshots/images/dev").glob("*.png"))
    dirty = sh("git", "status", "--porcelain")
    code_files = ("src/assessment_agent/llm.py",
                  "src/assessment_agent/evaluation/runner.py",
                  "src/assessment_agent/evaluation/vision_metrics.py",
                  "src/assessment_agent/evaluation/execution_status.py",
                  "src/assessment_agent/vision_parser.py",
                  "src/assessment_agent/tools/figure_transform.py",
                  "src/assessment_agent/tools/figure_transform_tools.py",
                  "src/assessment_agent/tools/figure_adapter.py",
                  "src/assessment_agent/skills/figure_reasoning.py")
    env_names = sorted(k for k in
                       ("LLM_API_KEY", "OPENAI_API_KEY", "LLM_VISION_MODEL",
                        "LLM_BASE_URL", "OPENAI_BASE_URL", "LLM_MODEL", "OPENAI_MODEL",
                        "LLM_TEXT_MODEL", "ASSESSMENT_OFFLINE")
                       if os.environ.get(k))  # 只记**哪些变量存在**，绝不记值
    return {
        "experiment_id": exp_id,
        "locked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "git_commit": sh("git", "rev-parse", "HEAD"),
        "git_dirty": bool(dirty),
        "dirty_digest_sha256": hashlib.sha256(dirty.encode("utf-8")).hexdigest()[:16],
        "code_sha256": {f: sha_file(ROOT / f) for f in code_files},
        "config_env_names_present": env_names,
        "provider_host": st.llm.base_url.split("//")[-1].split("/")[0],
        "vision_model": st.llm.vision_model,
        "text_model": st.llm.text_model,
        "temperature": st.llm.temperature,
        "max_tokens": st.llm.max_tokens,
        "offline_flag": st.offline,
        "api_key_present": bool(st.llm.api_key),
        "vision_prompt_sha256": sha_text(VISION_PARSE_SYSTEM),
        "image_sha256": {p.name: sha_file(p) for p in images},
        "selection_rules_declared_before_run": {
            "stage1_smoke": SMOKE_IDS,
            "stage2_full": "figshots_dev 全部 15 题（数据集行序，全部成功与失败保留）",
            "stage3_cross": "bench_dev 全部 35 张截图（不按 skills 文件筛、更不按解析结果筛）",
            "gating": "stage2 仅当 stage1 通过 API+Schema 健康检查；stage3 仅当 stage2 健康；"
                      "--force-continue 显式越级",
        },
        "note": "密钥不落盘；本 manifest 是实验配置唯一凭证",
    }


def sha_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def run_eval(split: str, mode: str, run_name: str, *, skills: Optional[str] = None,
             case_ids: Optional[str] = None, direct: bool = False,
             eval_batch: Optional[str] = None) -> Dict[str, Any]:
    """direct=True 时进程内调用 runner（跨 split 目录基准需要 runner 自带的
    _SPLIT_DIRS 映射；同一解释器、同一代码态，凭证一致）。"""
    if direct:
        import io
        import contextlib
        from assessment_agent.config import Settings
        from assessment_agent.evaluation.runner import EvaluationRunner
        from assessment_agent.pipeline import Pipeline

        # Settings.from_env(None) 读 os.environ + .env（真实环境变量优先），
        # 与 CLI 子进程同一事实源；离线开关下 pipeline 不会构造真实客户端，
        # 审计如实记 requests_sent=0 + offline_mode=True。
        settings = Settings.from_env()
        runner = EvaluationRunner(Pipeline(settings), data_dir=ROOT / "data",
                                  results_dir=ROOT / "results")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                summary = runner.run(split, eval_mode=mode, run_name=run_name,
                                     eval_batch=eval_batch or run_name,
                                     no_overwrite=True,
                                     route_mode="oracle" if mode == "oracle_solver" else "auto",
                                     skills=[s.strip() for s in (skills or "").split(",") if s.strip()] or None,
                                     case_ids=[q.strip() for q in (case_ids or "").split(",") if q.strip()] or None)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}
        out = Path(summary.output_dir)
        ev = out / "llm_evidence.json"
        audit = json.loads(ev.read_text(encoding="utf-8")) if ev.is_file() else None
        preds = [json.loads(l) for l in
                 (out / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        return {"output_dir": str(out), "metrics": summary.metrics, "audit": audit,
                "predictions": preds, "eval_batch": summary.metrics.get("eval_batch")}

    cmd = [sys.executable, "-X", "utf8", "-m", "assessment_agent.cli", "eval",
           "--split", split, "--eval-mode", mode, "--route-mode",
           "oracle" if mode == "oracle_solver" else "auto", "--run-name", run_name,
           "--no-overwrite"]
    if eval_batch:
        cmd += ["--eval-batch", eval_batch]
    if skills:
        cmd += ["--skills", skills]
    if case_ids:
        cmd += ["--case-ids", case_ids]
    cmd += ["--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          cwd=str(ROOT),
                          env={"PYTHONPATH": "src", "PYTHONIOENCODING": "utf-8",
                               **_keep_env()})
    if proc.returncode != 0:
        return {"error": proc.stderr[-500:], "cmd": " ".join(cmd[3:])}
    d = json.loads(proc.stdout)
    out = Path(d["output_dir"])
    ev = out / "llm_evidence.json"
    audit = json.loads(ev.read_text(encoding="utf-8")) if ev.is_file() else None
    preds = [json.loads(l) for l in
             (out / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return {"output_dir": str(out), "metrics": d["metrics"], "audit": audit,
            "predictions": preds, "eval_batch": d["metrics"].get("eval_batch")}


def _keep_env() -> Dict[str, str]:
    import os
    # R4d P1：父/子进程**同一配置事实源**。config.Settings.from_env 支持
    # OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL 别名，故子进程必须原样继承，
    # 否则会出现"父进程已配置、子进程判 NOT_CONFIGURED"的分裂口径。
    # 只透传变量本身——密钥不写入任何报告文件。
    keys = ("LLM_API_KEY", "LLM_MODEL", "LLM_VISION_MODEL", "LLM_BASE_URL",
            "LLM_TEXT_MODEL", "LLM_TEMPERATURE", "LLM_MAX_TOKENS",
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
            "ASSESSMENT_OFFLINE", "VERIFIER_USE_LLM", "VISION_PARSER",
            "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP")
    # ASSESSMENT_OFFLINE 透传：离线开关必须与主 manifest 同一事实源；审计的
    # offline_mode/requests_sent 字段负责区分"被离线拦截"与"配置存在但未调用"。
    return {k: v for k, v in os.environ.items() if k in keys and v is not None}


def attribute_case(row: Dict[str, Any]) -> Dict[str, Any]:
    """逐题 11 字段归因 + 错误类型（优先级取第一个命中）。

    字段来源是 predictions.jsonl 的 ``vision`` 明细（compare_extraction_detail，
    R2b 起内容级、R4c 补 grid/裁剪），全部保留在分母中，不按结果筛题。

    R4d 口径分离（评审 P0-2 第二处）：
      solver_proposed_answer = predicted_option（Solver 内部提议，即使未对外）
      final_answer           = predicted（Verifier accept/repair 后实际对外输出）
      answered_out_loud      = final_answer 非空
    """
    v = row.get("vision") or {}
    api_failed = row.get("failure_class") in ("model_api_error", "llm_unavailable")
    parse_failed = bool(v.get("parse_failed"))
    perception_dead = api_failed or parse_failed or not v
    gold = row.get("expected")
    final_answer = row.get("predicted")
    action = row.get("verification_action")
    answered = final_answer is not None and action in ("accept", "repair")
    routed_ok = None
    if row.get("input_mode") in ("auto_router", "image_only") and row.get("routed_skill"):
        routed_ok = row.get("routed_skill") == row.get("skill")
    fields = {
        "case_id": row["case_id"],
        "stem_ocr_correct": None if perception_dead else bool(v.get("stem_exact")),
        "stem_cer": None if perception_dead else v.get("stem_cer"),
        "option_label_mapping_correct": (None if perception_dead
                                         else v.get("option_label_recall") == 1.0),
        "option_text_correct": None if perception_dead else v.get("option_text_accuracy"),
        "stem_bbox_iou": None if perception_dead else v.get("diagram_iou_mean"),
        "per_option_bbox_iou_mean": None if perception_dead else v.get("option_iou_mean"),
        "grid_parse_correct": None if perception_dead else v.get("grid_parse_correct"),
        "crops_complete": None if perception_dead else v.get("crops_complete"),
        "router_correct": routed_ok,
        "solver_proposed_gold": _solver_proposed_gold(row, gold)
            if not perception_dead else None,
        "verifier_accepted": action in ("accept", "repair"),
        "solver_proposed_answer": row.get("predicted_option"),
        "final_answer": final_answer,
        "answered_out_loud": answered,
        "correct": row.get("correct"),
        "failure_class": row.get("failure_class"),
        "verification_action": action,
    }
    err = None
    if api_failed:
        err = "api_failure"
    elif parse_failed or not v:
        err = "ocr_error"
    elif (fields["stem_bbox_iou"] is not None and fields["stem_bbox_iou"] < 0.5) \
            or (fields["per_option_bbox_iou_mean"] is not None
                and fields["per_option_bbox_iou_mean"] < 0.5):
        err = "bbox_error"
    elif fields["option_label_mapping_correct"] is False:
        err = "option_map_error"
    elif fields["grid_parse_correct"] is False or fields["crops_complete"] is False:
        err = "grid_error"
    elif fields["router_correct"] is False:
        err = "router_error"
    elif answered and row.get("correct") is False:
        # 答错还对外作答：solver 提议本来就错 → solver_error；
        # 提议正确却被 verifier accept/repair 输出错值 → verifier_error
        err = ("solver_error" if fields["solver_proposed_gold"] is not True
               else "verifier_error")
    elif (not answered) and row.get("correct") is False and gold is not None \
            and fields["solver_proposed_gold"] is True:
        err = "verifier_error"  # solver 提出正解但未对外作答（验证拒/未验）
    elif (not answered) and row.get("correct") is False and gold is not None \
            and not perception_dead:
        err = "solver_error"
    fields["error_type"] = err
    return fields


def _false_accept_rank(row: Dict[str, Any]) -> int:
    """false accept（答错却 accept）时，按归因链最上游环节标记错误类别；
    其余情形返回 -1 不参与排序。对外输出以 final_answer（predicted）为准。"""
    if (row.get("correct") is not False
            or row.get("verification_action") not in ("accept", "repair")
            or row.get("predicted") is None):
        return -1
    v = row.get("vision") or {}
    if row.get("failure_class") in ("model_api_error", "llm_unavailable"):
        return FALSE_ACCEPT_PRIORITY["api_failure"]
    if v.get("parse_failed") or not v:
        return FALSE_ACCEPT_PRIORITY["ocr_error"]
    if (isinstance(v.get("diagram_iou_mean"), (int, float)) and v["diagram_iou_mean"] < 0.5) \
            or (isinstance(v.get("option_iou_mean"), (int, float)) and v["option_iou_mean"] < 0.5):
        return FALSE_ACCEPT_PRIORITY["bbox_error"]
    if v.get("option_label_recall") is not None and v["option_label_recall"] < 1.0:
        return FALSE_ACCEPT_PRIORITY["option_map_error"]
    if v.get("grid_parse_correct") is False or v.get("crops_complete") is False:
        return FALSE_ACCEPT_PRIORITY["grid_error"]
    if row.get("routed_skill") and row["routed_skill"] != row.get("skill"):
        return FALSE_ACCEPT_PRIORITY["router_error"]
    if row.get("predicted") != row.get("expected"):
        return FALSE_ACCEPT_PRIORITY["solver_error"]
    return FALSE_ACCEPT_PRIORITY["verifier_error"]


def _solver_proposed_gold(row: Dict[str, Any], gold: Optional[str]) -> Optional[bool]:
    """solver 是否**提出过**正确答案：最终答案为 gold，或 verification 记录
    （unverified/reject 时答案未外发，但候选中曾有 gold——保守取 predicted/solution 字段）。"""
    if gold is None:
        return None
    for key in ("predicted_option", "solution_answer"):
        if row.get(key) == gold:
            return True
    return None if (row.get("predicted_option") is None
                    and row.get("solution_answer") is None) else False


def _mode_execution(mode: str, preds: List[Dict[str, Any]],
                    audit: Optional[Dict[str, Any]]) -> str:
    """R4d：与 smoke **同源**的 execution_status（共享 classify_status）：
    oracle/auto 是离线可完全执行的 Solver 层模式（真实网络不需要）；
    image_only 的 EXECUTED 必须指定样本全部完成真实视觉解析——
    HTTP 总数（混有 router/solver 请求）不再直接当视觉成功数。"""
    if not preds:
        return "NOT_EXECUTED"
    if mode != "image_only":
        return "EXECUTED"
    a = (audit or {}).get("audit") or {}
    configured = bool(a.get("vision_model_configured")) and not a.get("offline_mode")
    st, _reason = classify_status(configured, bool(a.get("offline_mode")), a,
                                  preds, len(preds))
    return st


def stratified(run: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """分层指标：分子/分母齐备；未执行=None、缺失="missing"、真零=0。

    R4d 口径修正（评审 P0-2）：
    - end_to_end_objective_accuracy 完整取 overall_answer_accuracy 的
      hit/denominator/value 三元组（旧 answer_accuracy 是分母口径不同的 legacy，
      禁止 value 用 A、分子分母用 B 的混用）；
    - answered_out_loud 只统计**最终对外答案**（predicted 非空且 accept/repair），
      Solver 提议但被 unverified/reject 拦截的不算作答。
    """
    preds = run.get("predictions") or []
    m = run.get("metrics") or {}
    n = len(preds)
    def frac(hit, total):
        return {"numerator": hit, "denominator": total,
                "value": (round(hit / total, 4) if total else None)}
    def count(pred_fn):
        return [p for p in preds if pred_fn(p)]
    def is_final_answered(p: Dict[str, Any]) -> bool:
        return (p.get("predicted") is not None
                and p.get("verification_action") in ("accept", "repair"))
    answered = count(is_final_answered)
    api_fail = count(lambda p: p.get("failure_class") in ("model_api_error", "llm_unavailable"))
    parsed = count(lambda p: bool(p.get("vision")) and not p["vision"].get("parse_failed"))
    with_vision = count(lambda p: bool(p.get("vision")))
    ious = [p["vision"].get("diagram_iou_mean") for p in with_vision
            if isinstance(p["vision"].get("diagram_iou_mean"), (int, float))]
    oious = [p["vision"].get("option_iou_mean") for p in with_vision
             if isinstance(p["vision"].get("option_iou_mean"), (int, float))]
    omaps = count(lambda p: p.get("option_map_ok") is True)
    omaps_den = count(lambda p: p.get("option_map_ok") is not None)
    lat = [p.get("latency_ms") for p in preds if isinstance(p.get("latency_ms"), (int, float))]
    cap = m.get("capability") or {}
    # 主客观口径：R2c 确立的 overall_answer_accuracy（全部客观可答题分母）
    objective = m.get("overall_answer_accuracy") or {}
    return {
        "mode": mode,
        "cases": n,
        "execution_status": _mode_execution(mode, preds, run.get("audit")),
        "per_skill": _group_by_skill(preds),
        "vision_parse_success": frac(
            sum(1 for p in preds if bool((p.get("vision") or {}).get("vision_extraction_valid"))),
            n) if with_vision else "not_applicable",
        "evidence_completeness": frac(
            sum(1 for p in preds if bool((p.get("vision") or {}).get("evidence_complete"))),
            n) if with_vision else "not_applicable",
        "extraction_accuracy": frac(
            sum(1 for p in with_vision if p["vision"].get("stem_exact")), len(with_vision))
            if with_vision else "not_applicable",
        "region_iou": ({"stem_mean": round(sum(ious) / len(ious), 4),
                        "per_option_mean": round(sum(oious) / len(oious), 4),
                        "numerator_cases": len(ious),
                        "denominator_cases": len(with_vision)}
                       if with_vision else "not_applicable"),
        "option_map_accuracy": frac(len(omaps), len(omaps_den)) if omaps_den else "not_applicable",
        "router_accuracy": ("not_measured_in_oracle" if mode == "oracle_solver"
                            else ("not_measured_api_failure" if mode == "image_only"
                                  and _api_failure_only(preds)
                                  else frac(*_router_hits(preds, n)))),
        "solver_with_gold_extraction": ("not_applicable" if mode == "image_only"
                                        else frac(sum(1 for p in preds if p.get("correct")),
                                                  sum(1 for p in preds if p.get("expected") is not None))),
        "end_to_end_objective_accuracy": ({"value": objective.get("value"),
            "numerator": objective.get("hit"),
            "denominator": objective.get("denominator"),
            "caliber": "overall_answer_accuracy (R2c objective denominator)"}
            if objective else "missing"),
        "legacy_answer_accuracy_for_reference_only": m.get("answer_accuracy"),
        "answerable_coverage": m.get("answerable_coverage"),
        "selective_accuracy": m.get("selective_accuracy"),
        "false_accept": cap.get("false_accept_total", 0),
        "over_answer": cap.get("over_answer_total", 0),
        "unverified_abstention": m.get("abstention_total", 0),
        "answered_out_loud": frac(len(answered), n),
        "verified_answer_yield": frac(
            sum(1 for p in preds if p.get("correct") is True and is_final_answered(p)),
            int(objective.get("denominator") or 0)) if objective.get("denominator") else None,
        "api_failure_rate": frac(len(api_fail), n) if mode == "image_only" else "not_applicable",
        "parse_success": frac(len(parsed), len(with_vision)) if with_vision else "not_applicable",
        "latency": ({"p50_ms": round(statistics.median(lat), 1),
                     "p95_ms": round(statistics.quantiles(lat, n=20)[18], 1)}
                    if len(lat) >= 5 else {"raw_ms": lat}),
        "usage": ((run.get("audit") or {}).get("usage") or "missing"),
        "note": ("成功率极小的样本不支撑'已证明部署误放概率'的宣称；"
                 "None=未执行/不可比，missing=字段缺失，0=真实零值"),
    }


def _group_by_skill(preds: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按 skill 分组：各层指标的分母必须按题型分开呈现（跨题型感知验证要求）。"""
    grouped: Dict[str, Dict[str, Any]] = {}
    for p in preds:
        key = str(p.get("skill") or "?")
        g = grouped.setdefault(key, {"cases": 0, "answered": 0, "correct": 0,
                                     "api_failure": 0, "parse_failed": 0, "false_accept": 0})
        g["cases"] += 1
        if p.get("predicted") is not None and p.get("verification_action") in ("accept", "repair"):
            g["answered"] += 1
        if p.get("correct") is True:
            g["correct"] += 1
        fr = _false_accept_rank(p)
        if fr >= 0:
            g["false_accept"] += 1
            g.setdefault("false_accept_cause", []).append(
                next(k for k, v in FALSE_ACCEPT_PRIORITY.items() if v == fr))
        if p.get("failure_class") in ("model_api_error", "llm_unavailable"):
            g["api_failure"] += 1
        if (p.get("vision") or {}).get("parse_failed"):
            g["parse_failed"] += 1
    return grouped


def _api_failure_only(preds: List[Dict[str, Any]]) -> bool:
    return all(p.get("failure_class") in ("model_api_error", "llm_unavailable")
               for p in preds)


def _router_hits(preds: List[Dict[str, Any]], n: int) -> tuple:
    hits = sum(1 for p in preds
               if p.get("routed_skill") and p["routed_skill"] == p.get("skill"))
    return hits, n


def main() -> None:
    force = "--force-continue" in sys.argv
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    exp_id = f"{ts}-{os.getpid()}-{hashlib.sha256(str(ts).encode()).hexdigest()[:4]}"
    manifest = lock_manifest(exp_id)
    exp_dir = ROOT / "results" / "real_vlm" / exp_id
    if exp_dir.exists():  # R4d：禁止静默覆盖历史实验凭证
        raise FileExistsError(f"实验目录已存在，拒绝覆盖: {exp_dir}")
    exp_dir.mkdir(parents=True)

    configured = bool(manifest["api_key_present"] and manifest["vision_model"]
                      and not manifest["offline_flag"])
    offline = bool(manifest["offline_flag"])

    experiments: Dict[str, Any] = {}
    audits: List[Dict[str, Any]] = []

    def _merge_audit(run: Dict[str, Any]) -> None:
        if run.get("audit"):
            audits.append(run["audit"].get("audit") or {})

    def stage1() -> Dict[str, Any]:
        """Smoke：3 张完整 figshots 页面。真实 API 只在 image_only 模式被消耗。"""
        out: Dict[str, Any] = {}
        for mode in ("oracle_solver", "auto_router", "image_only"):
            run = run_eval(FIGSPLITS, mode, f"{exp_id}_smoke_{mode}",
                           case_ids=",".join(SMOKE_IDS),
                           eval_batch=f"{exp_id}_smoke_{mode}")
            out[mode] = run
            _merge_audit(run)
            if run.get("predictions"):
                (exp_dir / f"attribution_smoke_{mode}.json").write_text(
                    json.dumps([attribute_case(r) for r in run["predictions"]],
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        io = out.get("image_only") or {}
        rows = io.get("predictions") or []
        summary = vision_stage_summary(rows, io.get("audit"))
        audit = (io.get("audit") or {}).get("audit")
        status, reason = classify_status(configured, offline, audit, rows, len(SMOKE_IDS))
        ok, msg = health_gate(status, summary)
        return {"status": status, "reason": reason, "vision_stage_summary": summary,
                "health_gate": {"ok": ok, "message": msg},
                "stratified": {m: (stratified(r, m) if "predictions" in r
                                   else {"error": r.get("error")})
                               for m, r in out.items()}}

    def stage2(api_allowed: bool) -> Dict[str, Any]:
        """figshots_dev 全量 15 题（全部成功与失败保留）。
        熔断时 image_only（真实消耗 API 的模式）不执行，记 BLOCKED；
        oracle/auto 零 API 成本，保留用于基础设施回归（但不得称真实验证）。"""
        out: Dict[str, Any] = {}
        modes = ("oracle_solver", "auto_router", "image_only") if api_allowed \
            else ("oracle_solver", "auto_router")
        for mode in modes:
            run = run_eval(FIGSPLITS, mode, f"{exp_id}_full_{mode}",
                           eval_batch=f"{exp_id}_full_{mode}")
            out[mode] = run
            _merge_audit(run)
            if run.get("predictions"):
                att = [attribute_case(r) for r in run["predictions"]]
                (exp_dir / f"attribution_figshots_{mode}.json").write_text(
                    json.dumps(att, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
        if not api_allowed:
            out["image_only"] = {"error": "BLOCKED_BY_CIRCUIT_BREAKER（stage1 未过健康检查，"
                                          "不发起真实 API 消耗）"}
        io = out.get("image_only") or {}
        rows = io.get("predictions") or []
        audit = (io.get("audit") or {}).get("audit")
        summary = vision_stage_summary(rows, audit)
        status, reason = classify_status(configured, offline, audit, rows, 15)
        ok, msg = health_gate(status, summary)
        return {"status": status, "reason": reason, "vision_stage_summary": summary,
                "health_gate": {"ok": ok, "message": msg},
                "stratified": {m: (stratified(r, m) if "predictions" in r
                                   else {"error": r.get("error")})
                               for m, r in out.items()}}

    def stage3() -> Dict[str, Any]:
        """跨题型感知：bench_dev 全部 35 张截图，不按解析结果筛样本。"""
        run = run_eval("bench_dev", "image_only", f"{exp_id}_cross_bench",
                       eval_batch=f"{exp_id}_cross_bench", direct=True)
        _merge_audit(run)
        if "error" in run:
            return {"status": "EXECUTION_FAILED", "reason": run["error"],
                    "error": run["error"], "stratified": None}
        rows = run.get("predictions") or []
        skill_by_id = {p["case_id"]: str(p.get("skill") or "?") for p in rows}
        att = [attribute_case(r) for r in rows]
        (exp_dir / "attribution_cross_bench_image_only.json").write_text(
            json.dumps(att, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        by_skill: Dict[str, Dict[str, int]] = {}
        for a in att:
            g = by_skill.setdefault(skill_by_id.get(a["case_id"], "?"), {})
            g["cases"] = g.get("cases", 0) + 1
            if a["error_type"]:
                g[a["error_type"]] = g.get(a["error_type"], 0) + 1
        audit = (run.get("audit") or {}).get("audit")
        summary = vision_stage_summary(rows, audit)
        status, reason = classify_status(configured, offline, audit, rows, len(rows) or 35)
        return {"status": status, "reason": reason, "vision_stage_summary": summary,
                "per_skill": by_skill, "stratified": stratified(run, "image_only")}

    # ---- 分阶段熔断（R4d P1）：stage1 API/Schema 不健康 → stage2/3 的
    #      **真实视觉调用不发起**（oracle/auto 零成本模式仍运行作基础设施回归，
    #      但报告不得把任何后续称为"真实验证完成"）----
    experiments["stage1_smoke"] = stage1()
    gate1 = experiments["stage1_smoke"]["health_gate"]
    if not gate1["ok"] and not force:
        experiments["stage2_figshots_full"] = stage2(api_allowed=False)
        experiments["stage2_figshots_full"]["circuit_breaker"] = {
            "blocked_image_only": True,
            "reason": f"stage1 未过健康检查：{gate1['message']}——不发起后续真实调用，"
                      f"避免重复失败与费用。显式 --force-continue 可越级。"}
        experiments["stage3_cross_skill"] = {
            "status": "BLOCKED_BY_CIRCUIT_BREAKER", "stage": "3",
            "reason": "stage1 熔断：跨题型真实感知评测未发起",
            "vision_stage_summary": None, "stratified": None}
        status = experiments["stage1_smoke"]["status"]
        reason = experiments["stage1_smoke"]["reason"]
    else:
        experiments["stage2_figshots_full"] = stage2(api_allowed=True)
        gate2 = experiments["stage2_figshots_full"]["health_gate"]
        if not gate2["ok"] and not force:
            experiments["stage3_cross_skill"] = {
                "status": "BLOCKED_BY_CIRCUIT_BREAKER",
                "reason": f"stage2 未过健康检查：{gate2['message']}",
                "vision_stage_summary": None, "stratified": None}
            status = experiments["stage2_figshots_full"]["status"]
            reason = experiments["stage2_figshots_full"]["reason"]
        else:
            experiments["stage3_cross_skill"] = stage3()
            status = experiments["stage3_cross_skill"]["status"]
            reason = experiments["stage3_cross_skill"]["reason"]

    # ---- Δ_vision：与 stratified 同源（overall_answer_accuracy 主口径）----
    def objv(stage_key: str, mode_key: str):
        s = (experiments.get(stage_key, {}).get("stratified") or {}).get(mode_key) or {}
        v = s.get("end_to_end_objective_accuracy")
        if isinstance(v, dict):
            val = v.get("value")
            return val if isinstance(val, (int, float)) else None
        return None
    auto_obj = objv("stage2_figshots_full", "auto_router")
    io_obj = objv("stage2_figshots_full", "image_only")
    io_exec = str((experiments.get("stage2_figshots_full", {})
                   .get("stratified") or {}).get("image_only", {})
                  .get("execution_status", ""))
    if io_exec != "EXECUTED":
        io_obj = None  # 未真实执行的 0.0 不得进入差值（否则会冒充 image_only_objective）
    dv = {
        "formula": "Δ_vision = Acc_auto − Acc_image_only（同一数据集、同一主指标结构 "
                   "overall_answer_accuracy，分子/分母/value 三者同源）",
        "auto_objective": auto_obj,
        "image_only_objective": io_obj,
        "delta": None,
        "valid_only_with_real_vlm": True,
        "caveat": "视觉错误会级联改变路由与求解过程；Δ 只是端到端损失初步指标，"
                  "逐题差异须核对 attribution_*.json 后才能解读",
    }
    if io_exec == "EXECUTED" and isinstance(auto_obj, (int, float)) \
            and isinstance(io_obj, (int, float)):
        dv["delta"] = round(auto_obj - io_obj, 4)
        dv["valid_only_with_real_vlm"] = False
    else:
        dv["delta"] = "not_meaningful（image_only 未达 EXECUTED）"
    experiments["delta_vision"] = dv

    # ---- 合并审计（by_phase 也在内：vision 成功数与总响应数分开呈现）----
    def _sum_phase(key: str) -> int:
        return sum(int((a.get("by_phase") or {}).get("vision_parse", {}).get(key, 0))
                   for a in audits)
    audit_merged = {
        "requests_sent": sum(a.get("requests_sent", 0) for a in audits),
        "requests_succeeded": sum(a.get("requests_succeeded", 0) for a in audits),
        "vision_parse_http_success": _sum_phase("http_succeeded"),
        "vision_parse_schema_valid": _sum_phase("json_output_valid"),
        "error_types": {},
        "echoed_models": sorted({m for a in audits for m in (a.get("echoed_models") or [])}),
        "mock_or_injected_transport": any(a.get("mock_or_injected_transport") for a in audits),
        "note": "requests_succeeded 为全部阶段总数；EXECUTED 判定只看 vision_parse 阶段",
    }

    report = {
        "experiment": "R4d real-VLM staged experiment with unified execution status",
        "experiment_id": exp_id,
        "manifest": manifest,
        "status": status,
        "status_reason": reason,
        "force_continue": force,
        "audit_merged": audit_merged,
        "experiments": experiments,
        "topline_questions": {
            "vision_parse_success": "VLM 能否稳定输出符合 Schema 的区域化结果？",
            "evidence_completeness": "Schema 合法之外，题干/全部选项/图形/网格是否真被识别？",
            "verified_answer_yield": "客观可答题中多少得到**正确且通过验证的最终答案**？",
        },
        "blind_test_plan": {
            "status": "PLANNED_NOT_BUILT",
            "precondition": "真实模型 Dev 实验完成并冻结模型与算法配置后构建",
            "layers": ["自编程序合成页面", "独立人工编写页面",
                       "授权真实页面截图（当前缺失，不得以合成替代）"],
            "non_sharing": "不与开发集共享模板、图形原型、表格内容或选项排列",
        },
        "honesty": [
            "小调用量不得宣称已证明真实部署的误放概率",
            "所有失败保留在分母中；未执行(None)/缺失(missing)/零值(0)三态区分",
            "oracle/auto/image_only 同题对比的逐题差异必须人工核对归因后才能解读",
            "首次真实运行允许记录原始失败，不针对冻结 Test 调整模型 Prompt 或 Solver",
        ],
    }
    (exp_dir / "experiment.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_md(exp_dir / "report.md", report)
    print(json.dumps({"experiment_id": exp_id, "status": status, "reason": reason,
                      "dir": str(exp_dir)}, ensure_ascii=False))


def _slim(run: Dict[str, Any]) -> Dict[str, Any]:
    if "error" in run:
        return {"error": run["error"]}
    return {k: run.get(k) for k in ("output_dir", "eval_batch")} | {
        "metrics": run.get("metrics"), "audit": run.get("audit"),
        "n_predictions": len(run.get("predictions") or [])}


def _write_md(path: Path, report: Dict[str, Any]) -> None:
    lines = [f"# R4d 真实 VLM 分阶段实验报告（{report['experiment_id']}）",
             f"- 状态：**{report['status']}**（{report['status_reason']}）",
             f"- commit: {report['manifest']['git_commit']}"
             f"（dirty={report['manifest']['git_dirty']}）",
             f"- provider: {report['manifest']['provider_host']} / "
             f"vision: {report['manifest']['vision_model']} / "
             f"temp: {report['manifest']['temperature']} / "
             f"prompt sha: {report['manifest']['vision_prompt_sha256'][:16]}",
             f"- force_continue: {report.get('force_continue')}",
             "", "## 分阶段执行", ""]
    for key in ("stage1_smoke", "stage2_figshots_full", "stage3_cross_skill"):
        blk = report["experiments"].get(key) or {}
        gate = blk.get("health_gate") or {}
        lines.append(f"### {key}")
        lines.append(f"- 状态: {blk.get('status')}（{blk.get('reason')}）")
        if gate:
            lines.append(f"- 健康检查: {'PASS' if gate.get('ok') else 'FAIL'} · {gate.get('message')}")
        vsum = blk.get("vision_stage_summary")
        if vsum:
            lines.append(f"- Vision Parse Success: {json.dumps(vsum['vision_extraction_valid'], ensure_ascii=False)}"
                         f" · Evidence Completeness: {json.dumps(vsum['evidence_complete'], ensure_ascii=False)}")
        strat = blk.get("stratified")
        if isinstance(strat, dict):
            for mkey, s in strat.items():
                if not isinstance(s, dict) or "cases" not in s:
                    continue
                lines.append(f"#### {mkey}（n={s['cases']}，执行状态 {s['execution_status']}）")
                for mk in ("vision_parse_success", "evidence_completeness",
                           "extraction_accuracy", "option_map_accuracy",
                           "end_to_end_objective_accuracy",
                           "legacy_answer_accuracy_for_reference_only",
                           "answerable_coverage", "selective_accuracy",
                           "verified_answer_yield", "answered_out_loud",
                           "false_accept", "over_answer", "unverified_abstention",
                           "api_failure_rate", "parse_success"):
                    v = s.get(mk)
                    lines.append(f"- {mk}: {json.dumps(v, ensure_ascii=False)}")
        lines.append("")
    dv = report["experiments"].get("delta_vision", {})
    lines += ["## Δ_vision（与主指标同源）", f"- {dv.get('formula')}",
              f"- auto={dv.get('auto_objective')} · image_only={dv.get('image_only_objective')}"
              f" · Δ={dv.get('delta')}",
              f"- 注意：{dv.get('caveat')}", "", "## 首跑三个关注问题",]
    for qk, qv in report.get("topline_questions", {}).items():
        lines.append(f"- [{qk}] {qv}")
    lines += ["", "## 诚实边界"]
    lines += [f"- {h}" for h in report["honesty"]]
    lines.append(f"- 新盲测：{report['blind_test_plan']['status']}（前提与分层见 experiment.json）")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
