# 评测报告 · split=xingce_blind_v2 · eval_mode=oracle_solver

- 样本数：60
- 输入模式：oracle_solver（oracle_solver=标注题型+标准题干；auto_router=标准题干+自动路由；image_only=仅原始截图）

## 核心指标（legacy · R2b expected_status 口径，仅供历史对照；主指标见下方 answerability 口径块）

- **答案准确率（legacy）**：25.00%（60 道可答题中答对 15 道）
- **可答题覆盖率（legacy）**：25.00%（15/60）
- **选择性准确率**：100.00%（通过验证的答案中正确的比例）
- **路由准确率**：N/A（oracle_solver 模式直接使用标注题型）

- **拒答准确率**：0.00%（0/0）
- 耗时：总 2575.11s，平均 42918.5 ms，p50 5775.1 ms，p95 185899.8 ms

## 核心指标（主口径 · answerability 双维度）——客观可答性与能力口径（R2c/R3a，分母=所有有唯一客观答案样本，含未实现题型）

- **总体答题准确率 overall_answer_accuracy**：25.00%（15/60）；分母含当前 Skill 未实现但客观有答案的题型
- **已支持题型准确率 supported_answer_accuracy**：26.79%（15/56）
- **能力覆盖 capability_coverage**：93.33%（56/60）（静态：客观可答样本中当前已支持题型的比例）
- **作答覆盖 answered_coverage**：25.00%（15/60）（客观可答样本中实际给出通过校验答案的比例）
- **诚实弃权（不可判定+主观）**：N/A；不可判定子集 N/A，主观子集 N/A
- **有答案未实现而安全拒答（能力缺口，不计入诚实弃权）**：4 题
- **无标注客观可答样本（不计入答题分母，单独上报）**：0 题（其中作答 0）
- **验证结果计数**：误放 false_accept=0，误杀 false_reject=0，未验证受控弃权 unverified=0，越权作答 over_answer=0

注：旧 `answer_accuracy`/`abstention_accuracy` 为 R2b 兼容口径（分母只含标注 solved；弃权集混入有答案未实现样本），仅供历史对照；JSON 输出中 `primary_*` / `capability` 字段为 R3a 主口径，报告一律优先读取。

## 视觉内容指标（image_only，全部注明分母）

- **stem_exact_match**：0.0000（分母 60）
- **stem_cer**：0.0000（分母 60）
- **option_label_recall**：N/A（分母 0）
- **option_text_accuracy**：N/A（分母 0）
- **option_text_cer_mean**：N/A（分母 0）
- **option_region_iou_mean**：N/A（分母 0），按题取均值；全部金标准选项框参与，漏框按 0 计入每题分母
- **option_region_iou_pooled**：N/A（分母 0），逐题金标框数加权的框级均值 Σ(n_i·mean_i)/Σn_i（R4f 修正；旧口径 Σmean_i/Σn_i 已废弃）
- **diagram_region_recall**：N/A（分母 0）
- **diagram_region_iou_mean**：N/A（分母 0），IoU≥0.5 记命中；漏检按 0 计入分母
- **diagram_region_iou_pooled**：N/A（分母 0），同 option pooled 修正口径
- **table_cell_accuracy**：N/A（分母 0）
- **region_detection_failure_rate**：0.0000（分母 60）

## 失败归因分布（按最上游失效环节）

- solver_error：33
- unsupported_skill：12

## 逐题验证结果（verification_outcome，与 root_cause 正交）

- no_answer：45
- correct_accept：15

## 根因分布（root_cause：最初失效位置）

- solver_coverage：25
- none：15
- router：8
- solver_data_reference：6
- capability_gap：4
- data_extraction：1
- word_problem_parse：1

## 各题型详情

| 题型 | 答案准确率 | 可答题覆盖率 | 选择性准确率 | 路由准确率 | 平均耗时(ms) | p95(ms) | unsupported | failed | 主要失败原因 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| data_analysis | 12.50% | 12.50% | 100.00% | N/A | 5736.9 | 12992.9 | 0 | 7 | 资料分析计划构建失败: operation_unsupported {"question": "问：2021 年该市 GDP 约为多少亿元？"}×1, 资料分析计划构建失败: target_not_found {"why": "增长量计算需要现期值与增长率证据，或两个可定位时点"}×1, 资料分析计划构建失败: ambiguous_reference {"cols": ["GDP(亿元)", "财政收入(亿元)", "社会零售(亿元)"]}×1, 资料分析计划构建失败: operation_unsupported {"question": "问：2021—2023 年社会零售总额的年均值是多少亿元？"}×1, 资料分析计划构建失败: time_pair_incomplete {"pair": ["2022", "2023"]}×1, 资料分析计划构建失败: target_not_found {"why": "基期/现期计算需要现期(基期)值与增长率证据"}×1, 资料分析输入缺少表格/统计图数据区域（不做无证据作答）×1 |
| figure_reasoning | 0.00% | 0.00% | 0.00% | N/A | 0.2 | 0.3 | 0 | 7 | 图形推理需要题目截图：请用 --image 传入题图（纯文本无法还原图形）×7 |
| language_reasoning | 0.00% | 0.00% | 0.00% | N/A | 0.8 | 2.8 | 6 | 0 | unsupported_skill×6 |
| logical_reasoning | 18.18% | 18.18% | 100.00% | N/A | 83257.6 | 185814.8 | 0 | 9 | 命题判定不唯一（0 命中）: [{'label': 'A', 'verdict': 'False'}, {'label': 'B', 'verdict': 'False'}, {'label': 'C', 'verdict': 'False'}, {'label': 'D', 'verdict': 'False'}]×5, 命题判定不唯一（0 命中）: [{'label': 'A', 'verdict': 'None'}, {'label': 'B', 'verdict': 'None'}, {'label': 'C', 'verdict': 'None'}, {'label': 'D', 'verdict': 'None'}]×1, 未能从题干解析出任何比较约束×1, 命题判定不唯一（0 命中）: [{'label': 'A', 'verdict': 'False'}, {'label': 'B', 'verdict': 'None'}, {'label': 'C', 'verdict': 'False'}, {'label': 'D', 'verdict': 'False'}]×1, 未能识别出至少两个比较对象（可用 meta.logic_entities 显式指定）×1 |
| number_series | 100.00% | 100.00% | 100.00% | N/A | 2.2 | 3.5 | 0 | 0 | - |
| numeric_calculation | 22.22% | 22.22% | 100.00% | N/A | 104471.5 | 224768.2 | 0 | 7 | 结构化规划未成立: plan_unbound×5, 结构化规划未成立: llm_error_retry: 模型调用重试后仍失败: The read operation timed out×2 |
| personality | 0.00% | 0.00% | 0.00% | N/A | 2.2 | 3.3 | 4 | 0 | unsupported_skill×4 |
| verbal_understanding | 37.50% | 37.50% | 100.00% | N/A | 84138.3 | 186013.6 | 2 | 3 | unsupported_skill×2, 言语理解规则核无法可靠判定: no_blank {}×1, 言语理解规则核无法可靠判定: argument_unresolved {"outcomes": {"A": "neutral", "B": "neutral", "C": "neutral", "D": "neutral"}, "entailed": false}×1, 言语理解规则核无法可靠判定: multi_blank {"n": 2}×1 |
