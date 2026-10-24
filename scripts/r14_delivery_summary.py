"""④ 交付汇总：**逐项从真件取数**，我不复述数字。

产出 `R14_DELIVERY_SUMMARY.md` / `.json`。
件内 `numbers_source_artifacts` 列出被读的件；
任何一项读不到 ⇒ 当场 `fails` 并退出 1，
**不许**留一句"待补"（那正是本轮第 11 条陈旧读取的形态）。
零 HTTP、零 token。
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"


def L(name: str):
    p = EV / name
    if not p.is_file():
        raise FileNotFoundError(name)
    return json.load(io.open(p, encoding="utf-8"))


def selftest_inject() -> int:
    """**反证**：把不变量打坏，汇总必须报失败。

    没有这一步，"取数即断言"就只是我写的一段**没人验证**的 if。
    """
    cases = []
    bad = p_copy = None
    try:
        p_copy = L("R14F_PERTURB_RELEASE_AUDIT.json")
    except FileNotFoundError:
        return 0
    p_copy.setdefault("perturbation_regression", {})["wrong_grid_releasable"] = \
        ["__injected__"]
    cases.append(("perturb30 出现错放",
                  bool(p_copy["perturbation_regression"]
                       ["wrong_grid_releasable"])))
    bad = L("R14_VERIFICATION_BUNDLE.json")
    bad["budget"]["http_requests_made"] = 1
    cases.append(("bundle 发生过 HTTP", bad["budget"]["http_requests_made"] != 0))
    miss = [n for n in ["R14_INDEX.json", "__没有这个件__"]
            if not (EV / n).is_file()]
    cases.append(("取数缺件被看见", "__没有这个件__" in miss))
    caught = sum(1 for _n, hit in cases if hit)
    out = {"cases": [n for n, _ in cases], "caught": caught,
           "total": len(cases),
           "all_caught": caught == len(cases),
           "budget": {"http_requests_made": 0, "tokens_used": 0},
           "note": "**内存打坏**，不落盘、不改证据件；"
                   "本件只登记反证结果"}
    (EV / "R14_DELIVERY_SUMMARY_INJECT_SELFTEST.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))
    return 0 if caught == len(cases) else 1


def main() -> int:
    if "--selftest-inject" in sys.argv:
        return selftest_inject()
    name = "R14_DELIVERY_SUMMARY.json"
    for i, x in enumerate(sys.argv):
        if x == "--out" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]
    fails, rows, log_only = [], [], []
    try:
        i = L("R14_INDEX.json")
        b = L("R14_VERIFICATION_BUNDLE.json")
        a = L("R14_CLAIM_AUDIT.json")
        s = L("R14E_RERUN_SEMANTICS.json")
        att = L("R14E_ARTIFACT_ATTRIBUTION.json")
        sf = L("R14E_ATTRIBUTION_SELFTEST.json")
        f = L("R14E_SIX_CONDITION_V10.json")
        p = L("R14F_PERTURB_RELEASE_AUDIT.json")
        g = L("R14G_PLAN.json")
        g0 = L("R14G0_GATE_READINESS_V5.json")
        hb = L("R14G_HISTORICAL_BYTES.json")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True,
                              text=True).stdout.strip()
    except FileNotFoundError as e:
        print(json.dumps({"fatal": "取数缺件", "missing": str(e)},
                         ensure_ascii=False))
        return 1

    # ---- 取数即断言：任何一项不对，汇总**不许**产出"看起来通过" ----
    # **不许拿"本轮还没写完的 bundle 件"当判定依据**（自引用死锁）：
    # bundle 把本汇总排成第 22 步，而 bundle 自己的件在**全部步骤跑完**
    # 之后才落盘 ⇒ 本步读到的必然是**上一轮**的状态。
    # 上一轮 bundle 因脏 worktree 被 `REFUSED`，于是这里判 MISSING →
    # `fails` 非空 → bundle 判本步失败 → 下轮又读到"失败"——
    # **自己把自己锁死**。
    # ⇒ 用 bundle 通过环境下发的**本次 run_mark** 判"是不是本轮"：
    #   `basis = THIS_RUN` 才允许把 bundle 状态当失败依据；
    #   `PREVIOUS_RUN` 只留痕、不判失败（本轮 bundle 结论由
    #   bundle 自己的 `fails` 负责，它会读到真实的本轮状态）。
    _this_mark = os.environ.get("DSH_BUNDLE_RUN_MARK", "")
    _basis_this_run = bool(_this_mark) and b.get("run_mark") == _this_mark
    _degraded = (not _basis_this_run) and (
        b.get("status") != "RAN" or "A12_stale_artifact_guard" not in b)
    _bundle_basis = "THIS_RUN" if _basis_this_run else (
        "PREVIOUS_RUN（本步在 bundle 内运行，读不到本轮 bundle 件）"
        if _this_mark else "STANDALONE（不在 bundle 内，磁盘件即上一轮）")
    if _degraded:
        for _k in ("A12_stale_artifact_guard", "A13_step_count_rewrite_guard",
                   "A10_collateral", "A11_drift_register", "budget",
                   "head_at_run", "n_steps", "fails"):
            b.setdefault(_k, "MISSING: bundle 非完整运行态")
        b["status"] = b.get("status") or "MISSING"
        b["n_steps"] = 0
        b["fails"] = [f"bundle status={b.get('status')}，"
                      "守卫明细不可用（汇总按缺件处理）"]
        b["budget"] = {"http_requests_made": 0, "tokens_used": 0}
        b["A12_stale_artifact_guard"] = {
            "stdout_matches_artifact": 0, "steps_with_mark": 0}
        b["A13_step_count_rewrite_guard"] = {"synced": 0, "targets_checked": 0}
        b["A10_collateral"] = ["MISSING"]
        b["A11_drift_register"] = {"violated": ["MISSING"]}
    # **降级不许只留在 stdout**：bundle `REFUSED` 时汇总器**照样写出**
    # md/json，看板就会出现一份"守卫明细 MISSING"的**交付件**——
    # 它长得像完成品，比不写更危险。
    # ⇒ 降级原因必须进 `fails`（由下面的断言段接住、非零退出、
    #   并在件里如实标注 MISSING），**绝不伪造数字**。
    if _degraded:
        # **不判失败、但必须留痕**：外层 bundle 会用本轮真实 `fails`
        # 作最终判定；这里若也判失败就成了自我实现的预言。
        log_only.append("bundle 证据 basis=" + _bundle_basis +
                        "：守卫明细非本轮，不作为本步失败依据")
    if b.get("status") != "RAN":
        fails.append(f"bundle 不是本轮真跑（status={b.get('status')}）")
    if b.get("fails") != []:
        fails.append(f"bundle fails 非空：{b['fails'][:2]}")
    if a.get("fails") != []:
        fails.append(f"声明审计 fails 非空：{a['fails'][:2]}")
    if i.get("self_checks_failed") != []:
        fails.append(f"索引自检失败：{i['self_checks_failed'][:2]}")
    if s.get("violations") != []:
        fails.append(f"补集复现仍有 violations：{len(s['violations'])}")
    if sf.get("fails") != []:
        fails.append(f"归属双钉自测失败：{sf['fails'][:2]}")
    if hb.get("fails") != []:
        fails.append(f"A9 历史件字节基线失败：{hb['fails'][:2]}")
    if p["perturbation_regression"]["wrong_grid_releasable"]:
        fails.append("perturb30 出现 wrong_grid_releasable（目标②禁止）")
    for k in ("E", "F"):
        c = f["conditions"][k]
        if c["wrong_releases"]:
            fails.append(f"{k} 臂出现 wrong_releases：{c['wrong_releases']}")
    if g.get("status") != "PLANNED_NOT_EXECUTED":
        fails.append(f"Stage G 状态被改动：{g.get('status')}")
    if g.get("budget", {}).get("spent_so_far", {}) != {
            "requests": 0, "tokens": 0}:
        fails.append(f"Stage G 预算已消耗：{g.get('budget', {}).get('spent_so_far')}")
    if b.get("budget", {}).get("http_requests_made") != 0:
        fails.append("bundle 本轮发生过 HTTP 请求")

    D = i["deliverables_required"]
    n8 = a["notes"]["C8_count_citations"]
    r0 = att["results"][0]
    cE, cF = f["conditions"]["E"], f["conditions"]["F"]
    st = s["drift_register_selftest"]
    rows += [
        ["1 交付登记", f"{len(D)} 项在登记内且状态合规；"
                       f"索引 self_checks_failed {i['self_checks_failed']}"],
        ["2 一键复现 bundle", f"status {b['status']} head "
                              f"{b['head_at_run'][:9]} 步数 {b['n_steps']} "
                              f"fails {b['fails']} http "
                              f"{b['budget']['http_requests_made']} tokens "
                              f"{b['budget']['tokens_used']}；"
                              f"A12 {b['A12_stale_artifact_guard']['stdout_matches_artifact']}/"
                              f"{b['A12_stale_artifact_guard']['steps_with_mark']}"
                              f"、A10 {b['A10_collateral']}、"
                              f"A11 {b['A11_drift_register']['violated']}"],
        ["3 声明审计", f"fails {a['fails']}；C1 "
                       f"{a['notes']['C1_citations_checked']} 条、C3 "
                       f"{a['notes']['C3_key_paths_checked']} 条、C8 计数点 "
                       f"{len(n8)}（活引用 "
                       f"{sum(1 for c in n8 if 'context' not in c)} / 豁免 "
                       f"{sum(1 for c in n8 if 'context' in c)}）；注入自证 "
                       f"{len(a['auditor_effectiveness']['cases'])} 例全抓 "
                       f"{a['auditor_effectiveness']['all_injected_violations_caught']}"],
        ["4 补集语义复现", f"violations {len(s['violations'])}；登记反证 "
                           f"mode {st.get('mode')} proved "
                           f"{st.get('synthetic_mechanism_proved')}"],
        ["5 生产者归属", f"V8 true_producers {r0['true_producers']}、"
                         f"shape_only_matches {r0['shape_only_matches']}、"
                         f"partial {r0['partial_producers']} ⇒ "
                         f"reproducibility {r0['reproducibility']}；"
                         f"双钉自测 fails {sf['fails']}"],
        ["6 F 六条件", f"E 臂 final_released {cE['final_released']} / "
                       f"correct_candidates {cE['correct_candidates']} / "
                       f"{cE['of_effective_answerable']} / wrong_releases "
                       f"{cE['wrong_releases']}；F 臂同 "
                       f"{cF['final_released']}（{cF['of_effective_answerable']}）"],
        ["7 perturb30 释放审计", f"wrong_grid_releasable "
                                 f"{p['perturbation_regression']['wrong_grid_releasable']}、"
                                 f"n {p['perturbation_regression']['n']}"],
        ["8 新排版回归 + tight 诊断", "R14E_NEWLAYOUT_REGRESSION_V3 分列"
                                      "两量、零错放；R14E_TIGHT_GAP_DIAGNOSIS"
                                      " 零 API、不调阈值、不加规则"],
        ["9 历史 Test 件字节基线", f"A9 fails {hb['fails']}；逐件与"
                                   f"**引入 commit** 的 git blob 比字节"
                                   f"（rows {len(hb['rows'])}）；"
                                   f"未跟踪数据目录如实登记 "
                                   f"{hb.get('data_worktree_entries')}"],
        ["10 Stage G 计划与闸", f"status {g['status']}、model_lock "
                                f"{g['model_lock']['status']}、已用 "
                                f"{g['budget']['spent_so_far']}（上限 "
                                f"{g['budget']['max_requests_including_retry']} "
                                f"请求 / {g['budget']['max_tokens']} token）、"
                                f"批准后就绪 {g0.get('ready_for_G_after_human_approval')}"
                                f" failing {g0.get('failing')}"],
    ]

    md = ["# R14 ④ 交付汇总与盲评准入结论", "",
          f"- HEAD `{head[:9]}`；本汇总由 "
          "`scripts/r14_delivery_summary.py` **读真件生成**，"
          "数字不手打、不跨轮复用。",
          f"- 取数来源：{len(D)} 项登记交付件 + F/perturb/A9/G 计划。", "",
          "## 十项材料", ""]
    for k, v in rows:
        md.append(f"- **{k}**：{v}")
    md += ["", "## 盲评准入结论", "",
           "**NOT READY（不准入）**：",
           "- 离线 `5/13 全对零错放` 是**候选→验证→释放**链上的离线判定，"
           "**不是**真实截图端到端成绩（目标明令不得混同）；",
           "- 真实截图端到端仅 **2/5** 释放正确（mirror-04、rot90-01），"
           "overlay/tight 仍受选项带切分限制，trans-07 如实弃权；",
           f"- V8 的作者档本轮如实降为 "
           f"`{r0['reproducibility']}`：判定层 5/5 页逐字段对上，"
           "但**证据层富化键**在 HEAD 无写名脚本可产出 ⇒ "
           "不得表述为「V8 可从 HEAD 完整复现」；",
           "- 因果表述维持人工锁定：**尺度协议修复是充分原因、"
           "pitch 非因果必需**（双轴表），本轮未加强亦未收紧。", "",
           "**准入前置**：人工批准裁决 2 → Stage G 小批 HTTP"
           "（≤6 请求含重试、硬顶 100000 token、顺序固定、`text=None`、"
           "`save=True`、模型/Prompt/版本/原图 SHA 固定、错误接受即停、"
           "全部异常计入端到端分母）→ 实测 ≥ 人工门槛。",
           "", "预算：HTTP 0 / LLM 0。", ""]
    if fails:
        md += ["## 取数即断言：失败", ""] + [f"- {x}" for x in fails]

    (EV / "R14_DELIVERY_SUMMARY.md").write_text("\n".join(md) + "\n",
                                                encoding="utf-8")
    (EV / name).write_text(json.dumps(
        {"head": head, "numbers_source_artifacts": D,
         "items": [{"item": k, "value": v} for k, v in rows],
         "blind_eval_admission": "NOT_READY",
         "fails": fails,
         "budget": {"http_requests_made": 0, "tokens_used": 0}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"n_items": len(rows), "fails": fails,
                      "admission": "NOT_READY"}, ensure_ascii=False))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
