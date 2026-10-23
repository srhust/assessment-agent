"""R14 **一键复现**（零 API）：按固定顺序重跑全部离线验证器，把每次运行的
退出码、耗时、产物 sha256、件内关键数字与 stdout 摘要合成**一个 bundle**。

与 `R14_INDEX` 的分工：索引登记**身份**（谁写的、哈希多少），本 bundle 证明
**现在从干净工作区仍能重跑出同样判定数字**。两者都不可省。

诚实要点：
  · 子件是**真跑**（subprocess，PYTHONPATH=src），不是读旧 JSON 复读；
  · `f1_r14_perturb_release_audit.py` 是 SNAPSHOT_MODE（忽略 --out、固定写
    `evidence/R14F_PERTURB_RELEASE_AUDIT.json`），所以它**重跑后哈希必须不变**
    才算通过——这是设计里的强检查，不是运气；
  · `e2e_r14_head_reverify.py` 会**断言工作区干净**，故本脚本要求先 commit
    再跑（不干净就直接 fail，绝不悄悄 stash）；
  · 任何子件 nonzero 退出 → bundle 非零退出，并把 stdout/stderr 尾巴入账；
  · 全程 **0 HTTP / 0 token**，Stage G 不在本清单内（须人工批准）。
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

# (脚本, 该步要核对的现行件, 该件里必须保持的关键事实路径)
STEPS = [
    ("scripts/e2e_r14_stage_e_acceptance.py",
     "R14E_STAGE_E_ACCEPTANCE_V8.json",
     {"checks_failed": [], "wrong_releases": [], "llm_calls_used": 0},
     "evidence/R14E_STAGE_E_ACCEPTANCE_V8.json"),
    ("scripts/d1_r11_six_condition.py",
     "R14E_SIX_CONDITION_V10.json", None,
     "evidence/R14E_SIX_CONDITION_V10.json"),
    ("scripts/f1_r14_perturb_release_audit.py",
     "R14F_PERTURB_RELEASE_AUDIT.json",
     {"perturbation_regression.wrong_grid_releasable": []}, None),
    ("scripts/e2e_r14_newlayout_regression.py",
     "R14E_NEWLAYOUT_REGRESSION_V3.json",
     {"checks_failed": [], "wrong_releases": [], "llm_calls_used": 0,
      "layers": {"candidates_solved": 16, "accepted": 7, "released": 7}},
     "evidence/R14E_NEWLAYOUT_REGRESSION_V3.json"),
    ("scripts/e2e_r14_tight_gap_diagnosis.py",
     "R14E_TIGHT_GAP_DIAGNOSIS.json", None, None),
    ("scripts/e2e_r14_anchor_sweep.py",
     "R14E_ANCHOR_SWEEP.json", None, None),
    ("scripts/e2_r14_optionband_replay.py",
     "R14E_REAL_PAGE_REPLAY_V8.json", None, None),
    ("scripts/e2e_r14_six_condition_lineage.py",
     "R14E_LINEAGE_SIX_CONDITION.json", {"v10_equals_v9": True}, None),
    ("scripts/e2e_r14_stage_g_readiness.py",
     "R14G0_GATE_READINESS_V5.json",
     {"real_http_requests_made": 0, "failing": []}, None),
    ("scripts/e2e_r14_claim_audit.py", "R14_CLAIM_AUDIT.json",
     {"fails": []}, None),
    ("scripts/e2e_r14_index.py", "R14_INDEX.json",
     {"self_checks_failed": []}, None),
]
SNAPSHOT_MODE = {"scripts/f1_r14_perturb_release_audit.py"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def dig(obj, dotted):
    cur = obj
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


# 一次性运行日志（非证据件）不该挡住"工作区干净"闸门；证据件仍逐哈希核对
SCRATCH = re.compile(r"^(t_|tmp_|\.DS_Store)")


def worktree_clean():
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                       capture_output=True, text=True)
    lines = [x for x in (r.stdout or "").splitlines() if x.strip()]
    keep = []
    for x in lines:
        path = x[3:].strip().replace("\\", "/")
        base = path.rsplit("/", 1)[-1]
        if "R14_VERIFICATION_BUNDLE" in path or SCRATCH.match(base):
            continue
        keep.append(x)
    return (not keep), keep


def main() -> int:
    clean, dirty = worktree_clean()
    if not clean:
        print(json.dumps({"fatal": "工作区不干净：head_reverify / bundle 要求"
                                   "先提交再复现（不自动 stash）",
                          "dirty": dirty[:12]}, ensure_ascii=False, indent=1))
        return 2
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    # 缺件先失败：bundle 的意义是"能重跑并复现判定"，不是"先把空槽写进账"
    missing = [art for _, art, _, _ in STEPS
               if not (EV / art).is_file()]
    if missing:
        print(json.dumps({"fatal": "以下现行件不存在，无法核对", "missing": missing},
                         ensure_ascii=False))
        return 2
    before = {p.name: sha(p) for p in EV.glob("R14*") if p.is_file()}
    rows, fails = [], []
    for script, art, must, out_flag in STEPS:
        argv = [sys.executable, "-X", "utf8", str(ROOT / script)]
        if out_flag:
            argv += ["--out", out_flag]
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
               "PYTHONIOENCODING": "utf-8"}
        t0 = time.time()
        r = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", env=env)
        dt = round(time.time() - t0, 1)
        p = EV / art
        row = {"step": script, "artifact": art, "exit": r.returncode,
               "seconds": dt,
               "artifact_sha256_short": sha(p)[:12] if p.is_file() else None,
               "artifact_rewritten": (p.is_file() and
                                      before.get(art) != sha(p)),
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
                if got != want:
                    fails.append(f"{script} → {art} 的 {k} = {got!r}，"
                                 f"应为 {want!r}")
            if "llm_calls_used" in d and d.get("llm_calls_used") not in (0, None):
                fails.append(f"{art} llm_calls_used 非 0：{d['llm_calls_used']}")
        rows.append(row)

    # f1 是固定写：重跑后哈希**必须不变**
    if before.get("R14F_PERTURB_RELEASE_AUDIT.json"):
        now = sha(EV / "R14F_PERTURB_RELEASE_AUDIT.json")
        if now != before["R14F_PERTURB_RELEASE_AUDIT.json"]:
            fails.append("f1 固定件重跑后哈希变化 → 扰动审计判定发生漂移，"
                         f"{before['R14F_PERTURB_RELEASE_AUDIT.json'][:12]} → "
                         f"{now[:12]}")
    after_clean, after_dirty = worktree_clean()
    out = {"head_at_run": head, "n_steps": len(rows),
           "steps": rows, "fails": fails,
           "worktree_clean_before": clean,
           "worktree_clean_after": after_clean,
           "dirty_after": [x for x in after_dirty
                           if "R14_VERIFICATION_BUNDLE" not in x][:12],
           "budget": {"http_requests_made": 0, "tokens_used": 0,
                      "max_requests": 6, "max_tokens": 100000,
                      "note": "Stage G 不在本清单：须人工批准 R14_PROGRESS_STOPGATE"
                              ".md 后才可由 scripts/e2e_r14_stage_g_batch.py 发起"},
           "not_included": {
               "pytest": "由 `python -m pytest -q` 单独跑（本 bundle 不冒充测试结果）",
               "stage_g_batch": "未获人工批准，未执行；模型名仍 UNRESOLVED_MUST_BE_"
                                "CONFIRMED_BY_HUMAN",
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
