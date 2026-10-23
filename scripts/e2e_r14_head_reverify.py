"""HEAD 复验（零 API）：三项离线基线在**当前提交状态**重跑并逐字段比对，
证明本轮新增的回归/就绪脚本没有改动任何判定数字。

比对方式按脚本实测落点分两类，且**不改动任何历史件**：
  · d1 / 验收：`--out` 写**新文件名**（V10 / V8），与已入库 V9 / V7 比对；
  · f1：该脚本忽略 `--out`、固定写 `evidence/R14F_PERTURB_RELEASE_AUDIT.json`
    （历史件）。因此先取它的 **git HEAD 版本**作快照 → 重跑 → 用**重跑产物**
    与快照逐字段比对 → 随后**用快照还原工作区**，历史件字节不由本轮改动。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUNS = [
    ("scripts/d1_r11_six_condition.py", "R14E_SIX_CONDITION_V10.json",
     "R14E_SIX_CONDITION_V9.json", "evidence/R14E_SIX_CONDITION_V10.json"),
    ("scripts/e2e_r14_stage_e_acceptance.py",
     "R14E_STAGE_E_ACCEPTANCE_V8.json",
     "R14E_STAGE_E_ACCEPTANCE_V7.json",
     "evidence/R14E_STAGE_E_ACCEPTANCE_V8.json"),
    ("scripts/f1_r14_perturb_release_audit.py",
     "R14F_PERTURB_RELEASE_AUDIT.json", "R14F_PERTURB_RELEASE_AUDIT.json",
     "evidence/R14F_PERTURB_RELEASE_AUDIT.json"),
]
SNAPSHOT_MODE = {"scripts/f1_r14_perturb_release_audit.py"}


def load(name):
    return json.loads((ROOT / "evidence" / name).read_text(encoding="utf-8"))


def summarize(o):
    """取**判定数字**子集比对（不含时间戳/路径等易变字段）。"""
    skip = {"rows", "pages", "perturb_rows", "audit", "by_item",
            "per_item", "detail", "generated_at", "timestamp", "run_id", "cwd"}
    return {k: v for k, v in o.items() if k not in skip}


def main() -> int:
    notes, drift = {}, []
    for script, new_name, old_name, expected_rel in RUNS:
        argv = [sys.executable, "-X", "utf8", str(ROOT / script)]
        if script not in SNAPSHOT_MODE:
            # d1 把 `--out` 当**相对 cwd 的路径**用，故这里给 evidence/ 前缀，
            # 让新件与历史件同目录（先前一次运行把它写到了仓库根，实测记录）。
            argv += ["--out", expected_rel if script.startswith("scripts/d1")
                     else new_name]
        expected = ROOT / expected_rel
        snapshot = None
        if script in SNAPSHOT_MODE:
            g = subprocess.run(["git", "show", "HEAD:" + expected_rel],
                               cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8")
            if g.returncode != 0:
                drift.append(f"{script} 取不到历史件快照：{g.stderr[-120:]}")
                continue
            snapshot = g.stdout
        r = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            drift.append(f"{script} 运行失败 exit={r.returncode}: "
                         f"{(r.stderr or r.stdout)[-300:]}")
            continue
        if not expected.exists():
            drift.append(f"{script} 未产出可核对文件：{expected_rel}")
            continue
        rerun_text = expected.read_text(encoding="utf-8")
        if snapshot is not None:
            expected.write_text(snapshot, encoding="utf-8")  # 还原历史件
        new = json.loads(rerun_text)
        old = json.loads(snapshot) if snapshot is not None else load(old_name)
        s_new, s_old = summarize(new), summarize(old)
        same = json.dumps(s_new, ensure_ascii=False, sort_keys=True) == \
            json.dumps(s_old, ensure_ascii=False, sort_keys=True)
        notes[f"{new_name} vs {old_name}"] = {
            "artifact_written_to": expected_rel,
            "compare_mode": "committed-snapshot-rerun-product-then-restored"
            if snapshot is not None else "new-filename",
            "historical_artifact_restored": snapshot is not None,
            "identical_verdict_numbers": same,
            "differing_keys": sorted({k for k in set(s_new) | set(s_old)
                                      if s_new.get(k) != s_old.get(k)}),
        }
        if not same:
            drift.append(f"{new_name} 与 {old_name} 判定数字不同")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--",
                            "evidence/R14F_PERTURB_RELEASE_AUDIT.json"],
                           cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"head": head, "comparisons": notes, "drift": drift,
           "f1_worktree_after_restore": dirty or "(clean)",
           "llm_calls_used": 0,
           "note": "新文件名重跑 + 历史件快照还原：比对只读，不改历史结果件。"}
    dst = ROOT / "evidence" / "R14E_HEAD_REVERIFY.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not drift else 1


if __name__ == "__main__":
    sys.exit(main())
