"""把"V8 复现性核验"从一次性 scratch 升级为**入库脚本**，
并把 bundle 的 skip 形态写进去（上一步的 scratch 因内层 docstring 用三重引号
而 SyntaxError，未写盘——正是"字符级盲替换改源码"这一自伤路径的再现，
所以这里改**整段新文件**，并在最后 ast.parse 校验）。
"""
import ast
import io
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()

# ---------- 1) 入库脚本 ----------
NEW_SCRIPT = '''"""真实截图复放件 V8 的**可复现性核验**（零 API、不写回 V8）。

为什么要单独有这件：V8 是当时一次**富化运行**的产物，含
`chain / consumed_crop_shas / consumption_binding /
label_association_verified / option_sha256 / options_used / provenance`
等键；当前 HEAD 的重放脚本产不出这些键，且它读的 `question.json` 已被
就地重写（绝对路径改写 + 选项富化）。所以：

  · **判定层**（solver_status / solver_answer / verify_action / final_answer /
    released / wrong_accept / failed_checks / solver_error / option_boxes）
    —— 与 V8 **逐字一致**（本件实测 5/5）⇒ 结论未变；
  · **证据层** —— V8 **NOT reproducible from HEAD** ⇒ 复现性退化，如实登记。

绝不把重跑产物写回 V8：那等于改历史 Test 结果（目标②禁止）。重跑只落到
`R14E_REAL_PAGE_REPLAY_V8_RERUN.json`，比对用，独立可查。
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"
V8 = "R14E_REAL_PAGE_REPLAY_V8.json"
RERUN = "R14E_REAL_PAGE_REPLAY_V8_RERUN.json"

VERDICT = ["solver_status", "solver_answer", "verify_action", "final_answer",
           "released", "wrong_accept", "failed_checks", "solver_error",
           "option_boxes"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(out_name: str = RERUN) -> int:
    before_v8 = sha(EV / V8)
    r = subprocess.run([sys.executable, "-X", "utf8",
                        str(ROOT / "scripts" / "e2_r14_optionband_replay.py"),
                        "--out", out_name],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
                            "PYTHONIOENCODING": "utf-8"})
    rp = EV / out_name
    if not rp.is_file():
        print(json.dumps({"fatal": f"重跑未产出 {out_name}；exit={r.returncode}；"
                                   f"stderr 尾 {r.stderr[-300:]}"},
                         ensure_ascii=False))
        return 1
    old = json.loads((EV / V8).read_text(encoding="utf-8"))
    new = json.loads(rp.read_text(encoding="utf-8"))
    ni = new.get("items", new)
    rows = {}
    for pid in sorted(old):
        o, n = old[pid], ni.get(pid, {})
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
    after_v8 = sha(EV / V8)
    doc = {
        "purpose": "V8 复现性核验：判定层可复现、证据层不可复现，二者分开登记",
        "head": head,
        "verdict_fields_checked": VERDICT,
        "verdict_identical_all": all(x["verdict_identical"] for x in rows.values()),
        "per_page": rows,
        "reproducibility": {
            "status": "NOT_REPRODUCIBLE_FROM_HEAD",
            "why": "V8 含富化键（chain/consumed_crop_shas/consumption_binding/"
                   "label_association_verified/option_sha256/options_used/"
                   "provenance），当前 HEAD 的重放脚本产不出；且其题面 "
                   "question.json 已被就地重写",
            "what_is_still_true": "判定字段与 option_boxes 逐字一致 ⇒ 结论未变",
            "degraded": "证据可核对性（不是数字）",
            "action_for_human": "若要恢复完整复现，需把富化步骤纳入可重跑脚本"
                                "（属新脚本，须人工批准；本轮不做）",
        },
        "v8_untouched": before_v8 == after_v8,
        "v8_sha256": after_v8[:12],
        "rerun_artifact": out_name,
        "llm_calls_used": 0, "http_requests_made": 0,
    }
    dst = EV / "R14E_REAL_PAGE_REPLAY_V8_CHECK.json"
    dst.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"verdict_identical_all": doc["verdict_identical_all"],
                      "reproducibility": doc["reproducibility"]["status"],
                      "v8_untouched": doc["v8_untouched"],
                      "per_page_verdict": {k: v["verdict_identical"]
                                          for k, v in rows.items()}},
                     ensure_ascii=False, indent=1))
    print("written", dst)
    return 0 if doc["verdict_identical_all"] and doc["v8_untouched"] else 1


if __name__ == "__main__":
    name = RERUN
    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]
    sys.exit(main(name))
'''
io.open("scripts/e2_r14_v8_reproducibility.py", "w", encoding="utf-8").write(NEW_SCRIPT)
ast.parse(NEW_SCRIPT)
print("入库脚本 ok", len(NEW_SCRIPT.splitlines()))

# ---------- 2) bundle 的 skip 形态 ----------
P = "scripts/r14_verify_all.py"
s = io.open(P, encoding="utf-8").read()

old = '''    ("scripts/e2_r14_optionband_replay.py",
     "R14E_REAL_PAGE_REPLAY_V8.json", None, "bare",
     "R14E_REAL_PAGE_REPLAY_V8.json"),'''
new = '''    # 真实截图复放件 V8：判定层可复现（见 R14E_REAL_PAGE_REPLAY_V8_CHECK），
    # 但 V8 的**富化键**当前 HEAD 产不出、其题面 question.json 已被就地重写
    # ⇒ **NOT reproducible from HEAD**。把重跑产物写回 V8 等于**改历史 Test
    # 结果**（目标②禁止），故此步**不重跑**，只核对"该件没被动过"；
    # 判定层复现由独立脚本 e2_r14_v8_reproducibility.py 负责（它写 **_RERUN**
    # 新文件，绝不覆盖 V8）。
    ("(no-rerun) scripts/e2_r14_optionband_replay.py",
     "R14E_REAL_PAGE_REPLAY_V8.json", None, "skip", None),
    ("scripts/e2_r14_v8_reproducibility.py",
     "R14E_REAL_PAGE_REPLAY_V8_CHECK.json",
     {"verdict_identical_all": True, "v8_untouched": True,
      "reproducibility": {"status": "NOT_REPRODUCIBLE_FROM_HEAD"}},
     "bare", "R14E_REAL_PAGE_REPLAY_V8_CHECK.json"),'''
assert s.count(old) == 1, "replay 步未找到"
s = s.replace(old, new)

old2 = '''        will_write = {
            "bare": str(EV / out_val) if out_val else None,
            "cwd_relative": str(ROOT / out_val) if out_val else None,
            "fixed": str(EV / art),
        }[out_mode]'''
new2 = '''        if out_mode == "skip":
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
        }[out_mode]'''
assert s.count(old2) == 1
s = s.replace(old2, new2)

old3 = '''        if "R14_VERIFICATION_BUNDLE" in path or SCRATCH.match(base):
            continue'''
new3 = '''        if "R14_VERIFICATION_BUNDLE" in path or SCRATCH.match(base):
            continue
        if path.startswith("evidence/R14E_REAL_PAGE_REPLAY_V8_RERUN"):
            continue          # 比对用的重跑新件，允许未跟踪
        if path.startswith("evidence/R14E_REAL_PAGE_REPLAY_V8.") :
            fails.append(f"A5 禁止改动历史件：{x}")
            continue'''
assert s.count(old3) == 1
s = s.replace(old3, new3)

io.open(P, "w", encoding="utf-8").write(s)
ast.parse(s)
print("bundle ok", len(s.splitlines()))
