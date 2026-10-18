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

from phase6_vlm_smoke import classify_status  # noqa: E402

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


def lock_manifest() -> Dict[str, Any]:
    from assessment_agent.config import Settings
    from assessment_agent.prompts import VISION_PARSE_SYSTEM
    st = Settings.from_env()
    images = sorted((ROOT / "data/benchmark_figshots/images/dev").glob("*.png"))
    return {
        "locked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "git_commit": sh("git", "rev-parse", "HEAD"),
        "git_dirty": bool(sh("git", "status", "--porcelain")),
        "dirty_digest_sha256": hashlib.sha256(
            sh("git", "status", "--porcelain").encode("utf-8")).hexdigest()[:16],
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
            "smoke": SMOKE_IDS,
            "full": "figshots_dev 全部 15 题（数据集行序）",
            "cross_split": "bench_dev 全部截图样本（不按 skills 文件筛、更不按解析结果筛）；"
                           "题型对比在报告内按 skill 字段分组",
        },
        "note": "密钥不落盘；本 manifest 是实验配置唯一凭证",
    }


def sha_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def run_eval(split: str, mode: str, run_name: str, *, skills: Optional[str] = None,
             case_ids: Optional[str] = None, direct: bool = False) -> Dict[str, Any]:
    """direct=True 时进程内调用 runner（跨 split 目录基准需要 runner 自带的
    _SPLIT_DIRS 映射，CLI 无 --data-dir；同一解释器、同一代码态，凭证一致）。"""
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
           "oracle" if mode == "oracle_solver" else "auto", "--run-name", run_name]
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
    keys = ("LLM_API_KEY", "LLM_MODEL", "LLM_VISION_MODEL", "LLM_BASE_URL",
            "ASSESSMENT_OFFLINE",
            "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP")
    # ASSESSMENT_OFFLINE 透传：离线开关必须与主 manifest 同一事实源；审计的
    # offline_mode/requests_sent 字段负责区分"被离线拦截"与"配置存在但未调用"。
    return {k: v for k, v in os.environ.items() if k in keys and v is not None}


