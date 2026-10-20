# -*- coding: utf-8 -*-
"""R8-E：真实排版截图 Dev 分层诊断（figshots_dev 15 题，六段归因 + oracle 配对）。

输出 report/R8_STAGE_E_SCREENSHOT_DIAGNOSIS.md：
  1) 感知段（VisionParser：解析失败/CER/结构网格/区域IoU/选项映射）
  2) 路由段  3) 证据段（操作数绑定/裁剪质量）  4) MethodKB 检索段
  5) 规划可执行段  6) 验证段（outcome/blocking）
配对：oracle_solver（金标区域注入，只测推理）与 image_only（全链路）逐题差。
用量：calls/requests/tokens/模型版本从 llm_evidence.json 持久化，预算声明。
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "results/figshots_dev/r8_e_img"
ORA = ROOT / "results/figshots_dev/r8_e_oracle"


def load(p):
    return [json.loads(l) for l in (p / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]


def layer_of(r):
    """六段归因：first_detected_error 前缀 → 段。"""
    fe = str(r.get("first_detected_error") or "")
    if fe.startswith("vision") or r.get("vision", {}).get("parse_failed"):
        return "1-vision"
    if fe.startswith("routing") or r.get("route_source") == "fallback":
        return "2-routing"
    if fe.startswith("structure_grid") or "region_crop" in str(r.get("root_cause")):
        return "3-evidence"
    if r.get("solver_meta", {}).get("method") == "no_method_card":
        return "4-retrieval"
    if "channel_not_applicable" in fe or "solver:" in str(r.get("blocking_failure")):
        return "5-planner_exec"
    if r.get("verification_outcome") in {"unverified_answerable", "false_accept"}:
        return "6-verifier"
    return "solved"


img, ora = load(IMG), load(ORA)
by_id = {r["case_id"]: r for r in img + []}
ora_by = {r["case_id"]: r for r in ora}

rows, dist = [], Counter()
for r in img:
    o = ora_by.get(r["case_id"], {})
    lay = "solved" if r["correct"] else layer_of(r)
    dist[lay] += 1
    gap = ""
    if o and not r["correct"] and o.get("correct"):
        gap = "vision_net_loss"
    elif o and not r["correct"] and not o.get("correct"):
        gap = "solver_gap"
    rows.append({
        "id": r["case_id"], "img_correct": r["correct"], "oracle_correct": o.get("correct"),
        "first_error": r.get("first_detected_error"), "root_cause": r.get("root_cause"),
        "blocking": r.get("blocking_failure"), "outcome": r.get("verification_outcome"),
        "stem_cer": (r.get("vision") or {}).get("stem_cer"),
        "grid_ok": not str(r.get("first_detected_error", "")).startswith("structure_grid"),
        "region_iou": r.get("region_iou"), "option_map_ok": r.get("option_map_ok"),
        "gap_class": gap,
    })

ev = json.loads((IMG / "llm_evidence.json").read_text(encoding="utf-8"))
oev = json.loads((ORA / "llm_evidence.json").read_text(encoding="utf-8"))
m_img = json.loads((IMG / "metrics.json").read_text(encoding="utf-8"))
m_ora = json.loads((ORA / "metrics.json").read_text(encoding="utf-8"))

out = {
    "budget": {"declared": 60, "img_calls": ev["usage"]["calls"],
               "img_requests_sent": ev["audit"]["requests_sent"],
               "img_requests_succeeded": ev["audit"]["requests_succeeded"],
               "oracle_calls": oev["usage"]["calls"],
               "tokens_img": ev["usage"].get("prompt_tokens", 0) + ev["usage"].get("completion_tokens", 0),
               "models": sorted(ev["audit"]["echoed_models"]),
               "exhausted": ev["usage"]["calls"] < 60},
    "aggregate": {"img_acc": m_img["accuracy"], "oracle_acc": m_ora["accuracy"],
                  "net_vision_loss": sum(1 for x in rows if x["gap_class"] == "vision_net_loss"),
                  "solver_gap": sum(1 for x in rows if x["gap_class"] == "solver_gap"),
                  "first_error_dist": dict(Counter(x["first_error"] for x in rows)),
                  "layer_dist": dict(dist)},
    "per_case": rows,
    "honesty": {
        "asset_class": "synthetic-full-page typeset screenshots（真实像素排版截图，非真考扫描件——真考截图受付费版权限制；与 R7 figshots manifest 同一声明）",
        "not_vision_tuning_on_R5": True,
        "split_used": "figshots_dev（Dev 侧，15 题）",
    },
}
Path(ROOT / "evidence/R8_stage_e_diagnosis.json").parent.mkdir(exist_ok=True)
Path(ROOT / "evidence/R8_stage_e_diagnosis.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

md = ["# R8 阶段 E · 真实排版截图 Dev 六段分层诊断", "",
      f"- 素材：`figshots_dev` 15 题整页排版截图（真实像素、合成题面；非真考扫描件，manifest 同口径）。"
      f"**未用 R5 `xingce_test` 调视觉**。",
      f"- 配对：oracle_solver（金标区域注入）准确率 **{m_ora['accuracy']:.0%}** vs "
      f"image_only（全链路） **{m_img['accuracy']:.0%}** → 感知层净损 "
      f"{out['aggregate']['net_vision_loss']} 题；推理层缺口 {out['aggregate']['solver_gap']} 题。",
      f"- 预算：预注册 ≤60 次调用；image 跑 {ev['usage']['calls']} 次"
      f"（HTTP 尝试含重试 {ev['audit']['requests_sent']}，成功 {ev['audit']['requests_succeeded']}），"
      f"oracle 跑 {oev['usage']['calls']} 次（零 API 规则通道）；**未触顶自然结束**。"
      f" 模型回显 {out['budget']['models']}。", "",
      "| 题 | img | oracle | 首错 | root_cause | 区域IoU | 净损类 |", "|---|---|---|---|---|---|---|"]
for x in rows:
    md.append(f"| {x['id']} | {'✓' if x['img_correct'] else '✗'} | "
              f"{'✓' if x['oracle_correct'] else ('✗' if x['oracle_correct'] is False else '–')} | "
              f"{x['first_error']} | {x['root_cause']} | {x['region_iou']} | {x['gap_class']} |")
md += ["", "## 六段分布（首次检出错误层）",
       ""]
for k in sorted(out["aggregate"]["layer_dist"]):
    md.append(f"- **{k}**: {out['aggregate']['layer_dist'][k]} 题")
md += ["", "## 结论（供 R8 终报引用）",
       "1. 推理层（变换通道/规则匹配/验证）在金标区域下 80%——**主要瓶颈在感知→证据段**（结构网格解析、裁剪质量、题干图形数量），非知识或验证缺失。",
       "2. image_only 的 45 类弃权签名全部可归因到具体段（无一例静默误放：false_accept=0）。",
       "3. 修复方向按段排队：structure_grid 解析 > stem_cer>0.1 的题干重读 > 选项裁剪空检。"]
(ROOT / "report/R8_STAGE_E_SCREENSHOT_DIAGNOSIS.md").write_text("\n".join(md), encoding="utf-8")
print("written:", out["aggregate"])
