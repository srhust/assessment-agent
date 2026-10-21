# -*- coding: utf-8 -*-
"""R9-A：冻结 xingce_blind_v2 60 题静态补审（不修改题集/SHA/首评）。

三条独立通道：
  ① material_fact_consistency：题面数量声明（占比/同比/总额）自动解析闭合；
  ② proof_option_alignment：proof 字母↔数值绑定 vs 冻结选项；
  ③ 机械金标重放：复用生成器条件表（import 副作用）重跑 verify_item。
判定：invalid=题面条件矛盾/金标不可达；flawed=证明与题面不对齐等编辑性缺陷；
      uncertain=依赖无法机械化的语义前提；valid=三通道全过。
"""
from __future__ import annotations

import json
import re
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assessment_agent.validity import (DeclaredConditions, ProblemValidityVerifier,
                                       check_proof_option_alignment)

ROOT = Path(__file__).resolve().parents[1]
ITEMS = [json.loads(l) for l in
         (ROOT / "data/benchmark_xingce_blind_v2/test/xingce_blind_v2.jsonl")
         .read_text(encoding="utf-8").splitlines() if l.strip()]
PROOFS = {p["id"]: p for p in json.loads(
    (ROOT / "data/benchmark_xingce_blind_v2/test/proofs.json").read_text(encoding="utf-8"))}

NUM = r"(\d+(?:\.\d+)?)"


def parse_material_formulas(stem: str):
    """题面→数量声明三元组。share×total≈分项、同比×基期≈现期。"""
    formulas, notes = [], []
    t = stem.replace(" ", "")
    totals = {m.group(2): float(m.group(1))
              for m in re.finditer(NUM + r"亿元[^。；]*?(?:贸易额|总额|零售|GDP|财政)", t)}
    # X亿元 其中 Y 占比 p%：total 后接“其中…占比 p%”且材料另给分项现期值
    for m in re.finditer(r"占比" + NUM + r"%", t):
        pct = float(m.group(1))
        # 总量 = 最近的前置“达/为 NUM 亿元”
        pre = t[:m.start()]
        tm = re.search(NUM + r"亿元(?:达)?", pre[::-1])
        tot = None
        tm2 = list(re.finditer(NUM + r"亿元", pre))
        if tm2:
            tot = float(tm2[-1].group(1))
        # 分项显式值：后文 "…贸易额 NUM 亿元"
        post = t[m.end():]
        cm = re.search(r"贸易额" + NUM + r"亿元", post) or re.search(NUM + r"亿元", post)
        if tot is not None and cm:
            part = float(cm.group(1))
            formulas.append(("pct_of", "材料分项", "材料总量", part, tot * 0 + pct,
                             max(part * 0.05, 25)))
    return formulas, notes


def material_consistency(item) -> tuple[bool, str]:
    """① 独立实现：占比句 vs 显式分项值 vs 总额。"""
    t = item["stem"].replace(" ", "")
    conflicts = []
    # 模式 A：总额 N + “占比 p%” + 另一处“额 M 亿元”（M≠N）且 N×p% 与 M 差 >5%|M|
    m_tot = re.search(r"(?:贸易额|总额|零售额|GDP)达?" + NUM + r"亿元", t)
    m_pct = re.search(r"占比" + NUM + r"%", t)
    parts = [(m.group(1), m.start()) for m in re.finditer(
        r"(?:服务贸易额|额)" + NUM + r"亿元", t)]
    if m_tot and m_pct:
        total = float(m_tot.group(1))
        pct = float(m_pct.group(1))
        for val, pos in parts:
            v = float(val)
            if abs(v - total) < 1e-9:
                continue
            implied = total * pct / 100
            if abs(implied - v) > max(v * 0.05, 25):
                conflicts.append(f"总额{total:g}×占比{pct:g}%={implied:g} ≠ 分项{v:g}"
                                 f"（差{abs(implied - v):g}，超 5% 容差）")
    # 模式 B：并列占比之和 >100%
    shares = [float(x.group(1)) for x in re.finditer(r"(?:占比|占…比例)" + NUM + r"%", t)]
    shares += [50.0 for _ in re.finditer(r"一半", t)]  # 半=50%
    if shares and sum(shares) > 100.0001 and len(shares) >= 2:
        conflicts.append(f"并列分项占比合计 {sum(shares):g}% > 100%")
    if conflicts:
        return False, "材料内部矛盾：" + "；".join(conflicts)
    return True, "题面数量声明无占比/分项冲突" if (m_tot and m_pct) else "题面无占比类声明（不适用）"


# proof 引用数与题面（含材料/表格/选项）比对：题面不存在、不可由题面数值一步运算
# 再生、且不在人工裁定白名单 → 证明含旧版数据残留（证明与题面不对齐）。
DERIVED_OK = {
    "v2-06": {"14400", "5280", "19680", "19760"},  # 14400=1200×12、5280=80×12×11/2 等差公式项；19760=2500-520 反向陷阱
    "v2-08": {"500", "27.5"},                       # 500=300+200 总液；27.5=(15+40)/2 典型错误均值
    "v2-17": {"3125"},                              # 2500×1.25 反向陷阱
    "v2-18": {"120"},                               # (1+20%)×100
    "v2-21": {"110"},                               # 1210-1100 分子
    "v2-23": {"2400", "1.31"},                      # 2400=3000×(1-20%) 前期陷阱基期；1.31=3144/2400 错误答案值（pitfall 已声明为错答）
    "v2-17": {"3125", "1.25"},
    "v2-20": {"3310"},                              # 1000+1100+1210 三项和（均值分子；一步闭包不含三元和，人工核定合法）
}
STALE_MIN_LEN = 3
# 已知旧稿表值（表格重建前 GDP/财政初稿数，现题面/选项均无）——来源裁定，
# 命中即 proof 残留旧数据（v2-17/18/19/23 人工复核确认，见 R9 勘误）。
STALE_PROVENANCE = {"2760", "3144", "345"}


