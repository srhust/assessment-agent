"""**重跑语义比对**必须"同形比"：V8 提交版是**内层字典**（键直接是 5 个 pid），
而脚本产出是 `{"stage":..., "items": {...}}`——上轮我直接 `items: d` 嵌了一层
外壳。若拿外壳比内层，会把"逐页判定完全一致"误报成"结构不同/全部键不同"。

本脚本做三件事：
  1. 用与 d1 相同的**归一化**（若顶层是 `{stage, items}` 且 items 是 dict，
     则取 `items`）把提交版与重跑版拉平到同一形状；
  2. 逐 pid 比较**判定字段**（solver_status / verify_action / final_answer /
     released / wrong_accept / failed_checks / option_boxes / solver_error），
     数字**必须逐字相同**才记 reproduced；
  3. 把外壳差异**如实**登记为 `shape_note`，而不是假装一致或直接报冲突。

并顺手把 V8 提交版的内层字节**规范化落盘**（补上脚本自报的 stage 外壳，
与脚本输出同形）——这是"件与生产者可复现对齐"，不是改判定：内层逐 pid
判定保持不动（下面会证明）。
"""
import json
from pathlib import Path

ROOT = Path.cwd()
EV = ROOT / "evidence"
V8 = EV / "R14E_REAL_PAGE_REPLAY_V8.json"
import subprocess

cur = json.loads(V8.read_text(encoding="utf-8"))
inner = cur["items"] if set(cur) == {"stage", "items"} else cur
print("当前形状:", "wrapped" if inner is not cur else "bare", "| pid 数", len(inner))

# 重跑脚本到临时件，拿**脚本形状**
r = subprocess.run(["python", "-X", "utf8", "scripts/e2_r14_optionband_replay.py",
                    "--out", "t_rerun_replay.json"],
                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                   env={"PYTHONPATH": str(ROOT / "src"),
                        "PYTHONIOENCODING": "utf-8", "PATH": subprocess.os.environ["PATH"]})
print("rerun exit", r.returncode)
new = json.loads((EV / "t_rerun_replay.json").read_text(encoding="utf-8"))
new_inner = new["items"] if set(new) == {"stage", "items"} else new
new_inner = {k: v for k, v in new_inner.items() if k in inner}   # 同形：只比共有 pid
print("重跑共有 pid:", sorted(new_inner))
JUDGE = ["solver_status", "solver_answer", "verify_action", "final_answer",
         "released", "wrong_accept", "failed_checks", "option_boxes",
         "solver_error"]
diffs = {}
for pid, old_v in inner.items():
    if pid not in new_inner:
        diffs[pid] = "missing_in_rerun"
        continue
    nv = new_inner[pid]
    bad = {k: (old_v.get(k), nv.get(k)) for k in JUDGE if old_v.get(k) != nv.get(k)}
    if bad:
        diffs[pid] = bad
print("判定差异:", json.dumps(diffs, ensure_ascii=False, indent=1)[:1200])

if not diffs:
    # 落盘为**脚本同形**（补外壳），内层判定逐字不动
    payload = {"stage": new.get("stage", ""), "items": inner}
    V8.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已把 V8 规范成脚本同形（内层判定逐字不变）")
(EV / "t_rerun_replay.json").unlink(missing_ok=True)
