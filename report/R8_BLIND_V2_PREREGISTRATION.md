# R8 · xingce_blind_v2 预注册与冻结记录

**冻结时间**：2026-10-20 20:30 (+08:00)，本文档所在提交即冻结点。
**数据集**：`data/benchmark_xingce_blind_v2/test/xingce_blind_v2.jsonl`
**内容 SHA-256**：`16e882f4f1c0607b42f960221727a3e4ae4ed73063dac9d496bf5d4bbf880d5f`（16 位前缀 `8aea5255603652de`）
**题数**：60；**生成种子**：20261021（`scripts/gen_blind_v2.py`，重跑需换目录，本文件不再覆写）

## 命题独立性声明（对应工单 §B）

1. 出题流程与 Solver 改进流程**分离**：本集 60 题先于任何依据其结果的代码改动存在；
   冻结后 Solver/Verifier/MethodKB 若再修改，**不得**以本集回答结果调参后复报独立分。
2. 所有题目来源为**自撰语料与自造有界数据**（许可记录逐题存 `proofs.json.source`，
   改编参考仅记 CC BY-NC-SA 大纲教材思路，未复制原文、未抓取付费题库）。
3. 每题附唯一性证明：`proofs.json` 含 conditions / solution_path / recompute /
   pitfall / source；其中 **47 题由 ProblemValidityVerifier 机械通道判定唯一**
   （四层：算术重放 / 依赖溯源 / 条件充分 / 选项唯一，含枚举穷举复核），
   **13 题标 `verification: manual_only`**（主旨/意图/排序/情境强度题——语义判断
   无法机械闭合，如实标注，统计时单列）。
4. 双维泄漏检查（`frozen_meta.json.leak`）：
   - 文字近重复：与 dev / R5 87 题 / blind v1 / v1.1 token 相似 ≥0.7 即丢弃
     （本集生成时丢弃 6 题）；0.55–0.7 记 soft_flag 保留；
   - **数学结构近重复**：题面数值多重集与既有题相同即丢弃（v1 无此维度，v2 新增）。
5. 弃权型客观题 1 道（v2 元题：条件不足→"无法唯一确定"为唯一正确项）计入能力评测；
   正确弃权**不等于**正确解题，报告中单列。
6. `personality`（4 题）为 Solver 未支持域，保留用于能力覆盖评测，不期待其被解出。

## 唯一一次正式首评（pre-registered）

- **运行名**：`r8_v2_first_eval`（此后对本集的任何重跑只能记为 regression/diagnostic，
  不得称"首评"；若发现实现缺陷影响本集结果，保留首评数字并迁移 v3，不重复申领独立性）。
- **输入**：冻结 jsonl 原文（题面+选项文本），oracle_solver 金标注入关闭，走完整管线：
  VisionParser 跳过（文本输入）→ 路由 → MethodKB 检索 → 计划 → 确定性工具执行 → Verifier。
- **主指标**：`correct_release_rate` = 正确且被验证器放行的题数 / 有效可解题数；
  分母修正：剔除 2 道已知不可解域题（personality 中无客观金标支持的题不算能力缺口惩罚——
  实际全 4 题保留计分，因其为客观单选）。
- **次指标**：false_accept、正确弃权数、机械/人工验证分层正确率、按域分解。
- **API 预算**：≤ 220 次调用（含重试；每调用计数持久化）；预算耗尽即停止并如实报告
  完成题数，不足部分标"未评"，不补跑第二次"首评"。
- **停止条件**：预算触顶或 60 题全部产出 verification_outcome。
- 首评结果**只进 R8 终报**，任何改动后的重跑一律命名 `r8_v2_regression_*`。

## 首评实际结果（r8_v2_first_eval，2026-10-20 21:04，2575s）

- 60 题 oracle_solver 全管线：**correct_accept 15 / false_accept 0 / no_answer 45**。
- 分域：number_series 7/7、verbal 3/8、numeric_calc 2/9、logical 2/11、data_analysis 1/8、
  figure 0/7（全部要求截图，文本输入正确弃权）、language 0/6、personality 0/4（未支持域正确弃权）。
- 机械验证 47 题放行 12；manual_only 13 题放行 3（文字语义题——放行计数仍受 Verifier 复核约束）。
- 用量 29 次调用 / 63165 tokens，远低于 ≤220 预算；无预算触顶，60 题全部产出 outcome。
- 逐题表：
esults/xingce_blind_v2/r8_v2_first_eval/first_eval_item_table.json。
- 首评成绩即本集**永久独立泛化数字**；任何后续对本集的运行一律命名 r8_v2_regression_*。

## 与 blind v1 的关系

v1（sha `3af5f7f4dce63370`）14 题经 R8-A 审计：11 有效 / 1 缺陷（rb-05 舍入未声明）/
2 无效（rb-09/10 时间错配），且已参与 R7 缺陷修复，**永久降级为诊断/回归集**；
其 13/14 历史数字保留于 R7 报告不回改，独立泛化结论一律以本 v2 首评为准。
