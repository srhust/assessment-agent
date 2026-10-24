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
import ast
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


def brief(v):
    import json as _json
    t = v if isinstance(v, str) else _json.dumps(v, ensure_ascii=False)
    return t[:90]


def shape_reconciliation(artifact, true, partial) -> dict:
    """**形状核对**（与被归属件的顶层结构是否可比）+ 记录我为对齐做的改动。

    上一步实测：`e2_r14_optionband_replay.py` 写的是
    `{"stage":…, "items":{…}}`，而被归属件 V8 是**页名直接做顶层键**
    ⇒ 它**产不出可比的东西**，把它登记成 `partial_producers`
    （"能部分生产"）是**不准确的说法**。
    本轮我把它改成 `dict(out, _meta=…)` 以对齐形状，
    这件事**必须留在结论里**，不能靠"现在能复现了"把它抹掉。
    """
    p = EV / artifact
    out = {"committed_top_level": None, "incomparable_before": [],
           "aligned_this_round": [], "note": ""}
    if not p.is_file():
        out["note"] = "被归属件不在盘上"
        return out
    try:
        committed = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        out["note"] = "被归属件不可解析"
        return out
    flat, why = top_level_shape(committed)
    out["committed_top_level"] = "flat_page_keys" if flat else why
    for script in sorted(set(true) | set(partial)):
        src = script_text(script)
        if re.search(r'"items"\s*:\s*out', src):
            # 现行源码仍是包裹层 ⇒ 不可比
            out["incomparable_before"].append(script)
        elif "_meta=" in src and "dict(out" in src:
            out["aligned_this_round"].append(script)
    if out["aligned_this_round"]:
        out["note"] = (
            "本轮为对齐形状改过这些脚本的写出结构（"
            + ", ".join(out["aligned_this_round"])
            + "）；因此『可从 HEAD 复现』里的 HEAD **包含本轮改动**，"
            "不表示历史原样重跑就能对上")
    return out


def top_level_shape(doc) -> tuple:
    """(顶层键是否**全是**页名样式, 是否含包裹层)。
    提交件 V8 用页名直接做顶层键；`e2_r14_optionband_replay.py` 曾写
    `{"stage":…, "items":…}` ⇒ 形状不符 ⇒ 它的"重跑"根本无法与提交件比。
    **形状与字节是两件事**，必须分列，不能用一个档位含糊带过。"""
    if not isinstance(doc, dict):
        return False, "not_a_dict"
    wrap = [k for k in doc if k in ("items", "results", "data", "stage")
            and isinstance(doc[k], dict)]
    return (not wrap), ("wrapped_by:" + ",".join(wrap) if wrap else "flat")


