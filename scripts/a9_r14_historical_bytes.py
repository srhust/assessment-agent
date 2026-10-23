"""**A9：历史 Test 件的字节完整性必须逐字节对"引入 commit"核验。**

为什么不能只看 HEAD：`git status` 干净只证明"等于当前提交"，
而当前提交本身就可能已经被我改坏；真正的基线是**当初把这件数据写进仓库的那个
commit 的 blob**。本轮实测的正是这种事故——归属工具误挑中
`g1_r11_real_vision.py / g2_r12_real_vision.py / g3_r13_real_batch.py`
（三个会发真实 VLM 请求的历史批脚本），把
`evidence/R11G_REAL_VISION.json / R12G_ / R13G_` **重写**了（mtime 10-23 17:20），
我用 `git checkout --` 复原。

**同时纠正我自己记的一条事实**：早先的会话笔记声称"还覆写了
`data/vlm_responses.jsonl`，从 `backup_r14_apikey/` 恢复"。本轮实测：
仓库内外**都不存在** `data/vlm_responses.jsonl`，`scripts/**` 里也没有任何写出它
的代码，`backup_r14_apikey/` 目录不存在，`data/**` 自 10-21 起未被本轮改动
（扰动图片 mtime 全为 10-21 20:29，`data/benchmark_figshots_perturb/` 处于
gitignore）。那条笔记**不成立**，按事实收缩为"三个 `evidence/R?G_*.json`
被重写并从 git 复原"。写检查前先核事实，不然检查会固化一个假故事。
"""
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"

HIST = ["R11G_REAL_VISION.json", "R12G_REAL_VISION.json",
        "R13G_REAL_VISION.json", "R11G_FREEZE.json", "R12G_FREEZE.json",
        "R13G_REAL_PARSE_OFFLINE_REPLAY.json"]


def git(*a, binary=False):
    r = subprocess.run(["git", "-C", str(ROOT)] + list(a),
                       capture_output=True, text=not binary)
    return r.stdout


def blob(commit, rel):
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:{rel}"],
                       capture_output=True)
    return None if r.returncode else hashlib.sha256(r.stdout).hexdigest()


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    rows, fails = [], []
    for f in HIST:
        rel = f"evidence/{f}"
        p = EV / f
        if not p.is_file():
            fails.append(f"{f} 不在盘上")
            continue
        added = git("log", "--diff-filter=A", "--format=%h", "--", rel).split()
        intro = added[-1] if added else None
        now = sha(p)
        then = blob(intro, rel) if intro else None
        rows.append({"artifact": f, "introduced_by": intro,
                     "sha256_now": now, "sha256_at_introduction": then,
                     "identical": then == now,
                     "mtime": datetime.fromtimestamp(
                         p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                     "bytes": p.stat().st_size})
        if then != now:
            fails.append(f"{f} 与引入 commit {intro} 的字节**不一致**"
                         f"（历史 Test 结果被改动）")
    # `data/**` 的真实暴露面：本轮零改动，且不存在 jsonl 写出者
    dirty = [x for x in git("status", "--porcelain", "--ignored",
                             "--", "data").splitlines() if x.strip()]
    out = {"checks": "A9", "rows": rows, "fails": fails,
            "data_worktree_entries": dirty,
            "incident_record": {
                "what": "归属工具动态核验误挑中三个会发真实 VLM 请求的历史批脚本"
                        "（g1_r11 / g2_r12 / g3_r13），重写了 "
                        "evidence/R11G_REAL_VISION.json / R12G_ / R13G_ 三件；"
                        "已用 git checkout -- 复原，本检查逐字节确认等于引入 commit。",
                "detected_by": "git status 出现 M（人工核对时发现）",
                "prevention": "r14_attribute_producer.unsafe_to_run 拒绝式候选"
                              "（网络标记 / 编排器 / 无 --out）+ A6 附带改动检测"
                              " + 本 A9 字节基线",
                "claim_retracted": "会话早先笔记称同一事故还覆写了 "
                                   "data/vlm_responses.jsonl 并从 "
                                   "backup_r14_apikey/ 恢复；本轮实测该文件"
                                   "与其写出者、该备份目录**均不存在**，"
                                   "data/ 亦无本轮改动 ⇒ 该说法**不成立**，"
                                   "已按事实收缩。"},
            "budget": {"http_requests_made": 0, "tokens_used": 0},
            # **顶层**也要有：bundle 的 `dig` 按顶层键取值，只嵌在 budget
            # 里会让"零请求"这条断言拿到 None（本轮实测）
            "http_requests_made": 0, "llm_calls_used": 0,
            "head": git("rev-parse", "HEAD").strip()}
    (EV / "R14G_HISTORICAL_BYTES.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"n": len(rows), "fails": fails,
                      "all_identical": all(r["identical"] for r in rows)},
                     ensure_ascii=False))
    return 1 if fails else 0


