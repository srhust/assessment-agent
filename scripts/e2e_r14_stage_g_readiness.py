"""G0：Stage G 发批前的**零真实请求**就绪审计。

G 是人工闸门后的付费动作。闸门未开时唯一诚实可推进的部分，是把工单写死的
每条 G 约束变成**可执行证明**——用假 transport 驱动**真实客户端代码**
（`OpenAICompatibleClient` / `VisionParser`），而不是 grep 源码当证据。

  C1  原子请求预算 ≤6：重试同样扣额，达 cap 后**发起前**拒绝；
  C1b 重试被预算穿透的缺陷（R13-G 起因）：max_requests=1 时重试必须发不出去；
  C2a token 硬顶：达顶后下一次请求（含重试）在 socket 前被拒；
  C2b 在途如实计入：超顶那一次响应照记入账，不允许"最后一次免单"；
  C3  请求参数 `text=None` / `save=True`：用真实 VisionParser 跑通——
      题面选项文本恒为空、裁剪真实落盘、payload 里 `max_tokens` 在场；
  C3b 解析器**拒绝补造**：空内容 payload 必须抛 VisionParseError（不产假区域）；
  C4  版本固定：manifest 的 5 件**原图 SHA 复算**一致 + 代码版本 == HEAD
      + Prompt 源 `src/assessment_agent/prompts.py` SHA 锁定；
  C5  错误接受即停：`release_status` 对 (accept, 错答) 必给
      `wrong_accept=True`（abort 的触发位）；
  C6  异常全计入端到端分母：HTTP 5xx/429 留痕 `requests_sent`、
      `status_counts`、`error_types`（分母 = 计划件数，不除成功件）；
  C7  证据命名隔离：`evidence/R14G_*` 与历史 `R13G_*` 不互写。

任一不成立都必须在此暴露 `ready:false`，不能靠"闸门未开"跳过。
产物：evidence/R14G0_GATE_READINESS_V4.json（真实网络请求 0 次）
"""
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import httpx                                                   # noqa: E402
import assessment_agent.llm as llm                              # noqa: E402
from assessment_agent.config import LLMSettings                  # noqa: E402
from assessment_agent.llm import OpenAICompatibleClient          # noqa: E402
from assessment_agent.tools.release_scoring import release_status  # noqa: E402
from assessment_agent.vision_parser import VisionParser         # noqa: E402

CHECKS = []


def check(cid, name, ok, evidence):
    CHECKS.append({"id": cid, "name": name, "ok": bool(ok), "evidence": evidence})
    print(("PASS " if ok else "FAIL ") + cid + " " + name)


USAGE = {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020}


def ok_body(content, usage=None):
    return {"choices": [{"message": {"content": (
        content if isinstance(content, str) else json.dumps(content))},
        "finish_reason": "stop"}],
        "usage": dict(usage or USAGE), "model": "fixed-vision-model"}


def resp(payload):
    return httpx.Response(200, json=payload, request=httpx.Request(
        "POST", "https://mock.invalid/v1/chat/completions"))


def err_resp(code, body):
    return httpx.Response(code, json=body, request=httpx.Request(
        "POST", "https://mock.invalid/v1/chat/completions"))


def make_client(handler, **kw):
    st = LLMSettings(api_key="mock-key-not-secret",
                     base_url="https://mock.invalid/v1",
                     text_model="fixed-text-model",
                     vision_model="fixed-vision-model",
                     json_mode=False, max_retries=kw.pop("max_retries", 0))
    return OpenAICompatibleClient(st, transport=httpx.MockTransport(handler), **kw)


