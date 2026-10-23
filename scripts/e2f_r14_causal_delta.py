"""R14-C 尺度/pitch 因果实验：提交件 vs **现行 HEAD 重跑件** 的逐条差异分析。

起因：补集复现核对（`r14_semantic_repro.py`）在修正"比较发生在恢复之后"这一
**恒真比较**缺陷后，报出 `R14C_PITCH_ABLATION.json` 有 **9 处结论级差异**。
本脚本不掩盖也不重跑覆盖：把两份件（提交件 + `--out` 新名重跑件）并排算出
人工锁定结论的**两条判据**在两边各自是否成立：

  J1 **pitch 不变性**：同一页内 A_old_maxink / B_center_pitch / C_gold 三臂
     的 `solver_status` 是否一致（一致 ⇒ pitch 不是该页的因果变量）
  J2 **尺度协议效应**：同一臂加 `__legacy_scale` 后是否由 solved 变 failed
     （变 ⇒ 尺度协议是该页失败的充分原因）

输出 `R14C_PITCH_ABLATION_HEAD_DELTA.json` / `.md`（**新文件名**，绝不覆盖
提交件；提交件与 `RERUN2` 均保持原字节）。零 HTTP。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"
ARMS = ["A_old_maxink", "B_center_pitch", "C_gold"]


def head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()[:9]
    except Exception:  # noqa: BLE001
        return "unknown"


def arms_of(item_rows, suffix=""):
    r = {}
    for arm in ARMS:
        row = item_rows.get(arm + suffix) or {}
        cs = row.get("candidates") or []
        top = max(cs, key=lambda c: c.get("score", 0)) if cs else {}
        r[arm] = {"status": row.get("solver_status"),
                  "answer": row.get("final_answer"),
                  "margin": top.get("margin"),
                  "top_score": round(float(top.get("score", 0) or 0), 4)}
    return r


def analyze(art):
    per = {}
    for item, d in (art.get("items") or {}).items():
        rows = d.get("rows") or {}
        proto, legacy = arms_of(rows), arms_of(rows, "__legacy_scale")
        st_p = {a["status"] for a in proto.values()}
        st_l = {a["status"] for a in legacy.values()}
        j1 = len(st_p) == 1                       # 页内三臂同结论
        j2 = (st_p == {"solved"} and st_l == {"failed"})   # 尺度惩罚单独致败
        per[item] = {
            "label": d.get("label"),
            "protocol": proto, "legacy_scale": legacy,
            "J1_pitch_invariant": j1, "J2_scale_protocol_sufficient": j2,
        }
    return per


def main() -> int:
    committed_p = EV / "R14C_PITCH_ABLATION.json"
    rerun_p = EV / "R14C_PITCH_ABLATION_RERUN2.json"
    if not (committed_p.is_file() and rerun_p.is_file()):
        print("缺比较对象", committed_p.is_file(), rerun_p.is_file())
        return 2
    A = json.loads(committed_p.read_text(encoding="utf-8"))
    B = json.loads(rerun_p.read_text(encoding="utf-8"))
    pa, pb = analyze(A), analyze(B)

    rows = []
    for item in sorted(set(pa) | set(pb)):
        x, y = pa.get(item, {}), pb.get(item, {})
        flips = [a for a in ARMS
                 if (x.get("protocol", {}).get(a, {}).get("status")
                     != y.get("protocol", {}).get(a, {}).get("status"))
                 or (x.get("legacy_scale", {}).get(a, {}).get("status")
                     != y.get("legacy_scale", {}).get(a, {}).get("status"))]
        margins = {a: (x.get("protocol", {}).get(a, {}).get("margin"),
                       y.get("protocol", {}).get(a, {}).get("margin"))
                   for a in ARMS}
        rows.append({
            "item": item,
            "label_committed": x.get("label"), "label_head": y.get("label"),
            "J1_pitch_invariant_committed": x.get("J1_pitch_invariant"),
            "J1_pitch_invariant_head": y.get("J1_pitch_invariant"),
            "J2_scale_sufficient_committed": x.get("J2_scale_protocol_sufficient"),
            "J2_scale_sufficient_head": y.get("J2_scale_protocol_sufficient"),
            "status_changed_arms": flips,
            "margin_committed_vs_head": margins,
        })

    def agg(key, side):
        return sorted(r["item"] for r in rows if r[side])

    out = {
        "stage": "R14-C 因果实验在现行 HEAD 的复现差异（0 HTTP；提交件未改动）",
        "head": head(),
        "compared": {
            "committed": committed_p.name,
            "committed_sha256": __import__("hashlib").sha256(
                committed_p.read_bytes()).hexdigest()[:16],
            "rerun_at_head": rerun_p.name,
            "rerun_by": "c2_r14_pitch_ablation.py --out（新名，不覆盖提交件）",
        },
        "why_this_exists": (
            "补集复现核对修好'比较发生在恢复之后'的恒真缺陷后，"
            "报出本件 9 处结论级差异；本件逐条判明差异落在**数值**还是**结论**"),
        "claims": {
            "J1_pitch_invariant": {
                "definition": "同页 A/B/C 三臂 solver_status 一致 ⇒ pitch 非该页因果变量",
                "holds_committed": agg("J1_pitch_invariant_committed",
                                       "J1_pitch_invariant_committed"),
                "holds_head": agg("J1_pitch_invariant_head", "J1_pitch_invariant_head"),
            },
            "J2_scale_protocol_sufficient": {
                "definition": "同页三臂 protocol 全 solved 且 legacy_scale 全 failed"
                              " ⇒ 尺度协议单独致败（充分原因）",
                "holds_committed": agg("J2_scale_sufficient_committed",
                                       "J2_scale_sufficient_committed"),
                "holds_head": agg("J2_scale_sufficient_head",
                                  "J2_scale_sufficient_head"),
            },
        },
        "per_item": rows,
        "verdict": {
            "byte_identical": False,
            "conclusion_direction_preserved_on": sorted(
                r["item"] for r in rows
                if r["J2_scale_sufficient_committed"] == r["J2_scale_sufficient_head"]
                and r["J1_pitch_invariant_committed"] == r["J1_pitch_invariant_head"]),
            "changed_items": sorted(r["item"] for r in rows if r["status_changed_arms"]),
            "numeric_only_items": sorted(
                r["item"] for r in rows if not r["status_changed_arms"]
                and any(a != b for a, b in r["margin_committed_vs_head"].values())),
        },
        "budget": {"http_requests_made": 0, "llm_calls_used": 0},
    }
    dst = EV / "R14C_PITCH_ABLATION_HEAD_DELTA.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    L = ["# R14-C 因果实验：提交件 vs 现行 HEAD 重跑（`R14C_PITCH_ABLATION_HEAD_DELTA.json`）",
         "",
         f"- HEAD `{out['head']}`；提交件 sha 前缀 `{out['compared']['committed_sha256']}`"
         f"（**未改动**），重跑件 `{out['compared']['rerun_at_head']}` 由 "
         f"`{out['compared']['rerun_by']}`",
         "- 起因：`r14_semantic_repro.py` 修好一个**恒真比较**缺陷"
         "（恢复原样发生在语义比较**之前**，等于『原件 vs 原件』）后，"
         "本件暴露 9 处结论级差异。",
         "",
         "| 页 | J1 pitch 不变(旧→新) | J2 尺度充分(旧→新) | 状态改变臂 | margin 旧→新 |",
         "|---|---|---|---|---|"]
    for r in rows:
        m = r["margin_committed_vs_head"]
        ms = " / ".join(f"{a.split('_')[0]}:{m[a][0]}→{m[a][1]}" for a in ARMS)
        L.append(f"| {r['item']} | {r['J1_pitch_invariant_committed']}→"
                 f"{r['J1_pitch_invariant_head']} | "
                 f"{r['J2_scale_sufficient_committed']}→"
                 f"{r['J2_scale_sufficient_head']} | "
                 f"{','.join(r['status_changed_arms']) or '—'} | {ms} |")
    v = out["verdict"]
    L += ["",
          f"- **结论方向保持**的页：{v['conclusion_direction_preserved_on']}",
          f"- **状态发生变化**的页：{v['changed_items']}",
          f"- **仅数值漂移**的页：{v['numeric_only_items']}",
          "",
          "> 人工锁定表述为「尺度协议修复是充分原因、pitch 非因果必需」。"
          "本表只说明该表述在当前 HEAD 的**成立范围**是否改变，"
          "并**不**据此扩大或收紧结论。",
          "",
          "预算：HTTP 0 / LLM 0。"]
    (EV / "R14C_PITCH_ABLATION_HEAD_DELTA.md").write_text(
        "\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps({"per_item_changed": v["changed_items"],
                      "preserved": v["conclusion_direction_preserved_on"],
                      "numeric_only": v["numeric_only_items"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