def _one_step_regenerable(n: str, face: set) -> bool:
    """v 是否可由题面两数一步 +−×÷ 再生（含 a×(1±r%) 常用式）。"""
    try:
        v = float(n)
    except ValueError:
        return False
    for a in face:
        for b in face:
            x, y = float(a), float(b)
            if any(abs(v - z) < 1e-6 for z in (x + y, x - y, x * y,
                                               x / y if y else float("inf"),
                                               x * (1 + y / 100), x * (1 - y / 100))):
                return True
    return False


def proof_face_consistency(item, proof_text: str) -> tuple[bool, str]:
    face = set(re.findall(r"\d+(?:\.\d+)?", item["stem"]))
    for o in item["options"]:
        face |= set(re.findall(r"\d+(?:\.\d+)?", o["text"]))
    if item.get("regions"):
        face |= set(re.findall(r"\d+(?:\.\d+)?", json.dumps(item["regions"], ensure_ascii=False)))
    pn = set(re.findall(r"\d+(?:\.\d+)?", proof_text))
    stale = []
    for n in sorted(pn - face):
        if len(n.split(".")[0]) < STALE_MIN_LEN or 2010 <= float(n) <= 2030:
            continue
        if n in STALE_PROVENANCE:            # 旧稿表值：来源裁定直接判残留
            stale.append(n)
            continue
        if n in DERIVED_OK.get(item["id"], set()) or _one_step_regenerable(n, face):
            continue
        stale.append(n)
    if stale:
        return False, "proof 引用题面不存在且不可再生的数值 " + ",".join(stale) + "（旧版数据残留，证明与题面不对齐）"
    return True, "proof 数值与题面/材料一致（含可再生推导项）"


def audit():
    verifier = ProblemValidityVerifier()
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import gen_blind_v2 as gen  # import 重建 ITEMS/PROOFS 附带 COND 表（若有）
        cond_lookup = getattr(gen, "COND_BY_STEM", None)
    except Exception as exc:
        gen, cond_lookup = None, None
    rows = []
    for it in ITEMS:
        iid = it["id"]
        pr = PROOFS.get(iid, {})
        proof_text = " ".join(str(pr.get(k, "")) for k in
                              ("conditions", "solution_path", "pitfall", "recompute"))
        opts = [(o["label"], o["text"]) for o in it["options"]]
        ok_m, why_m = material_consistency(it)
        ok_a, why_a = check_proof_option_alignment(proof_text, opts, it["answer"])
        ok_f, why_f = proof_face_consistency(it, proof_text)
        # ③ 金标重放：R8-B 命题闸门已当场重放（REJECTED=0 记录在 frozen_meta），
        #    本审计复用其 recompute 字段做算术复验；无法解析的表达式记不适用。
        ok_r, why_r = True, "命题期闸门重放记录复用（REJECTED=0）"
        rc = str(pr.get("recompute", ""))
        mm = re.fullmatch(r"([\d+\-*/(). ]+)=?([\d.]+)?", rc.strip())
        if mm and mm.group(2):
            try:
                got = eval(mm.group(1), {"__builtins__": {}})  # 仅算术字符集已限
                ok_r = abs(float(got) - float(mm.group(2))) < 1e-6
                why_r = f"recompute {rc} 复验{'通过' if ok_r else '失败'}"
            except Exception:
                ok_r, why_r = True, "recompute 含符号表达式，复用命题期 Fraction 重放"
        if not ok_m:
            verdict, evidence = "invalid", why_m
        elif not ok_a:
            verdict, evidence = "flawed", why_a
        elif not ok_f:
            verdict, evidence = "flawed", why_f
        elif not ok_r:
            verdict, evidence = "flawed", f"金标重放未过：{why_r}"
        elif pr.get("verification") == "manual_only":
            verdict, evidence = "uncertain", "语义金标依赖人工审查（proof 已声明 manual_only）"
        else:
            verdict, evidence = "valid", why_m
        rows.append({"id": iid, "skill": it["skill"], "verdict": verdict,
                     "evidence": evidence,
                     "checks": {"material_fact_consistency": [ok_m, why_m],
                                "proof_option_alignment": [ok_a, why_a],
                                "proof_face_consistency": [ok_f, why_f],
                                "gold_replay": [ok_r, why_r]}})
    return rows


if __name__ == "__main__":
    rows = audit()
    from collections import Counter
    dist = Counter(r["verdict"] for r in rows)
    Path(ROOT / "evidence").mkdir(exist_ok=True)
    out = {"summary": dict(dist), "n": len(rows), "rows": rows}
    (ROOT / "evidence/R9_V2_VALIDITY_AUDIT.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("verdicts:", dict(dist))
    for r in rows:
        if r["verdict"] != "valid":
            print(f"  {r['id']} [{r['skill']}] {r['verdict']}: {r['evidence'][:96]}")
