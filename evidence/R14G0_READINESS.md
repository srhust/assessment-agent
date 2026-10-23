# R14 G0：Stage G 发批前的零请求就绪审计

脚本：`scripts/e2e_r14_stage_g_readiness.py`（`--out` 可指定文件名）
产物：`evidence/R14G0_GATE_READINESS_V5.json` · manifest：`evidence/R14G_PLAN.json`
**真实 HTTP 请求 0 次**（全部行为级检查由 `httpx.MockTransport` 驱动真实客户端
代码，域名 `mock.invalid`）；预算仍 **0/6 请求、0/100000 token**。

闸门未开时唯一诚实的推进，是把工单写死的每条 G 约束变成**可执行证明**
（grep 源码不算证据）。结论：`ready_for_G_after_human_approval: true`、
`failing: []`。

| 检查 | 内容 | 实测 |
|---|---|---|
| C2a | token 硬顶达顶后**发起前**拒绝 | cap 2100，两次 1050 后第 3 次 `LLMRequestBudgetExhausted`，`http_attempts=2` |
| C2b | 在途响应如实计入，无"最后一次免单" | 首响应 1050 > cap 500 仍入账（`total_tokens=1050`），后续被拒 |
| C1 | 原子请求预算（重试同扣额，闸为**逐请求**） | 429 重试扣额：`requests_sent=2`，第 2 次调用被拒 |
| C1b | 重试不得穿透预算（R13-G 超发 6/5 的形态） | `max_requests=1` + 持续 5xx：仅 1 次到 socket，第 2 次尝试被闸拒 |
| C1c | 预算耗尽留痕 | `sent=cap`、`succeeded=0`、`status_counts`/`error_types` 入账 |
| C1d | **G 批布局实测**（cap6 / 5 页 / 每页 1 次重试） | 无 429 → 5/5 全答；本场景 2 页首发 429 而 cap 只够 1 次重试 → 4 页作答 + **1 页 `BudgetExhausted`**（`sent=6 succ=4 tok=4080`，`{429:2, 200:4}`）。缺的那页必须计入端到端分母，**不得**用降低页数或放松闸抹掉 |
| C6 | 异常全计入分母 | 5xx 留痕；分母 = 计划件数（含失败件），不除成功件 |
| C5 | 错误接受即停的触发位 | `release_status(accept, B, A)` → `wrong_accept=True`；弃权不释放 |
| C3 | `text=None` / `save=True` 连通真实 `VisionParser` | 选项文本 `['','','']`（全空）、裁剪真实落盘（`diagram_d1.png`）、payload 含 `max_tokens` |
| C3b | 解析器不补造 | 空内容 payload → `VisionParseError` |
| C4 | 版本锁 | manifest 5 件**原图 SHA 复算一致**；`code_version == HEAD(32829d3d)`；Prompt 源 `src/assessment_agent/prompts.py`（`7c9dc3c8…`） |
| **C9** | G 批入口自身闸门 | 无 `--approve` → exit 2、`aborted_before_any_request`；伪造批准仍被 **model_lock** 拦下；`evidence/R14G_RESULT.json` **未生成** |
| C7 | 命名隔离 | `R14G_*` 仅 `PLAN`+`G0`，与 `R13G_*`（历史件）不互写 |

## 发批顺序即防线（`scripts/e2e_r14_stage_g_batch.py`）

闸门 → 预算(6/100000) → **模型锁** → 代码版本 → Prompt SHA → 原图 SHA →
配置存在；任一不过都在 socket 之前退出（C9 实测两条）。批准后仍需人工
把 `model_lock.status` 写成 `CONFIRMED_BY_HUMAN` 并给出模型名——本地
配置 `vision_model=null`、无 key，脚本**禁止**自选模型。

## 待人工补的两项

1. **模型名**：`evidence/R14G_PLAN.json` 的 `fixed.vision_model` 与
   `model_lock.status`；不锁定则永远发不出去（这是设计，不是缺陷）。
2. **批准凭据**：`--approve` 传人工评审结论。届时按 C1d 的实测语义记账：
   预算触顶导致的缺页与任何异常都计入分母。
