"""把**诊断字段**补回证据链，并只比**判定字段**。

事实：提交版 V8 的 `option_boxes` 是三元组 `[cell_bbox_page, label_bbox, ink]`；
重跑版是二元组 `[bbox_page, ink]`。原因不是判定漂移，而是我后来在诊断脚本里
把 emit 写窄了——**丢了 label_bbox**。这恰恰是 R14 目标①"标签一一对应"要看的
字段，属于**证据可核对性退化**，必须补回去（而不是把比较放宽成"只比 bbox 首元"
来蒙混过哈希检查）。

本步：
  1. `e2_r14_optionband_replay.py` 的 emit 改为三元组（cell_bbox_page +
     label_bbox + ink），与提交版同形；
  2. bundle 的比较改成**判定字段逐字相等**（solver_status / solver_answer /
     verify_action / final_answer / released / wrong_accept / failed_checks），
     诊断字段单独登记 `diagnostic_fields_identical`，不与判定混为一谈；
  3. **绝不覆盖**已提交的 V8 字节（目标②禁止"改历史 Test 结果"）：
     件里另存 `t_rerun_replay.json` 的重跑产物只做比对，比对完删除。
"""
import ast
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
P = ROOT / "scripts" / "e2_r14_optionband_replay.py"
s = io.open(P, encoding="utf-8").read()
old = '''            "option_boxes": [[c["bbox_page"], c["ink"]] for c in opt_cells][:6],'''
new = '''            # 三元组与已提交 V8 同形：cell_bbox_page + label_bbox + ink。
            # 曾写窄成二元组，丢了 label_bbox（正是"标签一一对应"要看的信息）。
            "option_boxes": [[c.get("cell_bbox_page") or c.get("bbox_page"),
                              c.get("label_bbox"), c["ink"]] for c in opt_cells][:6],'''
assert s.count(old) == 1, "emit 行未找到"
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
ast.parse(s)
print("emit 已补 label_bbox")

r = subprocess.run([sys.executable, "-X", "utf8", str(P),
                    "--out", "t_rerun_replay.json"],
                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                   env={"PYTHONPATH": str(ROOT / "src"),
                        "PYTHONIOENCODING": "utf-8"})
print("rerun exit", r.returncode)
old_doc = json.loads((ROOT / "evidence" / "R14E_REAL_PAGE_REPLAY_V8.json")
                     .read_text(encoding="utf-8"))
new_doc = json.loads((ROOT / "evidence" / "t_rerun_replay.json")
                     .read_text(encoding="utf-8"))
ni = new_doc.get("items", new_doc)
VERDICT = ["solver_status", "solver_answer", "verify_action", "final_answer",
           "released", "wrong_accept", "failed_checks", "solver_error"]
out = {}
for pid in sorted(old_doc):
    o, n = old_doc[pid], ni.get(pid, {})
    vd = {k: (o.get(k), n.get(k)) for k in VERDICT if o.get(k) != n.get(k)}
    out[pid] = {"verdict_identical": not vd,
                "diagnostic_option_boxes_identical":
                    o.get("option_boxes") == n.get("option_boxes"),
                "released": o.get("released"),
                "wrong_accept": o.get("wrong_accept")}
    if vd:
        out[pid]["verdict_diff"] = {k: str(v)[:200] for k, v in vd.items()}
print(json.dumps(out, ensure_ascii=False, indent=1))
print("判定全一致:", all(x["verdict_identical"] for x in out.values()),
      "| 诊断字段也同形:", all(x["diagnostic_option_boxes_identical"] for x in out.values()))
print("已提交 V8 字节是否被动过:",
      subprocess.run(["git", "status", "--porcelain", "evidence/R14E_REAL_PAGE_REPLAY_V8.json"],
                     cwd=ROOT, capture_output=True, text=True).stdout.strip() or "(clean)")
(ROOT / "evidence" / "t_rerun_replay.json").unlink(missing_ok=True)
