"""**发现**：`e2_r14_optionband_replay.py` 把当前件 V8 **改成了**默认件名
`R14E_REAL_PAGE_REPLAY.json`（已被取代的那份）。

这是 R14 目标②"任何重跑用新文件名、不得改历史件"的**真实违规**，上一轮索引
的 A2 只查**文档引用**、A1 只查"有无生产脚本"，两者都看不见这种写路径漂移。

本脚本做**逐件重跑语义比对**（复现路径由**磁盘真值**推出，绝不采信件内
自报的 `out`——那正是会漂移的东西）：
  1. **反推真实写路径**：对每个 `scripts/*.py` 扫字面量，取"该脚本唯一写出
     的那个 `R14*\.json`"；据此得到 `脚本 → 应写件` 的权威映射；
  2. 对每个**有作者脚本**的件，在当前 HEAD 用脚本自报的 `command`
     （没有就按映射拼默认命令）重跑；
  3. 重跑产物与**重跑前**的同一件做语义比对（解析成对象逐键比，不比字节，
     忽略 `_meta` / head / 时间戳一类运行元信息）；
  4. 若重跑改动了**别的**件 → `VIOLATION_write_path_drift`（写路径漂移，
     即"覆盖历史/取代件"）；若语义数字变了 → `semantic_drift`；
     两者都如实列出，不静默恢复了事。

重跑前把被覆盖的件先备份到临时目录并**原样恢复**，避免脚本本身污染仓库。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"
TMP = Path(tempfile.mkdtemp(prefix="r14_sem_"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


ART_RE = re.compile(r"R14[A-Z0-9_\-]*\.json")


def script_writes():
    """磁盘真值：每个脚本**字面量里写出的** R14 件名集合。"""
    out = {}
    for sp in sorted((ROOT / "scripts").glob("*.py")):
        try:
            t = sp.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        out[sp.name] = sorted(set(ART_RE.findall(t)))
    return out


def author_of(artifact: str, writes):
    """作者 = 写出该件名的脚本（排除纯读取方：审计器/索引/血缘等会**引用**
    别人件名，但那些是引用不是写出处——用 `write_text` 邻接判断收紧）。"""
    cands = []
    for name, arts in writes.items():
        if artifact not in arts:
            continue
        t = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        # 收紧：该件名必须出现在一次写出调用的**附近**（同一段代码里）
        for m in re.finditer(re.escape(artifact), t):
            lo, hi = max(0, m.start() - 220), min(len(t), m.end() + 220)
            if "write_text" in t[lo:hi] or "open(" in t[lo:hi]:
                cands.append(name)
                break
    return cands


def meta(name: str):
    p = EV / name
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    m = d.get("_meta") or {}
    return {"script": m.get("script"), "command": m.get("command"),
            "keys": sorted(m.keys())}, d


def semantic_equal(a, b):
    """比较**判定语义**：忽略 generated/head 之类元信息键。"""
    ignore = {"_meta", "head", "generated_at", "git", "code_version",
              "timestamp", "run_id"}
    if isinstance(a, dict) and isinstance(b, dict):
        ka = {k for k in a if k not in ignore}
        kb = {k for k in b if k not in ignore}
        if ka != kb:
            return False, f"键集不同 only_new={sorted(ka-kb)} only_old={sorted(kb-ka)}"
        for k in ka:
            eq, why = semantic_equal(a[k], b[k])
            if not eq:
                return False, f"{k}: {why}"
        return True, ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False, f"长度 {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            eq, why = semantic_equal(x, y)
            if not eq:
                return False, f"[{i}]{why}"
        return True, ""
    return (a == b, "" if a == b else f"{a!r} vs {b!r}")


def main() -> int:
    writes = script_writes()
    rows = []
    for p in sorted(EV.glob("R14*.json")):
        got = meta(p.name)
        if not got:
            continue
        m, doc = got
        writers = author_of(p.name, writes)
        cmd = m.get("command")
        if not cmd:
            cmd = (f'"{sys.executable}" -X utf8 scripts/{writers[0]}'
                   if writers else None)
        if not cmd:
            rows.append({"artifact": p.name, "status": "no_producer_no_command",
                         "meta_keys": m["keys"]})
            continue
        # 先备份可能被重跑覆盖的**所有** R14 件（重跑只写它自己那一个，但
        # 写路径已知会漂移，故整体备份）
        bak = TMP / p.name
        bak.mkdir(parents=True, exist_ok=True)
        for q in EV.glob("R14*.json"):
            shutil.copy2(q, bak / q.name)
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
               "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", env=env)
        wrote = []
        for q in sorted(EV.glob("R14*.json")):
            if sha(q) != sha(bak / q.name):
                wrote.append(q.name)
        # 恢复原样（本脚本只诊断，不落地）
        for q in (bak / ".").glob("*.json"):
            shutil.copy2(q, EV / q.name)
        row = {"artifact": p.name, "script": m["script"],
               "command_used": cmd, "author_scripts_by_disk": writers,
               "exit": r.returncode, "files_changed_by_rerun": wrote}
        unexpected = [w for w in wrote if w != p.name]
        if unexpected:
            row["status"] = "VIOLATION_write_path_drift"
            row["detail"] = (f"重跑 {m['script']} 声明写 "
                             f"{m['target']}，实际改动了 {unexpected}")
        elif wrote == [p.name]:
            same, why = semantic_equal(doc, json.loads(
                (EV / p.name).read_text(encoding="utf-8")))
            row["status"] = "semantic_drift" if not same else "reproduced"
            if not same:
                row["detail"] = why[:300]
        elif r.returncode != 0:
            row["status"] = "rerun_failed"
            row["detail"] = (r.stderr or "")[-200:]
        else:
            row["status"] = "rerun_wrote_nothing"
        rows.append(row)

    viol = [x for x in rows if x.get("status") in
            ("VIOLATION_write_path_drift", "semantic_drift", "rerun_failed")]
    out = {"n_checked": len(rows), "violations": viol,
           "rows": rows,
           "note": "本脚本只做诊断并**恢复原样**，不落地重跑结果；"
                   "恢复后仓库必须与运行前一致（由 git status 另证）",
           "budget": {"http_requests_made": 0}}
    (EV / "R14E_RERUN_SEMANTICS.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"n_checked": len(rows),
                      "violations": [{k: v for k, v in x.items()
                                      if k in ("artifact", "status", "detail",
                                               "files_changed_by_rerun")}
                                     for x in viol]},
                     ensure_ascii=False, indent=1))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main())
