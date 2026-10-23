# R14 交付件一致性审计（零 API）：先自证审计器有效，再对账

脚本：`scripts/e2e_r14_claim_audit.py` → `evidence/R14_CLAIM_AUDIT.json`
当前结果：**`fails []`**（40 条引用全部可核）、**审计器有效性 5/5**。
成本 0 HTTP / 0 token，`llm_calls_used 0`。

## 为什么需要这一步

本轮之前我自己犯过两类**引用错误**，都不是代码问题，而是交付件问题：

1. 把扰动审计写成 `R14E_PERTURB_RELEASE_AUDIT_V5.json` —— 该件**不存在**
   （实际件名 `R14F_PERTURB_RELEASE_AUDIT.json`）；
2. `R14E_NEWLAYOUT_REGRESSION_V2` 把 **Solver 证据链单元数**标成"消费单元"，
   与**落盘裁剪份数**混同 → 我的"4 页 tight 选项 0 份"表述**过强**（V3 已勘误）。

所以我需要一台**机器**替我盯着文档与件是否一致，而不是靠下一轮再人肉发现。

## 七类检查

| 项 | 内容 | 本轮实测 |
|---|---|---|
| C1 | 文档中每个 `R14*.{json,md}` 引用必须存在 | 40 条引用；**3 条**是"声明此件不存在/被取代"的**勘误语境**，如实豁免并记行号（STOPGATE:16、DELIVERABLES:53/106、G0:25） |
| C2 | 被取代件不得被当作当前结论引用 | V2 只允许出现在勘误语境；首版**真的抓到** STOPGATE 第 52 行还在把 V2 当当前件 + 保留"4 页消费 0 单元"旧表述，已改写为 V3 口径 |
| C3 | 文档抄录数字与件内实际值逐条对账 | V3 层计数 16/7/7、`wrong_releases []`、`checks_failed []`、扰动 contract 15/15 与 perturb_loc 23/30、`wrong_grid_releasable []`、血缘 F 候选/接受/释放 5 且 `v10_equals_v9 true`、HEAD 复验 `drift []`、缺口四成因与两量分列，全过 |
| C4 | 三层不混同 | 件内 `candidates_solved / accepted / released` 三独立键；文档无"候选=释放"式代称 |
| C5 | 禁止措辞与因果上限 | 无"真实截图突破 / blind eval READY / 已批准 G"等；因果表述不高于"尺度协议充分、pitch 非因果必需" |
| C6 | G 未执行 | `evidence/R14G_RESULT*.json` **不存在**；PLAN `spent_so_far` 仍 `{requests: 0, tokens: 0}` |
| C7 | 跨文档**结论一致性**（本轮新增） | 件的 `REPRODUCIBLE_FROM_HEAD / NOT_REPRODUCIBLE_FROM_HEAD / VERDICT_REPRODUCIBLE_EVIDENCE_KEYS_MISSING` 以 `R14E_ARTIFACT_ATTRIBUTION.json` 为**唯一真值**；任何句子同时出现"件族名 + 状态词"就必须与真值一致，除非该句处于**勘误语境**（±3 行内出现 撤回/勘误/上一版本/误判/retract/不得/禁止）。本轮实测：DELIVERABLES 的勘误行被正确豁免并登记 `C7_erratum_context`（该行同句含两个状态词，因为它在陈述"旧结论 → 新结论"）|

**为什么需要 C7**：上一轮我撤回"V8 不可复现"这个错误结论时**只改了 STOPGATE**，
DELIVERABLES 里仍留着已撤回的说法。C1/C2/C5 都抓不到它：引用的件**存在**、
不是被取代件、也不是禁用措辞。若人工只看 DELIVERABLES，会读到与真值相反的结论。
C7 把"结论必须有唯一真值来源"变成机器检查，并在自测里**注入同类违规**证明它会报。

## 审计器**自身**的有效性自证（关键）

一个恒真的检查器比没有检查器更糟。审计运行时会先在**临时副本**里注入 6 类
违规，要求子进程**必须报出对应类别**才算通过（父进程检查子进程 JSON 输出的
**失败项前缀**，而不是只看退出码）：

```
C1 引用不存在件 → caught (C1 …)
C2 被取代件当当前 → caught (C2 …)
C3 抄录 17 ≠ 件内 16 → caught (C3 …)
C5 "真实截图已突破" → caught (C5 …)
C6 伪造 R14G_RESULT.json → caught (C6 …)
C7 把已撤回的 NOT_REPRODUCIBLE_FROM_HEAD 留在交付文档 → caught (C7 …)
→ all_injected_violations_caught: true
```

**这里也自纠一处**：自测首版是**假的通过**——子进程在 `--no-selftest` 分支
里根本不 print，父进程 JSON 解析失败后把"未产出可解析结果"塞进 `fails`，
于是 `caught=True` 变成了无条件成立。修正为：子进程**必须**打印结果、
父进程必须看到**期望的检查类别**才记 caught；同时注入副本不写真审计件。
修正后 5/5 例都是 `parseable=True` 且命中期望类别。

## 与 G 闸门的关系

C6 把"G 未执行"变成**可复核事实**：任何一次误发都会在下一轮审计里显形。
本轮预算依旧 **0/6 请求、0/100000 token**，盲评 **NOT READY**（真实截图端到端
2/5），Stage G 仍待人工批准（含模型名锁定）。