BODY = {"choices": [{"message": {"content": json.dumps({"answer": "A"})},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 50,
                  "total_tokens": 1050}, "model": "fixed-vision-model"}

# ================= C2a token 硬顶：达顶后发起前拒绝 =================
n = {"k": 0}
c = make_client(lambda r: (n.__setitem__("k", n["k"] + 1), resp(BODY))[1],
                max_tokens=2100)
c.chat([{"role": "user", "content": "hi"}])
c.chat([{"role": "user", "content": "hi"}])
raised = None
try:
    c.chat([{"role": "user", "content": "hi"}])
except llm.LLMRequestBudgetExhausted as exc:
    raised = str(exc)
check("C2a", "token 硬顶达顶后在发出前拒绝（第 3 次请求被拒）",
      raised is not None and n["k"] == 2,
      {"http_attempts": n["k"], "usage_totals": dict(c._usage_totals),
       "max_tokens": c.max_tokens, "error": (raised or "")[:180]})

# ================= C2b 在途如实计入 =================
n2 = {"k": 0}
c2 = make_client(lambda r: (n2.__setitem__("k", n2["k"] + 1), resp(BODY))[1],
                 max_tokens=500)
c2.chat([{"role": "user", "content": "hi"}])
refused = None
try:
    c2.chat([{"role": "user", "content": "hi"}])
except llm.LLMRequestBudgetExhausted as exc:
    refused = str(exc)
tot = c2._usage_totals.get("total_tokens")
check("C2b", "在途 token 如实计入（1050>500 仍入账并拒后续，无免单）",
      tot == 1050 and refused is not None and n2["k"] == 1,
      {"total_tokens_after_first": tot, "http_attempts": n2["k"],
       "error": (refused or "")[:180]})

# ================= C1 请求预算：重试扣额 + 逐请求闸 =================
n3 = {"k": 0}
c3 = make_client(lambda r: (n3.__setitem__("k", n3["k"] + 1),
                            err_resp(429, {"error": "rate"})
                            if n3["k"] == 1 else resp(ok_body({"answer": "A"})))[1],
                 max_requests=2, max_retries=1)
first_ex = None            # 第 1 次调用：1 次 429 + 1 次重试 = 恰好耗尽预算
first_ok = False
try:
    first_ok = bool(c3.chat([{"role": "user", "content": "hi"}]).text)
except Exception as exc:  # noqa: BLE001
    first_ex = type(exc).__name__
second_ex = None           # 第 2 次调用：**必须被拒**（预算不因"整题已完成"退还）
try:
    c3.chat([{"role": "user", "content": "hi"}])
except llm.LLMRequestBudgetExhausted as exc:
    second_ex = "LLMRequestBudgetExhausted"
except Exception as exc:  # noqa: BLE001
    second_ex = type(exc).__name__
check("C1", "原子请求预算：429 重试同扣额，达 cap 后后续调用被拒（闸为**逐请求**）",
      c3._requests_sent == 2 and n3["k"] == 2 and first_ok
      and first_ex is None and second_ex == "LLMRequestBudgetExhausted",
      {"requests_sent": c3._requests_sent, "attempts_reached_socket": n3["k"],
       "max_requests": c3.max_requests, "call1_succeeded": first_ok,
       "call1_exception": first_ex, "call2_exception": second_ex,
       "status_counts": dict(c3._http_status_counts),
       "gate_semantics": "per-request（源码注释：逐题检查会被重试穿透超发）"})

# ================= C1b 重试不得穿透预算（R13-G 超发 6/5 的形态） ==========
n5 = {"k": 0}
c5 = make_client(lambda r: (n5.__setitem__("k", n5["k"] + 1),
                            err_resp(500, {"error": "boom"}))[1],
                 max_requests=1, max_retries=2)
exb = None
try:
    c5.chat([{"role": "user", "content": "hi"}])
except Exception as exc:  # noqa: BLE001
    exb = type(exc).__name__
check("C1b", "重试不穿透预算：max_requests=1 且持续 5xx 时第 2 次尝试被闸拒绝",
      n5["k"] == 1 and c5._requests_sent == 1
      and exb == "LLMRequestBudgetExhausted",
      {"attempts_reached_socket": n5["k"], "requests_sent": c5._requests_sent,
       "max_retries": c5.settings.max_retries, "max_requests": c5.max_requests,
       "exception": exb,
       "note": "闸在 socket 之前：R13-G 曾因此超发（计划 5、实发 6）。"})

# ================= C1c 预算耗尽后的**留痕**（计数已花，不抹账） =========
check("C1c", "预算耗尽留痕：requests_sent 达 cap、succeeded<sent、错误分类入账",
      c5._requests_sent == c5.max_requests
      and c5._requests_succeeded == 0
      and bool(c5._http_status_counts),
      {"requests_sent": c5._requests_sent,
       "requests_succeeded": c5._requests_succeeded,
       "status_counts": dict(c5._http_status_counts),
       "error_types": dict(c5._error_types),
       "note": "已花的请求/token **不因失败而退还**，G 批据此如实报分母。"})

# ============ C1d G 批预算布局：cap 6 / 5 页 / 每页允许 1 次重试 ============
n7 = {"k": 0}


def g_batch_handler(request):
    n7["k"] += 1
    # 第 1、4 次尝试模拟 429（即第 1 页首发失败、第 3 页首发失败）
    return err_resp(429, {"error": "rate"}) if n7["k"] in (1, 4) \
        else resp(ok_body({"answer": "A"}))


cg = make_client(g_batch_handler, max_requests=6, max_retries=1)
per_page = []
for p in range(5):
    ans, err = None, None
    try:
        ans = json.loads(cg.chat([{"role": "user",
                                   "content": f"page {p}"}]).text)
    except llm.LLMRequestBudgetExhausted:
        err = "BudgetExhausted"
    except Exception as exc:  # noqa: BLE001
        err = type(exc).__name__
    per_page.append({"page": p, "answered": ans is not None, "error": err})
answered = sum(1 for x in per_page if x["answered"])
exhausted = sum(1 for x in per_page if x["error"] == "BudgetExhausted")
check("C1d", "G 批布局实测：cap6/5 页/1 次重试 → 1 页重试仍成功 5/5；"
            "若发生 2 页重试则第 6 次后如实拒发（末记 BudgetExhausted，不伪装成功）",
      cg._requests_sent == 6 and answered == 4 and exhausted == 1,
      {"socket_attempts": n7["k"], "requests_sent": cg._requests_sent,
       "requests_succeeded": cg._requests_succeeded,
       "status_counts": dict(cg._http_status_counts),
       "token_total": cg._usage_totals.get("total_tokens"),
       "pages_answered": answered, "pages_budget_exhausted": exhausted,
       "per_page": per_page,
       "note": "0 次 429 时 5/5 全答；本场景 2 页首发失败但 cap 只够 1 次重试，"
               "缺的那 1 页**必须**留为 BudgetExhausted 并计入端到端分母——"
               "G 批禁止用调低页数或放松闸来抹掉它。"})

# ================= C6 异常留痕（计入分母） =================
n4 = {"k": 0}


def always_500(request):
    n4["k"] += 1
    return err_resp(500, {"error": "boom"})


c4 = make_client(always_500, max_requests=4, max_retries=1)
exc4 = None
try:
    c4.chat([{"role": "user", "content": "hi"}])
except Exception as exc:  # noqa: BLE001
    exc4 = type(exc).__name__
check("C6", "异常全计入分母（5xx 留痕 requests_sent / status_counts / error_types）",
      c4._requests_sent >= 2 and bool(c4._http_status_counts) and exc4 is not None,
      {"requests_sent": c4._requests_sent, "attempts": n4["k"],
       "status_counts": dict(c4._http_status_counts),
       "error_types": dict(c4._error_types), "exception": exc4,
       "denominator_rule": "端到端分母 = 计划件数（含失败件），不得只除成功件"})

# ================= C5 错误接受即停触发位 =================
rs_bad = release_status("accept", "B", "A")
rs_ok = release_status("accept", "A", "A")
rs_ab = release_status(None, None, "A")
rs_rep = release_status("repair", "A", "A")
check("C5", "错误接受即停：(accept,错答) 必给 wrong_accept=True（abort 触发位）",
      rs_bad.get("wrong_accept") is True and not rs_ok.get("wrong_accept")
      and not rs_ab.get("released"),
      {"wrong_accept": rs_bad, "correct_accept": rs_ok,
       "abstain": rs_ab, "repair_correct": rs_rep})

# ================= C3 text=None / save=True（真实类驱动） =================
plan = json.loads((ROOT / "evidence" / "R14G_PLAN.json").read_text(encoding="utf-8"))
first = plan["planned_calls"][0]
saved_payload = {}


def parse_handler(request):
    saved_payload.update(json.loads(request.content))
    w, h = first["mask_hw"][1], first["mask_hw"][0]
    opts, x0 = [], 4
    for lab in ("A", "B", "C"):
        opts.append({"label": lab, "bbox": [x0, 140, 60, 30]})
        x0 += 70
    content = json.dumps({
        "stem_text": "G0 连通性自证用题干（非评测内容）",
        "image_size": [w, h],
        "stem_matrix_bbox": [10, 40, 120, 60],
        "stem_matrix_grid": [1, 3],
        "options": opts,
        "diagram_regions": [{"region_id": "d1", "role": "stem_figure",
                             "bbox": [10, 40, 120, 60]}],
    })
    return resp(ok_body(content))


cl3 = make_client(parse_handler, max_requests=6, max_retries=0)
tmp = Path(tempfile.mkdtemp())
parser = VisionParser(cl3, crops_dir=tmp / "regions")
res = parser.parse(first["image_path"], hint="G0 参数连通性自证（非评测）")
crop_files = sorted(p.name for p in (tmp / "regions").glob("*.png")) \
    if (tmp / "regions").is_dir() else []
texts = [getattr(o, "text", None) for o in (res.options or [])]
text_none = bool(texts) and all(t in (None, "", "None") for t in texts)
has_maxtok = "max_tokens" in json.dumps(saved_payload)
check("C3", "text=None / save=True 连通真实 VisionParser（选项文本为空、裁剪落盘、"
            "payload 含 max_tokens）",
      bool(res.options) and text_none and len(crop_files) >= 1 and has_maxtok,
      {"declared_options": len(res.options or []),
       "all_option_text_empty": text_none, "option_texts": texts,
       "saved_crops": crop_files, "payload_has_max_tokens": has_maxtok,
       "payload_keys": sorted(saved_payload)[:8],
       "note": "G 批题面选项一律 text=None（图选项），裁剪必须落盘；此处用真实"
               "解析类跑通，不发真实请求。"})

# ================= C3b 解析器拒绝补造 =================
empty_called = {"n": 0}


def empty_handler(request):
    empty_called["n"] += 1
    return resp(ok_body({"stem_text": "", "options": [],
                         "diagram_regions": [], "image_size": [10, 10]},
                        usage={"total_tokens": 10}))


cl6 = make_client(empty_handler, max_requests=6, max_retries=0)
six_b = None
try:
    VisionParser(cl6, crops_dir=Path(tempfile.mkdtemp())).parse(
        first["image_path"], hint="空内容必须被拒")
except Exception as exc:  # noqa: BLE001
    six_b = type(exc).__name__
check("C3b", "解析器不补造：空内容 payload 抛 VisionParseError",
      six_b == "VisionParseError" and empty_called["n"] == 1,
      {"exception": six_b, "requests_used": empty_called["n"]})

# ================= C4 版本/Prompt/原图 SHA 复算 =================
git_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                       capture_output=True, text=True).stdout.strip()
mism = []
for p in plan["planned_calls"]:
    img = Path(p["image_path"])
    if not img.is_file():
        mism.append(f"{p['pid']}: 源图缺失")
        continue
    if hashlib.sha256(img.read_bytes()).hexdigest() != p["original_sha256"]:
        mism.append(f"{p['pid']}: SHA 不符")
