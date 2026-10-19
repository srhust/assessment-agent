# 评测报告 · split=figshots_dev · eval_mode=image_only

- 样本数：15
- 输入模式：image_only（oracle_solver=标注题型+标准题干；auto_router=标准题干+自动路由；image_only=仅原始截图）

## 核心指标（legacy · R2b expected_status 口径，仅供历史对照；主指标见下方 answerability 口径块）

- **答案准确率（legacy）**：0.00%（13 道可答题中答对 0 道）
- **可答题覆盖率（legacy）**：0.00%（0/13）
- **选择性准确率**：0.00%（通过验证的答案中正确的比例）
- **路由准确率**：100.00%（15/15）

- **拒答准确率**：100.00%（2/2）
- 耗时：总 699.59s，平均 46638.9 ms，p50 41321.7 ms，p95 70318.8 ms

## 核心指标（主口径 · answerability 双维度）——客观可答性与能力口径（R2c/R3a，分母=所有有唯一客观答案样本，含未实现题型）

- **总体答题准确率 overall_answer_accuracy**：0.00%（0/13）；分母含当前 Skill 未实现但客观有答案的题型
- **已支持题型准确率 supported_answer_accuracy**：0.00%（0/13）
- **能力覆盖 capability_coverage**：100.00%（13/13）（静态：客观可答样本中当前已支持题型的比例）
- **作答覆盖 answered_coverage**：0.00%（0/13）（客观可答样本中实际给出通过校验答案的比例）
- **诚实弃权（不可判定+主观）**：100.00%（2/2）；不可判定子集 100.00%（2/2），主观子集 N/A
- **有答案未实现而安全拒答（能力缺口，不计入诚实弃权）**：0 题
- **无标注客观可答样本（不计入答题分母，单独上报）**：0 题（其中作答 0）
- **验证结果计数**：误放 false_accept=0，误杀 false_reject=0，未验证受控弃权 unverified=1，越权作答 over_answer=0

注：旧 `answer_accuracy`/`abstention_accuracy` 为 R2b 兼容口径（分母只含标注 solved；弃权集混入有答案未实现样本），仅供历史对照；JSON 输出中 `primary_*` / `capability` 字段为 R3a 主口径，报告一律优先读取。

## E2E（image_only）模块级归因指标

- 仅截图样本数：15
- **Extraction Accuracy**（题干精确解析率）：0.00%；题干 CER 见 vision 段
- **Region IoU**（题干矩阵，漏检记 0）：52.57%（分母 15）
- **Option Mapping Accuracy**（标号序列完全一致）：100.00%
- **Image-only Answer Accuracy（可答题口径，R2b）**：0.00%；分母=有标准答案样本
- **Image-only Answerable Coverage**：0.00%
- **Abstention Accuracy（应拒答口径）**：100.00%；分母=标注 unsupported/failed 样本
- 旧混合口径 E2E（含拒答样本，仅历史对照）：13.33%

## 视觉内容指标（image_only，全部注明分母）

- **stem_exact_match**：0.0000（分母 15）
- **stem_cer**：0.2453（分母 15）
- **option_label_recall**：1.0000（分母 15）
- **option_text_accuracy**：0.0000（分母 15）
- **option_text_cer_mean**：1.0000（分母 15）
- **option_region_iou_mean**：0.4256（分母 15），按题取均值；全部金标准选项框参与，漏框按 0 计入每题分母
- **option_region_iou_pooled**：0.4226（分母 48），逐题金标框数加权的框级均值 Σ(n_i·mean_i)/Σn_i（R4f 修正；旧口径 Σmean_i/Σn_i 已废弃）
- **diagram_region_recall**：0.5333（分母 15）
- **diagram_region_iou_mean**：0.5257（分母 15），IoU≥0.5 记命中；漏检按 0 计入分母
- **diagram_region_iou_pooled**：0.5257（分母 15），同 option pooled 修正口径
- **table_cell_accuracy**：N/A（分母 0）
- **region_detection_failure_rate**：0.4667（分母 15）

## 失败归因分布（按最上游失效环节）

- region_missing_or_shifted：9
- extraction_text_error：4

## 逐题验证结果（verification_outcome，与 root_cause 正交）

- no_answer：12
- correct_abstention：2
- unverified_answerable：1

## 根因分布（root_cause：最初失效位置）

- region_crop：8
- extraction_ocr：4
- none：2
- solver_rule_selection：1

## 各题型详情

| 题型 | 答案准确率 | 可答题覆盖率 | 选择性准确率 | 路由准确率 | 平均耗时(ms) | p95(ms) | unsupported | failed | 主要失败原因 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| figure_reasoning | 0.00% | 0.00% | 0.00% | 100.00% | 46638.9 | 70318.8 | 0 | 14 | 题干可见图形只有 1 个，至少需要 3 个才能判断规律；变换通道：空格不在行末或已知格不足，变换通道不适用（序列）×3, 题干可见图形只有 2 个，至少需要 3 个才能判断规律；变换通道：空格不在行末或已知格不足，变换通道不适用（序列）×2, 空白格不在行末，当前只支持“求下一格”的序列题；变换通道：空格不在行末或已知格不足，变换通道不适用（序列）×2, 已有规律无法唯一匹配选项：未找到能同时解释所有完整行的叠加规则；变换通道：变换规律不可唯一判定×2, wrong_answer×1, 已有规律无法唯一匹配选项：图形个数外推为 1，但有 3 个选项匹配（无法唯一确定）; 边数外推为 4，但有 3 个选项匹配; 对称性应为 垂直=True 水平=True，但有 3 个选项匹配；变换通道：空格不在行末或已知格不足，变换通道不适用（序列）×1, 已有规律无法唯一匹配选项：未找到能同时解释所有完整行的叠加规则；变换通道：{'rotate_flip_grid_row': {'A': 1.0, 'B': 1.0, 'C': 1.0, 'D': 1.0}, 'rotate_flip_grid_col': {'A': 1.0, 'B': 1.0, 'C': 1.0, 'D': 1.0}}×1, 已有规律无法唯一匹配选项：未找到能同时解释所有完整行的叠加规则；变换通道：{'rotate_flip_grid_row': {'A': 0.997204, 'B': 0.31781, 'C': 1.0}}×1 |
