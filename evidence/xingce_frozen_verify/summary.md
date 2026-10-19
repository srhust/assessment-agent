# 评测报告 · split=xingce_test · eval_mode=oracle_solver

- 样本数：87
- 输入模式：oracle_solver（oracle_solver=标注题型+标准题干；auto_router=标准题干+自动路由；image_only=仅原始截图）

## 核心指标（legacy · R2b expected_status 口径，仅供历史对照；主指标见下方 answerability 口径块）

- **答案准确率（legacy）**：22.08%（77 道可答题中答对 17 道）
- **可答题覆盖率（legacy）**：24.68%（19/77）
- **选择性准确率**：89.47%（通过验证的答案中正确的比例）
- **路由准确率**：N/A（oracle_solver 模式直接使用标注题型）

- **拒答准确率**：90.00%（9/10）
- 耗时：总 1.84s，平均 21.1 ms，p50 0.6 ms，p95 107.7 ms

## 核心指标（主口径 · answerability 双维度）——客观可答性与能力口径（R2c/R3a，分母=所有有唯一客观答案样本，含未实现题型）

- **总体答题准确率 overall_answer_accuracy**：22.08%（17/77）；分母含当前 Skill 未实现但客观有答案的题型
- **已支持题型准确率 supported_answer_accuracy**：22.08%（17/77）
- **能力覆盖 capability_coverage**：100.00%（77/77）（静态：客观可答样本中当前已支持题型的比例）
- **作答覆盖 answered_coverage**：24.68%（19/77）（客观可答样本中实际给出通过校验答案的比例）
- **诚实弃权（不可判定+主观）**：100.00%（10/10）；不可判定子集 100.00%（6/6），主观子集 100.00%（4/4）
- **有答案未实现而安全拒答（能力缺口，不计入诚实弃权）**：0 题
- **无标注客观可答样本（不计入答题分母，单独上报）**：0 题（其中作答 0）
- **验证结果计数**：误放 false_accept=2，误杀 false_reject=0，未验证受控弃权 unverified=1，越权作答 over_answer=0

注：旧 `answer_accuracy`/`abstention_accuracy` 为 R2b 兼容口径（分母只含标注 solved；弃权集混入有答案未实现样本），仅供历史对照；JSON 输出中 `primary_*` / `capability` 字段为 R3a 主口径，报告一律优先读取。

## 视觉内容指标（image_only，全部注明分母）

- **stem_exact_match**：0.0000（分母 87）
- **stem_cer**：0.0000（分母 87）
- **option_label_recall**：N/A（分母 0）
- **option_text_accuracy**：N/A（分母 0）
- **option_text_cer_mean**：N/A（分母 0）
- **option_region_iou_mean**：N/A（分母 0），按题取均值；全部金标准选项框参与，漏框按 0 计入每题分母
- **option_region_iou_pooled**：N/A（分母 0），逐题金标框数加权的框级均值 Σ(n_i·mean_i)/Σn_i（R4f 修正；旧口径 Σmean_i/Σn_i 已废弃）
- **diagram_region_recall**：N/A（分母 0）
- **diagram_region_iou_mean**：N/A（分母 0），IoU≥0.5 记命中；漏检按 0 计入分母
- **diagram_region_iou_pooled**：N/A（分母 0），同 option pooled 修正口径
- **table_cell_accuracy**：N/A（分母 0）
- **region_detection_failure_rate**：0.0000（分母 87）

## 失败归因分布（按最上游失效环节）

- solver_error：55
- unsupported_skill：4
- verifier_false_accept：2

## 逐题验证结果（verification_outcome，与 root_cause 正交）

- no_answer：56
- correct_accept：17
- correct_abstention：9
- false_accept：2
- evidence_insufficient_correct_withheld：1
- unverified_answerable：1
- unverified_abstention：1

## 根因分布（root_cause：最初失效位置）

- solver_coverage：32
- none：27
- solver_data_reference：14
- word_problem_parse：7
- router：4
- solver_arithmetic：2
- evidence_insufficient_withheld：1

## 各题型详情

