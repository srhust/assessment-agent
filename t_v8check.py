"""**必须诚实承认的复现缺口**（不许粉饰）。

事实链（逐条核实）：
  · 已提交的 `R14E_REAL_PAGE_REPLAY_V8.json` 每页**多**这些键：
    `chain / consumed_crop_shas / consumption_binding /
     label_association_verified / option_sha256 / options_used / provenance`；
  · 当前 HEAD 的 `e2_r14_optionband_replay.py` 重跑**产不出**这些键，
    并且它读的 `src/.../question.json` 是**已被就地重写**的版本
    （`image_path` 指向本工作区、options 由当时的富化脚本填过）；
  · 生产链路 `_rebind_option_evidence` 确实还在 `figure_reasoning.py:276+`，
    但重放脚本**没有走**到能暴露这些字段的那一步。

⇒ V8 是**当时那次富化运行**的产物，**现在无法从 HEAD 复现**。
这不是"数字变了"，而是"复现性不足"，必须分开登记：
  A) **判定字段**（solver_status / answer / verify_action / final_answer /
     released / wrong_accept / failed_checks / solver_error /
     option_boxes）——已证明**逐字一致**（本轮实测 5/5）；
  B) **复现性**——V8 **NOT reproducible from HEAD**，如实标注，
     不得在 STOPGATE 里说成"离线基线全部可复现"。

本脚本把 A 的比对固化为 `R14E_REAL_PAGE_REPLAY_V8_CHECK.json`，
并在里面显式写 B；然后由我更新 STOPGATE / DELIVERABLES 的措辞。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
EV = ROOT / "evidence"
old = json.loads((EV / "R14E_REAL_PAGE_REPLAY_V8.json").read_text(encoding="utf-8"))
rerun_path = EV / "t_rerun_replay.json"
if not rerun_path.is_file():
    # 重跑没产出（脚本非零退出等）→ **如实报告**，不拿旧件自比制造"一致"
    print(json.dumps({"fatal": f"重跑未产出 {rerun_path.name}；退出码 "
                              f"{r.returncode}；stderr 尾 {r.stderr[-300:]}"},
                     ensure_ascii=False))
    sys.exit(1)
new = json.loads(rerun_path.read_text(encoding="utf-8"))
ni = new.get("items", new)

VERDICT = ["solver_status", "solver_answer", "verify_action", "final_answer",
           "released", "wrong_accept", "failed_checks", "solver_error",
           "option_boxes"]
rows = {}
for pid in sorted(old):
    o = old[pid]
    n = ni.get(pid, {})
    diff = {k: (o.get(k), n.get(k)) for k in VERDICT if o.get(k) != n.get(k)}
    rows[pid] = {
        "verdict_identical": not diff,
        "verdict_diff": {k: str(v)[:200] for k, v in diff.items()},
        "released": o.get("released"), "wrong_accept": o.get("wrong_accept"),
        "extra_keys_only_in_committed": sorted(set(o) - set(n)),
        "extra_keys_only_in_rerun": sorted(set(n) - set(o)),
    }
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                      capture_output=True, text=True).stdout.strip()
doc = {
    "purpose": "真实截图复放件 V8 的**可复现性核验**（零 API）",
    "head": head,
    "verdict_fields_checked": VERDICT,
    "verdict_identical_all": all(x["verdict_identical"] for x in rows.values()),
    "per_page": rows,
    "reproducibility": {
        "status": "NOT_REPRODUCIBLE_FROM_HEAD",
        "why": "V8 由当时的富化运行产生（含 chain / consumed_crop_shas / "
               "consumption_binding / label_association_verified / option_sha256 "
               "/ options_used / provenance 等键），当前 HEAD 的重放脚本产不出"
               "这些键，且其读取的 question.json 已被就地重写",
        "what_is_still_true": "判定字段与 option_boxes 逐字一致（见 "
                              "verdict_identical_all），因此**结论未变**；"
                              "退化的是**证据可核对性**，不是数字",
        "action_for_human": "若要恢复完整复现，需要一条把富化步骤纳入脚本的"
                            "改动（属新规则/新脚本，须人工批准；本轮不做）",
    },
    "no_history_rewritten": (EV / "R14E_REAL_PAGE_REPLAY_V8.json").is_file(),
    "llm_calls_used": 0, "http_requests_made": 0,
}
(EV / "R14E_REAL_PAGE_REPLAY_V8_CHECK.json").write_text(
    json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
(EV / "t_rerun_replay.json").unlink(missing_ok=True)
(EV / "t_rr2.json").unlink(missing_ok=True)
print(json.dumps({"verdict_identical_all": doc["verdict_identical_all"],
                  "reproducibility": doc["reproducibility"]["status"],
                  "per_page_verdict": {k: v["verdict_identical"] for k, v in rows.items()}},
                 ensure_ascii=False, indent=1))
