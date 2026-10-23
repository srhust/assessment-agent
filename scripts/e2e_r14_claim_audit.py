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
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"
DOCS = ["R14_PROGRESS_STOPGATE.md", "R14_STAGE_E_DELIVERABLES.md",
        "R14G_HISTORICAL_BYTES.md",
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
        # **C8 的注入案例**：改文档里的件数与地位计数 ⇒ 必须报 C8。
        # 上一步我漏了它（标签写 C8、期望 ID 却写 C7，等于从未测过 C8）。
        ("C8 文档总件数与索引真值不符", "C8",
         "c8_total", None),
        ("C8 文档地位计数（current）与索引真值不符", "C8",
         "c8_current", None),
        # **C3 引用存在性**的两个方向都要测：
        ("C3 登记引用了真值件中不存在的键", "C3",
         "c3_bad_key", None),
        ("C3 键名含点号但真实存在（不得假阳性）", "C3",
         "c3_dotted_ok", None),
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
        elif mode == "c3_bad_key":
            sp = fake / "scripts" / Path(__file__).name
            t = sp.read_text(encoding="utf-8")
            t = t.replace('("R14G_PLAN.json", "status"),',
                          '("R14G_PLAN.json", "status_typo_not_a_key"),')
            sp.write_text(t, encoding="utf-8")
            (fake / "evidence" / "_INJECTION_APPLIED").write_text(
                "yes" if "status_typo_not_a_key" in t else "NO",
                encoding="utf-8")
        elif mode == "c3_dotted_ok":
            # 直接单测解析器：含点号的真实键必须判存在
            import importlib.util as _iu
            spec = _iu.spec_from_file_location("ca", str(Path(__file__)))
            m = _iu.module_from_spec(spec)
            spec.loader.exec_module(m)
            ok = m.key_exists({"table": {"R14E_SIX_CONDITION_V10.json":
                                         {"conditions": {"F": 1}}}},
                              "table.R14E_SIX_CONDITION_V10.json.conditions.F")
            bad = m.key_exists({"table": {"x": {}}}, "table.y.z")
            (fake / "evidence" / "_INJECTION_APPLIED").write_text(
                "yes", encoding="utf-8")
            results.append({
                "case": label, "expect_check_id": expect_id,
                "injection_applied": "yes",
                "caught_expected_check": bool(ok and not bad),
                "note": f"含点号真实键判存在={ok} / 不存在键判不存在={not bad}",
                "sample_fail": "", "exit": 0,
                "child_output_parseable": True, "stderr_tail": ""})
            continue
        elif mode in ("c8_total", "c8_current"):
            # 只改**文档**里的数字，索引真值保持不动 ⇒ C8 必须发现不符
            p = fake / "evidence" / "R14_STAGE_E_DELIVERABLES.md"
            t = p.read_text(encoding="utf-8")
            want = ("55 件逐件登记" if mode == "c8_total" else "current 29 /")
            after = ("40 件逐件登记" if mode == "c8_total" else "current 12 /")
            t = t.replace(want, after)
            p.write_text(t, encoding="utf-8")
            # **每个注入案例都必须可证生效**。本轮实测踩到两次"注入静默失手"
            # （先标签写 C8/期望 ID 写 C7，后是 `current 29` 因文档换行根本没被
            #  替换）：失手的注入会让子进程**退出 0**，于是把"我的注入没落地"
            # 误报成"检查失效"——既冤枉检查，也永远测不到它。
            applied = after in t
            (fake / "evidence" / "_INJECTION_APPLIED").write_text(
                ("yes" if applied else "NO"), encoding="utf-8")
            if not applied:
                shutil.rmtree(tmp, ignore_errors=True)
                results.append({"case": label, "expect_check_id": expect_id,
                                "injection_applied": "NO",
                                "caught_expected_check": False,
                                "note": "injection silently no-op ⇒ "
                                        "该案例**未真正测试**对应检查"})
                continue
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
        applied = fake / "evidence" / "_INJECTION_APPLIED"
        inj_applied = (applied.read_text(encoding="utf-8")
                       if applied.is_file() else "n/a")
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
        if inj_applied == "NO":
            # 注入没落进文本 ⇒ 这次"抓不到"是**我的注入失手**，
            # 不是 C8 失效；必须单列出来而不是混成"检查不灵"
            caught = False
            inj_note = "injection silently no-op"
        else:
            inj_note = ""
        results.append({"case": label, "expect_check_id": expect_id,
                        "exit": r.returncode, "child_output_parseable": parseable,
                        "injection_applied": inj_applied,
                        "caught_expected_check": caught,
                        "note": inj_note,
                        "sample_fail": (out.get("fails") or [""])[0][:150],
                        "stderr_tail": "" if parseable else r.stderr[-200:]})
        shutil.rmtree(tmp, ignore_errors=True)
    return all(x["caught_expected_check"] for x in results), results


def key_exists(doc, dotted):
    """按 `a.b.c` 走一遍；**任一段不存在**返回 False。
    登记表写错键名时，必须报"引用不存在"而不是"数字不符"——
    后者把**检查器的 bug**伪装成**交付件的问题**（本轮实测犯过）。"""
    cur = doc
    parts = dotted.split(".")
    i = 0
    while i < len(parts):
        hit = False
        # **贪心合并**：真值件的键名本身可能含点号
        # （`table` 的键就是 `R14E_SIX_CONDITION_V10.json` 这样的**文件名**）。
        # 朴素按点切分会把一个**真实存在**的键判成不存在 ⇒
        # 存在性检查自己制造假阳性，把交付件冤枉成"数字不符"。
        for j in range(len(parts), i, -1):
            cand = ".".join(parts[i:j])
            if isinstance(cur, dict) and cand in cur:
                cur = cur[cand]
                i = j
                hit = True
                break
        if not hit:
            return False
    return True


def run_checks(write_audit):
    """核心检查。write_audit=False（自测子进程）时**不覆盖**真审计件。"""
    fails, notes = [], {}
    docs = {d: (EV / d).read_text(encoding="utf-8") for d in DOCS if (EV / d).is_file()}
    idx_p = EV / "R14_INDEX.json"
    index = json.loads(idx_p.read_text(encoding="utf-8")) if idx_p.is_file() else {}
    if not index:
        fails.append("C8 读不到 R14_INDEX.json，计数无从核对")
    c8f, c8c = check_c8(docs, index)
    fails += c8f
    _C8_CITED = c8c                     # 稍后并入 notes/审计件
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
    notes["C8_count_citations"] = _C8_CITED
    notes["C8_counts_checked"] = len(_C8_CITED)
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
    # 登记表里的每个**键路径**都必须真实存在（见 key_exists）。
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

    REFS = {   # 每条登记引用了**哪个件的哪些键路径**
        "V3 layers 16/7/7": [("R14E_NEWLAYOUT_REGRESSION_V3.json",
                              k) for k in ("layers.candidates_solved",
                                           "layers.accepted",
                                           "layers.released")],
        "V3 wrong_releases []": [("R14E_NEWLAYOUT_REGRESSION_V3.json",
                                  "wrong_releases")],
        "V3 checks_failed []": [("R14E_NEWLAYOUT_REGRESSION_V3.json",
                                 "checks_failed")],
        "验收 V8 checks_failed [] / wrong_releases []":
            [("R14E_STAGE_E_ACCEPTANCE_V8.json", "checks_failed"),
             ("R14E_STAGE_E_ACCEPTANCE_V8.json", "wrong_releases")],
        "扰动 contract 15/15": [("R14F_PERTURB_RELEASE_AUDIT.json",
                                 "fifteen_page_grid_contract.matches")],
        "扰动 perturb_loc 23/30": [("R14F_PERTURB_RELEASE_AUDIT.json",
                                    "perturbation_regression.matches"),
                                   ("R14F_PERTURB_RELEASE_AUDIT.json",
                                    "perturbation_regression.n")],
        "扰动 wrong_grid_releasable []":
            [("R14F_PERTURB_RELEASE_AUDIT.json",
              "perturbation_regression.wrong_grid_releasable")],
        "血缘 F 释放 5/接受 5/候选 5":
            [("R14E_LINEAGE_SIX_CONDITION.json",
              "table.R14E_SIX_CONDITION_V10.json.conditions.F"
              ".final_released"),
             ("R14E_LINEAGE_SIX_CONDITION.json",
              "table.R14E_SIX_CONDITION_V10.json.conditions.F"
              ".correct_candidates")],
        "血缘 V10==V9": [("R14E_LINEAGE_SIX_CONDITION.json", "v10_equals_v9")],
        "HEAD 复验 drift []": [("R14E_HEAD_REVERIFY.json", "drift")],
        # 复放件**没有** released_correct 这种汇总键——它就是 5 个页名做顶层键
        # （`ft-mirror-04` …），释放与否写在每页的 `released` 里。
        # 先前登记 `released_correct` 是我**凭空造的键名**，
        # 存在性检查一装上去就把它抓出来了（这正是装它的理由）。
        "真实截图件 2/5 释放": [
            ("R14E_REAL_PAGE_REPLAY_V8.json", f"{k}.released")
            for k in ("ft-mirror-04", "ft-mirror-04--tight", "ft-overlay-12",
                      "ft-rot90-01", "ft-trans-07")],
        "G0 ready true / failing []":
            [("R14G0_GATE_READINESS_V5.json",
              "ready_for_G_after_human_approval"),
             ("R14G0_GATE_READINESS_V5.json", "failing")],
        "G0 real_http_requests_made 0":
            [("R14G0_GATE_READINESS_V5.json", "real_http_requests_made")],
        "PLAN 预算 6 / 100000": [("R14G_PLAN.json",
                                  "budget.max_requests_including_retry"),
                                 ("R14G_PLAN.json", "budget.max_tokens")],
        "PLAN 未执行": [("R14G_PLAN.json", "status"),
                        ("R14G_PLAN.json", "budget.spent_so_far.requests")],
    }
    _DOCSRC = {"R14E_NEWLAYOUT_REGRESSION_V3.json": V3,
              "R14E_STAGE_E_ACCEPTANCE_V8.json": AC,
              "R14F_PERTURB_RELEASE_AUDIT.json": PE,
              "R14E_LINEAGE_SIX_CONDITION.json": LIN,
              "R14E_REAL_PAGE_REPLAY_V8.json": RV,
              "R14G0_GATE_READINESS_V5.json": G0,
              "R14G_PLAN.json": PLAN,
              "R14E_HEAD_REVERIFY.json": RV8}
    for label, refs in REFS.items():
        for artifact, kp in refs:
            src = _DOCSRC.get(artifact)
            if src is None:
                continue
            if not key_exists(src, kp):
                fails.append(f"C3 登记表 **{label}** 引用了 {artifact} 中"
                             f"**不存在的键** {kp} ⇒ 检查器自身写错了引用，"
                             f"不是交付件数字不符")
    notes["C3_key_paths_checked"] = sum(len(v) for v in REFS.values())

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



def check_c8(docs, index):
    """**计数一致性**：交付文档凡写"N 件"或"current k / superseded m …"，
    必须与 `R14_INDEX.json` 的真值相等。
    本轮实测：文档还写 **47 件**，索引真值已是 **52 件**（deny-by-default
    之后新增核对副产物与 A9 件）。C1/C2/C3/C5/C7 都只核对逐字引用与判定字段，
    **看不见计数过期**；而计数正是人工裁决用来判断覆盖面的东西。
    """
    fails, cited = [], []
    counts = collections.Counter(r["status"] for r in index.get("artifacts", []))
    total = len(index.get("artifacts", []))
    for name, text in docs.items():
        for m in re.finditer(r"(\d+)\s*件逐件登记", text):
            cited.append({"doc": name, "kind": "artifact_total",
                          "cited": int(m.group(1)), "truth": total})
            if int(m.group(1)) != total:
                fails.append(
                    f"C8 {name} 写 artifact_total={m.group(1)} 件，"
                    f"索引真值是 {total} 件 ⇒ 计数过期，请重跑 "
                    f"scripts/e2e_r14_index.py 后同步")
        for m in re.finditer(
                r"(?<![A-Za-z_])(current|superseded|historical|"
                r"erratum_kept|self|verification_byproduct)\s+(\d+)", text):
            # **直接扫 `状态 数字` 对**，不靠"某个中文词后面的一段"。
            # 先前写法要求 `）` 收尾，于是括号后第一个状态（恰是最要紧的
            # `current`）被静默漏检——本轮注入 `current 12` 后 C8 仍报
            # "没问题"，就是这漏检暴露出来的。
            k, v = m.group(1), int(m.group(2))
            ctx = text[max(0, m.start() - 60):m.end() + 60]
            # **结构判据，不用模糊词表**：被反引号或括号包围的计数是
            # **样例/旧值引用**（讲检查器怎么工作），不是对登记册的当前引用。
            # 上一版用"前文含审计/必须/后"——词距不可控，实测豁免**永不触发**。
            in_code = ctx.count("`") >= 2 and "`" in ctx[:30]
            in_paren = ("（" in ctx[:30] or "(" in ctx[:30])
            if in_code or in_paren:
                cited.append({"doc": name, "kind": k, "cited": v,
                              "truth": counts.get(k, 0),
                              "context": "example_or_old_value_skipped"})
                continue
            cited.append({"doc": name, "kind": k, "cited": v,
                          "truth": counts.get(k, 0)})
            if v != counts.get(k, 0):
                fails.append(f"C8 {name} 写 {k}={v}，索引真值 "
                             f"{counts.get(k, 0)} ⇒ 计数过期")
        # 中文写法"当前 25 / 被取代 18 / 核对副产物 4 / 历史 2 / 勘误保留 1 / 自指 2"
        ZH = {"当前": "current", "被取代": "superseded",
              "核对副产物": "verification_byproduct", "历史": "historical",
              "勘误保留": "erratum_kept", "自指": "self"}
        for m in re.finditer(
                r"(当前|被取代|核对副产物|历史|勘误保留|自指)\s*(\d+)", text):
            k = ZH[m.group(1)]
            cited.append({"doc": name, "kind": k, "cited": int(m.group(2)),
                          "truth": counts.get(k, 0)})
            if int(m.group(2)) != counts.get(k, 0):
                fails.append(f"C8 {name} 写 {m.group(1)}={m.group(2)}，"
                             f"索引真值 {counts.get(k, 0)} ⇒ 计数过期")
    return fails, cited


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
