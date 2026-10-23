"""Stage G 批的**唯一**发起入口：闸门 + manifest 校验 + 原子预算 + 如实入账。

零审批即零请求：`--approve` 是**人工批准位**（人工评审
evidence/R14_PROGRESS_STOPGATE.md 之后回填），未批准时本脚本在任何
网络调用之前就退出并写明原因，不发一次请求。

设计约束（全部来自工单，不做本地发明）：
  · 页数 = manifest 的 5 件（mirror-04 → rot90-01 → trans-07 → overlay-12 →
    余量新排版 1 件），顺序固定；
  · 请求上限 6（含重试）：`OpenAICompatibleClient(max_requests=6)`——闸在
    socket **之前**，重试同扣额，超限**拒绝发起**而非中途截断；
  · token 硬顶 100000：`max_tokens=100000`，达顶后下一次请求被拒、
    已在途响应**如实入账**（不"最后一次免单"）；
  · 题面选项 `text=None`（图选项），解析/Verifier 产物 `save=True` 落盘；
  · 模型 / Prompt / 代码版本 / 原图 SHA 取自 manifest 并在发前复算校验；
  · 错误接受即停：任一页 release_status 给 wrong_accept → 立即 abort，
    剩余页不再发（预算不退还，如实计入）；
  · 全部异常（HTTP 错 / 超时 / 预算拒发 / 解析失败）计入端到端分母。
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.artifacts import RunArtifacts               # noqa: E402
from assessment_agent.config import Settings                       # noqa: E402
from assessment_agent.llm import (LLMRequestBudgetExhausted,   # noqa: E402
                                   OpenAICompatibleClient)
from assessment_agent.schemas import Question                      # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry    # noqa: E402
from assessment_agent.tools.release_scoring import release_status  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask         # noqa: E402
from assessment_agent.verifier import Verifier                     # noqa: E402

MAX_REQUESTS = 6
MAX_TOKENS = 100000


def die(reason, extra=None):
    print(json.dumps({"stage": "G", "aborted_before_any_request": True,
                      "reason": reason, **(extra or {})},
                     ensure_ascii=False, indent=1), file=sys.stderr)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve", default="",
                    help="人工批准凭据（评审 R14_PROGRESS_STOPGATE.md 后回填）；"
                         "留空 = 不批准 = 零请求退出")
    ap.add_argument("--out", default="R14G_RESULT.json")
    a = ap.parse_args()

    if not a.approve.strip():
        return die("闸门未开：需要 --approve 传入人工批准凭据。"
                   "本次运行**未发起任何 HTTP 请求**。")

    plan_p = ROOT / "evidence" / "R14G_PLAN.json"
    plan = json.loads(plan_p.read_text(encoding="utf-8"))

    # ---- 发前校验：预算、模型锁、版本、原图 SHA（任一不符即零请求退出）
    if plan["budget"]["max_requests_including_retry"] != MAX_REQUESTS \
            or plan["budget"]["max_tokens"] != MAX_TOKENS:
        return die("manifest 预算与工单不符", {"budget": plan["budget"]})
    if plan.get("model_lock", {}).get("status") != "CONFIRMED_BY_HUMAN":
        return die("模型名未经人工确认锁定（model_lock.status="
                   f"{plan.get('model_lock', {}).get('status')}）；"
                   "禁止脚本自选模型。",
                   {"model_lock": plan.get("model_lock")})
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    pp = ROOT / "src" / "assessment_agent" / "prompts.py"
    prompt_sha = hashlib.sha256(pp.read_bytes()).hexdigest()
    if plan["fixed"]["code_version"] != head:
        return die("代码版本与 manifest 不符",
                   {"manifest": plan["fixed"]["code_version"], "head": head})
    if plan["fixed"].get("prompt_sha256") != prompt_sha:
        return die("Prompt 源与 manifest 不符",
                   {"manifest": plan["fixed"].get("prompt_sha256"),
                    "now": prompt_sha})
    mism = [p["pid"] for p in plan["planned_calls"]
            if not Path(p["image_path"]).is_file()
            or hashlib.sha256(Path(p["image_path"]).read_bytes()).hexdigest()
            != p["original_sha256"]]
    if mism:
        return die("原图 SHA 校验失败", {"pages": mism})

    st = Settings.from_env(env={"ASSESSMENT_OFFLINE": "0"})
    if not st.llm.configured:
        return die("LLM 未配置（无 key / 无 vision_model），不降级、不发请求。")

    client = OpenAICompatibleClient(st.llm, max_requests=MAX_REQUESTS,
                                    max_tokens=MAX_TOKENS)
    reg = SkillRegistry()
    fr = reg.require("figure_reasoning")
    rows, stop = [], False
    for call in plan["planned_calls"]:
        if stop:
            rows.append({"pid": call["pid"], "status": "skipped_after_abort",
                        "counted_in_denominator": True})
            continue
        d = ROOT / "runs" / f"r13g-{call['pid']}"
        q = Question.model_validate(json.loads(
            (d / "question.json").read_text(encoding="utf-8")))
        q = q.model_copy(update={
            "image_path": call["image_path"],
            # 题面选项一律 text=None（图选项）
            "options": [o.model_copy(update={"text": None})
                        for o in (q.options or [])]})
        row = {"pid": call["pid"], "seq": call["seq"],
               "original_sha256": call["original_sha256"],
               "declared_options": len(q.options or []),
               "option_text_none": all(o.text is None for o in (q.options or [])),
               "status": None, "error": None}
        try:
            ctx = SkillContext(settings=st,
                               artifacts=RunArtifacts(st.runs_dir,
                                                      run_id=f"r14g-{call['pid']}"))
            sol = fr.solve(q, ctx)
            vr = None
            if sol.status == "solved":
                vr = Verifier(st, registry=reg).verify(q, sol)
            rs = release_status(vr.action if vr else None,
                                vr.final_answer if vr else None, None)
            m = load_mask(call["image_path"])
            row.update({"solver_status": sol.status,
                        "answer": sol.answer,
                        "verify_action": vr.action if vr else None,
                        "final_answer": vr.final_answer if vr else None,
                        "page_ink": int(m.sum()),
                        "mask_hw": [int(m.shape[0]), int(m.shape[1])],
                        "status": "done",
                        "artifacts_saved": bool(list(
                            (ctx.artifacts.dir / "regions").glob("*.png"))),
                        "wrong_accept": rs.get("wrong_accept")})
        except LLMRequestBudgetExhausted as exc:
            row.update({"status": "budget_exhausted", "counted_in_denominator": True,
                        "error": str(exc)[:200]})
            stop = True                       # 预算触顶：停止后续发起
        except Exception as exc:  # noqa: BLE001
            row.update({"status": "error", "counted_in_denominator": True,
                        "error": f"{type(exc).__name__}: {exc}"[:200]})
        rows.append(row)
        if row.get("wrong_accept"):
            stop = True                       # 错误接受即停
            row["abort_reason"] = "wrong_accept"

    audit = client.audit_snapshot()
    out = {"stage": "G", "executed": True, "approval": a.approve,
            "planned": len(plan["planned_calls"]),
            "rows": rows,
            "requests_sent": audit.get("requests_sent"),
            "total_tokens": audit.get("total_tokens"),
            "budget": plan["budget"], "fixed": plan["fixed"],
            "model_lock": plan.get("model_lock"),
            "denominator_rule": "分母 = 计划件数（含 error/budget_exhausted/"
                                "skipped_after_abort 件），不得只除成功件",
            "abort": bool(stop),
            "llm_calls_used": audit.get("requests_sent"),
            "http_requests_made": audit.get("requests_sent")}
    dst = ROOT / "evidence" / a.out
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("planned", "requests_sent",
                                          "total_tokens", "abort", "denominator_rule")},
                     ensure_ascii=False))
    print("written", dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