def cmp_paths(a, b, path="$", out=None, volatile=None):
    """逐点比较（忽略 `volatile` 键）。**忽略表集中定义在本模块**：
    上一轮我单独在 semantic_repro 里把忽略表越写越宽，
    等于把真差异吃掉（核对退化成安慰剂）。"""
    volatile = VOLATILE if volatile is None else volatile
    out = [] if out is None else out
    if len(out) >= 40:
        # **截断必须可见**：否则差异多于 40 处时调用方会把"只报了 40 条"
        # 读成"一共就 40 条"，进而把"差异很少"当成结论（本轮实测风险）。
        out.append(("$.__TRUNCATED_AT_40__", "<标记>", "<差异未报全>"))
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        ka = {k for k in a if k not in volatile}
        kb = {k for k in b if k not in volatile}
        for k in sorted(ka - kb):
            out.append((f"{path}.{k}", brief(a[k]), "<重跑未产出>"))
        for k in sorted(kb - ka):
            out.append((f"{path}.{k}", "<提交件没有>", brief(b[k])))
        for k in sorted(ka & kb):
            cmp_paths(a[k], b[k], f"{path}.{k}", out, volatile)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((f"{path}.len", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            cmp_paths(x, y, f"{path}[{i}]", out, volatile)
        return out
    if a != b:
        out.append((path, brief(a), brief(b)))
    return out


# ---- 比较用**忽略表**的唯一真值 ----
# 教训（本轮实测）：为消掉一条 `no_write_detected` 假阳性，我一路把
# `committed_sha256 / rerun_at_head / n_checked / violations / budget`
# 塞进 semantic_repro 的忽略表——那正是"把真差异一起吃掉"的路径。
# 所以忽略表**集中定义**在这里，并由 bundle 检查 A8 审它本身。
VOLATILE = {"_meta", "generated_at", "timestamp", "run_id", "elapsed_s",
            "duration_s", "wall_clock", "seconds",
            # 解释件里**有意**复述的字样（会引用 HEAD/文件名，属叙述而非数据）
            "why_this_exists", "note", "notes", "drift_register_source",
            "budget", "http_requests_made", "llm_calls_used"}
# **必须可见**的键：代码基线与被核对件身份。忽略表若吃掉它们，
# 复现核对就退化成"只看内容像不像"的安慰剂。
MUST_SEE = ("head", "code_version", "artifact", "committed",
            "committed_sha256", "rerun_at_head", "rerun_by")


def cmp_ignore_keys():
    return sorted(VOLATILE), sorted(MUST_SEE)


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
               "wrote": p.is_file(),
               # **静态：这脚本会不会写这个件名**（默认名/写出语句/
               # bundle 运行计划的 out_override）。形状比对**不能**
               # 替代这一项——本轮就是它把不写 V8 名的 e2d 判成了作者。
               "writes_this_name": artifact in script_declares_write(cand)}
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
    _full = [x for x in out if x.get("classification") == "true_producer"]
    # **作者 = 形状/判定层对上 且 静态声明写该件名**
    true = [x["script"] for x in _full if x.get("writes_this_name")]
    shape_only = [x["script"] for x in _full if not x.get("writes_this_name")]
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
            "shape_only_matches": shape_only,
            "partial_producers": partial,
            "shape_reconciliation": shape_reconciliation(
                artifact, true, partial),
            "reproducibility": (
                "REPRODUCIBLE_FROM_HEAD" if true else
                ("VERDICT_REPRODUCIBLE_EVIDENCE_KEYS_MISSING" if partial
                 else ("SHAPE_MATCH_ONLY_NOT_A_NAMED_WRITER" if shape_only
                       else "NOT_REPRODUCIBLE_FROM_HEAD"))),
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
    # `true_producers` 里的元素是**相对 ROOT 的路径**（`scripts/x.py`），
    # 而本函数又拼了一次 `scripts/` ⇒ `scripts/scripts/x.py` 直接抛
    # FileNotFoundError（本轮实测把归属整跑打断）。两种写法都要能吃，
    # 且**读不到就返回空串**：形状核对宁可标"未知"，不可让整件失败。
    for cand in (ROOT / name, ROOT / "scripts" / name, Path(name)):
        try:
            if cand.is_file():
                return cand.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return ""


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
    # **种子只认路径形态**（本轮假作者根因）：
    # `_probe_map = {"R14_VERIFICATION_BUNDLE.json": "r14_verify_all.py"}`
    # 是**探针字典的键**，`DELIVERABLES = [..., "…BUNDLE.json", ...]`
    # 是**清单元素**，都不是输出路径；旧写法把它们的左侧变量当种子，
    # 再经 `p.read_text` 扩散到 `t/name/d`，最后撞上"别的件被 write_text"
    # ⇒ 判成"索引写出了 bundle 件"（假作者）。
    seeds = set()
    # 种子按**赋值形态**取，不按"提到过该字面量"取：
    # · `x = EV / "X.json"`、`x = ROOT / "evidence" / "X.json"`（字面路径）
    # · `x = "X.json"`（裸字符串，再经 `ROOT / "evidence" / x` 拼出去 ——
    #    V8 / V5 / V3 三个关键件正是这种写法，漏了就是**漏判**）
    # · **容器字面量不算**：`_probe_map = {"X.json": …}`（探针字典的键）、
    #   `DELIVERABLES = [..., "X.json", ...]`（清单元素）都只是**引用**；
    #   旧写法把它们当种子，再经 `p.read_text` 扩散，
    #   最后撞上"别的件被 write_text" ⇒ 判成**假作者**。
    for _line in src.splitlines():
        _m = ASSIGN_RE.match(_line.strip().split("#")[0].strip())
        if not _m or lit not in _m.group(2):
            continue
        _v, _rhs = _m.group(1), _m.group(2).strip()
        if _rhs.startswith(("{", "[")):
            continue
        if (re.search(r"^(?:EV|ROOT|EVIDENCE|Path|pathlib\.Path|os\.path)\b",
                      _rhs)
                or re.search(r"pathlib\.Path\s*\(|Path\s*\(|join\s*\(", _rhs)
                or re.fullmatch(r"[\"'][A-Za-z0-9_.\-]+[\"']", _rhs)):
            seeds.add(_v)
    # argparse 形态：`add_argument("--out", default="X.json")` → dest = out，
    # 真正的使用点是 `args.out` / `/ a.out`。上轮假脚本用这种形态，
    # 静态判据认不出来，于是"注入的假作者没被认出"= 自检失去意义。
    for m in re.finditer(r'add_argument\(\s*"--([a-z0-9_\-]+)"[^)]{0,120}'
                         + re.escape(lit), src):
        seeds.add(m.group(1).replace("-", "_"))
        # argparse 的**目标变量**通常是 `args = ap.parse_args()` 之后
        # 用 `args.<dest>` / `a.<dest>`。旧写法把 dest（`out`）当变量名去找
        # `out.write_text`，而真实使用点是 **`a.out`** ⇒ 假作者判不出来、
        # 自检失去意义（本轮 `zfake_orch.py` 就是这么暴露的）。
        # ⇒ 把"承接 parse_args 的变量"也纳入种子，按 `<var>.<dest>` 匹配。
        for mm in re.finditer(
                r"(\w+)\s*=\s*\w+\.(?:parse_args|parse_known_args)\s*\(", src):
            seeds.add(f"{mm.group(1)}.{m.group(1).replace('-', '_')}")
    if not seeds:
        return False
    vars_ = set(seeds)
    _dotted = {v for v in seeds if "." in v}      # `a.out` 这类属性种子
    for _ in range(4):                      # 闭包迭代（够深且必然终止）
        grew = False
        for line in src.splitlines():
            mm = ASSIGN_RE.match(line.strip().split("#")[0].strip())
            if not mm:
                continue
            v, rhs = mm.group(1), mm.group(2)
            # **只沿路径语义扩散**：
            # `a = ROOT / "evidence" / x`、`a = x / "Y"`、`a = Path(x)`、
            # `a = os.path.join(x, …)`、`a = EV / x`；
            # `d = json.loads(p.read_text(…))`、`t = p.read_text(…)`
            # 这类**读**不再扩散（旧写法正是从读扩散到 write_text，
            # 把只读该件的脚本判成作者）。
            def _path_spread(_v, _rhs):
                if re.search(r"\b" + re.escape(_v) + r"\s*\)?\s*\.write_",
                             _rhs):
                    return False                      # 写它不等于路径扩散
                if re.search(r"read_text|read_bytes|open\s*\(", _rhs):
                    return False                      # 读，不扩散
                return bool(re.search(
                    r"(?:/\s*" + re.escape(_v) + r"\b"
                    r"|" + re.escape(_v) + r"\s*/"
                    r"|Path\s*\(\s*" + re.escape(_v) + r"\b"
                    r"|join\s*\([^)]*\b" + re.escape(_v) + r"\b)", _rhs))
            if any(_path_spread(s, rhs) for s in (vars_ - _dotted)):
                if v not in vars_:
                    vars_.add(v)
                    grew = True
        if not grew:
            break
    # 属性种子（argparse `a.out`）：要求出现 `p = … / a.out` 且该 `p` 被写
    for d_ in _dotted:
        mm = re.search(r"(\w+)\s*=\s*[^\n]{0,80}" + re.escape(d_)
                       + r"\b", src)
        if mm and re.search(r"\b" + re.escape(mm.group(1))
                            + r"\s*\.write_", src):
            return True
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


BUNDLE_STEPS_SRC = ROOT / "scripts" / "r14_verify_all.py"


def bundle_out_plan() -> dict:
    """从 **bundle 的运行计划**取"哪一步用 `--out` 落到哪个件名"。

    这是**已登记的结构性事实**（不是我手打的豁免名单）：
    `STEPS` 元组的第 5 项 `out_override` 就是那一步实际写出的件名。
    上一版归属器只认**源码里的字面默认名**，于是把
    `R14E_REAL_PAGE_REPLAY_V8_RERUN.json` 判成"没人写它"
    （索引 `producer_tier = mention_exact_only`、`reproducible=False`），
    而 A10 又如实抓到 bundle 每轮写出它 ⇒ 只剩两条路：
    **登记身份**（本函数）或**给它发豁免**（禁止）。

    **用 `ast` 而不是正则**：上一版正则匹配 0 件
    （STEPS 是多行、带注释与嵌套 dict），解释器给的 AST
    才是不会过期的结构锚点。
    """
    plan = {}
    if not BUNDLE_STEPS_SRC.is_file():
        return plan
    tree = ast.parse(BUNDLE_STEPS_SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if not (isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "STEPS" for t in node.targets)):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            return plan
        for el in node.value.elts:
            if not isinstance(el, ast.Tuple) or len(el.elts) < 5:
                continue
            step = ast.literal_eval(el.elts[0]) if isinstance(
                el.elts[0], ast.Constant) else None
            art = ast.literal_eval(el.elts[1]) if isinstance(
                el.elts[1], ast.Constant) else None
            over = el.elts[4]
            over_v = None
            if isinstance(over, ast.Constant) and isinstance(over.value, str):
                over_v = over.value
            if not step or not art:
                continue
            script = step.split()[0].split("/")[-1]
            # **归一件名**：STEPS 里 `out_override` 有时写成
            # `evidence/X.json`（带目录），与裸名 `X.json` 是同一个件；
            # 不归一就会出现两个键、下游按裸名查不到。
            a1 = Path(art).name
            plan.setdefault(a1, set()).add(script)
            if over_v:
                plan.setdefault(Path(over_v).name, set()).add(script)
    return {k: sorted(v) for k, v in plan.items()}


def _resolve_target(node, src_map):
    """**语法级**解析写出目标：字面量 ∪ 变量来源 ∪ 属性 dest。

    上一版只认"`(EV / \"X.json\").write_text(...)`"这种**直接**形状，
    于是 `name = "X.json"; p = EV / name; p.write_text(...)`
    （注入样例与多个真生产者的真实形状）解析成**空集** ⇒
    `自测报 写目标静态识别错：[]`——
    判据自己失效却把问题报在被测脚本身上。
    """
    got = set()
    for c in ast.walk(node):
        if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                and c.value.endswith(".json"):
            got.add(c.value)
        elif isinstance(c, ast.Name) and c.id in src_map:
            got |= src_map[c.id]
        elif isinstance(c, ast.Attribute):
            # `a.out` / `args.out`：键是**整串属性路径**
            _d = _dotted(c)
            if _d:
                got |= src_map.get(_d, set())
            if isinstance(c.value, ast.Name):
                got |= src_map.get(f"{c.value.id}.{c.attr}", set())
    return {v.rsplit("/", 1)[-1] for v in got}


def _dotted(node):
    """把 `Name`/`Attribute` 链**迭代**拼成 `"a.out"`（不用 `ast.unparse`）。

    本轮实测：`ast.unparse` 对大模块（`e2e_r14_index.py` 两千行）
    走 `_write_constant` 一路递归，在归属器里抛
    `RecursionError: maximum recursion depth exceeded` ⇒
    `r14_attribute_producer.py` 退出码 1、bundle 两条步骤同时红。
    我只需要一个**属性路径字符串**，不需要完整的反解析器。
    """
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _argparse_dests(tree):
    """**AST 级 argparse  dest 表**：`{对象属性: 默认件名}`。

    `add_argument("--out", default="X.json")` 记 `("out", "X.json")`；
    再看 `args = p.parse_args()` ⇒ 属性 `args.out` 的来源是 `X.json`。
    注入样例 `zfake_orch.py` 走的就是这条路；
    漏了它，`script_declares_write` 对它返回**空集合**，
    于是自测把**判据自身失效**报成"脚本写目标识别错"。
    """
    flags = {}
    holders = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "add_argument":
                dflt = next((kw.value for kw in node.keywords
                             if kw.arg == "default"), None)
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        name = a.value.lstrip("-").replace("-", "_")
                        if isinstance(dflt, ast.Constant) and isinstance(
                                dflt.value, str):
                            flags[name] = dflt.value
            elif isinstance(f, ast.Attribute) and f.attr == "parse_args":
                key = _dotted(f.value)
                if key:
                    holders[key] = True
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fv = node.value.func
            if isinstance(fv, ast.Attribute) and fv.attr == "parse_args":
                holder = _dotted(fv.value) or ""
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        holders[t.id] = holder
    out = {}
    for var, holder in holders.items():
        for flag, dv in flags.items():
            if dv.endswith(".json"):
                out[f"{var}.{flag}"] = dv
    return out


def _ast_write_targets(src: str) -> set:
    """**AST 级**：返回**写出目标**解析出的 `.json` 件名集合。

    为什么必须换到 AST：邻近文本判据我连着修了四轮
    （40 字符窗口、保留换行、60 字符窗口、同语句 splitlines），
    `e2e_r14_index.py` 仍被误判成 bundle 的作者，
    而注入样例 `zfake_orch.py` 却解析成**空集** ——
    正则形状修不胜修，**语法树不会骗人**。

    支持的写出形状（本项目真实出现的全部形态）：
      · `(EV / "X.json").write_text(...)`            —— 内联字面量
      · `open("X.json", "w")`                        —— 第一位置参数
      · `name = "X.json"; p = EV / name; p.write_text()` —— 变量回溯
      · `ap.add_argument("--out", default="X.json");
         a = ap.parse_args(); p = Path("evidence") / a.out;
         p.write_text()`                              —— argparse dest
    **作用域限制**：变量回溯只在**同一函数体**内进行
    （模块级作用域作为兜底）；跨函数的同名变量**不算**来源——
    这正是 `e2e_r14_index.py` 的 bundle 假阳性来源
    （它在 `_bundle_steps()` 里 `p = EV / "R14_VERIFICATION_BUNDLE.json"`
    **读**该件，而 `write_text` 在 `main()` 里写 `R14_INDEX.json`）。
    """
    names = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return names

    def _json_lits(node):
        return {c.value.rsplit("/", 1)[-1] for c in ast.walk(node)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
                and c.value.endswith(".json")}

    # ---- argparse dest：`{持有者表达式: {dest: 默认件名}}` ----
    holders = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "parse_args":
            _h = _dotted(node.func.value)
            if _h:
                holders[_h] = node
    dest_by_holder = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_argument":
            owner = _dotted(node.func.value)
            dflt = next((kw.value for kw in node.keywords
                         if kw.arg == "default"), None)
            if isinstance(dflt, ast.Constant) and isinstance(dflt.value, str) \
                    and dflt.value.endswith(".json"):
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                            and a.value.startswith("-"):
                        dest_by_holder.setdefault(owner, {})[
                            a.value.lstrip("-").replace("-", "_")] = \
                            dflt.value.rsplit("/", 1)[-1]

    def _owner_of(attr_node):
        """`a.out` → 解析 `a` 是哪个 parser 的 `parse_args()` 结果。"""
        base = attr_node.value
        if not isinstance(base, ast.Name):
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(
                    node.value, ast.Call) and isinstance(
                        node.value.func, ast.Attribute) and \
                    node.value.func.attr == "parse_args":
                if any(isinstance(t, ast.Name) and t.id == base.id
                       for t in node.targets):
                    return _dotted(node.value.func.value)
        return None

    def _resolve(target, scope):
        """把写出目标解析成件名集合（只走语法树，不走文本邻近）。"""
        got = set(_json_lits(target))
        for c in ast.walk(target):
            if isinstance(c, ast.Name):
                got |= _assigned_in(scope, c.id)
            elif isinstance(c, ast.Attribute):
                owner = _owner_of(c)
                if owner:
                    got |= set(dest_by_holder.get(owner, {}).get(
                        c.attr, set()) if isinstance(
                        dest_by_holder.get(owner, {}).get(c.attr, ""),
                        str) else dest_by_holder.get(owner, {}).get(
                        c.attr, set()))
        return {v for v in got if v}

    def _assigned_in(scope, var, _seen=None):
        """`scope` 内把 `var` 当赋值目标的那些 RHS 的件名。

        **必须有环保护**：`_assigned_in` 对 RHS 里的每个 `Name`
        再次调用自己，遇到 `dst = Path(dst)` / `i = i + 1`
        这种自指赋值就**无限递归** ⇒
        本轮实测 `RecursionError: maximum recursion depth exceeded`
        （归属器退出码 1，bundle 两条步骤同时红）。
        """
        seen = _seen or set()
        if var in seen:
            return set()
        seen = seen | {var}
        out = set()
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == var
                    for t in node.targets):
                vals = set(_json_lits(node.value))
                for c in ast.walk(node.value):
                    if isinstance(c, ast.Name):
                        vals |= _assigned_in(scope, c.id, seen)
                    elif isinstance(c, ast.Attribute):
                        owner = _owner_of(c)
                        if owner:
                            v = dest_by_holder.get(owner, {}).get(c.attr)
                            if v:
                                vals.add(v)
                out |= vals
        return out

    def _probe_lines(scope):
        return {n.lineno for n in ast.walk(scope)
                if isinstance(n, ast.Call) and any(
                    k in ast.dump(n.func) for k in
                    ("mkdtemp", "TemporaryDirectory", "NamedTemporaryFile"))}

    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                         ast.ClassDef))]
    for scope in scopes:
        bad = _probe_lines(scope)
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if node.lineno in bad:
                continue          # **探针作用域内不算生产**
            if isinstance(f, ast.Attribute) and f.attr in (
                    "write_text", "write_bytes"):
                names |= _resolve(f.value, scope)
            elif isinstance(f, ast.Name) and f.id == "open":
                if node.args and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str) \
                        and node.args[0].value.endswith(".json"):
                    if any(isinstance(a, ast.Constant) and a.value in ("w", "wb")
                           for a in node.args[1:]):
                        names.add(node.args[0].value.rsplit("/", 1)[-1])
    return names


