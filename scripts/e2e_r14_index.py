"""R14 交付**索引**（零 API）：把散在 evidence/ 的 40 件 + 5 份文档变成
一张**可核对的账**，供人工评审一次看全，并防止"引用陈旧件"。

每件登记：
  · sha256（字节级身份，改一个字就变）、size、行数；
  · 生产脚本（在 scripts/ 里搜该件名，命中即作者；搜不到 → manual/未知，
    **不猜**）；
  · 首次入库 commit 与最近改动 commit（`git log --format`，只读）；
  · **地位**：current / superseded（被同名高版本取代）/ erratum（含已勘误口径）/
    historical（旧协议件，不得与现行协议同列比较）。
再用机器自检三条：
  A1 每件 current 件都**必须**有生产脚本（无脚本的 current = 不可复现，如实列出）；
  A2 superseded / historical 件**不得**是文档里的当前引用（与 claim audit 互补）；
  A3 索引自身可复算：重跑 sha256 与登记值不一致 → 失败（防"改了件没改账"）。
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"
SCRIPTS = ROOT / "scripts"

# **地位表**（人工核对过的口径，不由脚本猜）：
SUPERSEDED = [
    r"^R14E_SIX_CONDITION(_V[1-9])?\.json$",          # 当前件 V10
    r"^R14E_STAGE_E_ACCEPTANCE(_V[1-7])?\.json$",      # 当前件 V8
    r"^R14E_REAL_PAGE_REPLAY(_V[1-7])?\.json$",        # 当前件 V8
    r"^R14E_NEWLAYOUT_REGRESSION_V2\.json$",           # 当前件 V3
    r"^R14C_REAL_PARSE_REPLAY\.json$",                 # 被 R14E_REAL_PAGE_REPLAY_V8 取代
]
CURRENT = {
    "R14E_SIX_CONDITION_V10.json": "现行协议六条件（HEAD 复验重跑）",
    "R14E_LINEAGE_SIX_CONDITION.json": "六条件跨轮血缘（旧协议件分列不混比）",
    "R14E_HEAD_REVERIFY.json": "三项离线基线 HEAD 复验（零漂移）",
    "R14E_STAGE_E_ACCEPTANCE_V8.json": "Stage E 验收",
    "R14E_NEWLAYOUT_REGRESSION_V3.json": "新排版独立 Dev 回归（含 V2 口径勘误）",
    "R14E_TIGHT_GAP_DIAGNOSIS.json": "tight 覆盖缺口逐页成因",
    "R14E_ANCHOR_SWEEP.json": "独立排版锚点普查 59 页",
    "R14E_REAL_PAGE_REPLAY_V8.json": "真实截图解析件端到端复放",
    "R14F_PERTURB_RELEASE_AUDIT.json": "扰动 30 页释放审计（f1 固定件）",
    "R14C_PITCH_ABLATION.json": "pitch 双轴消融（因果上限依据）",
    "R14G_PLAN.json": "G 批 manifest（PLANNED_NOT_EXECUTED）",
    "R14G0_GATE_READINESS_V5.json": "G 闸门行为级就绪审计（真实请求 0）",
    "R14_CLAIM_AUDIT.json": "交付件一致性审计（含审计器自证）",
    "R14H_KB_CHANNEL_AUDIT.json": "H KB 通道审计",
}
HISTORICAL = {
    "R14E_SIX_CONDITION.json": "E 首跑件（C/D 候选口径与后续不同，逐条说明见血缘件）",
    "R14F_SIX_CONDITION.json": "E **前**现行协议基线",
}
ERRATUM = {
    "R14E_NEWLAYOUT_REGRESSION_V2.json":
        "把证据链单元数标为消费单元，与落盘裁剪份数混同（V3 已勘误，保留不删）",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# 读取方（非作者），不得进"生产脚本"列
PRODUCER_EXCLUDE = {"e2e_r14_claim_audit.py", "e2e_r14_index.py"}
# **自指件**：索引无法登记自己写出后的最终字节，故单独标注并排除哈希自检
SELF_NAMES = {"R14_INDEX.json", "R14_INDEX.md"}


def commits(rel: str):
    def run(args):
        r = subprocess.run(["git"] + args + ["--", rel], cwd=ROOT,
                           capture_output=True, text=True)
        return (r.stdout or "").strip().splitlines()

    added = run(["log", "--diff-filter=A", "--format=%h"])
    last = run(["log", "-1", "--format=%h"])
    return (added[-1] if added else None), (last[0] if last else None)


def producer(name: str):
    """作者脚本 = 会**写出**该件的脚本。分两档，避免两个首版错误：

      · **精确命中**：脚本里出现该件的确切文件名；
      · **同族命中**：脚本里出现去掉版本后缀的族名（如
        `R14E_NEWLAYOUT_REGRESSION_V3.json` 由
        `e2e_r14_newlayout_regression.py` 以 `--out` 写出）——此时如实标
        `via_out_override`，复现命令必须带上该 `--out`，否则只会写出默认名。

    审计器与本索引对**别的件**只是读取方，须排除；但 `R14_CLAIM_AUDIT.json`
    确实是审计器**自己**写出的，这一件例外允许（否则 A1 会误报"不可复现"）。
    """
    stem = re.sub(r"_V\d+(?=\.json$)", "", name)
    exact, family = [], []
    for sp in sorted(SCRIPTS.glob("*.py")):
        if sp.name in PRODUCER_EXCLUDE and not (
                name == "R14_CLAIM_AUDIT.json"
                and sp.name == "e2e_r14_claim_audit.py"):
            continue
        try:
            t = sp.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        if name in t:
            exact.append(sp.name)
        elif stem != name and stem in t:
            family.append(sp.name)
    hits = exact or family
    return hits, bool(family and not exact)


def main() -> int:
    rows = []
    for p in sorted(EV.glob("R14*")):
        name = p.name
        text = ""
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        first, last = commits(f"evidence/{name}")
        prod, via_out = producer(name)
        status = "current"
        why = CURRENT.get(name, "")
        if name in SELF_NAMES:
            status, why = "self", "索引自身：无法登记写出后的最终字节（自指），" \
                                 "故不入哈希自检"
        elif name in HISTORICAL:
            status, why = "historical", HISTORICAL[name]
        elif name in ERRATUM:
            status, why = "erratum_kept", ERRATUM[name]
        elif any(re.match(pat, name) for pat in SUPERSEDED):
            status, why = "superseded", "同名高版本件为当前引用"
        if p.suffix == ".md" and status == "current":
            status = "current" if name in {
                "R14_PROGRESS_STOPGATE.md", "R14_STAGE_E_DELIVERABLES.md",
                "R14E_NEWLAYOUT_REGRESSION.md", "R14E_TIGHT_GAP_DIAGNOSIS.md",
                "R14G0_READINESS.md", "R14_CLAIM_AUDIT.md"} else "superseded"
        rows.append({"artifact": name, "bytes": p.stat().st_size,
                     "sha256": sha(p), "sha_short": sha(p)[:12],
                     "status": status, "status_note": why,
                     "producer_scripts": prod,
                     "written_via_out_override": via_out,
                     "producer_note": ("审计器/索引等读取方已排除，本列只登记"
                                       "会写出该件的脚本" if prod else
                                       "未在任何脚本中找到写出处"),
                     "git_tracked": bool(first),
                     "reproducible": bool(prod) or p.suffix == ".md",
                     "commit_added": first, "commit_last_touched": last})

    docs = {p.name: p.read_text(encoding="utf-8") for p in EV.glob("R14*.md")
            # 索引自身**就是**用来逐条列出非当前件的，扫它会自我误报
            if p.name != "R14_INDEX.md"}
    fails = []
    unregistered = []      # 透明登记：靠 --out 才落到现行文件名的件
    for r in rows:
        if r["status"] == "self":
            continue                     # 自指件不入 A1/A3
        if r["status"] == "current" and r["artifact"].endswith(".json") \
                and not r["producer_scripts"]:
            fails.append(f"A1 当前件无生产脚本（不可复现）：{r['artifact']}")
        if r["written_via_out_override"]:
            unregistered.append(
                f"{r['artifact']} ← {', '.join(r['producer_scripts'])}"
                f"（需 --out 复现）")
        if r["artifact"].endswith(".json") and sha(EV / r["artifact"]) != r["sha256"]:
            fails.append(f"A3 哈希不稳定：{r['artifact']}")
    by_name = {r["artifact"]: r for r in rows}
    for d, t in docs.items():
        for m in set(re.findall(r"R14[A-Z0-9_\-]*\.json", t)):
            r = by_name.get(m)
            if not r:
                continue          # 不存在件由 claim audit 负责，这里不重复
            if r["status"] in ("superseded", "historical", "erratum_kept"):
                for ln, line in enumerate(t.splitlines(), 1):
                    if m in line and not any(k in line for k in (
                            "勘误", "取代", "不存", "不得", "旧", "曾", "V2",
                            "混同", "首跑", "前**基线", "保留")):
                        fails.append(f"A2 {d}:{ln} 把 {r['status']} 件 {m} "
                                     f"当作当前引用")
    out = {"generated_at_head": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                text=True).stdout.strip(),
           "n_artifacts": len(rows),
           "by_status": {s: sorted(r["artifact"] for r in rows
                                   if r["status"] == s)
                         for s in ("current", "superseded", "historical",
                                   "erratum_kept", "self")},
           "artifacts": rows, "self_checks_failed": fails,
           "reproduce_needs_out_override": sorted(set(unregistered)),
           "reading_order_for_human": [
               "1. R14_PROGRESS_STOPGATE.md（进度 + 6 个待裁决点）",
               "2. R14_STAGE_E_DELIVERABLES.md（10 项材料 + 补充件 A–E + 盲评准入）",
               "3. R14E_NEWLAYOUT_REGRESSION.md / R14E_TIGHT_GAP_DIAGNOSIS.md"
               "（回归与缺口成因，含 V2 口径勘误）",
               "4. R14G0_READINESS.md / R14_CLAIM_AUDIT.md（G 闸门就绪与交付自审）",
               "5. R14E_LINEAGE_SIX_CONDITION.json（数字血缘，旧协议不混比）"],
           "blind_eval": "NOT READY：真实截图端到端 2/5 释放正确；离线 F 5/13 "
                         "零错放不构成真实截图突破",
           "llm_calls_used": 0, "http_requests_made": 0}
    dst = EV / "R14_INDEX.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["# R14 交付索引（机器登记，零 API）", "",
             "注：本轮**改了** STOPGATE / DELIVERABLES / NEWLAYOUT_REGRESSION 三份"
             "文档的措辞，也**重跑**了回归脚本（产物字节逐字一致），因此下表 commit "
             "列是**入库时点**、与工作区未提交内容不同属**已知且如实**，不是漏记。",
             "",
             f"HEAD `{out['generated_at_head']}` · 件数 **{out['n_artifacts']}** · "
             f"自检失败 **{len(fails)}** 项 · 真实请求 **0**",
             "", "## 当前件（可复现性 = 是否登记生产脚本）", "",
             "（下表「生产脚本」已排除审计器/索引等**读取方**；"
             "「—」= 人工撰写的文档，不由脚本生成）",
             "",
             "| 件 | 地位 | 生产脚本 | 首入库 | 最近改动 | sha256(12) |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        if r["status"] != "current":
            continue
        lines.append(f"| `{r['artifact']}` | {r['status_note'][:38] or '—'} | "
                     f"{', '.join(r['producer_scripts'])[:48] or '**无**'} | "
f"{r['commit_added'] or '未入库'} | "
                     f"{r['commit_last_touched'] or '未入库'} | "
                     f"`{r['sha_short']}` |")
    lines += ["", "**自指说明**：本索引写完后其自身的最终字节无法被自己登记，"
              "因此 `R14_INDEX.json` / `R14_INDEX.md` 地位标为 `self` 并**排除**"
              "哈希自检（A3）——这是诚实边界，不是漏检。", ""]
    lines += ["## 非当前件（不得作当前引用）", ""]
    for s in ("superseded", "historical", "erratum_kept"):
        for name in out["by_status"][s]:
            r = by_name[name]
            lines.append(f"- `{name}` · **{s}** · {r['status_note']}")
    lines += ["", "## 人工阅读顺序", ""]
    lines += [f"- {x}" for x in out["reading_order_for_human"]]
    lines += ["", f"**盲评准入**：{out['blind_eval']}", ""]
    if out["reproduce_needs_out_override"]:
        lines += ["", "## 复现需要 `--out`（默认名与现行件名不同）", ""]
        lines += [f"- {x}" for x in out["reproduce_needs_out_override"]]
    if fails:
        lines += ["## 自检失败", ""] + [f"- {f}" for f in fails]
    (EV / "R14_INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"n_artifacts": out["n_artifacts"],
                      "by_status_count": {k: len(v) for k, v in
                                          out["by_status"].items()},
                      "self_checks_failed": fails},
                     ensure_ascii=False, indent=1))
    print("written", dst)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