def selftest_tripped() -> dict:
    """把某历史件改动一个字节 ⇒ A9 **必须**报错；复原后必须再次通过。
    全程只用 git 取回原字节，零 HTTP。"""
    import tempfile
    f = HIST[0]
    rel = f"evidence/{f}"
    p = EV / f
    orig = p.read_bytes()
    fails = []
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / f
            # 写进一个**临时**位置再挪进来，避免原地改写留下半截状态
            tmp.write_bytes(orig + b"\n")
            p.write_bytes(tmp.read_bytes())
            r = subprocess.run([sys.executable, __file__], cwd=str(ROOT),
                               capture_output=True, text=True)
            if r.returncode == 0:
                fails.append("改动历史件一字节后 A9 仍退出 0 ⇒ 检查不会触发")
            if f not in (r.stdout + r.stderr):
                fails.append(f"A9 触发了但没点名 {f}")
    finally:
        subprocess.run(["git", "-C", str(ROOT), "checkout", "--", rel],
                       capture_output=True)
        if p.read_bytes() != orig:
            fails.append(f"复原后字节与原始不一致：{f}")
    r2 = subprocess.run([sys.executable, __file__], cwd=str(ROOT),
                        capture_output=True, text=True)
    if r2.returncode != 0:
        fails.append(f"复原后 A9 未重新通过：{r2.stdout[-160:]}")
    return {"fails": fails, "artifact": f,
            "restored_identical": p.read_bytes() == orig,
            "http_requests_made": 0, "llm_calls_used": 0,
            "main_artifact_is_complete": (not (
                EV / "R14G_HISTORICAL_BYTES.json").is_file()) or all(
                    k in json.loads((EV / "R14G_HISTORICAL_BYTES.json")
                                    .read_text(encoding="utf-8"))
                    for k in ("checks", "rows", "fails", "incident_record")),
            "budget": {"http_requests_made": 0}}


if "--selftest" in sys.argv:
    st = selftest_tripped()
    # **不得覆盖主核验件**：上一步这里写的是 `R14G_HISTORICAL_BYTES.json`，
    # 把含 `code_version / head / MUST_SEE` 的完整核验结果换成只有 4 个键的
    # 反证摘要 ⇒ A8 断言"这些键必须可见"，而承载它的件**被自己抹掉了**。
    (EV / "R14G_HISTORICAL_BYTES_SELFTEST.json").write_text(
        json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    rc = main()          # 反证做完**必须**把主核验件恢复成完整形态
    full = json.loads((EV / "R14G_HISTORICAL_BYTES.json").read_text(
        encoding="utf-8"))
    must = [k for k in ("checks", "rows", "fails", "incident_record",
                        "data_worktree_entries", "budget")
            if k not in full]
    if must:
        print("主核验件缺键:", must)
    if rc != 0:
        print("反证后主检查未通过")
    print(json.dumps({"selftest_fails": st["fails"],
                      "main_rc": rc,
                      "main_artifact_keys": sorted(full),
                      "missing_keys": must}, ensure_ascii=False))
    sys.exit(1 if (st["fails"] or rc != 0 or must) else 0)

if __name__ == "__main__":
    sys.exit(main())
