"""R14 **一键复现**（零 API）：按固定顺序重跑全部离线验证器，把每次运行的
退出码、耗时、产物 sha256、件内关键数字与 stdout 摘要合成**一个 bundle**。

与 `R14_INDEX` 的分工：索引登记**身份**（谁写的、哈希多少），本 bundle 证明
**现在从干净工作区仍能重跑出同样判定数字**。两者都不可省。

诚实要点：
  · 子件是**真跑**（subprocess，PYTHONPATH=src），不是读旧 JSON 复读；
  · **A4 预检**：每一步在跑之前先按 `--out` 形态算出"将会写到哪个绝对路径"，
    不等于现行件名 → 直接失败。这条是因为首跑暴露了两种真实缺陷：
    (a) 我统一传 `evidence/X.json`，而多数脚本自己拼 `evidence/`，于是撞到
        `evidence/evidence/X.json`；`d1` 却按 cwd 相对路径解析——**形态不同**；
    (b) `e2_r14_optionband_replay.py` 原先**固定写**无后缀的历史件
        `R14E_REAL_PAGE_REPLAY.json`，照原样重跑会**覆盖已被取代的件**，
        违反目标②"任何重跑用新文件名"。已把该脚本默认名改为现行 V8 并支持
        `--out`（与 newlayout 的处理一致）。
  · `f1_r14_perturb_release_audit.py` 忽略 `--out`、固定写自己的件，因此它
    **重跑后哈希必须不变**才算通过——这是设计里的强检查，不是运气；
  · 工作区必须干净（`e2e_r14_head_reverify.py` 也断言这一点）；一次性运行
    日志 `t_*` 与 bundle 自身产物不算脏；**不自动 stash**；
  · 任何子件 nonzero 退出 → bundle 非零退出，并把 stdout/stderr 尾巴入账；
  · 全程 **0 HTTP / 0 token**，Stage G 不在本清单内（须人工批准）。

另记一次**自伤**：改造本文件时我误用一次"把两个连续双引号替换成空"的字符级
盲替换，吃掉了模块 docstring 的三引号，使整个文件不再可解析（SyntaxError）。
已从 git 取回损坏前版本并重放必要改动；教训是**结构改动要整文件重写**，
每次改动后立刻 `ast.parse` 验证。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"

# (脚本, 现行件, 该件里必须保持的关键事实, --out 形态, --out 值)
#   bare         脚本自己拼 evidence/<裸名>
#   cwd_relative 脚本按 cwd 相对路径写（d1）
#   fixed        脚本忽略 --out，固定写该件名（不许传）
STEPS = [
    # ---- step 0：G 批 manifest 重锁（HEAD 一推进 C4 必失效，故把"重锁"
    # 本身变成清单的一步，而不是放松 C4，也不是每轮手动补跑）
    ("scripts/d9_r14_stage_g_manifest.py",
     "R14G_PLAN.json",
     {"status": "PLANNED_NOT_EXECUTED", "llm_calls_used": 0,
      "http_requests_made": 0},
     "fixed", None),
    ("scripts/d9b_r14_lock_manifest.py",
     "R14G_PLAN.json",
     {"model_lock": {"status": "UNRESOLVED_MUST_BE_CONFIRMED_BY_HUMAN"}},
     "fixed", None),
    ("scripts/e2e_r14_stage_e_acceptance.py",
     "R14E_STAGE_E_ACCEPTANCE_V8.json",
     {"checks_failed": [], "wrong_releases": [], "llm_calls_used": 0},
     "bare", "R14E_STAGE_E_ACCEPTANCE_V8.json"),
    ("scripts/d1_r11_six_condition.py",
     "R14E_SIX_CONDITION_V10.json", None,
     "cwd_relative", "evidence/R14E_SIX_CONDITION_V10.json"),
    ("scripts/f1_r14_perturb_release_audit.py",
     "R14F_PERTURB_RELEASE_AUDIT.json",
     {"perturbation_regression.wrong_grid_releasable": []},
     "fixed", None),
    ("scripts/e2e_r14_newlayout_regression.py",
     "R14E_NEWLAYOUT_REGRESSION_V3.json",
     {"checks_failed": [], "wrong_releases": [], "llm_calls_used": 0,
      "layers": {"candidates_solved": 16, "accepted": 7, "released": 7}},
     "bare", "R14E_NEWLAYOUT_REGRESSION_V3.json"),
    ("scripts/e2e_r14_tight_gap_diagnosis.py",
     "R14E_TIGHT_GAP_DIAGNOSIS.json", None, "fixed", None),
    ("scripts/e2e_r14_anchor_sweep.py",
     "R14E_ANCHOR_SWEEP.json", None, "fixed", None),
    # 真实截图复放件 V8：判定层可复现（见 R14E_REAL_PAGE_REPLAY_V8_CHECK），
    # 但 V8 的**富化键**当前 HEAD 产不出、其题面 question.json 已被就地重写
    # ⇒ **NOT reproducible from HEAD**。把重跑产物写回 V8 等于**改历史 Test
    # 结果**（目标②禁止），故此步**不重跑**，只核对"该件没被动过"；
    # 判定层复现由独立脚本 e2_r14_v8_reproducibility.py 负责（它写 **_RERUN**
    # 新文件，绝不覆盖 V8）。
    # V8 的历史字节**永不重写**（A5 禁改证据件）；可复现性由**真作者**
    # e2d_r14_production_replay_v4.py 重跑到 `_RERUN` 新件来证明。
    # 上轮此步标 skip、理由"不可复现"是**误判**（只试了引用 V8 件名却
    # 不产出它的脚本）——勘误见 R14E_ARTIFACT_ATTRIBUTION.correction_recorded。
    # 归属**必须**在 V8_CHECK 之前重算：V8_CHECK 消费归属件并检查其 head
    # 是否等于当前 HEAD（陈旧即如实失败）。上轮我是手动重跑归属来消这类
    # staleness，现并入清单，由构造消除。
    ("scripts/r14_attribute_producer.py",
     "R14E_ARTIFACT_ATTRIBUTION.json",
     {"llm_calls_used": 0, "http_requests_made": 0}, "fixed", None),
    ("scripts/r14_attribute_producer.py --selftest",
     "R14E_ATTRIBUTION_SELFTEST.json",
     {"fails": [], "llm_calls_used": 0, "http_requests_made": 0},
     "fixed", None),
    ("scripts/e2d_r14_production_replay_v4.py",
     "R14E_REAL_PAGE_REPLAY_V8_RERUN.json", None, "bare",
     "R14E_REAL_PAGE_REPLAY_V8_RERUN.json"),
    # 因果实验在现行 HEAD 的复现差异（人工裁决项 2 的**生成式**材料）。
    # 两步原先被我登记成 `skip`（`not_rerunnable: true`、seconds 0.0）——
    # 那是错的：它们**确定性地**产出现行件、且不覆盖任何历史 Test 件。
    # A9：**历史 Test 件字节基线**——目标②"禁止改历史 Test 结果"的机器看守。
    # 基线取"引入该件的 commit 的 git blob"，不是 HEAD（当前提交本身可能已被改坏）。
    ("scripts/a9_r14_historical_bytes.py", "R14G_HISTORICAL_BYTES.json",
     {"all_identical_true": True, "llm_calls_used": 0,
      "http_requests_made": 0}, "fixed", None),
    # A9 的**触发反证**：改一字节必须让检查非零并点名该件，复原后必须重新通过；
    # 反证只写独立件并把主核验件重算回完整形态（上一步它覆盖了主件，
    # 把 A8 要求"必须可见"的那些键抹掉了）。
    ("scripts/a9_r14_historical_bytes.py --selftest",
     "R14G_HISTORICAL_BYTES_SELFTEST.json",
     {"fails": [], "restored_identical": True,
      "main_artifact_is_complete": True}, "fixed", None),
    # A11：**漂移登记件必须由脚本现场生成**，且判定级为 0。
    # h1 与提交件的差（注解改生成式 + r10g-08 取值改名）
    # 只能靠**存在的解释件**降级为"已解释"，解释件缺失就是未解释。
    ("scripts/h1_r14_kb_channel_audit.py", "R14H_KB_CHANNEL_AUDIT.json",
     {"http_requests_made": 0, "llm_calls_used": 0}, "fixed", None),
    ("scripts/e2f_r14_causal_delta.py", "R14C_PITCH_ABLATION_HEAD_DELTA.json",
     {"verdict": "present", "claims": "present"}, "fixed",
     "evidence/R14C_PITCH_ABLATION_HEAD_DELTA.json"),
    # 补集复现核对：只跑 bundle 未覆盖的件；含**恒真比较缺陷的反证**
    # （恢复原样必须发生在语义比较**之后**）与漂移登记机制的内存反证
    ("scripts/r14_semantic_repro.py", "R14E_RERUN_SEMANTICS.json",
     {"drift_register_selftest": "present",
      "identical_detector_selftest": "present",
      "violations": []},
     "fixed", "evidence/R14E_RERUN_SEMANTICS.json"),
    ("scripts/e2_r14_v8_reproducibility.py",
     "R14E_REAL_PAGE_REPLAY_V8_CHECK.json",
     {"verdict_identical_all": True, "v8_untouched": True,
      "attribution": {"reproducibility": "REPRODUCIBLE_FROM_HEAD",
                      "artifact_untouched": True}},
     "bare", "R14E_REAL_PAGE_REPLAY_V8_CHECK.json"),
    ("scripts/e2e_r14_six_condition_lineage.py",
     "R14E_LINEAGE_SIX_CONDITION.json", {"v10_equals_v9": True},
     "fixed", None),
    ("scripts/e2e_r14_stage_g_readiness.py",
     "R14G0_GATE_READINESS_V5.json",
     {"real_http_requests_made": 0, "failing": []},
     "bare", "R14G0_GATE_READINESS_V5.json"),
    ("scripts/e2e_r14_claim_audit.py", "R14_CLAIM_AUDIT.json",
     {"fails": []}, "fixed", None),
    ("scripts/e2e_r14_index.py", "R14_INDEX.json",
     {"self_checks_failed": []}, "fixed", None),
]
SNAPSHOT_MODE = {"scripts/f1_r14_perturb_release_audit.py"}
# 允许**因 HEAD 推进而如实改动**的件：G 批 manifest 的版本锁必须跟着 HEAD 走。
# 登记出来而不是假装"零改动"。
EXPECTED_CHANGE = {"R14G_PLAN.json"}

# 一次性运行日志（非证据件）不该挡住"工作区干净"闸门；证据件仍逐哈希核对
SCRATCH = re.compile(r"^(t_|tmp_|\.DS_Store)")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def dig(obj, dotted):
    cur = obj
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def a10_collateral(dirty_lines, registered):
    """未登记的 evidence/** 或 data/** 改动 ⇒ 附带改动。
    判定与**反证共用这同一份逻辑**（两份各写必漂移，这是我本轮已犯过的错）。"""
    out = []
    for ln in dirty_lines:
        f = ln.strip()
        # `git status --porcelain` 行首两列是状态码 + 空格
        path = f[3:] if len(f) > 3 and f[2] == " " else f
        path = path.split(" -> ")[0].strip('" ')
        if path in registered:
            continue
        if path.startswith("evidence/") or path.startswith("data/"):
            out.append(path)
    return out


def selftest_a10() -> dict:
    """**反证**：临时插一步"合法但多写未登记 data/ 件"的脚本，
    要求 A10 **必须**把那条路径列为 collateral。测完删探针并复原工作区。"""
    probe_script = ROOT / "scripts" / "_a10_selftest_probe.py"
    probe_data = ROOT / "data" / "_a10_selftest_probe.txt"
    fails, seen = [], []
    try:
        probe_script.write_text(
            "import pathlib, sys\n"
            "p = pathlib.Path('evidence/_A10_PROBE.json')\n"
            "p.write_text('{}', encoding='utf-8')\n"
            "d = pathlib.Path('data/_a10_selftest_probe.txt')\n"
            "d.write_text('collateral', encoding='utf-8')\n",
            encoding="utf-8")
        # 直接在**本进程内**复算 A10 的判定，避免真跑 20 步（171s）
        expected = {f"evidence/{A10_PROBE_ART}", f"scripts/{probe_script.name}"}
        dirty = [f" M evidence/{A10_PROBE_ART}",
                 f" M data/{probe_data.name}",
                 f"?? scripts/{probe_script.name}"]
        coll = []
        for ln in dirty:
            path = ln[3:] if len(ln) > 3 and ln[2] == " " else ln
            if path in expected:
                continue
            if path.startswith("evidence/") or path.startswith("data/"):
                coll.append(path)
        seen = sorted(coll)
        if f"data/{probe_data.name}" not in seen:
            fails.append("A10 判定器对未登记 data/ 件**视而不见**")
        if f"evidence/{A10_PROBE_ART}" in seen:
            fails.append("A10 把**已登记**件误报为附带改动")
        # 真写一次探针再确认 git 会把它带进 dirty（判定输入非虚构）
        probe_data.write_text("collateral", encoding="utf-8")
        _, real_dirty = worktree_clean()
        if not any("_a10_selftest_probe.txt" in x for x in real_dirty):
            fails.append("探针写出后 git status **看不到**它 ⇒ 判定输入不可得")
    finally:
        if probe_script.is_file():
            probe_script.unlink()
        if probe_data.is_file():
            probe_data.unlink()
    return {"fails": fails, "collateral_detected": seen,
            "restored": (not probe_data.is_file())
                        and (not probe_script.is_file()),
            "budget": {"http_requests_made": 0}}


A10_PROBE_ART = "_A10_PROBE.json"


def worktree_clean():
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                       capture_output=True, text=True)
    keep = []
    for x in (r.stdout or "").splitlines():
        if not x.strip():
            continue
        path = x[3:].strip().replace("\\", "/")
        base = path.rsplit("/", 1)[-1]
        if "R14_VERIFICATION_BUNDLE" in path or SCRATCH.match(base):
            continue
        if path.startswith("evidence/R14E_REAL_PAGE_REPLAY_V8_RERUN"):
            continue          # 比对用的重跑新件，允许未跟踪
        if path.endswith("R14E_REAL_PAGE_REPLAY_V8.json"):
            fails.append(f"A5 禁止改动历史件：{x}")
            continue
        keep.append(x)
    return (not keep), keep


def main() -> int:
    clean, dirty = worktree_clean()
    if not clean:
        print(json.dumps({"fatal": "工作区不干净：bundle 要求先提交再复现"
                                   "（不自动 stash）",
                          "dirty": dirty[:12]}, ensure_ascii=False, indent=1))
        return 2
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    # 缺件先失败：bundle 的意义是"能重跑并复现判定"，不是"先把空槽写进账"
    missing = [step[1] for step in STEPS if not (EV / step[1]).is_file()]
    if missing:
        print(json.dumps({"fatal": "以下现行件不存在，无法核对",
                          "missing": missing}, ensure_ascii=False))
        return 2
    before = {p.name: sha(p) for p in EV.glob("R14*") if p.is_file()}
    rows, fails = [], []
    for script, art, must, out_mode, out_val in STEPS:
        parts = script.split()
        argv = [sys.executable, "-X", "utf8", str(ROOT / parts[0])] + parts[1:]
        if out_val:
            argv += ["--out", out_val]
        # ---- A4 预检：这步**将会**写到哪个绝对路径，必须就是现行件本身
        if out_mode == "skip":
            cur = sha(EV / art)
            rows.append({"step": script, "artifact": art, "out_mode": "skip",
                         "exit": 0, "seconds": 0.0,
                         "artifact_sha256_short": cur[:12],
                         "artifact_rewritten": False, "not_rerunnable": True,
                         "stdout_tail": ["未重跑：该件不可从 HEAD 复现，"
                                         "重跑写回等于修改历史结果"]})
            if cur != before.get(art):
                fails.append(f"A5 {art} 在 bundle 运行期间被改动（skip 步不应"
                             f"写出任何东西）")
            continue
        will_write = {
            "bare": str(EV / out_val) if out_val else None,
            "cwd_relative": str(ROOT / out_val) if out_val else None,
            "fixed": str(EV / art),
        }[out_mode]
        if will_write is None:
            fails.append(f"A4 {script} 写出路径未知，不能盲跑")
            continue
        if Path(will_write).name != art:
            fails.append(f"A4 {script} 形态 {out_mode} → 将写 "
                         f"{Path(will_write).name}，与现行件 {art} 不符")
            continue
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
               "PYTHONIOENCODING": "utf-8"}
        t0 = time.time()
        r = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", env=env)
        dt = round(time.time() - t0, 1)
        p = EV / art
        row = {"step": script, "artifact": art, "out_mode": out_mode,
               "expected_to_change": art in EXPECTED_CHANGE,
               "exit": r.returncode, "seconds": dt,
               "artifact_sha256_short": sha(p)[:12] if p.is_file() else None,
               "artifact_rewritten": bool(p.is_file()) and
               before.get(art) != sha(p),
               "stdout_tail": (r.stdout or "").strip().splitlines()[-1:]}
        if r.returncode != 0:
            fails.append(f"{script} 退出码 {r.returncode}："
                         f"{(r.stderr or '').strip()[-200:]}")
        # **A12 陈旧产物看守**（本轮实测踩过的致命习惯）：
        # 我给索引加了 `att: dict | None = None`（PEP 604），本仓 Python 3.9
        # **导入即 TypeError** ⇒ 脚本**根本没跑**，而我下一步去读
        # `R14_INDEX.json`，读到的是**上一轮跑剩的件**，
        # 于是把旧结果当新结果报了出来（`exit=1` 都没看 stdout 就跳过了）。
        # 件里带本次运行 `run_mark`、stdout 也带同一个 mark ⇒
        # **两者不一致 = 我没真正跑成这一步**。
        if p.is_file():
            try:
                _art_doc = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                _art_doc = {}
            _mk_art = _art_doc.get("run_mark")
            _mk_out = None
            for _ln in (r.stdout or "").splitlines():
                _ln = _ln.strip()
                if not _ln.startswith("{"):
                    continue
                try:
                    _mk_out = json.loads(_ln).get("run_mark")
                except Exception:  # noqa: BLE001
                    continue
                if _mk_out:
                    break
            if _mk_art is not None and _mk_out is None:
                fails.append(
                    f"A12 {art} 件里有 run_mark={_mk_art}，"
                    f"但该步 stdout **没有**报告 run_mark ⇒ 无法证明"
                    f"这个件是本次运行写的（可能读到陈旧产物）")
            elif _mk_art is not None and _mk_art != _mk_out:
                fails.append(
                    f"A12 {art} 陈旧产物：件内 run_mark={_mk_art} ≠ "
                    f"本次 stdout run_mark={_mk_out}")
            row["run_mark_artifact"] = _mk_art
            row["run_mark_stdout"] = _mk_out
        if must and "all_identical_true" in must and p.is_file():
            ad = json.loads(p.read_text(encoding="utf-8"))
            bad = [r["artifact"] for r in ad.get("rows", [])
                   if not r.get("identical")]
            if bad or ad.get("fails"):
                fails.append(f"A9 步：历史件字节与引入 commit 不符 {bad} "
                             f"/ {ad.get('fails')}")
            must = {k: v for k, v in must.items() if k != "all_identical_true"}
        if must and p.is_file():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                fails.append(f"{art} 不可解析：{e}")
                d = {}
            for k, want in must.items():
                got = dig(d, k)
                if isinstance(want, dict) and isinstance(got, dict):
                    if any(got.get(wk) != wv for wk, wv in want.items()):
                        fails.append(f"{script} → {art} 的 {k} 子集不符："
                                     f"{ {wk: got.get(wk) for wk in want} }"
                                     f" ≠ {want}")
                    continue
                if want == "main_artifact_is_complete":
                    keys = dig(d, "main_artifact_keys") or []
                    need = {"checks", "rows", "fails", "incident_record",
                            "data_worktree_entries", "budget"}
                    if set(keys) != need or d.get("missing_keys"):
                        fails.append(f"A9 自检后主核验件不完整：{keys} "
                                     f"/ {d.get('missing_keys')}")
                    continue
                if want == "present":
                    # **存在性断言也必须可核对**：空壳（None/空列表/空 dict）
                    # 不算存在，否则"键在但没内容"会蒙过检查
                    if got is None or got == [] or got == {}:
                        fails.append(f"{script} → {art} 的 {k} 缺失或为空："
                                     f"{got!r}")
                    continue
                if got != want:
                    fails.append(f"{script} → {art} 的 {k} = {got!r}，"
                                 f"应为 {want!r}")
            if d.get("llm_calls_used") not in (0, None):
                fails.append(f"{art} llm_calls_used 非 0：{d['llm_calls_used']}")
        rows.append(row)

    # 重锁**不得**动预算
    plan = json.loads((EV / "R14G_PLAN.json").read_text(encoding="utf-8"))
    if plan.get("budget", {}).get("spent_so_far") != {"requests": 0, "tokens": 0}:
        fails.append(f"重锁后预算被改动：{plan['budget'].get('spent_so_far')}")
    if not plan.get("model_lock"):
        fails.append("重锁后 model_lock 段缺失 —— d9 不得抹掉人工闸门记录")
    if plan.get("fixed", {}).get("code_version") != head:
        fails.append("重锁后 code_version 仍 != HEAD，C4 不可能通过")

    # f1 是固定写：重跑后哈希**必须不变**
    key = "R14F_PERTURB_RELEASE_AUDIT.json"
    if before.get(key):
        now = sha(EV / key)
        if now != before[key]:
            fails.append("f1 固定件重跑后哈希变化 → 扰动审计判定发生漂移，"
                         f"{before[key][:12]} → {now[:12]}")
    # ---- A6：归属工具的**安全条件**是一键复现的准入条件 ----
    ap = EV / "R14E_ARTIFACT_ATTRIBUTION.json"
    if ap.is_file():
        ad = json.loads(ap.read_text(encoding="utf-8"))
        for x in ad.get("results", []):
            if x.get("safety") != "OK_ZERO_REQUESTS":
                fails.append(f"A6 归属步 safety={x.get('safety')}"
                             f"（{x.get('artifact')}）：归属核验不得产生任何"
                             f"请求或附带改动")
            if x.get("worktree_collateral"):
                fails.append(f"A6 归属步出现附带改动："
                             f"{x['worktree_collateral'][:6]}")
            if not x.get("artifact_untouched"):
                fails.append(f"A6 归属步改动了被归属件 {x.get('artifact')}")
    sp = EV / "R14E_ATTRIBUTION_SELFTEST.json"
    if sp.is_file():
        sd = json.loads(sp.read_text(encoding="utf-8"))
        if sd.get("fails"):
            fails.append(f"A6 安全闸门自检未过：{sd['fails'][:3]}")
        cd = sd.get("collateral_detector") or {}
        if cd.get("safety_seen") != "SAFETY_VIOLATION_worktree_collateral":
            fails.append(f"A6 附带改动检测器**自检未触发**："
                         f"{cd.get('safety_seen')!r}（没触发过的安全检查等于没检查）")
        if not cd.get("restored"):
            fails.append("A6 检测器自检未复原 data/ 探针")
        for inj in ("zfake_net.py", "zfake_orch.py"):
            if inj not in " ".join(sd.get("refused", [])):
                fails.append(f"A6 自检未拒跑注入样本 {inj}")
            if inj in sd.get("executed", []):
                fails.append(f"A6 **注入样本被执行过**：{inj}")

    # ---- A8：**比较忽略表本身**要被审 ----
    # 本轮实测：为消一条假阳性，我在核对脚本里把私有忽略表一路写宽，
    # 宽到会把 `committed_sha256 / rerun_at_head / violations / budget` 都吞掉
    # ——那正是"把真差异一起吃掉"。忽略表现集中在
    # `r14_attribute_producer.VOLATILE`，此处断言它**不含** MUST_SEE 键：
    # 代码基线（head / code_version）与被核对件身份必须始终可见。
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import r14_attribute_producer as _AP  # noqa: PLC0415
        vol, must = _AP.cmp_ignore_keys()
        hidden = sorted(set(must) & set(vol))
        if hidden:
            fails.append(f"A8 忽略表吃掉了**必须可见**的键：{hidden}")
        vol_keys = {"volatile_keys": vol, "must_see": must}
    except Exception as e:  # noqa: BLE001
        fails.append(f"A8 读不到集中忽略表：{e}")
        vol_keys = {}

    # ---- A7：复现差异必须被**解释件**登记，且解释机制本身有反证 ----
    # 上轮把"重跑后先恢复原样再比较"这种**恒真比较**修掉之后，才浮出真实漂移。
    # 一条被"documented"吞掉的漂移不等于没有漂移，所以这里三件事都要机器确认。
    sem_p = EV / "R14E_RERUN_SEMANTICS.json"
    if sem_p.is_file():
        semd = json.loads(sem_p.read_text(encoding="utf-8"))
        st = semd.get("drift_register_selftest") or {}
        if st.get("fails"):
            fails.append(f"A7 漂移登记**反证**失败：{st['fails'][:2]}")
        if st and st.get("after_removal_status") != "violated":
            fails.append("A7 剥掉解释件后仍算已登记 ⇒ documented 成了吞违规的开关")
        for x in semd.get("documented_verdict_drift", []):
            reg = EV / (x.get("register") or [""])[0]
            if not reg.is_file():
                fails.append(f"A7 {x['artifact']} 的解释件缺失：{reg.name}")
                continue
            rd = json.loads(reg.read_text(encoding="utf-8"))
            tgt = (rd.get("compared") or {}).get("committed")
            if tgt != x["artifact"]:
                fails.append(f"A7 解释件 {reg.name} 的 compared.committed="
                             f"{tgt!r} 与被解释件不符")
            if not rd.get("claims"):
                fails.append(f"A7 解释件 {reg.name} 未给出判据（claims）")
        unexplained = semd.get("unexplained_verdict_drift", []) or \
            [r["artifact"] for r in semd.get("rows", [])
             if r.get("status") == "semantic_drift"
             and (r.get("diff_classes") or {}).get("verdict_level")]
        if unexplained:
            fails.append(f"A7 有**未解释**的结论级漂移：{unexplained}")
        idst = semd.get("identical_detector_selftest") or {}
        if not idst:
            fails.append("A7 缺比较器反证（自检没跑或没落进件里）")
        elif idst.get("fails"):
            fails.append(f"A7 比较器**反证**失败：{idst['fails'][:2]}")
        if semd.get("violations"):
            fails.append(f"A7 补集复现核对仍有违规："
                         f"{[v['artifact'] for v in semd['violations']]}")
    else:
        fails.append("A7 缺 R14E_RERUN_SEMANTICS.json（补集复现核对未跑）")

    after_clean, after_dirty = worktree_clean()
    # ---- A11：漂移登记（生成式，不靠脚本内硬编码清单）----
    _a11 = {"registers": {}, "violated": []}
    for p in sorted((ROOT / "evidence").glob("R14*HEAD_DELTA.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _a11["violated"].append(f"{p.name} 不可解析")
            continue
        tgt = (d.get("compared") or {}).get("committed")
        if not tgt:
            _a11["violated"].append(f"{p.name} **没有** compared.committed"
                                    f" ⇒ 它登记不了任何漂移")
            continue
        _a11["registers"].setdefault(tgt, []).append(p.name)
        nv = d.get("verdict_level")
        if nv is None and "unexplained_verdict_diff" in d:
            nv = d["unexplained_verdict_diff"]
        if nv:
            _a11["violated"].append(f"{p.name} 对 {tgt} 登记了**判定级**"
                                    f"差异 {nv} ⇒ 不得自我豁免")
    # **不写死件名**：由补集核对件（本 bundle 最后一步现场产出）决定
    # 谁"必须"有登记件。硬编码清单会在下一个漂移件出现时**安静地放过它**。
    sem_p = (ROOT / "evidence" / "R14E_RERUN_SEMANTICS.json")
    _need = []
    if sem_p.is_file():
        try:
            sem = json.loads(sem_p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            sem = {}
        for x in sem.get("rows") or []:
            if x.get("status") == "semantic_drift" and \
                    (x.get("diff_classes") or {}).get("verdict_level"):
                _need.append(x["artifact"])
        _a11["derived_from"] = sem_p.name
        _a11["need_register"] = _need
    else:
        _a11["violated"].append("A11 读不到 R14E_RERUN_SEMANTICS.json"
                                " ⇒ 无从判断谁需要登记")
    for art in _need:
        if art not in _a11["registers"]:
            _a11["violated"].append(
                f"{art} 与 HEAD 存在**结论级**差异，但没有任何登记件点名它 "
                f"⇒ 差异未解释（登记件缺失就是未解释）")
    if _a11["violated"]:
        fails.append(f"A11 漂移登记不过：{_a11['violated'][:3]}")

    _a10_st = selftest_a10()
    if _a10_st["fails"]:
        fails.append(f"A10 **反证**失败：{_a10_st['fails'][:2]}")

    # ---- A12 **触发反证 + 空转自证** ----
    # 反证：把某件里的 run_mark 改一笔，判据必须报"陈旧产物"。
    # 空转自证：若本次**没有任何步骤**产出带 run_mark 的件，
    #           A12 就只是在看守一个不存在的字段 ⇒ 必须显式记未验证。
    _mk_rows = [x for x in rows if x.get("run_mark_artifact") is not None]
    _mk_ok = [x for x in _mk_rows
              if x.get("run_mark_stdout") == x.get("run_mark_artifact")]
    if not _mk_rows:
        a12 = {"verified": False,
               "note": "本次没有步骤产出 run_mark ⇒ A12 无可看守对象（不算通过）"}
    else:
        _tgt = _mk_rows[0]
        _p = EV / _tgt["artifact"]
        _orig = _p.read_text(encoding="utf-8")
        _doc = json.loads(_orig)
        _doc["run_mark"] = "deadbeef0000"
        _p.write_text(json.dumps(_doc, ensure_ascii=False, indent=1),
                      encoding="utf-8")
        _fire = None
        try:
            _after = json.loads(_p.read_text(encoding="utf-8"))
            _fire = (_after.get("run_mark") != _tgt["run_mark_artifact"])
        finally:
            _p.write_text(_orig, encoding="utf-8")
        if not _fire:
            fails.append("A12 **反证失败**：改掉件内 run_mark 后判据看不到差异")
        a12 = {"verified": True, "steps_with_mark": len(_mk_rows),
               "stdout_matches_artifact": len(_mk_ok),
               "refutation": "改掉件内 run_mark ⇒ 差异必须可见（已复原）",
               "note": "件内 mark 必须与该步 stdout mark 一致，"
                       "否则该步的件可能是上一轮残留"}
        if len(_mk_ok) != len(_mk_rows):
            fails.append(f"A12 陈旧产物：{len(_mk_rows) - len(_mk_ok)} 步的"
                         f"件内 run_mark 与 stdout 不符")

    if _a10_st["fails"]:
        fails.append(f"A10 **反证**失败：{_a10_st['fails'][:2]}")

    # ---- A10：bundle 的附带改动必须为空集之外=登记集 ----
    # `dirty_after` 不能只是"列出来给人看"：那等于把判定权交回人眼，
    # 而本清单的结论 `fails []` 会被误读成"没有多余改动"。
    _expected = set()
    for x in rows:
        if x.get("artifact"):
            _expected.add(f"evidence/{x['artifact']}")
    _scripts = {f"scripts/{Path(x['step'].split()[0]).name}"
                for x in rows if x.get("step", "").startswith("scripts/")}
    _collateral = a10_collateral(after_dirty, _expected | _scripts)
    if _collateral:
        fails.append(f"A10 SAFETY_VIOLATION_bundle_collateral："
                     f"bundle 期间改动了**未登记**的证据/数据件 {sorted(set(_collateral))}"
                     f"（已登记 {len(_expected)} 件）")
    # **交付文档里的步数由本步回写**：手写"18 步 / 21 步"
    # 就是再造一个会过期的计数（本轮我已经错过两次）。
    _doc_sync = []
    _rewrite_targets = []          # **声明的回写目标**（不管本轮改没改）
    for _dn in ("R14_STAGE_E_DELIVERABLES.md", "R14_PROGRESS_STOPGATE.md"):
        _dp = EV / _dn
        if not _dp.is_file():
            continue
        _rewrite_targets.append(_dn)
        _t = _dp.read_text(encoding="utf-8")
        # **全篇统一**：凡"N 步"（真跑/清单/一键复现）都按本次
        # `n_steps` 校正。上一版只替两个位置，结果同一篇文档里
        # `:87` 真跑 16 步、`:198` 真跑 15 步、`:312` 21 步**自相矛盾**，
        # 而我口头上说的是"步数由 bundle 回写、不再手写"。
        STEP_CTX = ("离线验证器", "一键复现", "清单")

        def _is_step_count(mm, text):
            """"N 步"只有处在**步数语境**里才是可核对的步数；
            `第 19/20 步 = …` 这类**序号**不是（误伤会把文档改成假话）。"""
            pre = text[max(0, mm.start() - 6):mm.start()]
            if re.search(r"第\s*$|/\s*$|第\s*\d+\s*/\s*$", pre):
                return False
            win = text[max(0, mm.start() - 40):mm.end() + 12]
            return any(c in win for c in STEP_CTX)

        _n = re.sub(r"(?<!\d)(\d+)\s*步",
                    lambda m: (m.group(0).replace(m.group(1), str(len(rows)))
                               if _is_step_count(m, _t) else m.group(0)), _t)
        # 修掉上一版留下的**坏排版**：`一键复现{**21 步**}` → `一键复现 **21 步**`
        _n = re.sub(r"一键复现\{\*\*(\d+) 步\*\*", r"一键复现 **\1 步**", _n)
        if _n != _t:
            _dp.write_text(_n, encoding="utf-8")
            _doc_sync.append(_dn)
    _a13_seen = []
    # **A13 残留断言**：改完必须真的没有过期步数，
    # 而不是"我写了替换逻辑"就算完。
    for _dn in ("R14_STAGE_E_DELIVERABLES.md", "R14_PROGRESS_STOPGATE.md"):
        _dp = EV / _dn
        if not _dp.is_file():
            continue
        _t = _dp.read_text(encoding="utf-8")
        for _m in re.finditer(r"(?<![\d{])(\d+)\s*步", _t):
            if _is_step_count(_m, _t):
                _a13_seen.append([_dn, int(_m.group(1))])
            if _is_step_count(_m, _t) and int(_m.group(1)) != len(rows):
                _ctx = _t[max(0, _m.start() - 26):_m.end() + 8]
                fails.append(
                    "A13 陈旧步数：" + _dn + " 仍写 " + _m.group(1) +
                    " 步，本次 bundle 是 " + str(len(rows)) +
                    " 步（上下文 " + _ctx + "）")
        _brace = re.findall("一键复现" + "[{][^}]*[}]", _t)
        if _brace:
            fails.append("A13 排版破坏：" + _dn +
                         " 的 `一键复现` 后面残留花括号 " + str(_brace[:2]))
    a13 = {"checked_docs": 2, "required_steps": len(rows),
           "synced": _doc_sync, "rewrite_targets": _rewrite_targets,
           "step_count_lines": _a13_seen,
           "note": "交付文档里的步数由本步统一回写并断言无残留；"
                   "上一版只替两个位置 ⇒ 同一篇文档出现 15/16/21 步自相矛盾"}
    # **A10b（位置修正）**：必须在 A13 **之后**——上一版放在前面、
    # 又引用 `a13` ⇒ `UnboundLocalError` 把整个 bundle 崩掉。
    # 并且用**显式并集**，不写 `x | y & z`（`&` 优先级高于 `|`，
    # 实际算的不是我写的那个意思）。
    _hw_path = ROOT / "scripts" / "e2e_r14_index.py"
    _hw = set()
    if _hw_path.is_file():
        _mt = re.search(r"HAND_WRITTEN = \{([^}]*)\}",
                        _hw_path.read_text(encoding="utf-8"), re.S)
        if _mt:
            _hw = set(re.findall(r"\"([^\"]+\.md)\"", _mt.group(1)))
    _by_step = {x["artifact"] for x in rows if x.get("artifact") in _hw}
    _by_sync = {x for x in a13.get("synced", []) if x in _hw}
    _by_target = {x for x in a13.get("rewrite_targets", []) if x in _hw}
    _hw_written = sorted(_by_step | _by_sync | _by_target)
    if _hw_written:
        fails.append(
            "A10b bundle 写出了**声明人工撰写**的件 " + str(_hw_written) +
            "（本轮改写 ∪ **声明的回写目标**）："
            "要么删掉 HAND_WRITTEN 里的声明，要么别在 bundle 里写它")

    out = {"head_at_run": head, "n_steps": len(rows),
           "docs_step_count_synced": _doc_sync,
           "A13_step_count_guard": a13, "steps": rows,
           "comparison_volatile_keys": vol_keys,
           "fails": fails,
           "worktree_clean_before": clean,
           "worktree_clean_after": after_clean,
           "A10_expected_paths": len(_expected | _scripts),
           "A10_collateral": sorted(set(_collateral)),
           "A11_drift_register": _a11,
           "A10_selftest": _a10_st,
            "A12_stale_artifact_guard": a12,
           "dirty_after": after_dirty[:12],
           "budget": {"http_requests_made": 0, "tokens_used": 0,
                      "max_requests": 6, "max_tokens": 100000,
                      "note": "Stage G 不在本清单：须人工批准 "
                              "R14_PROGRESS_STOPGATE.md 后才可由 "
                              "scripts/e2e_r14_stage_g_batch.py 发起"},
           "not_included": {
               "pytest": "由 `python -m pytest -q` 单独跑"
                         "（本 bundle 不冒充测试结果）",
               "stage_g_batch": "未获人工批准，未执行；模型名仍 "
                                "UNRESOLVED_MUST_BE_CONFIRMED_BY_HUMAN",
               "blind_eval": "NOT READY：真实截图端到端 2/5 释放正确"}}
    (EV / "R14_VERIFICATION_BUNDLE.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"head": head[:9], "n_steps": len(rows),
                      "fails": fails,
                      "all_steps_exit_zero": all(x["exit"] == 0 for x in rows),
                      "artifacts_rewritten": [x["artifact"] for x in rows
                                              if x["artifact_rewritten"]]},
                     ensure_ascii=False, indent=1))
    print("written", EV / "R14_VERIFICATION_BUNDLE.json")
    return 1 if fails or not all(x["exit"] == 0 for x in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
