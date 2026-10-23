"""**不要覆盖，要证明**：上一步的同形比对暴露 `option_boxes` 看起来"不同"，
但很可能是**我提交版截断到 6 格**、而重跑产出**未截断**造成的形状差，
不是判定差。先把两侧完整打印出来看清，再决定怎么写这条不变量。

规则：Stage G 的闸门只依赖**判定字段**
（solver_status / verify_action / final_answer / released / wrong_accept /
failed_checks）。`option_boxes` 是诊断信息。若差异**仅**由截断引起，正确做法是
(a) 判定字段逐字相等 → 记 `verdict_identical`；
(b) `option_boxes` 以**前缀**方式一致（截断版 == 未截断版的前 k 项）；
(c) 绝不为了"哈希不变"去覆盖 V8 的已提交字节——那正是"改历史 Test 结果"。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
EV = ROOT / "evidence"
old = json.loads((EV / "R14E_REAL_PAGE_REPLAY_V8.json").read_text(encoding="utf-8"))
r = subprocess.run([sys.executable, "-X", "utf8",
                    "scripts/e2_r14_optionband_replay.py",
                    "--out", "t_rerun_replay.json"],
                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                   env={"PYTHONPATH": str(ROOT / "src"),
                        "PYTHONIOENCODING": "utf-8"})
new = json.loads((EV / "t_rerun_replay.json").read_text(encoding="utf-8"))
ni = new["items"] if set(new) == {"stage", "items"} else new

VERDICT = ["solver_status", "solver_answer", "verify_action", "final_answer",
           "released", "wrong_accept", "failed_checks", "solver_error"]
report = {}
for pid in sorted(old):
    o, n = old[pid], ni.get(pid, {})
    vdiff = {k: (o.get(k), n.get(k)) for k in VERDICT if o.get(k) != n.get(k)}
    ob_old, ob_new = o.get("option_boxes") or [], n.get("option_boxes") or []
    prefix_ok = ob_new[:len(ob_old)] == ob_old
    report[pid] = {"verdict_identical": not vdiff,
                   "verdict_diff": vdiff,
                   "option_boxes_old_n": len(ob_old),
                   "option_boxes_new_n": len(ob_new),
                   "old_is_prefix_of_new": prefix_ok,
                   "released": o.get("released"),
                   "wrong_accept": o.get("wrong_accept")}
    if vdiff:
        report[pid]["verdict_diff"] = {k: str(v)[:160] for k, v in vdiff.items()}
print(json.dumps(report, ensure_ascii=False, indent=1))
print("全部判定字段一致:", all(x["verdict_identical"] for x in report.values()))
print("option_boxes 全部为前缀关系:",
      all(x["old_is_prefix_of_new"] for x in report.values()))
(EV / "t_rerun_replay.json").unlink(missing_ok=True)