def attribute_case(row: Dict[str, Any]) -> Dict[str, Any]:
    """逐题 11 字段归因 + 错误类型（优先级取第一个命中）。

    字段来源是 predictions.jsonl 的 ``vision`` 明细（compare_extraction_detail，
    R2b 起内容级、R4c 补 grid/裁剪），全部保留在分母中，不按结果筛题。
    """
    v = row.get("vision") or {}
    api_failed = row.get("failure_class") in ("model_api_error", "llm_unavailable")
    parse_failed = bool(v.get("parse_failed"))
    perception_dead = api_failed or parse_failed or not v
    gold = row.get("expected")
    answered = row.get("predicted_option") is not None
    verified_accept = row.get("verification_action") == "accept"
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
        "verifier_accepted": verified_accept if answered else None,
        "answered_out_loud": answered,
        "correct": row.get("correct"),
        "failure_class": row.get("failure_class"),
        "verification_action": row.get("verification_action"),
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
    elif answered and verified_accept and row.get("correct") is False:
        err = "verifier_error" if fields["solver_proposed_gold"] is False else "solver_error"
    elif answered and not verified_accept and row.get("correct") is False:
        err = "solver_error"
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
    其余情形返回 -1 不参与排序。"""
    if row.get("correct") is not False or row.get("verification_action") != "accept":
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
    if row.get("predicted_option") != row.get("expected"):
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
    """模式感知的执行状态：oracle/auto 是离线可完全执行的 Solver 层模式；
    image_only 必须看真实调用审计——凭证缺失或 API 全失败不得标 EXECUTED。"""
    if not preds:
        return "NOT_EXECUTED"
    if mode != "image_only":
        return "EXECUTED"
    a = (audit or {}).get("audit") or {}
    if not a.get("requests_sent"):
        return "NOT_EXECUTED（真实调用凭证缺失）"
    if not a.get("requests_succeeded"):
        return "API_FAILED（全部请求失败）"
    return "EXECUTED"


def stratified(run: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """分层指标：分子/分母齐备；未执行=None、缺失="missing"、真零=0。"""
    preds = run.get("predictions") or []
    m = run.get("metrics") or {}
    n = len(preds)
    def frac(hit, total):
        return {"numerator": hit, "denominator": total,
                "value": (round(hit / total, 4) if total else None)}
    def count(pred_fn):
        return [p for p in preds if pred_fn(p)]
    answered = count(lambda p: p.get("predicted_option") is not None)
    api_fail = count(lambda p: p.get("failure_class") in ("model_api_error", "llm_unavailable"))
    parsed = count(lambda p: bool(p.get("vision")) and not p["vision"].get("parse_failed"))
    with_vision = count(lambda p: bool(p.get("vision")))
    def ratio_on(key, cmp_fn, denom):
        hits = count(lambda p: p.get(key) is not None and cmp_fn(p[key]))
        return frac(len(hits), len(denom))
    ious = [p["vision"].get("diagram_iou_mean") for p in with_vision
            if isinstance(p["vision"].get("diagram_iou_mean"), (int, float))]
    oious = [p["vision"].get("option_iou_mean") for p in with_vision
             if isinstance(p["vision"].get("option_iou_mean"), (int, float))]
    omaps = count(lambda p: p.get("option_map_ok") is True)
    omaps_den = count(lambda p: p.get("option_map_ok") is not None)
    lat = [p.get("latency_ms") for p in preds if isinstance(p.get("latency_ms"), (int, float))]
    cap = m.get("capability") or {}
    obj = m.get("answer_accuracy")
    return {
        "mode": mode,
        "cases": n,
        "execution_status": _mode_execution(mode, preds, run.get("audit")),
        "per_skill": _group_by_skill(preds),
        "extraction_accuracy": (ratio_on_stem := frac(
            sum(1 for p in with_vision if p["vision"].get("stem_exact")), len(with_vision))
            if with_vision else "not_applicable"),
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
        "end_to_end_objective_accuracy": ({"value": obj,
            "numerator": (m.get("overall_answer_accuracy") or {}).get("hit"),
            "denominator": (m.get("overall_answer_accuracy") or {}).get("denominator")}
            if obj is not None else "missing"),
        "answerable_coverage": m.get("answerable_coverage"),
        "selective_accuracy": m.get("selective_accuracy"),
        "false_accept": cap.get("false_accept_total", 0),
        "over_answer": cap.get("over_answer_total", 0),
        "unverified_abstention": m.get("abstention_total", 0),
        "answered_out_loud": frac(len(answered), n),
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
        if p.get("predicted_option") is not None:
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
    manifest = lock_manifest()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    exp_dir = ROOT / "results" / "real_vlm" / ts
    exp_dir.mkdir(parents=True, exist_ok=True)

    configured = manifest["api_key_present"] and manifest["vision_model"] and not manifest["offline_flag"]

    experiments: Dict[str, Any] = {}
    audits: List[Dict[str, Any]] = []
    # 1) 冒烟（3 题）→ 全量（15 题）× 三模式
    for mode in ("oracle_solver", "auto_router", "image_only"):
        smoke = run_eval(FIGSPLITS, mode, f"smoke_{mode}", case_ids=",".join(SMOKE_IDS))
        full = run_eval(FIGSPLITS, mode, f"full_{mode}")
        experiments[f"figshots_{mode}"] = {"smoke": _slim(smoke), "full": _slim(full)}
        if smoke.get("audit"):
            audits.append(smoke["audit"].get("audit") or {})
        if full.get("audit"):
            audits.append(full["audit"].get("audit") or {})
        experiments[f"figshots_{mode}"]["stratified_smoke"] = stratified(smoke, mode) if "predictions" in smoke else {"error": smoke.get("error")}
        experiments[f"figshots_{mode}"]["stratified_full"] = stratified(full, mode) if "predictions" in full else {"error": full.get("error")}
        # 归因明细单独大文件（不进主报告以控体积）
        if full.get("predictions"):
            att = [attribute_case(r) for r in full["predictions"]]
            (exp_dir / f"attribution_figshots_{mode}.json").write_text(
                json.dumps(att, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 2) 跨题型感知（不筛样本）。split→目录映射：bench_dev 的截图独立集在
    #    benchmark_real/dev（--data-dir 必须显式指定，默认 data_dir 会错误拼到 data/dev）
    cross = run_eval("bench_dev", "image_only", "cross_bench", direct=True)
    # 不按题型文件筛样本：bench_dev 全部截图题都进入真实 VLM 感知评测，
    # 资料分析/数学/图形/言语子集的对比在报告里按 skill 字段分组呈现
    if "error" in cross:
        experiments["cross_skill_image_only"] = {"error": cross["error"],
                                                 "stratified": None}
    else:
        experiments["cross_skill_image_only"] = {"run": _slim(cross)}
        if cross.get("predictions"):
            skill_by_id = {p["case_id"]: str(p.get("skill") or "?")
                           for p in cross["predictions"]}
            att = [attribute_case(r) for r in cross["predictions"]]
            (exp_dir / "attribution_cross_bench_image_only.json").write_text(
                json.dumps(att, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            by_skill: Dict[str, Dict[str, int]] = {}
            for a in att:
                g = by_skill.setdefault(skill_by_id.get(a["case_id"], "?"), {})
                g["cases"] = g.get("cases", 0) + 1
                if a["error_type"]:
                    g[a["error_type"]] = g.get(a["error_type"], 0) + 1
            experiments["cross_skill_image_only"]["stratified"] = stratified(cross, "image_only")
            experiments["cross_skill_image_only"]["per_skill"] = by_skill
        else:
            experiments["cross_skill_image_only"]["stratified"] = None

    # 3) Δ_vision 初步指标（auto − image_only，客观口径；逐题归因另行核对）
    def objv(key):
        s = experiments.get(key, {}).get("stratified_full", {})
        v = s.get("end_to_end_objective_accuracy")
        if isinstance(v, dict):  # 分层块存的是 {value,numerator,denominator}
            return v.get("value") if isinstance(v.get("value"), (int, float)) else None
        return v if isinstance(v, (int, float)) else None
    dv = {
        "formula": "Δ_vision = Acc(auto_router) − Acc(image_only)，同一数据集",
        "auto_objective": objv("figshots_auto_router"),
        "image_only_objective": objv("figshots_image_only"),
        "delta": None,
        "valid_only_with_real_vlm": True,
        "caveat": "本次 image_only 真实调用凭证缺失：其 0.0 是**未执行**而非能力，"
                  "Δ 数值无意义，必须重跑带真实 VLM 后才能解读；视觉错误还会级联"
                  "改变路由与求解过程，逐题归因见 attribution_*.json",
    }
    io_exec = "EXECUTED" == str(experiments.get("figshots_image_only", {})
                                 .get("stratified_full", {}).get("execution_status", ""))
    if io_exec and dv["auto_objective"] is not None and dv["image_only_objective"] is not None:
        dv["delta"] = round(dv["auto_objective"] - dv["image_only_objective"], 4)
        dv["valid_only_with_real_vlm"] = False
    else:
        dv["delta"] = "not_meaningful（image_only 未真实执行）"
    experiments["delta_vision"] = dv

    # 4) 执行状态（以审计计数判定，退出码不算证据）
    sent = sum(a.get("requests_sent", 0) for a in audits)
    succ = sum(a.get("requests_succeeded", 0) for a in audits)
    audit_merged = {
        "requests_sent": sent, "requests_succeeded": succ,
        "error_types": {}, "echoed_models": sorted({m for a in audits for m in (a.get("echoed_models") or [])}),
        "mock_or_injected_transport": any(a.get("mock_or_injected_transport") for a in audits),
    }
    io_full = experiments.get("figshots_image_only", {}).get("stratified_full", {})
    parse_ok = ((io_full.get("parse_success") or {}).get("numerator", 0)
                if isinstance(io_full.get("parse_success"), dict) else 0)
    # 有效样本数 = 实际跑了多少题（冒烟阶段可能只有部分集，选择规则已预先声明）
    n_ran = io_full.get("cases") or 0
    status, reason = classify_status(configured, manifest["offline_flag"],
                                     audit_merged if sent else None, parse_ok, n_ran or 15)

    report = {
        "experiment": "R4c real-VLM end-to-end with layered attribution",
        "manifest": manifest,
        "status": status,
        "status_reason": reason,
        "audit_merged": audit_merged,
        "experiments": experiments,
        "blind_test_plan": {
            "status": "PLANNED_NOT_BUILT",
            "precondition": "真实模型 Dev 实验完成并冻结模型与算法配置后构建",
            "layers": ["自编程序合成页面", "独立人工编写页面", "授权真实页面截图（当前缺失，不得以合成替代）"],
            "non_sharing": "不与开发集共享模板、图形原型、表格内容或选项排列",
        },
        "honesty": [
            "小调用量不得宣称已证明真实部署的误放概率",
            "所有失败保留在分母中；未执行(None)/缺失(missing)/零值(0)三态区分",
            "oracle/auto/image_only 同题对比的逐题差异必须人工核对归因后才能解读",
        ],
    }
    (exp_dir / "experiment.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_md(exp_dir / "report.md", report)
    print(json.dumps({"status": status, "reason": reason, "dir": str(exp_dir)},
                     ensure_ascii=False))


def _slim(run: Dict[str, Any]) -> Dict[str, Any]:
    if "error" in run:
        return {"error": run["error"]}
    return {k: run.get(k) for k in ("output_dir", "eval_batch")} | {
        "metrics": run.get("metrics"), "audit": run.get("audit"),
        "n_predictions": len(run.get("predictions") or [])}


def _write_md(path: Path, report: Dict[str, Any]) -> None:
    lines = [f"# R4c 真实 VLM 端到端实验报告",
             f"- 状态：**{report['status']}**（{report['status_reason']}）",
             f"- commit: {report['manifest']['git_commit']}"
             f"（dirty={report['manifest']['git_dirty']}）",
             f"- provider: {report['manifest']['provider_host']} / "
             f"vision: {report['manifest']['vision_model']} / "
             f"temp: {report['manifest']['temperature']} / "
             f"prompt sha: {report['manifest']['vision_prompt_sha256'][:16]}",
             "", "## 分层指标", ""]
    for key, blk in report["experiments"].items():
        if key == "delta_vision" or not isinstance(blk, dict):
            continue
        for tag in ("stratified_smoke", "stratified_full", "stratified"):
            s = blk.get(tag)
            if isinstance(s, dict) and "cases" in s:
                lines.append(f"### {key} · {tag}（n={s['cases']}）")
                for mk in ("extraction_accuracy", "option_map_accuracy",
                           "end_to_end_objective_accuracy", "answerable_coverage",
                           "selective_accuracy", "false_accept", "over_answer",
                           "unverified_abstention", "api_failure_rate", "parse_success"):
                    v = s.get(mk)
                    lines.append(f"- {mk}: {json.dumps(v, ensure_ascii=False)}")
                lines.append("")
    dv = report["experiments"].get("delta_vision", {})
    lines += ["## Δ_vision（初步）", f"- {dv.get('formula')}",
              f"- auto={dv.get('auto_objective')} · image_only={dv.get('image_only_objective')}"
              f" · Δ={dv.get('delta')}",
              f"- 注意：{dv.get('caveat')}", "", "## 诚实边界"]
    lines += [f"- {h}" for h in report["honesty"]]
    lines.append(f"- 新盲测：{report['blind_test_plan']['status']}（前提与分层见 experiment.json）")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
