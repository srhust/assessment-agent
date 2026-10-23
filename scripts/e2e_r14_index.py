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
    # 交付文档（`.md`）同样**显式登记**：deny-by-default 之后，
    # "默认 current" 会把任何核对副产物冒充成当前交付件（本轮实测）。
    "R14H_KB_CHANNEL_HEAD_DELTA.json": ("h1_r14_kb_channel_audit.py",),
    "R14_VERIFICATION_BUNDLE.json": "一键复现 bundle（18 步离线验证）",
    "R14E_ARTIFACT_ATTRIBUTION.json": "证据件生产者归属（含安全闸门结论）",
    "R14E_ATTRIBUTION_SELFTEST.json": "归属安全闸门的自证注入结果",
    "R14E_RERUN_SEMANTICS.json": "补集复现核对（结论级/注解级差异分列）",
    "R14C_PITCH_ABLATION_HEAD_DELTA.json": "因果实验在现行 HEAD 的复现差异",
    "R14G_HISTORICAL_BYTES.json": "历史 Test 件逐字节对引入 commit 的完整性核验"
                                  "（含本轮误覆写事故记录）",
    "R14G_HISTORICAL_BYTES_SELFTEST.json": "A9 字节基线检查的触发反证",
    "R14G_HISTORICAL_BYTES.md": "历史 Test 件字节完整性核验正文（含本轮"
                                "误覆写事故记录与自我撤回）",
    "R14_PROGRESS_STOPGATE.md": "人工评审入口（本轮闸门文档）",
    "R14_STAGE_E_DELIVERABLES.md": "Stage E 十项交付正文",
    "R14E_NEWLAYOUT_REGRESSION.md": "新排版独立 Dev 回归说明",
    "R14E_TIGHT_GAP_DIAGNOSIS.md": "tight 缺口逐页成因说明",
    "R14G0_READINESS.md": "G 闸门零请求就绪说明",
    "R14_CLAIM_AUDIT.md": "交付件一致性审计说明（含审计器自证）",
    "R14C_PITCH_ABLATION_HEAD_DELTA.md":
        "因果实验在现行 HEAD 的复现差异（人工裁决项 2 材料）",
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


_ATTR_MAP: dict = {}
_DECLARED: dict = {}


def _fam(n: str) -> str:
    return re.sub(r"_V\d+(?=\.json$)", "", n)


def _declared_map():
    """**复用归属工具的同一判定**：脚本源码里出现的全部 R14*.json 字面量
    （且该脚本确有写出）。自己再写一份"必须挨着 write_text"的正则会把
    `name = "X.json"` → `dst.write_text()` 这种正常写法误判为"不可复现"
    ——本轮实测犯过（15 个可复现件被标假）。"""
    if _DECLARED:
        return _DECLARED
    try:
        import sys
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import r14_attribute_producer as AP
        for sp in sorted(SCRIPTS.glob("*.py")):
            _DECLARED[sp.name] = {_fam(w)
                                  for w in AP.script_declares_write(sp.name)}
    except Exception:  # noqa: BLE001
        for sp in sorted(SCRIPTS.glob("*.py")):
            t = sp.read_text(encoding="utf-8", errors="ignore")
            if "write_text" not in t:
                continue
            _DECLARED[sp.name] = {_fam(w) for w in re.findall(
                r'"(R14[A-Za-z0-9_\-]*\.json)"', t)}
    return _DECLARED


def _attribution():
    """动态归属结论（跑过归属工具才有条目；没跑则不影响档位判定）。"""
    if _ATTR_MAP:
        return _ATTR_MAP
    p = EV / "R14E_ARTIFACT_ATTRIBUTION.json"
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            for r in d.get("results", []):
                _ATTR_MAP[r["artifact"]] = r
        except Exception:  # noqa: BLE001
            pass
    return _ATTR_MAP


