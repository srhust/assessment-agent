"""生成 Stage G 发批 manifest（**只读、零请求**）。

manifest 把工单写死的每条约束落成一个**人工签字即可执行**的固定计划：
页数/顺序/参数/模型/Prompt/代码版本/原图 SHA/token 与请求预算。
本脚本**不发请求、不导入网络客户端**，只产计划与哈希锁。
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.schemas import Question                    # noqa: E402
from assessment_agent.tools.figure_page_localizer import localize_page  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask        # noqa: E402

ORDER = ["ft-mirror-04", "ft-rot90-01", "ft-trans-07", "ft-overlay-12"]
EXTRA = "ft-mirror-04--tight"      # 余量 1 张：新排版（tight 裁边）真实截图件
MAX_REQUESTS_INCL_RETRY = 6
MAX_TOKENS = 100000


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    calls = []
    for pid in ORDER + [EXTRA]:
        d = ROOT / "runs" / f"r13g-{pid}"
        img = d / "image" / "original.png"
        qj = d / "question.json"
        if not img.is_file() or not qj.is_file():
            print(f"!! 缺件：{pid} -> {img}", file=sys.stderr)
            return 1
        q = Question.model_validate(json.loads(qj.read_text(encoding="utf-8")))
        mask = load_mask(str(img))
        n_cells = len((localize_page(mask, n_options=len(q.options or []) or 2)
                       .get("option_regions") or []))
        calls.append({
            "seq": len(calls) + 1, "pid": pid,
            "image_path": str(img), "original_sha256": sha(img),
            "question_sha256": sha(qj),
            "declared_options": len(q.options or []),
            "localizer_option_cells": n_cells,
            "mask_hw": [int(mask.shape[0]), int(mask.shape[1])],
            "page_ink": int(mask.sum()),
            "text": None, "save": True,
            "retry_policy": "最多 1 次重试，重试同样扣请求预算",
        })
    git_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    prompt_path = ROOT / "src" / "assessment_agent" / "prompts" / "figure_reasoning.txt"
    manifest = {
        "stage": "G",
        "status": "PLANNED_NOT_EXECUTED",
        "approval_required": "evidence/R14_PROGRESS_STOPGATE.md 人工批准后方可发起",
        "planned_calls": calls,
        "budget": {"max_requests_including_retry": MAX_REQUESTS_INCL_RETRY,
                   "planned_requests": len(calls),
                   "headroom_for_retry": MAX_REQUESTS_INCL_RETRY - len(calls),
                   "max_tokens": MAX_TOKENS,
                   "env": "ASSESSMENT_LLM_MAX_TOKENS=100000",
                   "spent_so_far": {"requests": 0, "tokens": 0}},
        "fixed": {"code_version": git_sha,
                  "worktree_dirty": bool(dirty),
                  "prompt_file": str(prompt_path.relative_to(ROOT))
                  if prompt_path.is_file() else None,
                  "prompt_sha256": sha(prompt_path) if prompt_path.is_file() else None,
                  "request_params": {"text": None, "save": True},
                  "model": "见 model_note（不使用本地记录，须与人工确认一致）"},
        "model_note": "模型名必须与工单/人工确认一致后写入本 manifest 再发起；"
                      "G0 就绪审计会因该字段未锁而判 failing——这是**刻意的**，"
                      "禁止未经确认自动选模型。",
        "stop_rules": ["错误接受即停（release_status 判定 wrong_accept → abort）",
                       "全部异常（超时/HTTP 错/解析失败）计入端到端分母",
                       "token 达硬顶后拒绝发起后续请求，已花如实入账"],
        "evidence_prefix": "evidence/R14G_*",
        "llm_calls_used": 0, "http_requests_made": 0,
    }
    dst = ROOT / "evidence" / "R14G_PLAN.json"
    # **防自毁**：manifest 重算只该更新版本/哈希，**不该**顺手删掉人工闸门
    # 记录。本轮实测发生过：重算后 `model_lock` 整段消失，等于悄悄放宽了
    # 发批前置条件。旧件里有就原样携带；旧件里没有（首次生成）则保持缺失，
    # 由 d9b/人工补，**绝不虚构**。
    carried = None
    if dst.is_file():
        try:
            old_ml = json.loads(dst.read_text(encoding="utf-8")).get("model_lock")
        except Exception:  # noqa: BLE001
            old_ml = None
        if old_ml:
            manifest["model_lock"] = old_ml
            carried = old_ml.get("status")
    manifest["regen_note"] = (
        "d9 重算只更新 code_version / 各件 SHA / prompt SHA；model_lock "
        "段从旧件原样携带（缺失则不虚构）。"
        f"本次携带状态：{carried or '无（旧件不含 model_lock，需 d9b/人工补）'}")
    dst.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(json.dumps({"planned": len(calls), "budget": manifest["budget"],
                      "code_version": git_sha,
                      "model_lock_carried": carried,
                      "prompt_sha256": manifest["fixed"]["prompt_sha256"],
                      "order": [c["pid"] for c in calls]},
                     ensure_ascii=False, indent=1))
    print("written", dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