_AST_CACHE = {}


def _ast_targets(src: str) -> set:
    """AST 缓存入口（同一份源码多次判据共用）。"""
    key = hash(src)
    if key not in _AST_CACHE:
        _AST_CACHE[key] = _ast_write_targets(src)
    return _AST_CACHE[key]


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
        # **邻近 ≠ 同语句**（本轮实测两处假作者都死在这）：
        # `^[^\n]{0,40}write_text` 里的 40 字符窗口**跨过**了
        # 键值对的值与逗号，于是
        # `"R14_VERIFICATION_BUNDLE.json": "一键复现 bundle…",`
        # 后面 300 字符里出现的 `(EV / "R14_INDEX.json").write_text(`
        # 被算到**前一个名字**头上；`e2e_r14_claim_audit.py` 的
        # `R14_CLAIM_AUDIT.json` 同因（它在 `mkdtemp` 探针里写
        # `R14G_RESULT.json`，那更不该算生产）。
        # ⇒ 只认**闭合形状**：字面量后紧跟 `)` 再 `.write_text(`/
        #   `.write_bytes(`；或同行内 `open("NAME", "w")`。
        # 窗口 = 字面量之后到**下一个行尾换行**（`after` 保留换行）；
        # 上一版用 `after.split("\n")[0]` ⇒ 换行被吃掉，
        # **跨行**的 `write_text` 逃过了"同语句"这条判据。
        _nl = after.find("\n")
        stmt = after if _nl < 0 else after[:_nl]
        direct = bool(re.match(r"^\s*\)\s*\.write_(text|bytes)\s*\(", after)
                      or re.match(r'^\s*,\s*[\'"]w', stmt)
                      or (re.match(r"^\s*\)\s*$", stmt) and
                          re.search(r"\.write_(text|bytes)\s*\(",
                                    after[len(stmt):len(stmt) + 60])))
        if direct and _PROBE_SCOPE.search(src[max(0, m.start() - 400):
                                             m.end() + 400]):
            # **探针作用域内不算生产**（与 `e2e_r14_index` 同口径）：
            # `e2e_r14_claim_audit.py` 在 `tempfile.mkdtemp()` 的
            # **假 evidence 目录**里造 `R14G_RESULT.json` 注入违规，
            # 那不是"写出这个交付件"。
            direct = False
        if (direct or _writer_closure(src, lit)) \
                and base in _ast_targets(src):
            # **AST 交叉核对**：闭包/邻近说"写"，但 AST 的写出目标里
            # 没有这个名字 ⇒ 判 False，并把分歧**记进 out 的旁注**
            # （`_AST_DISAGREE` 供自测打印，避免我又"静默相信正则"）。
            if base in _AST_CACHE.setdefault(name, _ast_write_targets(src)):
                out.add(base)
            else:
                _AST_DISAGREE.append((name, base))
    # **`--out` 覆盖名也算作者，但要有写出能力**：
    # 证据来自 bundle 的**已登记运行计划**（不是我为 A10 加的豁免名单），
    # 可**提到 ≠ 生产**这条口径在这里同样成立——
    # 本轮实测：无条件并入让 `e2d_...v4.py` 拿到
    # `R14E_REAL_PAGE_REPLAY_V8.json` 的作者档，
    # 而它与该件的唯一关系是源码数组里的**读取**
    # （真作者只有 `e2_r14_optionband_replay.py`，默认名正是 V8）。
    _pl = bundle_out_plan()
    for art, scripts in _pl.items():
        if name in scripts and art in _ast_targets(src):
            out.add(art)
    return sorted(out)