prompts_py = ROOT / "src" / "assessment_agent" / "prompts.py"
check("C4", "manifest 5 件原图 SHA 复算一致 + 代码版本==HEAD + Prompt 源锁定",
      not mism and plan["fixed"]["code_version"] == git_head and prompts_py.is_file(),
      {"planned": len(plan["planned_calls"]), "sha_mismatches": mism,
       "code_version": git_head,
       "code_version_matches_manifest": plan["fixed"]["code_version"] == git_head,
       "worktree_dirty_at_plan_time": plan["fixed"].get("worktree_dirty"),
       "worktree_dirty_now": bool(dirty),
       "prompt_source": "src/assessment_agent/prompts.py",
       "prompt_sha256": hashlib.sha256(prompts_py.read_bytes()).hexdigest()
       if prompts_py.is_file() else None,
       "model_note": plan.get("model_note")})

# ===== C9 G 批入口自身的闸门行为（子进程实跑，退出在任意网络调用之前） =====
batch = ROOT / "scripts" / "e2e_r14_stage_g_batch.py"
r_no = subprocess.run([sys.executable, "-X", "utf8", str(batch)],
                      cwd=ROOT, capture_output=True, text=True)
r_fake = subprocess.run([sys.executable, "-X", "utf8", str(batch),
                         "--approve", "auto-continuation-round-not-human"],
                        cwd=ROOT, capture_output=True, text=True)