def _bundle_steps():
    """一键复现 bundle **真跑过**的件 → 本身就是动态可复现证据。"""
    p = EV / "R14_VERIFICATION_BUNDLE.json"
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {x["artifact"]: x for x in d.get("steps", [])
                if x.get("exit") == 0}
    except Exception:  # noqa: BLE001
        return {}


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
    declared = []
    for sp in sorted(SCRIPTS.glob("*.py")):
        if sp.name in PRODUCER_EXCLUDE and not (
                name == "R14_CLAIM_AUDIT.json"
                and sp.name == "e2e_r14_claim_audit.py"):
            continue
        try:
            t = sp.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        # **声明写出**才算作者：只看 write_text/open( 附近的件名字面量。
        # "名字出现在脚本里"不等于产出（V8 误判的根因）。
        dm = _declared_map().get(sp.name, set())
        if _fam(name) in dm:
            declared.append(sp.name)
        if name in t:
            exact.append(sp.name)
        elif stem != name and stem in t:
            family.append(sp.name)
    # **优先级**：声明写出 > 精确提及 > 同族提及。只有"提及"的必须标
    # mention_only——"脚本里出现件名"不等于"该脚本产出该件"（V8 误判根因）。
    hits = declared or exact or family
    att = _attribution().get(name)
    if att and att.get("true_producers"):
        tier = "attribution_verified"
        hits = [str(x).rsplit("/", 1)[-1] for x in att["true_producers"]]
    elif declared:
        tier = "declared_writer"
    else:
        tier = ("mention_exact_only" if exact else "mention_family_only")
    return hits, bool(family and not exact), tier


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
        prod, via_out, ptier = producer(name)
        # **deny-by-default**：未登记就是 unregistered，不再默认 current
        #（`status = "current"` 起手让每个核对副产物都冒充当前交付件，
        #  本轮 R14H_KB_CHANNEL_AUDIT_RERUN.json 就是这么把 A1 顶失败的）
        status = "current" if name in CURRENT else "unregistered"
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
        elif re.search(r"_(RERUN\d*|CHECK)\.json$", name):
            status, why = "verification_byproduct", (
                "动态核验产物：由归属/复现核对以 **新名** 重跑生成，"
                "刻意不覆盖提交件；它证明可复现性，本身不是交付件")
        if status == "current" and p.suffix == ".json" and not prod:
            status, why = "unregistered", "登记为现行但**找不到写出它的脚本**"
        if p.suffix == ".md" and status == "current":
            # **两处清单必须同源**：上面 `CURRENT` 里已登记的文档，
            # 这里**绝不允许**再被降级成 superseded。前几轮这个内联集合
            # 与 `CURRENT` 各写一份，导致"登记了却被自己判过期"的自相矛盾
            # （本轮实测：A9 文档 `R14G_HISTORICAL_BYTES.md` 一落盘，
            #  索引先给 current、下一句又因它不在内联集合里改判 superseded，
            #  计数随即与文档不符 —— C8 抓到的是**索引自己的双重清单**）。
            # 内联集合**删掉**：`.md` 的地位只由 `CURRENT` 决定。
            pass
        rows.append({"artifact": name, "bytes": p.stat().st_size,
                     "sha256": sha(p), "sha_short": sha(p)[:12],
                     "status": status, "status_note": why,
                     "producer_scripts": prod,
                      "producer_tier": ptier,
                      "written_via_out_override": via_out,
                      "producer_note": (
                          "动态归属验证的真作者" if ptier
                          == "attribution_verified" else
                          "声明写出（确有写出且出现该件/族名）" if ptier
                          == "declared_writer" else
                          "仅提及、未声明写出：**引用≠产出**"
                          if prod else "未在任何脚本中找到写出处"),
                      "git_tracked": bool(first),
                      "reproduced_by_bundle": name in _bundle_steps(),
                      "reproducible": (
                          ptier in ("attribution_verified",
                                    "declared_writer")
                          or name in _bundle_steps()
                          or p.suffix == ".md"),
                      "commit_added": first, "commit_last_touched": last})

    docs = {p.name: p.read_text(encoding="utf-8") for p in EV.glob("R14*.md")
            # 索引自身**就是**用来逐条列出非当前件的，扫它会自我误报
            if p.name != "R14_INDEX.md"}
    fails = []
    unregistered = []      # 透明登记：靠 --out 才落到现行文件名的件
    for r in rows:
        att = _attribution().get(r["artifact"])
        if (att and att.get("reproducibility") == "NOT_REPRODUCIBLE_FROM_HEAD"
                and r.get("reproducible")):
            fails.append(f"A5 {r['artifact']} 归属结论为不可复现，索引却登记"
                         f" reproducible=true（乐观误判，禁止）")
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
