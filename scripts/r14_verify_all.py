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
        for inj in ("zfake_net.py", "zfake_orch.py"):
            if inj not in " ".join(sd.get("refused", [])):
                fails.append(f"A6 自检未拒跑注入样本 {inj}")
            if inj in sd.get("executed", []):
                fails.append(f"A6 **注入样本被执行过**：{inj}")

    after_clean, after_dirty = worktree_clean()
    out = {"head_at_run": head, "n_steps": len(rows), "steps": rows,
           "fails": fails,
           "worktree_clean_before": clean,
           "worktree_clean_after": after_clean,
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