res_file = (ROOT / "evidence" / "R14G_RESULT.json")


def _jout(txt):
    try:
        return json.loads(txt.strip().splitlines()[0] if False else
                          txt[txt.index("{"):])
    except Exception:  # noqa: BLE001
        return {}


j_no, j_fake = _jout(r_no.stderr), _jout(r_fake.stderr)
check("C9", "G 批入口零请求自证：无 --approve 直接退出；伪造批准仍被 model_lock "
            "拦下（模型名未经人工确认禁止发起）",
      r_no.returncode == 2 and j_no.get("aborted_before_any_request") is True
      and r_fake.returncode == 2
      and "model_lock" in json.dumps(j_fake, ensure_ascii=False)
      and not res_file.exists(),
      {"no_approve_exit": r_no.returncode,
       "no_approve_reason": (j_no.get("reason") or "")[:120],
       "fake_approve_exit": r_fake.returncode,
       "fake_approve_reason": (j_fake.get("reason") or "")[:120],
       "result_file_written": res_file.exists(),
       "note": "校验顺序即防线：闸门 → 预算 → **模型锁** → 代码版本 → Prompt "
               "SHA → 原图 SHA → 配置存在；任一不过都在 socket 之前退出。"})

# ================= C7 命名隔离 =================
r14g = sorted(p.name for p in (ROOT / "evidence").glob("R14G_*"))
# **C7 的真实意图**是"G 批**结果件**不得在未获批准前存在"，不是
# "不许有任何 R14G_ 前缀文件"。本轮 A9 落了三个**零 HTTP 离线核验**产物
# （R14G_HISTORICAL_BYTES.{json,md} / _SELFTEST.json），字面判定把 C7 顶失败，
# 逼我在"改坏检查"和"删掉证据"之间二选一。现按语义分档，
# 白名单**只放核验/计划件**：真 G 批结果件不在其中，一出现立刻失败。
_ALLOWED_NONRESULT = ("R14G_PLAN.json", "R14G_HISTORICAL_BYTES.json",
                      "R14G_HISTORICAL_BYTES.md",
                      "R14G_HISTORICAL_BYTES_SELFTEST.json")
