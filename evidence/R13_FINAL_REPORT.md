# R13 最终报告：视觉选项证据闭合与端到端正确作答突破

基线 `07c864e`（R12 条件收口）。提交链：`0990a6c`(A) → `cd1b2b6`(B/D核心) →
`288de1a`(C) → `235ce28`(E) → `3ca187b`(D) → `9abe1f5`(F) → 本次(G/H)。
测试套件 **650 passed / 2 skipped**；R12 全部原件与 SHA 保留。

## 1. Verifier 严格释放记账（六条件，三层）
`evidence/R13A_SIX_CONDITION_VERIFIER_BACKED.json`（脚本保存并核验
`Verifier.final_answer`，缺字段一律停第 1 层）：

| 条件 | 正确候选 | 已验证接受 | 最终释放 | 错误释放 | ev_missing |
|---|---|---|---|---|---|
| A 双通道全量 | 13/13 | 10 | **10/13** | 0 | 0 |
| C 仅 VLM 金标区域 | 1 | 1 | **1/13** | 0 | 0 |
| E 金标题干裁剪+像素选项 | 1 | 1 | **1/13** | 0 | 0 |
| F 像素题干+像素选项(真实解析缓存) | 1 | 1 | **1/13** | 0 | 0 |

**F 从 R12 的 0/13 到 1/13**：像素→像素端到端首次由验证器真实返回
`final_answer:"A"` 背书释放（ft-mirror-04）。

## 2. 五个子指标（人工裁决口径替代单一 15/15）
`evidence/R13A_SUBMETRICS.json`：网格尺寸 **15/15**；空白格 13/15；
目标格 13/15；**选项对应 8/15**（Stage B/C 的主瓶颈，已量化）；内容完整 15/15。
逐题翻转与人工复核一致：4 改善 / 5 退化（净 -1），其中 3 页曾以
`ok/assumed_target` 传递错误网格。

## 3. 安全门与三反例（P0）
`rows_hint_mismatch`、`stem_continuation_suspect`（含列对齐保护）、
槽形宽高比、`release_eligible=(status=="ok")`、复核同链路 merge、
复核拒绝非 ok 布局。三反例现全部**不可释放**；30 页扰动集
"尺寸≠金标且可释放" = **0**（tests/test_r13a_safety_gates.py 固化）。
代价如实报告：native15 中 ft-overlay-13 尺寸正确但目标未定位 → ambiguous
不可释放（原可"错误地"释放）。

## 4. 选项证据复盘（真实查看，非只读 JSON）
`evidence/R13B_OPTION_REPLAY.json` + 32 张图板。来源声明：R12-G 原始解析
**artifact_unavailable**（save=False）；复盘用 dev15 历史真实 VLM 输出（缓存件，
已标 provenance）。视觉桥目检确认 mirror-04 的 VLM 选项裁剪与金标同形
（含两枚近重复 ┐ 干扰项）。

## 5. mirror-04「E 可释放、F 不可释放」的确定性根因（已修复）
不是规则、不是阈值：求解路径仅在**未显式给网格**时启用等距外推，
复核传显式网格走均分槽 → 两套槽协议 → 画布哈希必然漂移 → 像素证据释放
被 100% 拦截。统一协议后漂移探针 MATCH（`494a8b32c560af02`），
F 立即达标。全程未动 tol=0.15 / margin=0.05。

## 6. OptionEvidence 门
label-anchor 帧（字母左、图形同带）→ `geometry_frame`、
`label_association_verified`、`clipping_detected`（仅带外同区间有墨才算切割）。
验证器 `figure.option_evidence_gate`：关联非**证实为真**或存在切割 → 不释放。
毒化演示：单格关联 True→False 即 accept→unverified。
`--tight` 页字母被裁没 → 现被门保守拦截（覆盖率下降是授权代价；
test_r10d 断言按新政策更新，注明收紧方向）。

