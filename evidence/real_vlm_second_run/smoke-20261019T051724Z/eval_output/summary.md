# 评测报告 · split=figshots_dev · eval_mode=image_only

- 样本数：3
- 输入模式：image_only（oracle_solver=标注题型+标准题干；auto_router=标准题干+自动路由；image_only=仅原始截图）

## 核心指标（legacy · R2b expected_status 口径，仅供历史对照；主指标见下方 answerability 口径块）

- **答案准确率（legacy）**：0.00%（3 道可答题中答对 0 道）
- **可答题覆盖率（legacy）**：0.00%（0/3）
- **选择性准确率**：0.00%（通过验证的答案中正确的比例）
- **路由准确率**：100.00%（3/3）

- **拒答准确率**：0.00%（0/0）
- 耗时：总 122.71s，平均 40903.8 ms，p50 41017.5 ms，p95 43240.1 ms

## 核心指标（主口径 · answerability 双维度）——客观可答性与能力口径（R2c/R3a，分母=所有有唯一客观答案样本，含未实现题型）

- **总体答题准确率 overall_answer_accuracy**：0.00%（0/3）；分母含当前 Skill 未实现但客观有答案的题型
- **已支持题型准确率 supported_answer_accuracy**：0.00%（0/3）
- **能力覆盖 capability_coverage**：100.00%（3/3）（静态：客观可答样本中当前已支持题型的比例）
- **作答覆盖 answered_coverage**：0.00%（0/3）（客观可答样本中实际给出通过校验答案的比例）
- **诚实弃权（不可判定+主观）**：N/A；不可判定子集 N/A，主观子集 N/A
- **有答案未实现而安全拒答（能力缺口，不计入诚实弃权）**：0 题
- **无标注客观可答样本（不计入答题分母，单独上报）**：0 题（其中作答 0）
- **验证结果计数**：误放 false_accept=0，误杀 false_reject=0，未验证受控弃权 unverified=1，越权作答 over_answer=0

注：旧 `answer_accuracy`/`abstention_accuracy` 为 R2b 兼容口径（分母只含标注 solved；弃权集混入有答案未实现样本），仅供历史对照；JSON 输出中 `primary_*` / `capability` 字段为 R3a 主口径，报告一律优先读取。

## E2E（image_only）模块级归因指标

- 仅截图样本数：3
- **Extraction Accuracy**（题干精确解析率）：0.00%；题干 CER 见 vision 段
- **Region IoU**（题干矩阵，漏检记 0）：74.31%（分母 3）
- **Option Mapping Accuracy**（标号序列完全一致）：100.00%
- **Image-only Answer Accuracy（可答题口径，R2b）**：0.00%；分母=有标准答案样本
- **Image-only Answerable Coverage**：0.00%
- **Abstention Accuracy（应拒答口径）**：N/A；分母=标注 unsupported/failed 样本
- 旧混合口径 E2E（含拒答样本，仅历史对照）：0.00%

## 视觉内容指标（image_only，全部注明分母）

- **stem_exact_match**：0.0000（分母 3）
- **stem_cer**：0.1429（分母 3）
- **option_label_recall**：1.0000（分母 3）
- **option_text_accuracy**：0.0000（分母 3）
- **option_text_cer_mean**：1.0000（分母 3）
- **option_region_iou_mean**：0.3977（分母 3），按题取均值；全部金标准选项框参与，漏框按 0 计入每题分母
- **option_region_iou_pooled**：0.0994（分母 12）
- **diagram_region_recall**：1.0000（分母 3）
- **diagram_region_iou_mean**：0.7431（分母 3），IoU≥0.5 记命中；漏检按 0 计入分母
- **table_cell_accuracy**：N/A（分母 0）
- **region_detection_failure_rate**：0.0000（分母 3）

## 失败归因分布（按最上游失效环节）

- extraction_text_error：2
- region_missing_or_shifted：1

## 逐题验证结果（verification_outcome，与 root_cause 正交）

- no_answer：2
- unverified_answerable：1

## 根因分布（root_cause：最初失效位置）

- extraction_ocr：2
- solver_rule_selection：1

## 各题型详情

| 题型 | 答案准确率 | 可答题覆盖率 | 选择性准确率 | 路由准确率 | 平均耗时(ms) | p95(ms) | unsupported | failed | 主要失败原因 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| figure_reasoning | 0.00% | 0.00% | 0.00% | 100.00% | 40903.8 | 43240.1 | 0 | 2 | wrong_answer×1, 已有规律无法唯一匹配选项：未找到能同时解释所有完整行的叠加规则；变换通道：{'rotate_flip_grid_row': {'A': 0.810474, 'B': 0.928034, 'C': 1.0, 'D': 0.919608}, 'rotate_flip_grid_col': {'A': 0.810474, 'B': 0.928034, 'C': 1.0, 'D': 0.919608}}×1, 已有规律无法唯一匹配选项：未找到能同时解释所有完整行的叠加规则；变换通道：变换规律不可唯一判定×1 |
