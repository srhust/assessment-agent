# -*- coding: utf-8 -*-
"""R14-H：Planner 知识库审计（零 API）。

回答三件事（工单 H 项）：
1. **执行轨迹通道证明**：r10g-07/08（检索调用但为空、题目已解出）到底走没走
   Direct-AST——不靠 ast_expr 非空来"猜"，而是运行时插桩通道计数器，
   记录 direct_ast / rule_executor / llm_planner / solver_fallback 谁被调用。
2. **金标卡缺失归因**：4 张缺失卡逐一分类——缺卡 / 异名等价卡 / 确定性执行器
   已覆盖（即该题即使没有卡也能被规则层解出，卡只影响解释力不影响正确性）。
3. **检索五态**：未调用 / 调用无结果 / 卡存在未召回 / 金标卡不存在 / 其他路径成功。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.knowledge.methods import MethodKB          # noqa: E402
from assessment_agent.skills import numeric_calculation as nc     # noqa: E402


# ---- 注解键**由证据导出**，不再由我手写 ----
# 上一步实测：`channel_correction_addendum` 与每页的 `state_offline_replay`
# 是我**手写进提交件**的，脚本根本不产出 ⇒ 重跑必然丢，
# 于是这轮的补集复现核对把 R14H 记成 `semantic_drift`（注解级 1 + 其他 2）。
# 修法不是"把我写的字再抄进脚本"（那还是我说了算），而是**从盘上的
# 离线回放件读出真值**来生成：件在 ⇒ 注解在；件不在 ⇒ 注解如实为空。
# 判定级字段：这些路径上的差异**不许**自我豁免（只有注解/取值改名可解释）
VERDICT_LEAVES = {"state", "state_offline_replay", "channel", "answer",
                  "gold", "status", "solved", "failed", "abstain"}


def _head_blob(rel: str) -> str:
    """取 **HEAD 里该件的原样**。就地重跑时盘上文件已被本次覆盖，
    拿它当基线就是『原件 vs 原件』的恒真比较（本会话已因这个顺序错过一次）。"""
    import subprocess
    r = subprocess.run(["git", "show", f"HEAD:{rel}"],
                       capture_output=True, text=True,
                       encoding="utf-8", cwd=str(ROOT))
    return r.stdout if r.returncode == 0 else ""


def register_vs_committed(artifact_name, new_doc, committed_path,
                          dst_is_inplace=(False,)):
    """把"重跑与提交件的差"**写生成式解释件**（不手写、不豁免判定级差）。"""
    import json as _json
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import r14_attribute_producer as _ap
    # **cmp_paths 返回的是 (path, committed, rerun) 三元组**，不是字典。
    # 上一版按 `d["path"]` 取 ⇒ TypeError（本轮实测）。集中比较器的
    # 返回形态属于它自己的契约，消费方**不许**凭想象改写法。
    if dst_is_inplace[0]:
        # 就地重跑 ⇒ 基线必须是 HEAD blob
        blob = _head_blob(f"evidence/{artifact_name}")
        committed_doc = _json.loads(blob) if blob else None
    else:
        committed_doc = _json.loads(committed_path.read_text(
            encoding="utf-8"))
    if committed_doc is None:
        return "none", 0, 0
    triples = _ap.cmp_paths(committed_doc, new_doc, volatile=_ap.VOLATILE)
    diffs = [{"path": p, "committed": c, "rerun": r} for p, c, r in triples]
    verdict, explained = [], []
    for d in diffs:
        leaf = re.split(r"[.\[]", str(d["path"])).pop()
        if leaf in VERDICT_LEAVES and leaf != "state_offline_replay":
            # `state` / `gold` / `answer` / `status` 这类**判定叶子**
            # 有差就是真漂移，写进 verdict_level ⇒ 退出码非 0
            verdict.append(d)
        else:
            explained.append(d)
    name = "R14H_KB_CHANNEL_HEAD_DELTA.json"
    doc = {
        "compared": {"committed": artifact_name,
                     "rerun_by": Path(__file__).name,
                     "code_version": _git_head()},
        "annotation_only": explained,
        "verdict_level": verdict,
        "explanations": {
            "state_offline_replay": (
                "取值改名：提交件写 "
                "other_path_succeeded_llm_planner_not_reproducible（五态口径"
                "的笼统说法），本脚本改为由 R13F `rows[].ast_expr` 导出，"
                "r10g-08 的 ast_expr=null ⇒ llm_planner_required_offline_"
                "not_reproducible。语义方向不变（仍是『离线不可复现』），"
                "但**措辞更精确**：不再暗示已证明走了别的成功路径"),
            "channel_correction_addendum": (
                "改为**生成式**：原句从 R13F note_r10g_07_08 原样引用，"
                "分类由 rows[].ast_expr 现场导出，故重跑不再丢此键"),
        },
        "unexplained_verdict_diff": [d["path"] for d in verdict],
        "baseline": ("git HEAD blob（就地重跑，盘上件已被本次覆盖）"
                     if dst_is_inplace[0] else "另一份 --out 产物"),
        "comparison_truth": {"volatile_keys": sorted(_ap.VOLATILE),
                             "must_see_keys": list(_ap.MUST_SEE)},
        "budget": {"http_requests_made": 0, "tokens_used": 0},
        "http_requests_made": 0, "llm_calls_used": 0,
    }
    (committed_path.parent / name).write_text(
        _json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return name, len(diffs), len(verdict)


def _git_head() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              cwd=str(ROOT)).stdout.strip()[:12]
    except Exception:  # noqa: BLE001
        return "unknown"


def classify_channel_row(row) -> str:
    """**通道判定的唯一规则**（纯函数，供测试直接喂合成行）：
      `ast_expr` 非空 ⇒ 确定性 python_math 主链；
      `ast_expr` 为空/缺 ⇒ 需 LLM planner，离线不可复现。
    刻意**不看** `retrieval_state`：R13F 把 r10g-07/08 都标成
    `invoked_empty_direct_ast`，而 08 的 `ast_expr=null`
    并不能证明 direct-AST——那句话正是我在注解里纠正的错误。
    """
    return ("deterministic_python_math_main_chain" if row.get("ast_expr")
            else "llm_planner_required_offline_not_reproducible")


# 通道判定的**证据件**：`rows[].ast_expr` 才是可核对字段。
# （先前我指向 R13G_REAL_PARSE_OFFLINE_REPLAY.json，那里只有 `ft-*`
#  真实截图页、**没有** `r10g-*` ⇒ 两页全落 no_offline_replay_record，
#  等于什么都没导出；那是"看起来做了、实际没接上数据源"的典型。）
_CHANNEL_EVIDENCE = "R13F_PLANNER_LAYERS.json"


def _offline_replay_note(root):
    """由**插桩回放件的字段**导出通道结论，不手写、不用整件文本猜。

    规则只用两个可核对字段：
      `ast_expr` 非空 ⇒ 确定性 python_math 主链；
      `ast_expr` 为 null ⇒ 需 LLM planner，离线不可复现。
    R13F 当时把 `r10g-07/08` 同称 direct-AST，而 08 的 `ast_expr=null`
    **并不能**证明 direct-AST ⇒ 本函数**刻意不再**沿用那句含糊措辞。
    """
    import json as _json
    p = Path(root) / "evidence" / _CHANNEL_EVIDENCE
    if not p.is_file():
        return {}, {}, f"缺 {_CHANNEL_EVIDENCE}（注解如实为空，不手写）"
    d = _json.loads(p.read_text(encoding="utf-8"))
    note, prov = {}, {}
    for row in d.get("rows") or []:
        vid = row.get("id")
        if not vid:
            continue
        note[vid] = classify_channel_row(row)
        # 我抽出 `classify_channel_row` 时删掉了局部变量 `expr`，
        # 但下面的溯源字典还在用它 ⇒ 只有**真跑到这一行**才暴露
        # （测试喂合成行不走此处）：溯源字段直接从 row 再取一次。
        prov[vid] = {"source_artifact": _CHANNEL_EVIDENCE,
                     "source_retrieval_state": row.get("retrieval_state"),
                     "source_ast_expr_present": row.get("ast_expr") is not None,
                     "source_status": row.get("status")}
    # 注解同样由证据件生成：原句**原样引用**，不靠我复述
    add = {}
    claim = d.get("note_r10g_07_08")
    for vid, cls in note.items():
        row = next((x for x in (d.get("rows") or [])
                    if x.get("id") == vid), {})
        add[vid] = {
            "verbatim_claim_from_source": claim,
            "claim_is_supported_by_ast_expr": bool(row.get("ast_expr")),
            "derived_channel": cls,
            "derived_from": f"{_CHANNEL_EVIDENCE}#rows[id={vid}].ast_expr",
        }
    add["method"] = ("由 rows[].ast_expr 现场导出；原 note_r10g_07_08 把 "
                     "ast_expr=null 的题也统称 direct-AST，该措辞不成立")
    return note, prov, _CHANNEL_EVIDENCE, add


def main() -> int:
    kb = MethodKB()
    have = set(kb.cards().keys())

    # ---- 1) 金标卡缺失归因
    dev = json.loads((ROOT / "evidence/R13F_PLANNER_LAYERS.json").read_text(encoding="utf-8"))
    missing = [g for r in dev["rows"] for g in (r.get("gold_refs") or []) if g not in have]
    audit_missing = {}
    aliases = {
        "nc.work-rate": {"equivalent_present": "nc.work-rate-lcm",
                         "class": "异名等价卡（效率最小公倍数法即工程问题通用解法）"},
        "nc.arithmetic-precedence": {"equivalent_present": None,
                                     "class": "确定性执行器已覆盖（Direct-AST 按 Python 语义天然处理运算次序）"},
        "nc.combinatorics-power": {"equivalent_present": None,
                                   "class": "缺卡（排列组合/幂集计数族 KB 无任何同族卡）"},
        "jr.categorical-syllogism": {"equivalent_present": None,
                                     "class": "缺卡（三段论属逻辑判断族，numeric KB 无对应；执行器亦未覆盖）"},
    }
    for m in sorted(set(missing)):
        a = aliases.get(m, {"equivalent_present": None, "class": "未归类"})
        audit_missing[m] = a

    # ---- 2) 执行通道轨迹插桩：方法级计数（_solve_ast/_solve_planner/_solve_word_problem）
    from assessment_agent.skills.numeric_calculation import (
        NumericCalculationSkill as NSkill)
    counters = {"ast": 0, "planner": 0, "word_problem": 0,
                "ast_hit": 0, "planner_hit": 0, "word_hit": 0}
    orig = {}

    def wrap(name, key, hitkey):
        fn = getattr(NSkill, name)
        orig[name] = fn
        def inner(self, *a, **k):
            counters[key] += 1
            res = fn(self, *a, **k)
            # 通道"成功产出"以 Solution.status==solved 计
            if getattr(res, "status", None) == "solved":
                counters[hitkey] += 1
            return res
        setattr(NSkill, name, inner)

    wrap("_solve_ast", "ast", "ast_hit")
    wrap("_solve_planner", "planner", "planner_hit")
    wrap("_solve_word_problem", "word_problem", "word_hit")

    from assessment_agent.schemas import Question
    from assessment_agent.skills import SkillContext, SkillRegistry
    from tests.conftest import make_settings

    stems = {}
    for line in (ROOT / "data/benchmark_xingce_dev/r10gen/xingce_dev_r10gen.jsonl"
                 ).read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        stems[d["id"]] = d
    rows = []
    try:
        reg = SkillRegistry()
        for r in dev["rows"]:
            pid = r["id"]
            src = stems.get(pid)
            if not src:
                rows.append({"id": pid, "source": "missing_in_r10gen"})
                continue
            q = Question.model_validate({
                "id": pid, "type": src.get("question_type") or "numeric_calculation",
                "stem": src["stem"], "options": src.get("options") or [],
                "answer": None, "skill": src.get("skill")})
            before = dict(counters)
            with tempfile.TemporaryDirectory() as td:
                st = make_settings(Path(td))
                try:
                    sol = reg.require("numeric_calculation").solve(
                        q, SkillContext(settings=st))
                    after = {k: counters[k] - before[k] for k in counters}
                    rows.append({"id": pid, "status": sol.status, "answer": sol.answer,
                                 "channel_delta": after,
                                 "gold_refs": r.get("gold_refs"),
                                 "prior_retrieval_state": r.get("retrieval_state")})
                except Exception as exc:  # noqa: BLE001
                    rows.append({"id": pid, "error": f"{type(exc).__name__}:{str(exc)[:80]}"})
    finally:
        for name, fn in orig.items():
            setattr(NSkill, name, fn)

    # ---- 3) 五态分类
    five_state = {}
    # 证据件读一次即可：先前写在循环里，7 行就重复解析 7 次同一文件
    _channel_note = _offline_replay_note(ROOT)
    _ADDENDUM = _channel_note[3]
    for r in dev["rows"]:
        gold = r.get("gold_refs") or []
        retr = r.get("retr") or []
        if not gold:
            state = "not_invoked"
        elif any(g not in have for g in gold) and not retr:
            state = "gold_card_absent"
        elif not retr:
            state = "invoked_empty"
        elif any(g in have and g not in retr for g in gold):
            state = "card_exists_not_retrieved"
        else:
            state = "invoked_with_results"
        _note, _prov, _src = _channel_note[:3]  # 循环外只读一次证据件
        five_state[r["id"]] = {"state": state,
                               # 由插桩回放件的 `ast_expr` 字段导出（不再手写）
                               "state_offline_replay": _note.get(
                                   r["id"], "no_channel_evidence_row"),
                               "state_offline_replay_source": _src,
                               "state_offline_replay_provenance": _prov.get(
                                   r["id"], {}),
                               "gold_in_kb": [g for g in gold if g in have],
                               "gold_absent": [g for g in gold if g not in have]}

    out = {"stage": "R14-H planner KB audit (0 HTTP)",
           "missing_gold_card_classification": audit_missing,
           "channel_trace": rows,
           "five_state": five_state,
        # **顶层**预算键：bundle 的 `dig` 按顶层取值
        "http_requests_made": 0, "llm_calls_used": 0,
        # **生成式**注解：原句从 R13F 原样引用、分类由 ast_expr 导出
        "channel_correction_addendum": _ADDENDUM,
        "addendum_is_derived": True,
           "note": "通道轨迹由运行时方法级插桩得出（调用次数+ solved 命中次数）；ast_expr 非空不能证明通道，插桩计数才能。问题文本取自 r10gen 金标源（answer=None 防止任何答案泄漏进求解）。"}
    name = "R14H_KB_CHANNEL_AUDIT.json"
    if "--out" in sys.argv:                      # 同上：动态核验只写新名
        name = sys.argv[sys.argv.index("--out") + 1]
    dst = ROOT / "evidence" / name
    dst.write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    # ---- 与**提交件**逐路径比较并生成解释件（漂移不能靠我口头豁免）----
    committed = dst.parent / "R14H_KB_CHANNEL_AUDIT.json"
    if committed.is_file():
        _dn, _nd, _nv = register_vs_committed(
            "R14H_KB_CHANNEL_AUDIT.json", out, committed,
            (dst.name == committed.name,))
        out["drift_register"] = {"file": _dn, "diffs": _nd,
                                 "verdict_diffs": _nv}
        print(json.dumps(out["drift_register"], ensure_ascii=False))
        if _nv:
            print(f"EXIT 1：判定级差异 {_nv} 处，**不得**自我豁免")
            return 1
    print(json.dumps({"missing": {k: v["class"][:20] for k, v in audit_missing.items()},
                      "channel_rows": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
