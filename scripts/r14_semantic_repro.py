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


VOLATILE = {"_meta", "head", "generated_at", "git", "code_version",
            "timestamp", "run_id", "sha256", "artifact_sha256", "elapsed_s",
            "duration_s", "wall_clock"}
# **逐点收集**差异而不是遇到第一处就返回：上轮 c2 的真实情况是
# "缺一个手写 addendum" **并且** "两处 label 判定变了"；早退让报告只说前者，
# 把更重的那条（判定漂移）盖住了。
MAX_DIFFS = 40


def _brief(v):
    t = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return t[:90]


def diff_paths(a, b, path="$", out=None):
    """返回 [(路径, 提交值, 重跑值)]；`VOLATILE` 键（时间/自指哈希）不参与比较。"""
    out = [] if out is None else out
    if len(out) >= MAX_DIFFS:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        ka = {k for k in a if k not in VOLATILE}
        kb = {k for k in b if k not in VOLATILE}
        for k in sorted(ka - kb):
            out.append((f"{path}.{k}", _brief(a[k]), "<重跑未产出>"))
        for k in sorted(kb - ka):
            out.append((f"{path}.{k}", "<提交件没有>", _brief(b[k])))
        for k in sorted(ka & kb):
            diff_paths(a[k], b[k], f"{path}.{k}", out)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((f"{path}.len", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            diff_paths(x, y, f"{path}[{i}]", out)
        return out
    if a != b:
        out.append((path, _brief(a), _brief(b)))
    return out


# 判定字段：这些**变了**才叫"结论漂移"；只有手写注解/addendum 缺失是"记账差异"。
VERDICT_LEAVES = {"solver_status", "final_answer", "released", "release",
                  "verdict", "label", "margin", "answer", "correct",
                  "gold_match", "state", "status"}
ANNOTATION_KEYS = {"causal_conclusion", "channel_correction_addendum",
                   "addendum", "notes", "note", "erratum"}


def classify_diffs(diffs):
    """把逐点差异分成**结论级**与**注解级**，别把两者混成一个数字。"""
    verdict, annotation, other = [], [], []
    for path, old, new in diffs:
        leaf = path.rsplit(".", 1)[-1].rsplit("]", 1)[-1]
        top = path.lstrip("$").lstrip(".").split(".")[0].split("[")[0]
        if leaf in ANNOTATION_KEYS or top in ANNOTATION_KEYS:
            annotation.append((path, old, new))
        elif leaf in VERDICT_LEAVES:
            verdict.append((path, old, new))
        else:
            other.append((path, old, new))
    return {"verdict_level": len(verdict), "annotation_only": len(annotation),
            "other": len(other),
            "verdict_paths": [p for p, _, _ in verdict][:20],
            "verdict_examples": [{"path": p, "committed": o, "rerun": n}
                                 for p, o, n in verdict][:12]}


def semantic_equal(a, b):
    out = diff_paths(a, b)
    return (not out), out



# ---- 安全闸门：**复用**归属工具（同一份规则，两处各写必漂移） ----
sys.path.insert(0, str(Path(__file__).resolve().parent))
import r14_attribute_producer as AP  # noqa: E402


def bundle_artifacts():
    p = EV / "R14_VERIFICATION_BUNDLE.json"
    if not p.is_file():
        return set()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        got = {x["artifact"] for x in d.get("steps", []) if x.get("artifact")}
        # bundle 里以 `_RERUN` / `_CHECK` 新名重跑的件，其**母件**也算已覆盖：
        # V8 的权威复现核对是 bundle 步（用真作者 e2d 重跑到 V8_RERUN 并比
        # **归一化判定字段**），本工具不该再用"别的脚本的默认名"重复判一次。
        for a in list(got):
            stem = a[:-5] if a.endswith(".json") else a
            for suf in ("_RERUN", "_CHECK"):
                if stem.endswith(suf):
                    got.add(stem[:-len(suf)] + ".json")
        return got
    except Exception:  # noqa: BLE001
        return set()


RUN_ALL = "--all" in sys.argv
_BUNDLE_ONLY = set()   # 在 main 里填（避免导入期读盘顺序问题）


def _index_statuses():
    """件地位真值：current / superseded / historical / erratum_kept / self。"""
    p = EV / "R14_INDEX.json"
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {r["artifact"]: r["status"] for r in d.get("artifacts", [])}
    except Exception:  # noqa: BLE001
        return {}


_NOT_CURRENT = {}


def main() -> int:
    global _BUNDLE_ONLY, _NOT_CURRENT
    _NOT_CURRENT = {k: v for k, v in _index_statuses().items()
                    if v != "current"}
    _BUNDLE_ONLY = bundle_artifacts()
    writes = script_writes()
    rows = []
    for p in sorted(EV.glob("R14*.json")):
        got = meta(p.name)
        if not got:
            continue
        if p.name in _NOT_CURRENT:
            rows.append({"artifact": p.name, "status": "skipped_not_current",
                         "why": "写出者已前进到现行件名；对历史/被取代件重跑"
                                "核对既无意义又接近\u201c改历史 Test 结果\u201d"
                                "（目标②禁止）。地位真值取自 R14_INDEX.json",
                         "index_status": _NOT_CURRENT[p.name]})
            continue
        m, doc = got
        # 作者判定 = **声明写出**（与归属工具同源），不再用"件名邻接
        # write_text"的土法——那个判据太窄：`dst = EV / name` 后就找不到了。
        fam = AP._fam(p.name)
        exact, familial = [], []
        for n in writes.keys():
            decl = AP.script_declares_write(n)
            if p.name in decl:
                exact.append(n)          # **精确**写出该件名
            elif fam in {AP._fam(x) for x in decl}:
                familial.append(n)        # 同族（靠 --out 才落到现行名）
        declared = exact + familial
        safe, refused = [], []
        for n in declared:
            why = AP.unsafe_to_run(n)
            (refused if why else safe).append((n, why or ""))
        cmd_script = safe[0][0] if safe else None
        # **先**问"bundle 是否已核过这件"（比**件名**，上轮误比脚本名 → 永不命中，
        # 于是把 bundle 已重跑过的件又跑一遍）
        if p.name in _BUNDLE_ONLY and not RUN_ALL:
            rows.append({"artifact": p.name, "status": "covered_by_bundle",
                         "declared_writers": declared,
                         "note": "一键复现 bundle 已重跑该件并核对件内不变量，"
                                 "本处只跑**补集**（不重复计时）"})
            continue
        if not cmd_script:
            rows.append({"artifact": p.name,
                         "status": ("refused_unsafe_candidates" if refused
                                    else "no_producer_no_command"),
                         "declared_writers": declared,
                         "note": "唯一声明写出者被安全闸门拒跑（多为不支持 "
                                 "--out：重跑会在固定历史名下落盘），且不在 "
                                 "bundle 清单内 ⇒ 本工具**无法**动态核验，"
                                 "如实登记而不是猜一个结论",
                         "refused": [{"script": n, "reason": w}
                                     for n, w in refused]})
            continue
        # 先备份可能被重跑覆盖的**所有** R14 件（重跑只写它自己那一个，但
        # 写路径已知会漂移，故整体备份）
        bak = TMP / p.name
        bak.mkdir(parents=True, exist_ok=True)
        # 备份/复原范围**必须**含 `.md`：只备份 `.json` 时，重跑索引会把
        # `R14_INDEX.md` 改掉却无从复原（本轮实测发生，已 git checkout 还原）。
        _mts = {q.name: q.stat().st_mtime_ns
                for q in list(EV.glob("R14*.json")) + list(EV.glob("R14*.md"))}
        for q in list(EV.glob("R14*.json")) + list(EV.glob("R14*.md")):
            shutil.copy2(q, bak / q.name)
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
               "PYTHONIOENCODING": "utf-8", "ASSESSMENT_OFFLINE": "1"}
        snap_b = AP._snapshot()
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(ROOT / "scripts" / cmd_script)],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", env=env)
        collateral = AP._collateral(snap_b)   # evidence/ **和** data/
        # 本脚本**只诊断不落地**：evidence/ 由下面统一 restore 复原；
        # evidence/ **之外**（如 data/）的附带改动这里**立刻删掉新增文件**，
        # 已存在的文件由 snap_b 里的哈希对照**如实报告**（不假装修好）。
        restored_outside = []
        for rel in collateral:
            q = ROOT / rel
            if not rel.startswith("evidence/") and q.is_file():
                q.unlink()
                restored_outside.append(rel)
        wrote = []
        touched = []
        for q in sorted(list(EV.glob("R14*.json")) + list(EV.glob("R14*.md"))):
            if sha(q) != sha(bak / q.name):
                wrote.append(q.name)
            elif _mts.get(q.name) != q.stat().st_mtime_ns:
                touched.append(q.name)   # 写了但字节完全相同 = 复现到位
        # **先取重跑产物**再恢复原样。上轮把恢复放在比较**之前**，
        # 于是 `semantic_equal(doc, 现件)` 比的是"原件 vs 原件"——
        # **一个恒真的比较**，把 c2/h1 真实的判定漂移（label 变了、
        # 手写附 adds 不在脚本产出里）全判成 reproduced。这是本轮抓到的
        # 第二个"检查其实没在检查"。
        rerun_doc = None
        if (EV / p.name).is_file():
            try:
                rerun_doc = json.loads(
                    (EV / p.name).read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                rerun_doc = None
        # 恢复原样（本脚本只诊断，不落地）
        for q in list((bak / ".").glob("*.json")) + list((bak / ".").glob("*.md")):
            shutil.copy2(q, EV / q.name)
        row = {"artifact": p.name, "script": cmd_script,
               "declared_writers": declared,
               "refused_unsafe": [{"script": n, "reason": w} for n, w in refused],
               "worktree_collateral": collateral,
               "collateral_removed": restored_outside,
               "safety": ("OK_ZERO_REQUESTS"
                          if not [c for c in collateral
                                  if not c.startswith("evidence/")]
                          else "SAFETY_VIOLATION_worktree_collateral"),
               "offline": True,
               "exit": r.returncode, "files_changed_by_rerun": wrote}
        # 一个脚本合法地会写**多件**（索引写 json+md、审计写 json+md）。
        # 只有写出**别人的历史件名**才算写路径漂移。
        own = {p.name} | {x for x in wrote
                          if AP._fam(x) == AP._fam(p.name)
                          or x in set(AP.script_declares_write(cmd_script))}
        unexpected = [w for w in wrote if w not in own]
        row["own_declared_outputs_touched"] = sorted(set(wrote) & own)
        if unexpected:
            row["status"] = "VIOLATION_write_path_drift"
            row["detail"] = (f"重跑 scripts/{cmd_script} 除本件外还改动了"
                             f"非自指件 {unexpected}")
        elif r.returncode != 0:
            row["status"] = "rerun_failed"
        elif wrote == [p.name]:
            same, why = semantic_equal(doc, rerun_doc)
            row["status"] = "semantic_drift" if not same else "reproduced"
            if not same:
                row["diff_count"] = len(why)
                row["diff_classes"] = classify_diffs(why)
                row["diffs"] = [{"path": p, "committed": o, "rerun": n}
                                for p, o, n in why[:MAX_DIFFS]]
                row["detail"] = (f"{len(why)} 处差异："
                                 f"结论级 {row['diff_classes']['verdict_level']}"
                                 f" / 注解级 {row['diff_classes']['annotation_only']}"
                                 f" / 其他 {row['diff_classes']['other']}")
        elif not wrote and p.name in touched:
            # 重跑产物与现件**逐字节相同** = 复现到位（上轮误报"没写出"）
            same2, w2 = semantic_equal(doc, rerun_doc)
            row["status"] = "reproduced_identical" if same2 else "semantic_drift"
            if not same2:
                row["diff_count"] = len(w2)
                row["diff_classes"] = classify_diffs(w2)
                row["diffs"] = [{"path": p, "committed": o, "rerun": n}
                                for p, o, n in w2[:MAX_DIFFS]]
            row["rerun_byte_identical"] = True
        else:
            row["status"] = "no_write_detected"
            row["detail"] = (r.stderr or "")[-200:]
        rows.append(row)

    viol = [x for x in rows if x.get("status") in
            ("VIOLATION_write_path_drift", "rerun_failed")
            or (x.get("status") == "semantic_drift"
                and (x.get("diff_classes") or {}).get("verdict_level"))]
    annotated = [x for x in rows if x.get("status") == "semantic_drift"
                 and not (x.get("diff_classes") or {}).get("verdict_level")]
    out = {"n_checked": len(rows), "violations": viol,
           "annotation_only_drift": [
               {"artifact": x["artifact"],
                "missing_from_script": x["diff_classes"]["annotation_only"],
                "paths": [d["path"] for d in x["diffs"]][:6]}
               for x in annotated],
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