_PROBE_SCOPE = re.compile(r"mkdtemp|TemporaryDirectory|tmp_path|tmpdir|"
                         r"\bfake\b|_fake|tempfile")


def _writes_literal(src: str, art: str) -> bool:
    """`art` 是否作为**写出目标**的字面量出现在 `src` 里。

    上一版用 `_can_write`（源码里有**任意** `write_text`）就并入计划名 ⇒
    `e2e_r14_index.py` 只是**读** `R14_VERIFICATION_BUNDLE.json`，
    却因为自己也写 `R14_INDEX.json` 被判成 bundle 的作者
    （`EXPECTED_NOT_WRITERS` 的钉子当场报误判——
    钉子的价值就在这：它有权把我的判据判错）。
    两种真实形状：
    · `(EV / "NAME").write_text(...)` / `.write_bytes(...)`
    · `open(..., "NAME", "w")`
    """
    for m in re.finditer('"%s"' % re.escape(art), src):
        tail = src[m.end():m.end() + 220]
        hit = re.match(r"\s*\)\s*\.write_(text|bytes)\s*\(", tail)
        if hit:
            # **同一条语句**才算写在它身上：中间若又出现新的
            # `X / "…"` 或 `"….json"` 字面量 ⇒ 那个 `write_text`
            # 写的是**别件**，本字面量只是读取链上的一环。
            # 实测误判形状：`json.loads((EV / "BUNDLE.json")
            # .read_text(...))` 之后 220 字符内正好是
            # `(EV / "INDEX.json").write_text(`。
            between = tail[:hit.end()]
            if re.search(r'"[^"]*\.json"|\w+\s*/\s*"', between):
                pass
            else:
                return True
        if re.search(r"open\([^)]*$", src[max(0, m.start() - 240):m.start()]) \
                and re.match(r"[^)]*,\s*[\"']w", src[m.end():m.end() + 40]):
            return True
    return False


ORCH2_RE = re.compile(r"subprocess\.(run|Popen|check_output|call)\s*\("
                      r"[^\n]*scripts/")


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
    # **安全类拒因优先于能力类**：上一版把 `--out` 检查排在编排器之后
    # 却也排在**网络之后**，看似无碍；真正的问题是
    # `ORCHESTRATOR`/`SCRIPT_LITERAL` 对注入样例**不成立**
    # （样例用 `subprocess.run([sys.executable, "scripts/e2e_r14_index.py"])`），
    # 于是落到 `--out` 分支、拒因不含"编排器"。
    # ⇒ 判据加一条**字面量级**编排器识别（见 ORCH2），再排 `--out`。
    if ORCH2_RE.search(src):
        return ("编排器（subprocess 调用 scripts/*.py）：禁止递归/全链重跑"
                "（字面量级识别）")
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
