# R14 ④ 交付汇总与盲评准入结论

- HEAD `b56b90f02`；本汇总由 `scripts/r14_delivery_summary.py` **读真件生成**，数字不手打、不跨轮复用。
- 取数来源：14 项登记交付件 + F/perturb/A9/G 计划。

## 十项材料

- **1 交付登记**：14 项在登记内且状态合规；索引 self_checks_failed []
- **2 一键复现 bundle**：basis STANDALONE_PREVIOUS（独立运行，磁盘件即最新一轮）；status RAN head b56b90f02 步数 21 fails [] http 0 tokens 0；A12 1/1、A10 []、A11 []
- **3 声明审计**：fails []；C1 63 条、C3 27 条、C8 计数点 23（活引用 13 / 豁免 10）；注入自证 13 例全抓 True
- **4 补集语义复现**：violations 0；登记反证 mode synthetic proved True
- **5 生产者归属**：V8 true_producers []、shape_only_matches ['scripts/e2d_r14_production_replay_v4.py']、partial ['scripts/e2_r14_optionband_replay.py'] ⇒ reproducibility VERDICT_REPRODUCIBLE_EVIDENCE_KEYS_MISSING；双钉自测 fails []
- **6 F 六条件**：E 臂 final_released 5 / correct_candidates 5 / 5/13 / wrong_releases []；F 臂同 5（5/13）
- **7 perturb30 释放审计**：wrong_grid_releasable []、n 30
- **8 新排版回归 + tight 诊断**：R14E_NEWLAYOUT_REGRESSION_V3 分列两量、零错放；R14E_TIGHT_GAP_DIAGNOSIS 零 API、不调阈值、不加规则
- **9 历史 Test 件字节基线**：A9 fails []；逐件与**引入 commit** 的 git blob 比字节（rows 6）；未跟踪数据目录如实登记 ['!! data/benchmark_figshots_perturb/']
- **10 Stage G 计划与闸**：status PLANNED_NOT_EXECUTED、model_lock UNRESOLVED_MUST_BE_CONFIRMED_BY_HUMAN、已用 {'requests': 0, 'tokens': 0}（上限 6 请求 / 100000 token）、批准后就绪 True failing []

## 盲评准入结论

**NOT READY（不准入）**：
- 离线 `5/13 全对零错放` 是**候选→验证→释放**链上的离线判定，**不是**真实截图端到端成绩（目标明令不得混同）；
- 真实截图端到端仅 **2/5** 释放正确（mirror-04、rot90-01），overlay/tight 仍受选项带切分限制，trans-07 如实弃权；
- V8 的作者档本轮如实降为 `VERDICT_REPRODUCIBLE_EVIDENCE_KEYS_MISSING`：判定层 5/5 页逐字段对上，但**证据层富化键**在 HEAD 无写名脚本可产出 ⇒ 不得表述为「V8 可从 HEAD 完整复现」；
- 因果表述维持人工锁定：**尺度协议修复是充分原因、pitch 非因果必需**（双轴表），本轮未加强亦未收紧。

**准入前置**：人工批准裁决 2 → Stage G 小批 HTTP（≤6 请求含重试、硬顶 100000 token、顺序固定、`text=None`、`save=True`、模型/Prompt/版本/原图 SHA 固定、错误接受即停、全部异常计入端到端分母）→ 实测 ≥ 人工门槛。

预算：HTTP 0 / LLM 0。

