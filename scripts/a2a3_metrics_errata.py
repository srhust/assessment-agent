# -*- coding: utf-8 -*-
"""R10-A2/A3：统一指标分母勘误 + OCR 双指标拆分（零 API，只读历史产物，不改原件）。

勘误规则：
1. 所有"可答题"指标的分母 = 金标 answerability == has_unique_answer 的题集（非 tag、非 status 推断）。
2. 不可问题集（undecidable / insufficient_conditions）单列"正确弃权块"：
   未释放答案=弃权正确；释放=弃权失守（错误接受的一种，单独计数）。
3. OCR 拆为两个指标：
   - 文字精确匹配率 extraction_text_exact：抽取题干与金标题面规范化后全等；
   - 关键条件完整率 key_conditions_complete：金标题面全部数值条件 + 选项映射齐备
     （源自 runs/e2e-r9e_*/extraction.json 与预测行 option_map_ok）。
输出 evidence/R10A_METRICS_ERRATA.{json,md}；R9 原产物保持原样。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]

ARMS: List[tuple] = [
    ("xingce_dev", "rules_only", r"r9[cdef]?_xingce_dev_rules_only|rules_only"),
    ("xingce_dev", "rules+templates", r"r9d2_dev_fix"),
    ("xingce_dev", "rules+planner", r"rules_planner"),
    ("xingce_dev", "image_only(R9E单跑)", r"r9e_dev_imageonly"),
    ("xingce_dev_variants", "rules_only", r"rules_only"),
    ("xingce_dev_variants", "rules+templates", r"r9d2_variants_fix"),
    ("xingce_dev_variants", "rules+planner", r"rules_planner"),
]


def latest_pred(split: str, pattern: str) -> Path:
    base = ROOT / "results" / split
    cand = [p for p in base.iterdir() if (p / "predictions.jsonl").is_file() and re.search(pattern, p.name)] \
        if base.is_dir() else []
    assert cand, f"缺产物 {split}/{pattern}"
    cand.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cand[0] / "predictions.jsonl"


def norm(stem: str) -> str:
    return re.sub(r"\s+", "", (stem or ""))


def key_conditions_ok(row: Dict[str, Any], gold: Dict[str, Any]) -> bool:
    """关键条件完整 = 金标全部数值条件出现在抽取题干 + 选项映射正确。"""
    ex_stem = row.get("extracted_stem")
    if ex_stem is None:
        rp = ROOT / "runs" / f"e2e-r9e_dev_imageonly-{row['case_id']}" / "extraction.json"
        if rp.is_file():
            try:
                ex_stem = json.loads(rp.read_text(encoding="utf-8")).get("stem")
            except Exception:
                ex_stem = None
    if not isinstance(ex_stem, str):
        return False
    gold_nums = set(re.findall(r"\d+(?:\.\d+)?", norm(gold["stem"])))
    got_nums = set(re.findall(r"\d+(?:\.\d+)?", norm(ex_stem)))
    return gold_nums.issubset(got_nums) and bool(row.get("option_map_ok"))


def main() -> None:
    gold = {json.loads(l)["id"]: json.loads(l)
            for f in ("data/benchmark_xingce_dev/dev/xingce_dev.jsonl",
                      "data/benchmark_xingce_dev/variants/xingce_dev_variants.jsonl")
            for l in (ROOT / f).read_text(encoding="utf-8").splitlines() if l.strip()}
    out: Dict[str, Any] = {"corpora": {}}
    for split, arm, pat in ARMS:
        rows = [json.loads(l) for l in latest_pred(split, pat).read_text(encoding="utf-8").splitlines() if l.strip()]
        # 工单要求：从金标 answerability 统一计算——金标优先，预测行仅兜底
        def av(r):
            g = gold.get(r["case_id"], {}).get("answerability")
            return g or r.get("answerability") or "has_unique_answer"
        ans = [r for r in rows if av(r) == "has_unique_answer"]
        non = [r for r in rows if av(r) != "has_unique_answer"]
        for r in rows:
            r["_av"] = av(r)
        solved = [r for r in ans if r.get("status") == "solved"]
        correct = [r for r in solved if r.get("correct")]
        fa_ans = [r for r in solved if not r.get("correct") and r.get("verification_action") == "accept"]
        breach = [r for r in non if r.get("status") == "solved" and r.get("verification_action") == "accept"]
        abstain_ok = len(non) - len(breach)
        m: Dict[str, Any] = {
            "n": len(rows), "n_answerable": len(ans), "n_unanswerable": len(non),
            "可答正确释放率": round(len(correct) / len(ans), 4) if ans else None,
            "候选覆盖率": round(len(solved) / len(ans), 4) if ans else None,
            "候选正确率": round(len(correct) / len(solved), 4) if solved else None,
            "错误接受数_可答": len(fa_ans),
            "错误接受_ids": sorted(r["case_id"] for r in fa_ans),
            "弃权失守数": len(breach),
            "正确弃权数": abstain_ok,
            "各题首失败阶段": _stages(rows),
        }
        if split == "xingce_dev" and "image_only" in arm:
            exact = sum(1 for r in rows if r.get("extraction_accurate") is True)
            kc = sum(1 for r in rows if key_conditions_ok(r, gold.get(r["case_id"], {})))
            m["OCR_文字精确匹配率"] = {"value": round(exact / len(rows), 4), "num": exact}
            m["OCR_关键条件完整率"] = {"value": round(kc / len(rows), 4), "num": kc}
            m["usage"] = json.loads((ROOT / "results/xingce_dev/r9e_dev_imageonly/llm_evidence.json")
                                    .read_text(encoding="utf-8")).get("usage")
        out["corpora"].setdefault(split, {})[arm] = m
    out["rule"] = ("分母=金标 answerability==has_unique_answer；不可答单列弃权块；"
                   "文字精确匹配≠关键条件完整（后者允许非逐字但数值条件齐备）")
    (ROOT / "evidence" / "R10A_METRICS_ERRATA.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# R10-A2/A3 指标勘误（原件未动，规则：" + out["rule"] + "）", ""]
    for split, arms in out["corpora"].items():
        lines.append(f"## {split}")
        lines.append("| 臂 | 可答n | 正确释放率 | 覆盖 | 精度 | 错接(可答) | 弃权正确/失守 |")
        lines.append("|---|---|---|---|---|---|---|")
        for a, m in arms.items():
            lines.append(f"| {a} | {m['n_answerable']} | {m['可答正确释放率']} | {m['候选覆盖率']} "
                         f"| {m['候选正确率']} | {m['错误接受数_可答']} | {m['正确弃权数']}/{m['弃权失守数']} |")
        io = arms.get("image_only(R9E单跑)")
        if io:
            lines.append(f"\nimage_only：文字精确匹配 {io['OCR_文字精确匹配率']['value']}"
                         f"（{io['OCR_文字精确匹配率']['num']}/26） vs "
                         f"关键条件完整 {io['OCR_关键条件完整率']['value']}"
                         f"（{io['OCR_关键条件完整率']['num']}/26）")
        lines.append("")
    (ROOT / "evidence" / "R10A_METRICS_ERRATA.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def _stages(rows):
    st: Dict[str, int] = {}
    for r in rows:
        if r.get("_av") != "has_unique_answer":
            k = "abstain_breach" if (r.get("status") == "solved" and r.get("verification_action") == "accept") else "abstain_ok"
        elif r.get("correct") and r.get("status") == "solved":
            k = "none"
        elif r.get("status") != "solved":
            k = "solve:" + str(r.get("failure_reason") or "")[:24]
        elif r.get("verification_action") == "accept" and not r.get("correct"):
            k = "release:false_accept"
        elif r.get("verification_action") != "accept":
            k = "verify:" + str(r.get("verification_action"))
        else:
            k = "other"
        st[k] = st.get(k, 0) + 1
    return st


if __name__ == "__main__":
    main()