## 7. 三通道对照与仲裁政策
`evidence/R13D_CHANNEL_AUDIT.json`：CH1 形状归一 / CH2 位置保序 /
CH3 规则驱动（旋转翻转叠加族=CH1 权威；平移族=CH2 权威；冲突=弃权；
**禁止平均分数**）。关键负发现：trans-07 两通道**一致指向错误项 B**——
剩余失败是规则覆盖缺口（identity-shift 非该页真算子），不是证据丢失；
故 **不**把 CH2 接入求解器（会制造错误自信）。overlay-12 B<A 同理入 R14。

## 8. 无文字题干合法流程
管线守卫：无题干文字但有可信图形区域 → 正常进入 figure_reasoning（金标
零注入，断言 stem==''）；文字与图形俱无 → 仍拒。Mock 证明：
solved A + verifier accept。真实截图批中 `ft-mirror-04--tight` 经合法流程
进入后因 VLM 选项区为 0 而**如实弃权**（不再是管线异常）。

## 9. Planner 五层召回（作废前缀口径）
`evidence/R13F_PLANNER_LAYERS.json`：L0 状态分桶（5 invoked_with_results /
2 invoked_empty_direct_ast——r10g-07/08 用已存 expr 证明是 direct-AST，
非日志丢失）；L1 Exact 1/7；L2 Skill 5/7；L3 算子覆盖均值 0.286；
L4 词面适用（下界，含诚实缺陷声明）；L5 计划执行有效 4/7。
**KB 缺口**：r10g-03 金标卡 `nc.combinatorics-power` 不存在——先缺卡后落召回，
双重错误纠正了 R12 的"功能命中"叙述。

## 10. 真实 HTTP 批（预算合规）
`evidence/R13G_REAL_VISION.json`：原子闸 `max_requests=6` 在 llm.py 每次
发送前（含每次重试）检查——实际发出 **6/6**（含 1 次 ReadTimeout 重试，
未越界），token **13,655**（usage_snapshot 真实值），**0 错误释放，
0 最终释放**，预注册停止规则未触发。
残余段（零 API 重放 `R13G_REAL_PARSE_OFFLINE_REPLAY.json` 已定位）：
真实 VLM (a) 把题干矩阵框截断在末图形（`[1,2]` 无空槽）——cue 前缀容错
使 R9-B 像素重数恢复 `[1,3] blanks=1`；(b) 但重建画布按列中位宽 26 切分
而实际节距为 52，格错位使变换残差 0.99 → 弃权。两处失败方向全部安全
（拒绝作答而非猜测）。R14 首要靶点：assemble_stem_layout 的 canvas
pitch 估计。

## 附带修复（G）
题干截断线索容错（≥4 字前缀且近句尾）；选项宽裁剪标签剥离
（strip_label_from_crop，与门同一不变量）；预算测试 4 例
（重试不可穿透 cap / env 兜底 / 无 cap 行为不变）。

## 盲测就绪判定
**仍 NOT READY**：live 截图批 0 释放、平移族规则缺口未修、
选项映射金标一致 8/15→已修门但覆盖待真实批检验。解除条件：
G 批出现≥1 例最终释放（用 R14 修复 canvas pitch 后重跑 6 项预算），
且错误释放保持 0。

## 人工复核入口
GitHub `srhust/assessment-agent` HEAD（本轮提交信息含全部数字）；
关键文件：`evidence/R13{A_SUBMETRICS,A_SIX_CONDITION_VERIFIER_BACKED,B_OPTION_REPLAY,C(gates via tests),D_CHANNEL_AUDIT,F_PLANNER_LAYERS,G_REAL_VISION,G_REAL_PARSE_OFFLINE_REPLAY,ERRATA_R12}.{json,md}`、
`tests/test_r13{a_safety_gates,c_option_evidence,e_textless_stem,g_budget}.py`、
`scripts/{a1,b1,d2,f2,g3}_r13*.py`、`src/assessment_agent/tools/option_evidence.py`、
`llm.py` 原子闸。未独立运行 650 测试套；dev15 缓存件≠R12 模型原始输出。