_stray = [x for x in r14g if x not in _ALLOWED_NONRESULT]
check("C7", "证据命名隔离：除 PLAN 与**离线核验件白名单**外，"
      "不得存在任何 R14G_* 结果件",
      _stray == [],
      {"R14G_files": r14g, "stray_result_files": _stray,
       "allowed_nonresult": list(_ALLOWED_NONRESULT),
       "why_allowlist": "白名单只放零 HTTP 的核验/计划产物；R14G_RESULT* 等"
                        "真结果件不在其中，一旦出现立刻失败",
       "R13G_files_count": len(list((ROOT / "evidence").glob("R13G_*")))})

out = {"checks": CHECKS,
       "ready_for_G_after_human_approval": all(x["ok"] for x in CHECKS),
       "failing": [x["id"] for x in CHECKS if not x["ok"]],
       "real_http_requests_made": 0,
       "budget": plan["budget"],
       "note": "全部行为级检查由**假 transport**（httpx.MockTransport，域名 "
               "mock.invalid）驱动真实客户端代码；真实网络请求 0 次。闸门未开时"
               "唯一诚实的推进是把每条 G 约束变成可执行证明。"}
name = "R14G0_GATE_READINESS_V5.json"
for i, a in enumerate(sys.argv):
    if a == "--out" and i + 1 < len(sys.argv):
        name = sys.argv[i + 1]
dst = ROOT / "evidence" / name
dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"ready": out["ready_for_G_after_human_approval"],
                  "failing": out["failing"],
                  "real_http_requests_made": 0}, ensure_ascii=False))
print("written", dst)
sys.exit(0 if out["ready_for_G_after_human_approval"] else 1)