| 题型 | 答案准确率 | 可答题覆盖率 | 选择性准确率 | 路由准确率 | 平均耗时(ms) | p95(ms) | unsupported | failed | 主要失败原因 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| data_analysis | 12.50% | 12.50% | 100.00% | N/A | 0.9 | 1.9 | 0 | 15 | 资料分析计划构建失败: time_pair_incomplete {"pair": ["Q3", "Q4"]}×1, 资料分析计划构建失败: operation_unsupported {"question": "依据下表数据回答本题。第三季度销售额约占全年销售额的："}×1, 资料分析计划构建失败: operation_unsupported {"operation": "growth_amount"}×1, 资料分析计划构建失败: operation_unsupported {"question": "依据下表数据回答本题。2021 年 B 馆参观人数是多少万人？"}×1, 资料分析计划构建失败: time_pair_incomplete {"pair": ["2021", "2022"]}×1, 资料分析计划构建失败: target_not_found {"why": "倍数关系需要两个明确对象", "rows": 1}×1, 资料分析计划构建失败: operation_unsupported {"question": "根据以下资料回答问题。2022 年健身器材出口额约为多少亿元？"}×1, 资料分析计划构建失败: ambiguous_reference {"cols": ["出口额(亿元)", "同比增长率"]}×1, 资料分析计划构建失败: operation_unsupported {"question": "根据以下资料回答问题。2023 年球类器材出口额比 2021 年约增长了："}×1, 资料分析计划构建失败: operation_unsupported {"question": "2020 年，全国服装出口额比 2019 年："}×1, 资料分析计划构建失败: operation_unsupported {"question": "2021 年，全国针织、梭织服装及衣着附件总出口额约占纺织品服装出口总额的："}×1, 资料分析计划构建失败: time_pair_incomplete {"pair": ["2020", "2021"]}×1, 资料分析计划构建失败: ambiguous_reference {"cols": ["出口额(亿美元)", "同比增长率"]}×1, 资料分析计划构建失败: operation_unsupported {"question": "2020 年，中国对“一带一路”沿线国家纺织品服装贸易顺差额约为多少亿美元？"}×1 |
| figure_reasoning | 45.45% | 45.45% | 100.00% | N/A | 67.3 | 163.8 | 0 | 6 | 已有规律无法唯一匹配选项：图形个数外推为 1，但有 4 个选项匹配（无法唯一确定）; 边数外推为 7，但有 4 个选项匹配; 对称性应为 垂直=True 水平=True，但有 4 个选项匹配; 墨迹面积外推到 379px，多个选项接近，无法唯一确定；变换通道：变换规律不可唯一判定×1, wrong_answer×1, 已有规律无法唯一匹配选项：图形个数外推为 1，但有 4 个选项匹配（无法唯一确定）; 边数外推为 4，但有 4 个选项匹配; 质心按等步长平移到 (0.49, 0.48)，多个选项接近; 对称性应为 垂直=True 水平=True，但有 4 个选项匹配；变换通道：变换规律不可唯一判定×1, 已有规律无法唯一匹配选项：未找到能同时解释所有完整行的叠加规则；变换通道：变换规律不可唯一判定×1, 题干可见图形只有 2 个，至少需要 3 个才能判断规律；变换通道：{'rotate_flip_seq': {'A': 0.0, 'B': 0.599295, 'C': 0.064935, 'D': 0.0}}×1, 选项区只检测到 1 个图形，至少需要 2 个×1 |
| language_reasoning | 0.00% | 0.00% | 0.00% | N/A | 1.8 | 7.7 | 0 | 6 | wrong_answer×2, 语言推理规则核无法可靠判定: no_matching_relation {"signature": ["FUNCTION"], "verdicts": {"A": "unknown_pair", "B": "unknown_pair", "C": "unknown_pair", "D": "unknown_pair"}}×1, 语言推理规则核无法可靠判定: no_matching_relation {"signature": ["GENUS_DOWN"], "verdicts": {"A": "unknown_pair", "B": "unknown_pair", "C": "unknown_pair", "D": "unknown_pair"}}×1, 语言推理规则核无法可靠判定: no_matching_relation {"signature": ["ANT"], "verdicts": {"A": "unknown_pair", "B": "unknown_pair", "C": "unknown_pair", "D": "unknown_pair"}}×1, 语言推理规则核无法可靠判定: no_matching_word_relation {"verdicts": {"A": "unknown_pair", "B": "mismatch", "C": "unknown_pair", "D": "unknown_pair"}}×1, 语言推理规则核无法可靠判定: target_not_in_lexicon {"word": "繁荣"}×1, 语言推理规则核无法可靠判定: no_lexicon_evidence {"stem_pair": ["下雨", "地面湿"]}×1 |
| logical_reasoning | 20.00% | 20.00% | 100.00% | N/A | 27.2 | 139.1 | 0 | 8 | 未能识别出至少两个比较对象（可用 meta.logic_entities 显式指定）×6, 未能从题干解析出任何比较约束×2 |
| number_series | 45.45% | 45.45% | 100.00% | N/A | 0.6 | 1.3 | 0 | 7 | 数列求解失败：没有匹配到任何已知规律×6 |
| numeric_calculation | 27.27% | 45.45% | 60.00% | N/A | 57.4 | 307.6 | 0 | 7 | 未能从题干中解析出算术表达式×5, wrong_answer×2, 计算结果与所有选项均不匹配×1 |
| personality | 0.00% | 0.00% | 0.00% | N/A | 2.0 | 5.3 | 4 | 0 | - |
| verbal_understanding | 0.00% | 0.00% | 0.00% | N/A | 2.0 | 9.9 | 4 | 8 | unsupported_skill×4, 言语理解规则核无法可靠判定: no_collocation_hit {"winners": []}×3, 言语理解规则核无法可靠判定: no_covering_option {"coverage": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}}×2, 言语理解规则核无法可靠判定: argument_unresolved {"outcomes": {"A": "neutral", "B": "neutral", "C": "neutral", "D": "neutral"}, "entailed": false}×2 |
