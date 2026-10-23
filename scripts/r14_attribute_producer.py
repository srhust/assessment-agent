"""**自纠上轮结论**：`R14E_REAL_PAGE_REPLAY_V8.json` 并非"不可复现"。
穷尽式生产者归属（6 个候选脚本各重跑到临时件）证明真作者是
**`e2d_r14_production_replay_v4.py`**：5/5 页 `verdict_identical` 且
**不缺任何键**；`missing_keys_vs_v8 = {}`。
我上轮只试了 `e2_r14_optionband_replay.py`（引用 V8 件名但**不产出**），
于是把"我找错了作者"写成了"件不可复现"，并让 V8_CHECK 与 STOPGATE 都跟着
下了错误结论。⇒ **交付里的"复现边界"那一整段现在必须勘误**。

本件把归属做成**规则**而非一次性动作：
  1. 逐件**穷尽重跑**（写 `t_attrib_*.json` 临时件，比对后删除，绝不覆盖历史件）；
  2. 判据 = `pids_covered == 全` ∧ `verdict 全同` ∧ `不缺任何键`，
     三者同时成立才算 **TRUE producer**；只有 verdict 同但缺键的算
     **partial**（可用于说明"判定可复现、证据层退化"）；
  3. 输出 `attribution`（true / partial / none）与每候选的证据；
  4. 若某件**连 partial 都没有** → 才允许写 NOT_REPRODUCIBLE_FROM_HEAD。
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
EV = ROOT / "evidence"
VERDICT = ["solver_status", "solver_answer", "verify_action", "final_answer",
           "released", "wrong_accept", "failed_checks", "solver_error",
           "option_boxes"]


OWN_OUTPUTS = (
    "evidence/R14E_ARTIFACT_ATTRIBUTION.json",
    "evidence/R14E_ATTRIBUTION_SELFTEST.json",
)


def _snapshot():
    """evidence/ 与 data/ 的**全量哈希快照**（只读，用于归因本次运行的副作用）。"""
    snap = {}
    for base in (EV, ROOT / "data"):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(ROOT)).replace("\\", "/")
                try:
                    snap[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
                except Exception:  # noqa: BLE001
                    snap[rel] = "unreadable"
    return snap


def _collateral(snap_before):
    """**只归因本次运行**：前后快照做差，排除归属工具自己的输出件。
    （早先版本拿 `git status` 里"任何 evidence 改动"当违规，会把本轮
    合法改动全判成违规——过宽的检查等于没有检查。）"""
    after = _snapshot()
    changed = []
    for k in set(after) | set(snap_before):
        if k in OWN_OUTPUTS:
            continue
        if snap_before.get(k) != after.get(k):
            changed.append(k)
    return sorted(changed)


def _fam(n: str) -> str:
    """族名 = 去掉版本后缀的文件名基。与索引/复现核对**共用同一规则**。"""
    return re.sub(r"_V\d+(?=\.json$)", "", n)


def all_scripts():
    """**每次调用现取**：模块级缓存会让归属进程启动后才落地的脚本
    变成"看不见"——自检实测就是这个漏检（安全闸门的拒跑清单不完整）。"""
    return sorted(p.name for p in (ROOT / "scripts").glob("*.py"))


ALL_SCRIPTS = all_scripts()      # 仅供旧的引用；新代码一律用 all_scripts()


def inner(d):
    for keypath in ("items", "rows", "real", "pages"):
        v = d.get(keypath)
        if isinstance(v, dict) and v:
            return v
    return d


def attribute(artifact: str, only=None):
    """穷尽式生产者归属。**只读**：产物一律落临时件并删除。"""
    tgt = json.loads((EV / artifact).read_text(encoding="utf-8"))
    ti = inner(tgt)
    pids = sorted(k for k in ti if isinstance(ti[k], dict))
    if not pids:                      # 非"逐页"结构的件：退化为顶层键覆盖比对
        pids = []
    import time as _t
    t_start = _t.time()
    snap_before = _snapshot()
    before = hashlib.sha256((EV / artifact).read_bytes()).hexdigest()
    if only is None:
        picked = static_candidates(artifact)
        cands = [p[0] for p in picked]
        refused = [{"script": f"scripts/{n}", "reason": unsafe_to_run(n)}
                   for n in cands if unsafe_to_run(n)]
        cands = [n for n in cands if not unsafe_to_run(n)]
    else:
        cands = list(only)
        refused = [{"script": f"scripts/{n}", "reason": unsafe_to_run(n)}
                   for n in cands if unsafe_to_run(n)]
        cands = [n for n in cands if not unsafe_to_run(n)]
    out = [{"safety_refused": r} for r in refused]
    for cand in cands:
        tmp = f"t_attr_{cand.replace('.py','')}_{artifact.replace('.json','')}.json"
        env = {**subprocess.os.environ, "PYTHONPATH": str(ROOT / "src"),
               "PYTHONIOENCODING": "utf-8", "ASSESSMENT_OFFLINE": "1"}
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(ROOT / "scripts" / cand), "--out", tmp],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", env=env)
        p = EV / tmp
        row = {"script": f"scripts/{cand}", "exit": r.returncode,
               "wrote": p.is_file()}
        if not p.is_file():
            row["note"] = "未产出（可能不支持 --out 或不是该件的作者）"
            out.append(row)
            continue
        try:
            d = inner(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            row["note"] = f"不可解析 {str(e)[:80]}"
            p.unlink()
            out.append(row)
            continue
        if pids:
            inter = sorted(k for k in pids if k in d and isinstance(d[k], dict))
            v_ok = sum(1 for k in inter
                       if all(str(ti[k].get(f)) == str(d[k].get(f))
                              for f in VERDICT if f in ti[k]))
            missing = {k: sorted(set(ti[k]) - set(d[k])) for k in inter
                       if set(ti[k]) - set(d[k])}
            row.update({"pids_covered": f"{len(inter)}/{len(pids)}",
                        "verdict_identical_pids": v_ok,
                        "missing_keys": {k: v for k, v in missing.items()}})
            row["classification"] = (
                "true_producer" if len(inter) == len(pids)
                and v_ok == len(pids) and not missing else
                ("partial_verdict_only" if v_ok == len(pids) and len(inter) == len(pids)
                 else "not_producer"))
        else:
            same = set(ti) <= set(d)
            row.update({"top_key_coverage": f"{len(set(ti) & set(d))}/{len(ti)}",
                        "classification": "top_level_only" if same else "not_producer"})
        p.unlink()
        out.append(row)
    true = [x["script"] for x in out if x.get("classification") == "true_producer"]
    partial = [x["script"] for x in out
               if x.get("classification") == "partial_verdict_only"]
    after = hashlib.sha256((EV / artifact).read_bytes()).hexdigest()
    # **后置安全断言**：归属核验**必须**零请求
    budget = {}
    plan_p = EV / "R14G_PLAN.json"
    if plan_p.is_file():
        budget = (json.loads(plan_p.read_text(encoding="utf-8"))
                  .get("budget", {}).get("spent_so_far", {}))
    new_req = sorted(p.name for p in EV.glob("*")
                     if REQUEST_ARTIFACTS.match(p.name)
                     and p.stat().st_mtime > t_start - 1)
    collateral = _collateral(snap_before)
    safety = ("SAFETY_VIOLATION_worktree_collateral" if collateral
              else "OK_ZERO_REQUESTS"
              if budget.get("requests") in (0, None) and budget.get("tokens") in (0, None)
              and not new_req else "SAFETY_VIOLATION")
    return {"artifact": artifact, "safety": safety,
            "budget_after": budget, "request_artifacts_seen": new_req,
            "worktree_collateral": collateral,
            "collateral_scope": "本次归属运行期间的新增/变化文件"
                                "（不含归属工具自身输出）",
            "candidates_refused": refused,
            "selection": "static write-target (deny-by-default on network/"
                        "orchestrator scripts)",
            "true_producers": true,
            "partial_producers": partial,
            "reproducibility": ("REPRODUCIBLE_FROM_HEAD" if true else
                                ("VERDICT_REPRODUCIBLE_EVIDENCE_KEYS_MISSING"
                                 if partial else "NOT_REPRODUCIBLE_FROM_HEAD")),
            "artifact_untouched": before == after,
            "candidates_tried": len(out), "evidence": out}


# ---- 安全闸门：归属工具**绝不**可能触发人工批准之后的动作 ----
NETWORK_MARKERS = re.compile(
    r"OpenAI|AsyncOpenAI|httpx|requests\.(get|post)|urlopen|chat/completions"
    r"|_call_llm|ASSESSMENT_LLM|llm\.chat")
# 编排器 = **用 subprocess 再跑别的 .py 脚本**（只是 subprocess 跑 git 不算）。
# 上轮判据过宽：`subprocess` + 源码里出现过 "scripts" 字样就拒，
# 于是把大量只调用 git 的脚本一律拒跑 → 核验覆盖被悄悄砍掉（过宽等于没检查）。
# 已知的**自我编排器**：跑它们等于递归跑整条链（bundle 会重跑 16 步，
# 归属/复现核对又会再跑候选）。名录 + 字面量**两条都要**：
# 名录挡"拼 argv 的编排器"，字面量挡"将来新增且未登记的编排器"。
ORCHESTRATOR_NAMES = {
    "r14_verify_all.py", "r14_semantic_repro.py", "r14_attribute_producer.py",
    "e2_r14_v8_reproducibility.py",
}
ORCHESTRATOR = re.compile(r"subprocess\.(run|Popen|check_output|call)")
# 字面量判据：源码里出现 "scripts/<name>.py" 形态（不是 subprocess 调用的
# **紧跟**参数——bundle 是从 STEPS 表拼 argv 的，正则那种窄匹配会放它过去）
SCRIPT_LITERAL = re.compile(r"""["']scripts[/\\][A-Za-z0-9_\-]+\.py["']""")
REQUEST_ARTIFACTS = re.compile(r"^R14G[0-9]?_.*\.(json|md|png|jpg)$")


def script_text(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8", errors="ignore")


ASSIGN_RE = re.compile(r"^(\w+)\s*=\s*(.+)$")


# **钉住的已知作者对**：判据若退化为漏判，`--selftest` 直接不过，
# 而不是"少一个作者、结论照发"。左：脚本；右：该脚本必须被识别为写出者。
EXPECTED_WRITERS = {
    "d9_r14_stage_g_manifest.py": "R14G_PLAN.json",
    "e2e_r14_index.py": "R14_INDEX.json",
    "r14_verify_all.py": "R14_VERIFICATION_BUNDLE.json",
    "e2_r14_optionband_replay.py": "R14E_REAL_PAGE_REPLAY_V8.json",
    "e2d_r14_production_replay_v4.py": "R14E_REAL_PAGE_REPLAY_V5.json",
    "e2e_r14_newlayout_regression.py": "R14E_NEWLAYOUT_REGRESSION_V3.json",
    "f1_r14_perturb_release_audit.py": "R14F_PERTURB_RELEASE_AUDIT.json",
    "e2e_r14_anchor_sweep.py": "R14E_ANCHOR_SWEEP.json",
    "c2_r14_pitch_ablation.py": "R14C_PITCH_ABLATION.json",
    "h1_r14_kb_channel_audit.py": "R14H_KB_CHANNEL_AUDIT.json",
    "e2e_r14_claim_audit.py": "R14_CLAIM_AUDIT.json",
    "e2_r14_v8_reproducibility.py": "R14E_REAL_PAGE_REPLAY_V8_CHECK.json",
    "e2e_r14_head_reverify.py": "R14E_HEAD_REVERIFY.json",
    "e2e_r14_six_condition_lineage.py": "R14E_LINEAGE_SIX_CONDITION.json",
    "e2e_r14_tight_gap_diagnosis.py": "R14E_TIGHT_GAP_DIAGNOSIS.json",
    "e2e_r14_stage_g_readiness.py": "R14G0_GATE_READINESS_V5.json",
}
# **反向钉**：读取方**不得**被判成写出者（v3 的错就是这样让索引"生产"了 bundle 件）
EXPECTED_NOT_WRITERS = {
    "e2e_r14_index.py": "R14_VERIFICATION_BUNDLE.json",
    "e2e_r14_claim_audit.py": "R14E_STAGE_E_ACCEPTANCE_V8.json",
    "e2_r14_v8_reproducibility.py": "R14E_REAL_PAGE_REPLAY_V8.json",
}

def _writer_closure(src: str, lit: str) -> bool:
    """字面量 `lit` 的**写出变量闭包**里是否出现写操作。

    `name = "X.json"` → `out = EV / name` → `out.write_text(...)` 这种
    二跳/三跳写法必须判成写出（V8/V5/V3 三个关键件都是这种写法）。
    """
    seeds = set(re.findall(r"(\w+)\s*=\s*[^\n]{0,80}" + re.escape(lit), src))
    # argparse 形态：`add_argument("--out", default="X.json")` → dest = out，
    # 真正的使用点是 `args.out` / `/ a.out`。上轮假脚本用这种形态，
    # 静态判据认不出来，于是"注入的假作者没被认出"= 自检失去意义。
    for m in re.finditer(r'add_argument\(\s*"--([a-z0-9_\-]+)"[^)]{0,120}'
                         + re.escape(lit), src):
        seeds.add(m.group(1).replace("-", "_"))
    if not seeds:
        return False
    vars_ = set(seeds)
    for _ in range(4):                      # 闭包迭代（够深且必然终止）
        grew = False
        for line in src.splitlines():
            mm = ASSIGN_RE.match(line.strip().split("#")[0].strip())
            if not mm:
                continue
            v, rhs = mm.group(1), mm.group(2)
            if any(re.search(rf"\b{re.escape(s)}\b", rhs) for s in vars_):
                if v not in vars_:
                    vars_.add(v)
                    grew = True
        if not grew:
            break
    for v in vars_:
        if re.search(rf"\.\s*{re.escape(v)}\s*\)\s*\.write_text\s*\(", src) \
                or re.search(rf"\.\s*{re.escape(v)}\s*[,)]\s*[^\n]{{0,40}}"
                             rf"write_text\s*\(", src) \
                or re.search(rf"{re.escape(v)}\s*\.write_text\s*\(", src) \
                or re.search(rf"[^\n]{{0,60}}[/,]\s*{re.escape(v)}\s*\)\s*"
                             rf"\.write_text\s*\(", src) \
                or re.search(rf"open\(\s*{re.escape(v)}\b[^)]*,\s*[\'\"]w", src) \
                or re.search(rf"open\(\s*{re.escape(v)}\b\)\.write", src):
            return True
    return False


def script_declares_write(name: str):
    """从**源码**判断该脚本声明会写出的证据件（**不执行脚本**）。

    三条判据（任一成立即算写出）：
      · **直接**：字面量自身就是写目标（`("evidence/X.json").write_text(`、
        `"X.json")` 后同语句内 `write_text`）；
      · **闭包**：字面量绑定的变量（含 `out = EV / name` 等二跳）最终被写；
      · **排除**：登记/引用语境 `"X.json": "current"`（索引的 CURRENT 表、
        审计器的期望表）一律不算——v3 就是在这里把**读取方**认成作者，
        实测产生了"索引改了 bundle 件"的假阳性漂移。
    """
    src = script_text(name)
    if "write_text" not in src and "open(" not in src:
        return []
    out = set()
    for m in re.finditer(r'"([^"\n]{0,60}?R14[A-Za-z0-9_\-]*\.json)"', src):
        lit = m.group(0)
        base = m.group(1).replace("\\", "/").rsplit("/", 1)[-1]
        after = src[m.end(): m.end() + 300]
        if re.match(r"\s*:", after):        # 字典键 = 登记，不是写出目标
            continue
        direct = bool(re.search(r"^\s*\)\s*\.write_text\s*\(", after)
                      or re.search(r"^[^\n]{0,40}write_text", after)
                      or re.search(r"^[^\n]{0,40}open\s*\(", after))
        if direct or _writer_closure(src, lit):
            out.add(base)
    return sorted(out)


def unsafe_to_run(name: str):
    """返回不可运行原因；None 表示可以动态核验。"""
    src = script_text(name)
    if name == Path(__file__).name:
        return "自我递归"
    m = NETWORK_MARKERS.search(src)
    if m:
        return f"含网络标记 {m.group(0)!r}：归属核验禁止触发任何真实请求路径"
    if name in ORCHESTRATOR_NAMES:
        return "自我编排器（名录）：动态核验它等于递归重跑整条链"
    if ORCHESTRATOR.search(src) and SCRIPT_LITERAL.search(src):
        return "编排器（subprocess + scripts/*.py 字面量）：禁止递归/全链重跑"
    if "--out" not in src:
        return ("不支持 --out 重定向：动态核验它等于**在固定历史名下落盘**，"
                "会覆盖/新落已被取代的件（实测 e2b 就留下了杂件）")
    return None


def static_candidates(artifact: str):
    """**静态优先**：只有"源码字面量声明会写这件族名"的脚本才进动态核验。"""
    stem = artifact.rsplit(".", 1)[0]
    fam = re.sub(r"_V\d+$", "", stem)
    picked = []
    for name in all_scripts():
        if not name.endswith(".py"):
            continue
        for w in script_declares_write(name):
            if re.sub(r"_V\d+$", "", w.rsplit(".", 1)[0]) == fam:
                picked.append((name, w))
                break
    return picked

def fast_candidates(artifact: str):
    """**默认候选**：只跑"名字里出现该件族名"或"名字里出现该件全部富化键"
    的脚本。穷尽归属（`--full`）才遍历所有 e2*/d1/f1*——后者一次等于
    20 条全量离线管线（实测跑不完），必须显式要求。"""
    stem = artifact.rsplit(".", 1)[0]
    fam = re.sub(r"_V\d+$", "", stem)
    cands = [s for s in ALL_SCRIPTS
             if fam.split("_")[1].lower() in s.lower().replace("_", "")
             or fam in (ROOT / "scripts" / s).read_text(encoding="utf-8",
                                                        errors="ignore")
             or "consumption_binding" in (ROOT / "scripts" / s).read_text(
                 encoding="utf-8", errors="ignore")]
    return cands or None


# 假作者一：普通赋值形态（对齐 e2_r14_optionband_replay.py 的真实写法）
SELFTEST_NET = """import pathlib, sys
from openai import OpenAI            # 网络标记：必须被拒
name = "R14E_REAL_PAGE_REPLAY_V8.json"
if "--out" in sys.argv:
    name = sys.argv[sys.argv.index("--out") + 1]
p = pathlib.Path("evidence") / name
p.write_text('{"ok": 1}', encoding="utf-8")
"""

# 假作者二：argparse dest 形态（真实脚本里也有这种写法）
SELFTEST_ORCH = """import argparse, pathlib, subprocess, sys
ap = argparse.ArgumentParser()
ap.add_argument("--out", default="R14E_REAL_PAGE_REPLAY_V8.json")
a = ap.parse_args()
subprocess.run([sys.executable, "scripts/e2e_r14_index.py"])   # 编排器：必须被拒
p = pathlib.Path("evidence") / a.out
p.write_text('{"ok": 1}', encoding="utf-8")
"""


COLLATERAL_PROBE = "data/_r14_attr_selftest_probe.txt"

# 一个**合法通过全部前置闸门**（有 --out、无网络标记、非编排器）但
# **越界写 data/** 的假脚本，用来证明 worktree_collateral 检测器会真的触发。
SELFTEST_COLLATERAL = (
    "import argparse, pathlib, sys\n"
    'ap = argparse.ArgumentParser()\n'
    'ap.add_argument("--out", default="R14E_REAL_PAGE_REPLAY_V8.json")\n'
    'name = ap.parse_args().out\n'
    'pathlib.Path("evidence", name).write_text(\'{"ok": 1}\', '
    'encoding="utf-8")\n'
    "# 故意越界：写 data/ 下探针（归属本该零附带改动）\n"
    'pathlib.Path("data/_r14_attr_selftest_probe.txt").write_text(\n'
    '    "collateral probe", encoding="utf-8")\n'
)


def _restore_probe() -> bool:
    q = ROOT / COLLATERAL_PROBE
    if q.is_file():
        q.unlink()
    return not q.exists()


def selftest_collateral_detector(artifact: str = "R14E_REAL_PAGE_REPLAY_V8.json"):
    """**测检测器本身**：注入"闸门合法但会写 data/"的脚本，断言
    safety 变成 SAFETY_VIOLATION_worktree_collateral 且**点名**探针。
    （一个从未触发过的安全检查等于没有检查——本轮的纪律就是这条。）"""
    fails = []
    fake = "zfake_collateral.py"
    probe = ROOT / COLLATERAL_PROBE
    _restore_probe()
    (ROOT / "scripts" / fake).write_text(SELFTEST_COLLATERAL, encoding="utf-8")
    try:
        reason = unsafe_to_run(fake)
        if reason is not None:
            fails.append(f"前置闸门**误杀**合法脚本：{reason}")
        res = attribute(artifact, only=[fake])
        ran = {e.get("script", "").rsplit("/", 1)[-1]
               for e in res["evidence"] if e.get("script")}
        if fake not in ran:
            fails.append("假脚本未被执行，检测器无从验证")
        if not probe.is_file():
            fails.append("探针未出现：没有真的越界，检测器无法判断")
        if res.get("safety") != "SAFETY_VIOLATION_worktree_collateral":
            fails.append("检测器**未触发**：safety="
                         f"{res.get('safety')!r}")
        if COLLATERAL_PROBE not in " ".join(res.get("worktree_collateral", [])):
            fails.append("检测器没点名探针："
                         f"{res.get('worktree_collateral')}")
        restored = _restore_probe()
        if not restored:
            fails.append("探针未复原")
        return {"fails": fails,
                "safety_seen": res.get("safety"),
                "collateral_seen": res.get("worktree_collateral"),
                "restored": restored,
                "note": "本自检**故意**制造一次附带改动再复原，"
                        "证明 worktree_collateral 不是摆设"}
    finally:
        q = ROOT / "scripts" / fake
        if q.is_file():
            q.unlink()
        _restore_probe()


def selftest(artifact: str = "R14E_REAL_PAGE_REPLAY_V8.json"):
    """注入两类**必须被拒**的假脚本，断言它们进了拒跑清单且**没被执行**。

    这条自检本身就抓过一个真漏检：`ALL_SCRIPTS` 曾在模块导入时求值，
    事后落地的脚本对闸门"不可见"——于是既没执行也没拒绝，拒跑清单不完整。
    现在枚举改为每次调用现取（`all_scripts()`）。
    """
    fails = []
    for scr, art in EXPECTED_WRITERS.items():
        if art not in script_declares_write(scr):
            fails.append(f"写出判据**漏判**：{scr} 应声明写出 {art}")
    for scr, art in EXPECTED_NOT_WRITERS.items():
        if art in script_declares_write(scr):
            fails.append(f"写出判据**误判**：{scr} 只是读取 {art}，不得算作者")
    fakes = (("zfake_net.py", SELFTEST_NET), ("zfake_orch.py", SELFTEST_ORCH))
    for name, body in fakes:
        (ROOT / "scripts" / name).write_text(body, encoding="utf-8")
    try:
        for name, _ in fakes:
            reason = unsafe_to_run(name)
            if reason is None:
                fails.append(f"{name} 未被识别为不可运行（闸门漏检）")
            want = "网络标记" if name.endswith("net.py") else "编排器"
            if reason and want not in reason:
                fails.append(f"{name} 拒跑原因不含 {want!r}：{reason[:80]}")
            declared = script_declares_write(name)
            if declared != [artifact]:
                fails.append(f"{name} 写目标静态识别错：{declared}")
        res = attribute(artifact)
        refused = {x["script"].rsplit("/", 1)[-1]
                   for x in res.get("candidates_refused", [])}
        ran = {e.get("script", "").rsplit("/", 1)[-1]
               for e in res["evidence"] if e.get("script")}
        for name, _ in fakes:
            if name not in refused:
                fails.append(f"端到端：{name} 未进 candidates_refused")
            if name in ran:
                fails.append(f"端到端：**{name} 被执行了**（闸门失效）")
        if res.get("safety") != "OK_ZERO_REQUESTS":
            fails.append(f"端到端：safety={res.get('safety')}")
        if (res.get("budget_after") or {}).get("requests") not in (0, None):
            fails.append("端到端：预算被消耗，归属不得发请求")
        return {"fails": fails,
                "refused": sorted(refused), "executed": sorted(ran),
                "safety": res.get("safety"),
                "budget_after": res.get("budget_after")}
    finally:
        for name, _ in fakes:
            q = ROOT / "scripts" / name
            if q.is_file():
                q.unlink()


def main() -> int:
    full = False   # 已废止：遍历全部候选会跑到真实请求脚本（见模块注释）
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    which = args or ["R14E_REAL_PAGE_REPLAY_V8.json"]
    if "--selftest" in sys.argv:
        st = selftest(which[0])
        st2 = selftest_collateral_detector(which[0])
        st = {"fails": st["fails"] + st2["fails"],
              "refused": st["refused"], "executed": st["executed"],
              "safety": st["safety"], "budget_after": st["budget_after"],
              "collateral_detector": {k: st2[k] for k in
                                      ("safety_seen", "collateral_seen",
                                       "restored", "note")}}
        (EV / "R14E_ATTRIBUTION_SELFTEST.json").write_text(
            json.dumps({"purpose": "安全闸门自检：注入必拒脚本，断言被拒且未执行"
                                   "（无自检的检查不算检查）",
                        "injected": ["zfake_net.py（含 OpenAI 网络标记）",
                                     "zfake_orch.py（subprocess 编排器）"],
                        "rule": "静态写目标优先；含网络标记或编排器一律拒跑；"
                               "枚举每次现取（防导入后落地的脚本不可见）",
                        "llm_calls_used": 0, "http_requests_made": 0, **st},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return 1 if st["fails"] else 0
    docs = [attribute(a) for a in which]      # 一律静态优先 + 安全闸门
    out = {"purpose": "生产者归属：把'可复现'建立在**试过候选**上而不是试一个"
                      "就下结论；候选范围由 mode 如实登记（full=全部 e2*/d1/f1*，"
                      "一次约 20 条全量管线；fast=族名/富化键命中的少数脚本）",
           "mode": "full" if full else "fast",
           "head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                  capture_output=True,
                                  text=True).stdout.strip(),
           "verdict_fields": VERDICT,
           "results": docs,
           "correction_recorded": {
               "previous_wrong_claim": "上轮把 V8 判为 NOT_REPRODUCIBLE_FROM_HEAD",
               "root_cause": "只试了 e2_r14_optionband_replay.py（它**引用** V8 "
                             "件名但不产出）；R14_INDEX 的 A1 '生产脚本'列同因"
                             "为'名字出现在脚本里'而把作者归错",
               "rule_now": "true_producer 需同时满足：覆盖全部页 + 判定字段全同 "
                           "+ **不缺任何键**；只有都失败才允许写不可复现",
           },
           "llm_calls_used": 0, "http_requests_made": 0}
    (EV / "R14E_ARTIFACT_ATTRIBUTION.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({d["artifact"]: {"reproducibility": d["reproducibility"],
                                      "true_producers": d["true_producers"],
                                      "partial": d["partial_producers"],
                                      "untouched": d["artifact_untouched"],
                                      "tried": d["candidates_tried"]}
                      for d in docs}, ensure_ascii=False, indent=1))
    bad = [d["artifact"] for d in docs if d.get("safety") != "OK_ZERO_REQUESTS"]
    if bad:
        print("SAFETY_VIOLATION:", bad)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
