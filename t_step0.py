"""把 bundle 的 manifest 重锁步**并入清单**，作为第 0 步。

本轮实测到的**结构性**问题（不是偶发）：G0 的 C4 断言
`manifest.code_version == git HEAD`。HEAD 每次推进都会让它失败一次，
而修它的正确动作是**重算 manifest**（d9 → d9b）。于是出现死结：

    跑 bundle 前要求工作区干净 → 必须先 commit
    commit 又推进 HEAD → C4 立刻失效

上一轮我就是在这个死结里手动补跑 d9/d9b，才顺手暴露了"d9 会抹掉
model_lock"的缺陷。正确做法不是放松 C4（禁止项），也不是每次手动补，
而是**把重锁本身变成 bundle 的一步**，并要求它跑完后 C4 通过：

  step 0a  scripts/d9_r14_stage_g_manifest.py  （重算版本/哈希；d9 现已原样
          携带 model_lock，不再抹掉人工闸门）
  step 0b  scripts/d9b_r14_lock_manifest.py    （回写 prompt 源哈希、模型名
          与 key 的**实况**）
  然后才是 G0 复跑。

注意：0a/0b 会**改动** `evidence/R14G_PLAN.json`（HEAD 变了，锁必须跟着变），
所以 f1 的"重跑哈希不变"仍单独成立，而 R14G_PLAN 的变动要**如实登记**为
`expected_to_change`，不许伪装成"零改动"。
"""
import ast
import io

P = "scripts/r14_verify_all.py"
s = io.open(P, encoding="utf-8").read()

old = '''STEPS = [
    ("scripts/e2e_r14_stage_e_acceptance.py",'''
new = '''STEPS = [
    # ---- step 0：G 批 manifest 重锁（HEAD 一推进 C4 必失效，故把"重锁"
    # 本身变成清单的一步，而不是放松 C4，也不是每轮手动补跑）
    ("scripts/d9_r14_stage_g_manifest.py",
     "R14G_PLAN.json",
     {"status": "PLANNED_NOT_EXECUTED", "llm_calls_used": 0,
      "http_requests_made": 0},
     "fixed", None),
    ("scripts/d9b_r14_lock_manifest.py",
     "R14G_PLAN.json",
     {"model_lock": None},          # 占位，稍后由"实况断言"替换
     "fixed", None),
    ("scripts/e2e_r14_stage_e_acceptance.py",'''
assert s.count(old) == 1
s = s.replace(old, new)

# d9b 的断言改成"锁已如实暴露"（不写 null 让评审去猜）
s = s.replace('''    ("scripts/d9b_r14_lock_manifest.py",
     "R14G_PLAN.json",
     {"model_lock": None},          # 占位，稍后由"实况断言"替换
     "fixed", None),''',
'''    ("scripts/d9b_r14_lock_manifest.py",
     "R14G_PLAN.json",
     {"model_lock": {"status": "UNRESOLVED_MUST_BE_CONFIRMED_BY_HUMAN"}},
     "fixed", None),''')

# R14G_PLAN 允许因 HEAD 推进而改动：登记为 expected_to_change
old2 = '''        row = {"step": script, "artifact": art, "out_mode": out_mode,'''
new2 = '''        row = {"step": script, "artifact": art, "out_mode": out_mode,
               "expected_to_change": art in EXPECTED_CHANGE,'''
assert s.count(old2) == 1
s = s.replace(old2, new2)

old3 = '''SNAPSHOT_MODE = {"scripts/f1_r14_perturb_release_audit.py"}'''
new3 = '''SNAPSHOT_MODE = {"scripts/f1_r14_perturb_release_audit.py"}
# 允许**因 HEAD 推进而如实改动**的件：G 批 manifest 的版本锁必须跟着 HEAD 走。
# 登记出来而不是假装"零改动"。
EXPECTED_CHANGE = {"R14G_PLAN.json"}'''
assert s.count(old3) == 1
s = s.replace(old3, new3)

# 顶层 model_lock 断言需要"子集比较"：改成对 dict 做子集匹配
old4 = '''            for k, want in must.items():
                got = dig(d, k)
                if got != want:'''
new4 = '''            for k, want in must.items():
                got = dig(d, k)
                if isinstance(want, dict) and isinstance(got, dict):
                    if any(got.get(wk) != wv for wk, wv in want.items()):
                        fails.append(f"{script} → {art} 的 {k} 子集不符："
                                     f"{ {wk: got.get(wk) for wk in want} }"
                                     f" ≠ {want}")
                    continue
                if got != want:'''
assert s.count(old4) == 1
s = s.replace(old4, new4)

# 预算不变断言：重锁后 spent_so_far 仍须 0/0
old5 = '''    # f1 是固定写：重跑后哈希**必须不变**'''
new5 = '''    # 重锁**不得**动预算
    plan = json.loads((EV / "R14G_PLAN.json").read_text(encoding="utf-8"))
    if plan.get("budget", {}).get("spent_so_far") != {"requests": 0, "tokens": 0}:
        fails.append(f"重锁后预算被改动：{plan['budget'].get('spent_so_far')}")
    if not plan.get("model_lock"):
        fails.append("重锁后 model_lock 段缺失 —— d9 不得抹掉人工闸门记录")
    if plan.get("fixed", {}).get("code_version") != head:
        fails.append("重锁后 code_version 仍 != HEAD，C4 不可能通过")

    # f1 是固定写：重跑后哈希**必须不变**'''
assert s.count(old5) == 1
s = s.replace(old5, new5)

io.open(P, "w", encoding="utf-8").write(s)
ast.parse(s)
print("ok lines", len(s.splitlines()))
