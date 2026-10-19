"""R4d：真实执行状态的统一判定（smoke 与实验脚本共用的唯一口径）。

设计约束（评审指令）：
- EXECUTED 语义 = **指定样本全部完成真实视觉解析**，而不是"有任意一次 HTTP
  2xx"。requests_succeeded 是客户端总响应数（混有 router/solver/verifier 请求），
  不得直接当视觉成功数——视觉链路成功以每题三段记录为准：
      vision_http_success    —— 该题 vision_parse 阶段有 2xx 响应
      vision_schema_valid    —— 模型输出可解析为合法 JSON（schema 层）
      vision_extraction_valid—— VisionParser 结构化产物可用（区域/裁剪/未致命降级）
- "API 请求成功但 JSON 结构错误"不算视觉解析成功；legacy_payload 降级单独计数。
- mock/注入 transport/离线一律不得 EXECUTED。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

STATUSES = ("NOT_CONFIGURED", "CONFIGURED_NOT_CALLED", "API_FAILED",
            "PARTIAL_EXECUTED", "EXECUTED")


def _rflag(r: Dict[str, Any], key: str) -> bool:
    """每题三段标志：优先顶层字段（实验行），其次 vision 明细 dict（predictions 行）。"""
    v = r.get(key)
    if v is None:
        v = (r.get("vision") or {}).get(key)
    return bool(v)


def vision_stage_summary(case_rows: List[Dict[str, Any]],
                         audit: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """每题三段记录 → 链路健康摘要（Vision Parse Success / Evidence Completeness）。

    R4e P1-1：证据完整分三档分别计数——
    region（区域与选项标签齐，=旧 evidence_complete 口径）/
    structural（图形题再加网格解析与裁剪完整）/
    solver_ready（结构齐且求解器实际可读到落盘裁剪文件）。
    旧键 evidence_complete 保留（值=region 档），不追改历史口径。
    """
    n = len(case_rows)
    http_ok = sum(1 for r in case_rows if _rflag(r, "vision_http_success"))
    schema_ok = sum(1 for r in case_rows if _rflag(r, "vision_schema_valid"))
    extraction_ok = sum(1 for r in case_rows if _rflag(r, "vision_extraction_valid"))
    complete = sum(1 for r in case_rows if _rflag(r, "evidence_complete"))
    structural = sum(1 for r in case_rows if _rflag(r, "structural_evidence_complete"))
    ready = sum(1 for r in case_rows if _rflag(r, "solver_ready"))
    phase = ((audit or {}).get("by_phase") or {}).get("vision_parse", {})
    return {
        "cases": n,
        "vision_http_success": {"numerator": http_ok, "denominator": n},
        "vision_schema_valid": {"numerator": schema_ok, "denominator": n},
        "vision_extraction_valid": {"numerator": extraction_ok, "denominator": n},
        "evidence_complete": {"numerator": complete, "denominator": n},
        "region_evidence_complete": {"numerator": complete, "denominator": n},
        "structural_evidence_complete": {"numerator": structural, "denominator": n},
        "solver_ready": {"numerator": ready, "denominator": n},
        "phase_audit": dict(phase),
        "note": "schema 合法但证据不完整（漏选项/九宫格识别成一行）不计入 evidence_complete；"
                "R4e 起区域/结构/solver_ready 三档分别报告（区域档沿用旧口径）",
    }


def classify_status(configured: bool, offline: bool,
                    audit: Optional[Dict[str, Any]],
                    case_rows: List[Dict[str, Any]],
                    expected_cases: int) -> Tuple[str, str]:
    """五态判定唯一实现。case_rows = 每题三段记录（vision_stage_summary 的输入）。"""
    if not configured or offline:
        return ("NOT_CONFIGURED",
                "未配置 LLM_API_KEY/LLM_VISION_MODEL，或 ASSESSMENT_OFFLINE=1 关闭了网络")
    if audit is None:
        return ("CONFIGURED_NOT_CALLED", "评测未产出 llm_evidence.json：无任何调用凭证")
    if audit.get("mock_or_injected_transport"):
        return ("CONFIGURED_NOT_CALLED",
                "检测到注入 transport/mock 客户端：不构成真实模型执行证据")
    sent = int(audit.get("requests_sent", 0))
    if sent == 0:
        return ("CONFIGURED_NOT_CALLED", "配置存在但本次评测未发出任何 HTTP 请求")
    vision = (audit.get("by_phase") or {}).get("vision_parse") or {}
    v_http = int(vision.get("http_succeeded", 0))
    v_schema = int(vision.get("json_output_valid", 0))
    v_struct = sum(1 for r in case_rows if _rflag(r, "vision_extraction_valid"))
    if v_http == 0:
        return ("API_FAILED",
                f"已发起 {sent} 次真实请求但视觉阶段 0 次 2xx"
                f"（错误类型 {audit.get('error_types')}，"
                f"状态码 {audit.get('http_status_counts')}）")
    if v_struct >= expected_cases and v_schema >= expected_cases:
        return ("EXECUTED",
                f"{expected_cases}/{expected_cases} 题完成真实视觉解析"
                f"（vision 2xx {v_http}，schema 合法 {v_schema}，结构化可用 {v_struct}，"
                f"回显模型 {audit.get('echoed_models')}）")
    if v_http < expected_cases:
        return ("PARTIAL_EXECUTED",
                f"视觉请求仅 {v_http}/{expected_cases} 题获得 2xx；"
                f"schema 合法 {v_schema}，结构化可用 {v_struct}——全失败计数保留在分母")
    # HTTP 全成功但 schema/结构化未满额：这是"链路连通但未完成真实解析"，
    # 评审明确要求不得报 EXECUTED
    return ("PARTIAL_EXECUTED",
            f"vision HTTP 成功 {v_http}/{expected_cases}，但 schema 合法 {v_schema}、"
            f"结构化可用 {v_struct}——API 成功不等于视觉解析成功")


def health_gate(status: str, stage_summary: Dict[str, Any]) -> Tuple[bool, str]:
    """分阶段熔断的健康判据：至少 1 题完成三段全通的真实视觉解析才允许扩大。
    零样本分母（本阶段根本没有产出）= 不可判定，同样熔断——不得把空跑当健康。"""
    denom = (stage_summary.get("vision_extraction_valid") or {}).get("denominator", 0)
    num = (stage_summary.get("vision_extraction_valid") or {}).get("numerator", 0)
    if denom == 0:
        return False, f"熔断：{status}（本阶段视觉三段记录分母为 0，无法证明链路健康）"
    ok = (status in ("EXECUTED", "PARTIAL_EXECUTED") and num >= 1)
    if ok:
        return True, f"链路健康：{status}，至少 1 题结构化解析可用，允许扩大样本"
    return False, (f"熔断：{status}（"
                   f"vision_http {stage_summary.get('vision_http_success')}，"
                   f"schema {stage_summary.get('vision_schema_valid')}，"
                   f"结构化 {stage_summary.get('vision_extraction_valid')}）"
                   "——后续阶段不发起，避免重复失败调用与费用")
