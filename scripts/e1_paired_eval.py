# -*- coding: utf-8 -*-
"""R9 Stage E：独立 Dev 全模块整页截图 ↔ 同题文本 oracle 配对评估（一次运行，预算先声明）。

固定项（运行前声明，运行中不改）：
  样本     = data/benchmark_xingce_dev/dev/xingce_dev.jsonl 全部 26 题（六模块真页截图，
             920px 宽，零缺图——已核验），无抽样无挑选
  模型     = scripts/r61_dsh_env.py 通道（人工裁决指定 glm-5.3-flash，temperature=0）
  prompt   = 生产抽取/路由/规划 prompt（本脚本零修改）
  预算     = LLM_CALL_BUDGET=120（image_only 26 题 × ≤4 调用/题上限），单轮执行
  文本对照 = r9d_dev_fix（oracle_solver，同题同码 llm=None 确定性通道——文本侧上限）

指标（工单 Stage E 口径）：
  OCR 条件完整率   extraction_stem_ok=True 占比（题干关键条件被完整抽出）
  路由准确率       routed_skill==gold skill 占比
  方法检索命中率   solver method/kb 命中金标族占比（可得则算，不可得如实 N/A）
  规划执行成功率   status=solved 占比
  正确释放率       solved∧correct 占比
  错误接受率       solved∧¬correct∧verification_action=accept / 已释放
  净损失           文本 oracle 对而截图错的题集（逐题列出）+ 数量
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
BUDGET = "120"
ORACLE_PRED = None  # 自动定位 r9d_dev_fix


def load_pred(path: Path) -> Dict[str, Dict[str, Any]]:
    return {r["case_id"]: r for r in
            (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())}


def find_latest(split: str, pattern: str) -> Optional[Path]:
    base = ROOT / "results" / split
    cand = [p for p in base.iterdir() if (p / "predictions.jsonl").is_file() and re.search(pattern, p.name)] \
        if base.is_dir() else []
    if not cand:
        return None
    cand.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cand[0] / "predictions.jsonl"


def _matrix_gold_ids() -> set:
    """金标带矩阵布局的 dev 题 id（区域完整率分母）。"""
    out = set()
    for l in (ROOT / "data/benchmark_xingce_dev/dev/xingce_dev.jsonl").read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        lay = r.get("layout")
        if isinstance(lay, str) and lay and lay not in ("text", "text-material"):
            out.add(r["id"])  # 矩阵类金标（table/seq/grid9）
        elif isinstance(lay, dict) and lay:
            out.add(r["id"])
    return out


def _load_usage() -> Dict[str, Any]:
    p = ROOT / "results" / "xingce_dev" / "r9e_dev_imageonly" / "llm_evidence.json"
    if not p.is_file():
        return {}
    ev = json.loads(p.read_text(encoding="utf-8"))
    u, a = ev.get("usage") or {}, ev.get("audit") or {}
    return {"calls_succeeded": u.get("calls"), "requests_sent": a.get("requests_sent"),
            "retries_timeouts": sum(v for k, v in (a.get("error_types") or {}).items()),
            "total_tokens": u.get("total_tokens"), "model": u.get("model")}


def main() -> None:
    # 1) image_only 单轮运行（真实 VLM；产物存在则复用——预算 120，实际 32 成功/36 发送/4 ReadTimeout）
    run_name = "r9e_dev_imageonly"
    env = dict(os.environ)
    env.update({"PYTHONPATH": "src", "PYTHONIOENCODING": "utf-8",
                "ASSESSMENT_R4G_LAYOUT": "off", "LLM_CALL_BUDGET": BUDGET})
    img_pred = find_latest("xingce_dev", re.escape(run_name))
    if img_pred is None:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "scripts/r61_dsh_env.py",
             "-m", "assessment_agent", "eval", "--split", "xingce_dev",
             "--eval-mode", "image_only", "--run-name", run_name, "--no-overwrite"],
            cwd=ROOT, env=env)
        if proc.returncode != 0:
            raise SystemExit(f"image_only 运行失败 exit={proc.returncode}")
        img_pred = find_latest("xingce_dev", re.escape(run_name))
    assert img_pred is not None
    oracle_pred = find_latest("xingce_dev", r"r9d_dev_fix")
    assert oracle_pred is not None, "文本 oracle 基线缺失"

    img = load_pred(img_pred)
    orc = load_pred(oracle_pred)

    n = len(img)
    def pct(num: int, den: int = n):
        return round(num / den, 4) if den else None

    # OCR 条件完整率：题干文本抽取精确（extraction_accurate）；
    # 区域完整率：有矩阵金标的题 region_iou≥0.5（漏检记 0，分母=金标有矩阵题数）
    ocr_full = sum(1 for r in img.values() if r.get("extraction_accurate") is True)
    mat_ids = {cid for cid in img if (img[cid].get("region_iou") is not None)
               or cid in _matrix_gold_ids()}
    region_full = sum(1 for cid in mat_ids
                      if (img[cid].get("region_iou") or 0) >= 0.5)
    usage = _load_usage()
    # 路由准确：与金标 skill 比对——金标从 corpus 读取
    gold = {json.loads(l)["id"]: json.loads(l)["skill"]
            for l in (ROOT / "data/benchmark_xingce_dev/dev/xingce_dev.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()}
    route_ok = sum(1 for cid, r in img.items() if r.get("routed_skill") == gold.get(cid))
    solved = [r for r in img.values() if r.get("status") == "solved"]
    correct = [r for r in solved if r.get("correct")]
    false_accept = [r for r in solved if r.get("correct") is False
                    and r.get("verification_action") == "accept"]
    # 检索命中：method 含族名或 planner 检索 meta；不可得则 N/A
    retrieval_hits = 0
    retrieval_computable = False
    for r in img.values():
        m = (r.get("solver_meta") or {})
        if m.get("methods_retrieved") is not None or m.get("retrieval"):
            retrieval_computable = True
            break
    net_loss = [cid for cid in img
                if orc.get(cid, {}).get("correct") and not img[cid].get("correct")]
    net_gain = [cid for cid in img
                if img[cid].get("correct") and not orc.get(cid, {}).get("correct")]

    report = {
        "budget_declared": {"LLM_CALL_BUDGET": BUDGET, "runs": "single",
                            "model_channel": "scripts/r61_dsh_env.py (glm-5.3-flash, temperature=0)",
                            "prompt": "production (unmodified)"},
        "n_items": n,
        "usage": usage,
        "OCR条件完整率": {"value": pct(ocr_full), "num": ocr_full,
                          "note": "extraction_accurate（题干文本抽取精确），分母=全部 26 题"},
        "区域完整率": {"value": round(region_full / len(mat_ids), 4) if mat_ids else None,
                       "num": region_full, "denominator": len(mat_ids),
                       "note": "金标含矩阵布局的题，region_iou≥0.5 记命中，漏检计 0"},
        "路由准确率": {"value": pct(route_ok), "num": route_ok},
        "方法检索命中率": {"value": "N/A(单跑产物未持久化检索候选清单；如实报告，不造数)"},
        "规划执行成功率": {"value": pct(len(solved)), "num": len(solved)},
        "正确释放率": {"value": pct(len(correct)), "num": len(correct)},
        "错误接受率": {"value": round(len(false_accept) / len(solved), 4) if solved else None,
                       "num": len(false_accept), "ids": sorted(r["case_id"] for r in false_accept)},
        "文本oracle_截图净损失": {"count": len(net_loss), "ids": sorted(net_loss)},
        "截图侧净增益(对照)": {"count": len(net_gain), "ids": sorted(net_gain)},
        "oracle_正确基线": {"count": sum(1 for r in orc.values() if r.get("correct")), "of": len(orc)},
        "逐题": {cid: {"gold": gold.get(cid), "routed": img[cid].get("routed_skill"),
                       "status": img[cid].get("status"), "correct": img[cid].get("correct"),
                       "verify": img[cid].get("verification_action"),
                       "extract_accurate": img[cid].get("extraction_accurate"),
                       "region_iou": img[cid].get("region_iou"),
                       "oracle_correct": orc.get(cid, {}).get("correct")}
                 for cid in sorted(img)},
    }
    out = ROOT / "evidence" / "R9E_PAIRED_EVAL.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "逐题"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
