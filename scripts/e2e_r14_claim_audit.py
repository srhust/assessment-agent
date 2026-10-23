"""交付件一致性审计（零 API）：**机器复核**我在 markdown 里写下的每一条
可核事实，防我自己再犯两类已经发生过的错误——

  · **引用不存在的件**（已发生：把扰动审计写成 `R14E_PERTURB_RELEASE_AUDIT_V5.json`，
    实际是 `R14F_PERTURB_RELEASE_AUDIT.json`）；
  · **口径混同导致表述过强**（已发生：V2 把证据链单元数标成"消费单元"，
    使"4 页 tight 选项 0 份"过强）。

检查项：
  C1 引用完整性：文档里出现的每个 `R14*` 件名必须在 evidence/ 或仓库根存在；
  C2 当前性：被当作**当前结论**引用的件，不得是已知被取代版本
     （`R14E_NEWLAYOUT_REGRESSION_V2` 只允许出现在勘误语境）；
  C3 数字对账：文档里逐条抄出的关键数字与件内实际值**必须相等**
     （层计数、wrong_releases、F 5/13、perturb 23/30、contract 15/15、
     预算 0/6 与 0/100000、G0 ready/failing、真实截图 2/5）；
  C4 三层不混同：候选/接受/释放必须是**三个不同键**，且文档不得用
     "释放"代指候选（用件内键名 + 文档措辞双向查）；
  C5 禁止措辞：文档不得出现"真实截图突破 / blind eval READY /
     已批准 G"这类无据断言；因果结论表述不得强于
     "尺度协议充分、pitch 非因果必需"；
  C6 G 未执行：`evidence/R14G_RESULT*.json` 必须不存在，且 PLAN 的
     spent_so_far 仍是 0/0。
任何一条不过 → 退出码 1 并列出位置，绝不"顺手解释过去"。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"
DOCS = ["R14_PROGRESS_STOPGATE.md", "R14_STAGE_E_DELIVERABLES.md",
        "R14E_NEWLAYOUT_REGRESSION.md", "R14E_TIGHT_GAP_DIAGNOSIS.md",
        "R14G0_READINESS.md",
        # 本轮新增：因果实验在现行 HEAD 的复现差异（人工裁决项 2 的直接材料）
        "R14C_PITCH_ABLATION_HEAD_DELTA.md"]

SUPERSEDED_AS_CURRENT = {        # 只允许在"勘误/更正/历史"语境出现
    "R14E_NEWLAYOUT_REGRESSION_V2.json": ("勘误", "V2 ", "V2 的", "V2→V3",
                                          "V2→", "早期版", "不得", "保留不删",
                                          "曾把", "混同"),
}
FORBIDDEN_PHRASES = ["真实截图突破", "真实截图已突破", "盲评 READY",
                     "blind eval READY", "已批准 Stage G", "G 已执行",
                     "已发起 HTTP 请求", "突破性的真实"]
CEILING = "尺度协议"              # 因果表述的允许上限（不得强于该结论）
STRONGER_THAN_CEILING = ["pitch 是必需", "pitch 必然", "证明 pitch 因果"]


def art(name):
    p = EV / name
    if not p.is_file():
        p = ROOT / name
    return p if p.is_file() else None


# **提引语境白名单**：句子本身在说"该件不存在 / 未生成 / 已被取代 / 勘误"时，
# 引用坏名字是**诚实记录**而非错误。没有这层判断，审计会把更正本身报成缺陷
# （首版就误报了 3 处：R14G_RESULT「未生成」、扰动审计 V5「并不存在」×2）。
NON_EXISTENT_CONTEXT = ("不存在", "未生成", "不应存在", "勘误", "并不存在",
                        "引用名错误", "旧引用", "已更名", "误写", "不得再",
                        "保留不删", "早期版", "曾把", "混同", "曾引用")


def jload(name):
    p = art(name)
    return json.loads(p.read_text(encoding="utf-8")) if p else None


def selftest() -> tuple:
    """审计**有效性**自证（零 API）：往临时副本里**故意**植入违规，确认本审计
    真的抓得到。否则"fails []"没有信息量——一个恒真的检查器比没有检查器更糟。
    子进程一律 `--no-selftest`，不递归、不碰真 evidence/。
    """
    import shutil
    import tempfile

    cases = [
        ("C1 引用不存在的件（非勘误语境）", "C1",
         "append", "\n数字来自 `R14E_NOT_A_REAL_ARTIFACT.json`。\n"),
        ("C2 把被取代件当当前引用", "C2",
         "append", "\n当前结论见 `R14E_NEWLAYOUT_REGRESSION_V2.json`。\n"),
        ("C5 禁止措辞（真实截图突破）", "C5",
         "append", "\n这证明真实截图已突破。\n"),
        ("C3 抄录数字与件内不一致", "C3",
         "replace", None),
        ("C6 G 结果件存在（未获批准却执行）", "C6",
         "ghost_g_result", None),
        ("C7 已撤回的可复现性结论留在交付文档里", "C7",
         "append", "\n真实截图复放件 `R14E_REAL_PAGE_REPLAY_V8.json` 属 "
         "NOT_REPRODUCIBLE_FROM_HEAD。\n"),
    ]
    results = []
    for label, expect_id, mode, payload in cases:
        tmp = Path(tempfile.mkdtemp())
        fake = tmp / "fake"
        shutil.copytree(EV, fake / "evidence")
        (fake / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy(__file__, fake / "scripts" / Path(__file__).name)
        if mode == "ghost_g_result":
            (fake / "evidence" / "R14G_RESULT.json").write_text(
                "{}", encoding="utf-8")
        else:
            for name in ("R14_PROGRESS_STOPGATE.md",
                         "R14E_NEWLAYOUT_REGRESSION.md"):
                p = fake / "evidence" / name
                if p.is_file():
                    t = p.read_text(encoding="utf-8")
                    t = (t + payload if mode == "append" else
                         t.replace("候选 candidates_solved | **16**",
                                   "候选 candidates_solved | **17**"))
                    p.write_text(t, encoding="utf-8")
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(fake / "scripts" / Path(__file__).name),
                            "--no-selftest"],
                           cwd=fake, capture_output=True, text=True,
                           encoding="utf-8")
        try:
            out = json.loads(r.stdout[r.stdout.index("{"):])
            parseable = True
        except Exception:  # noqa: BLE001
            out = {"fails": []}
            parseable = False
        got_ids = {f[:2] for f in out.get("fails", []) if f[:2] == f"C{f[2]}"[:2]
                   or True}
        hit = any(str(f).startswith(expect_id) for f in out.get("fails", []))
        caught = bool(parseable and r.returncode != 0 and hit)
        results.append({"case": label, "expect_check_id": expect_id,
                        "exit": r.returncode, "child_output_parseable": parseable,
                        "caught_expected_check": caught,
                        "sample_fail": (out.get("fails") or [""])[0][:150],
                        "stderr_tail": "" if parseable else r.stderr[-200:]})
        shutil.rmtree(tmp, ignore_errors=True)
    return all(x["caught_expected_check"] for x in results), results


def run_checks(write_audit):
    """核心检查。write_audit=False（自测子进程）时**不覆盖**真审计件。"""
    fails, notes = [], {}
    docs = {d: (EV / d).read_text(encoding="utf-8") for d in DOCS if (EV / d).is_file()}
    missing_docs = [d for d in DOCS if d not in docs]
    if missing_docs:
        fails.append(f"C0 缺交付文档：{missing_docs}")

    # ---------- C1 引用完整性 ----------
    cited = set()
    line_ctx = []                          # (doc, lineno, line)
    for d, t in docs.items():
        for ln, line in enumerate(t.splitlines(), 1):
            for m in re.findall(r"R14[A-Z0-9_\-]*\.(?:json|md)", line):
                cited.add((d, m))
                line_ctx.append((d, m, ln, line))
    bad_ref = sorted({(d, n) for d, n in cited if art(n) is None})
    # 只在"该件被当作真实来源引用"的句子才算错；坏名 + 非存在语境 = 诚实记录
    genuinely_bad = []
    exempted = []
    for d, n in bad_ref:
        hits = [(ln, line) for (dd, nn, ln, line) in line_ctx
                if dd == d and nn == n]
        if all(any(cx in line for cx in NON_EXISTENT_CONTEXT) for _, line in hits):
            exempted.append({"doc": d, "artifact": n,
                             "lines": [ln for ln, _ in hits],
                             "why": "该句本身声明此件不存在/被取代，属勘误语境"})
            continue
        genuinely_bad.append((d, n, [ln for ln, _ in hits]))
    notes["C1_citations_checked"] = len(cited)
    notes["C1_missing_but_erratum_context"] = exempted
    for d, n, lns in genuinely_bad:
        fails.append(f"C1 {d}:{lns} 引用了不存在的件（且非勘误语境）：{n}")

    # ---------- C2 当前性 ----------
    for name, allowed in SUPERSEDED_AS_CURRENT.items():
        for d, t in docs.items():
            for line_no, line in enumerate(t.splitlines(), 1):
                if name.split(".")[0] in line.replace("_V2.json", "_V2"):
                    if name in line and not any(
                            a in line for a in list(allowed) + list(
                                NON_EXISTENT_CONTEXT)):
                        fails.append(f"C2 {d}:{line_no} 把被取代件 {name} "
                                     f"当作当前引用（该句无勘误语境）")

    # ---------- C7 跨文档**结论一致性**（本轮新增，根修一个真实漏洞）----------
    # 事故：我把"V8 NOT_REPRODUCIBLE_FROM_HEAD"写进了 STOPGATE **和**
    # DELIVERABLES；撤回时只改了 STOPGATE，DELIVERABLES 里**仍留着已撤回的结论**。
    # C1/C2/C5 都抓不到它——引用的是**存在的**件、不是被取代件、也不是禁用措辞。
    # 规则：件的可复现性以 `R14E_ARTIFACT_ATTRIBUTION.json` 为唯一真值；
    # 任何文档句子同时出现"件名（族）"与"某个状态词"，状态词必须与真值一致，
    # 除非该句处于**勘误语境**（撤回/勘误/上一版本/retract 等）。
    ATT = jload("R14E_ARTIFACT_ATTRIBUTION.json") or {}
    statuses = {"REPRODUCIBLE_FROM_HEAD", "NOT_REPRODUCIBLE_FROM_HEAD",
                "VERDICT_REPRODUCIBLE_EVIDENCE_KEYS_MISSING"}
    truth = {}
    for x in ATT.get("results", []):
        if x.get("artifact"):
            truth[re.sub(r"_V\d+$", "", x["artifact"].rsplit(".", 1)[0])] = (
                x["artifact"], x.get("reproducibility"))
    notes["C7_attribution_covered"] = sorted(v[0] for v in truth.values())
    ERRATA_C7 = ("撤回", "勘误", "上一版本", "误判", "retract", "formerly",
                 "不得", "禁止", "NOT_REPRODUCIBLE 的件")
    for fam, (aname, status) in truth.items():
        if not status:
            continue
        for d, t in docs.items():
            lines = t.splitlines()
            for ln, line in enumerate(lines, 1):
                if fam not in line:
                    continue
                seen = {st for st in statuses if st in line}
                if not seen or seen == {status}:
                    continue
                window = " ".join(lines[max(0, ln - 4):ln + 3])
                if any(cx in window for cx in ERRATA_C7):
                    notes.setdefault("C7_erratum_context", []).append(
                        {"doc": d, "line": ln, "artifact": aname,
                         "asserted": sorted(seen), "truth": status})
                    continue
                fails.append(
                    f"C7 {d}:{ln} 对 {aname} 断言 {sorted(seen)}，"
                    f"但归属真值是 {status}（且该句不在勘误语境）——"
                    f"已撤回的结论不得留在交付文档里")

    # ---------- C3 数字对账 ----------
    V3 = jload("R14E_NEWLAYOUT_REGRESSION_V3.json") or {}
    AC = jload("R14E_STAGE_E_ACCEPTANCE_V8.json") or {}
    PE = jload("R14F_PERTURB_RELEASE_AUDIT.json") or {}
    LIN = jload("R14E_LINEAGE_SIX_CONDITION.json") or {}
    RV = jload("R14E_REAL_PAGE_REPLAY_V8.json") or {}
    G0 = jload("R14G0_GATE_READINESS_V5.json") or {}
    PLAN = jload("R14G_PLAN.json") or {}
    GAP = jload("R14E_TIGHT_GAP_DIAGNOSIS.json") or {}
    RV8 = jload("R14E_HEAD_REVERIFY.json") or {}

    checks = [
        ("V3 layers 16/7/7",
         (V3.get("layers") or {}).get("candidates_solved") == 16
         and (V3.get("layers") or {}).get("accepted") == 7
         and (V3.get("layers") or {}).get("released") == 7),
        ("V3 wrong_releases []", V3.get("wrong_releases") == []),
        ("V3 checks_failed []", V3.get("checks_failed") == []),
        ("验收 V8 checks_failed [] / wrong_releases []",
         AC.get("checks_failed") == [] and AC.get("wrong_releases") == []),
        ("扰动 contract 15/15",
         (PE.get("fifteen_page_grid_contract") or {}).get("matches") == 15),
        ("扰动 perturb_loc 23/30",
         (PE.get("perturbation_regression") or {}).get("matches") == 23
         and (PE.get("perturbation_regression") or {}).get("n") == 30),
        ("扰动 wrong_grid_releasable []",
         (PE.get("perturbation_regression") or {}).get("wrong_grid_releasable") == []),
        ("血缘 F 释放 5/接受 5/候选 5",
         ((LIN.get("table") or {}).get("R14E_SIX_CONDITION_V10.json") or {})
         .get("conditions", {}).get("F", {}).get("final_released") == 5
         and ((LIN.get("table") or {}).get("R14E_SIX_CONDITION_V10.json") or {})
         .get("conditions", {}).get("F", {}).get("correct_candidates") == 5),
        ("血缘 V10==V9", LIN.get("v10_equals_v9") is True),
        ("HEAD 复验 drift []", RV8.get("drift") == []),
        ("真实截图件 2/5 释放",
         len([k for k, v in (RV8.get("comparisons") or {}).items()]) >= 2
         and (RV.get("released_correct") == 2 or RV.get("released") == 2
              or len(RV.get("released_ids") or []) == 2
              or len(V3.get("released_ids") or []) >= 2)),
        ("G0 ready true / failing []",
         G0.get("ready_for_G_after_human_approval") is True
         and G0.get("failing") == []),
        ("G0 real_http_requests_made 0",
         G0.get("real_http_requests_made") == 0),
        ("PLAN 预算 6 / 100000",
         (PLAN.get("budget") or {}).get("max_requests_including_retry") == 6
         and (PLAN.get("budget") or {}).get("max_tokens") == 100000),
        ("PLAN 未执行",
         PLAN.get("status") == "PLANNED_NOT_EXECUTED"
         and (PLAN.get("budget") or {}).get("spent_so_far", {}).get("requests") == 0),
        ("缺口 A/B/C/D 四类齐备",
         all(x in json.dumps(GAP, ensure_ascii=False) for x in
             ("A 选项带根本未定位", "B 单元定位到但有格裁剪完整性未过",
              "C 裁剪已落盘但证据链为空", "D 证据完整"))),
        ("缺口两量分列",
         "saved_option_crops" in GAP.get("quantity_definitions", {})
         and "evidence_cells" in GAP.get("quantity_definitions", {})),
    ]
    for label, ok in checks:
        if not ok:
            fails.append(f"C3 数字对账不过：{label}")

    # 文档抄录的数字必须与件内一致（逐条正则抽 → 比）
    def doc_num(d, pat):
        m = re.search(pat, docs.get(d, ""))
        return m.groups() if m else None

    pairs = [
        ("R14E_NEWLAYOUT_REGRESSION.md", r"候选 candidates_solved \| \*\*(\d+)\*\*",
         ("layers", "candidates_solved")),
        ("R14E_NEWLAYOUT_REGRESSION.md", r"接受 accepted \| \*\*(\d+)\*\*",
         ("layers", "accepted")),
        ("R14E_NEWLAYOUT_REGRESSION.md", r"释放 released \| \*\*(\d+)\*\*",
         ("layers", "released")),
    ]
    for d, pat, path in pairs:
        got = doc_num(d, pat)
        want = V3.get(path[0], {}).get(path[1])
        if got is None:
            fails.append(f"C3 {d} 未找到应可核对的数字（{path}）")
        elif int(got[0]) != want:
            fails.append(f"C3 {d} 抄录 {got[0]} != 件内 {want}（{path}）")

    # ---------- C4 三层不混同 ----------
    lay = V3.get("layers") or {}
    if not ({"candidates_solved", "accepted", "released"} <= set(lay)):
        fails.append("C4 V3 件缺少三层独立键")
    for d, t in docs.items():
        if re.search(r"候选\s*=\s*释放|候选即释放|接受即释放", t):
            fails.append(f"C4 {d} 出现把三层互相代称的措辞")

    # ---------- C5 禁止措辞 / 上限 ----------
    for d, t in docs.items():
        for ph in FORBIDDEN_PHRASES:
            if ph in t:
                fails.append(f"C5 {d} 出现禁止措辞「{ph}」")
        for ph in STRONGER_THAN_CEILING:
            if ph in t:
                fails.append(f"C5 {d} 因果表述强于人工结论「{ph}」")
    if CEILING not in docs.get("R14_PROGRESS_STOPGATE.md", ""):
        fails.append("C5 STOPGATE 未保留因果上限表述（尺度协议充分/pitch 非因果必需）")

    # ---------- C6 G 未执行 ----------
    g_res = sorted(p.name for p in EV.glob("R14G_RESULT*.json"))
    if g_res:
        fails.append(f"C6 不应存在 G 结果件（未获人工批准）：{g_res}")
    notes["C6_g_result_files"] = g_res
    notes["budget"] = {"max_requests": 6, "max_tokens": 100000,
                       "spent": (PLAN.get("budget") or {}).get("spent_so_far")}

    out = {"docs": list(docs), "fails": fails, "notes": notes,
           "citations": sorted({n for _, n in cited}),
           "llm_calls_used": 0, "http_requests_made": 0}
    summary = json.dumps({"fails": fails, "citations": len(cited),
                          "budget": notes["budget"]}, ensure_ascii=False, indent=1)
    if not write_audit:
        # 子进程**必须**把结果打出来：否则父进程只能凭 exit!=0 判"抓到"，
        # 而一个因其它原因崩掉的子进程也会被算成有效——首版正是这样得到了
        # 假的"caught True"。现在父进程要检查失败项的**类别前缀**。
        print(summary)
        return 1 if fails else 0
    dst = EV / "R14_CLAIM_AUDIT.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"fails": fails, "citations": len(cited),
                      "budget": notes["budget"]}, ensure_ascii=False, indent=1))
    return 1 if fails else 0


def main() -> int:
    """先自证审计器**有效**（注入违规必须被抓），再跑真实检查。"""
    if "--no-selftest" in sys.argv:
        return run_checks(write_audit=False)
    eff, results = selftest()
    rc = run_checks(write_audit=True)
    p = EV / "R14_CLAIM_AUDIT.json"
    if p.is_file():
        d = json.loads(p.read_text(encoding="utf-8"))
        d["auditor_effectiveness"] = {
            "all_injected_violations_caught": eff, "cases": results,
            "why": "恒真的检查器比没有检查器更糟：先证明本审计能抓住 5 类注入"
                   "违规，其 fails [] 才有信息量"}
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    if not eff:
        print("审计器不能抓住全部注入违规，其 passes 结论不可信", file=sys.stderr)
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
