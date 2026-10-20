# 评测报告 · split=xingce_dev · eval_mode=image_only

- 样本数：12
- 输入模式：image_only（oracle_solver=标注题型+标准题干；auto_router=标准题干+自动路由；image_only=仅原始截图）

## 核心指标（legacy · R2b expected_status 口径，仅供历史对照；主指标见下方 answerability 口径块）

- **答案准确率（legacy）**：0.00%（12 道可答题中答对 0 道）
- **可答题覆盖率（legacy）**：0.00%（0/12）
- **选择性准确率**：0.00%（通过验证的答案中正确的比例）
- **路由准确率**：90.00%（9/10）

- **拒答准确率**：0.00%（0/0）
- 耗时：总 277.21s，平均 23100.9 ms，p50 16154.8 ms，p95 46413.8 ms

## 核心指标（主口径 · answerability 双维度）——客观可答性与能力口径（R2c/R3a，分母=所有有唯一客观答案样本，含未实现题型）

- **总体答题准确率 overall_answer_accuracy**：0.00%（0/12）；分母含当前 Skill 未实现但客观有答案的题型
- **已支持题型准确率 supported_answer_accuracy**：0.00%（0/12）
- **能力覆盖 capability_coverage**：100.00%（12/12）（静态：客观可答样本中当前已支持题型的比例）
- **作答覆盖 answered_coverage**：0.00%（0/12）（客观可答样本中实际给出通过校验答案的比例）
- **诚实弃权（不可判定+主观）**：N/A；不可判定子集 N/A，主观子集 N/A
- **有答案未实现而安全拒答（能力缺口，不计入诚实弃权）**：0 题
- **无标注客观可答样本（不计入答题分母，单独上报）**：0 题（其中作答 0）
- **验证结果计数**：误放 false_accept=0，误杀 false_reject=0，未验证受控弃权 unverified=0，越权作答 over_answer=0

注：旧 `answer_accuracy`/`abstention_accuracy` 为 R2b 兼容口径（分母只含标注 solved；弃权集混入有答案未实现样本），仅供历史对照；JSON 输出中 `primary_*` / `capability` 字段为 R3a 主口径，报告一律优先读取。

## E2E（image_only）模块级归因指标

- 仅截图样本数：12
- **Extraction Accuracy**（题干精确解析率）：25.00%；题干 CER 见 vision 段
- **Region IoU**（题干矩阵，漏检记 0）：N/A（分母 0）
- **Option Mapping Accuracy**（标号序列完全一致）：100.00%
- **Image-only Answer Accuracy（可答题口径，R2b）**：0.00%；分母=有标准答案样本
- **Image-only Answerable Coverage**：0.00%
- **Abstention Accuracy（应拒答口径）**：N/A；分母=标注 unsupported/failed 样本
- 旧混合口径 E2E（含拒答样本，仅历史对照）：0.00%

## 视觉内容指标（image_only，全部注明分母）

- **stem_exact_match**：0.2500（分母 12）
- **stem_cer**：0.3652（分母 12）
- **option_label_recall**：0.8333（分母 12）
- **option_text_accuracy**：0.1750（分母 10）
- **option_text_cer_mean**：0.3474（分母 10）
- **option_region_iou_mean**：0.4464（分母 12），按题取均值；全部金标准选项框参与，漏框按 0 计入每题分母
- **option_region_iou_pooled**：0.4464（分母 48），逐题金标框数加权的框级均值 Σ(n_i·mean_i)/Σn_i（R4f 修正；旧口径 Σmean_i/Σn_i 已废弃）
- **diagram_region_recall**：0.0000（分母 2）
- **diagram_region_iou_mean**：0.0000（分母 2），IoU≥0.5 记命中；漏检按 0 计入分母
- **diagram_region_iou_pooled**：0.0000（分母 2），同 option pooled 修正口径
- **table_cell_accuracy**：N/A（分母 0）
- **region_detection_failure_rate**：0.1667（分母 12）

## 失败归因分布（按最上游失效环节）

- region_missing_or_shifted：10
- model_api_error：2

## 逐题验证结果（verification_outcome，与 root_cause 正交）

- no_answer：12

## 根因分布（root_cause：最初失效位置）

- region_crop：8
- data_extraction：2
- pipeline：2

## 各题型详情

| 题型 | 答案准确率 | 可答题覆盖率 | 选择性准确率 | 路由准确率 | 平均耗时(ms) | p95(ms) | unsupported | failed | 主要失败原因 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| data_analysis | 0.00% | 0.00% | 0.00% | 100.00% | 19980.8 | 21083.9 | 0 | 2 | 资料分析输入缺少表格/统计图数据区域（不做无证据作答）×2 |
| figure_reasoning | 0.00% | 0.00% | 0.00% | 0.00% | 39424.9 | 39607.2 | 0 | 2 | pipeline_error: 视觉结构化解析失败：视觉解析调用失败: 模型输出不是合法 JSON: Expecting ',' delimiter: line 13 column 114 (char 688)×1, pipeline_error: 视觉结构化解析失败：视觉解析调用失败: 模型返回为空，无法解析 JSON×1 |
| language_reasoning | 0.00% | 0.00% | 0.00% | 0.00% | 9010.8 | 9010.8 | 1 | 0 | unsupported_skill×1 |
| logical_reasoning | 0.00% | 0.00% | 0.00% | 100.00% | 11515.9 | 13350.5 | 0 | 2 | 未能识别出至少两个比较对象（可用 meta.logic_entities 显式指定）×2 |
| number_series | 0.00% | 0.00% | 0.00% | 100.00% | 8624.0 | 8624.0 | 0 | 1 | 数列求解失败：没有匹配到任何已知规律×1 |
| numeric_calculation | 0.00% | 0.00% | 0.00% | 100.00% | 12126.7 | 12233.7 | 0 | 2 | 计算结果与所有选项均不匹配×2 |
| verbal_understanding | 0.00% | 0.00% | 0.00% | 100.00% | 46740.1 | 49676.3 | 0 | 2 | 言语理解规则核无法可靠判定: no_covering_option {"coverage": {"A": 0.11, "B": 0.89, "C": 0.0, "D": 0.32}}×1, 言语理解规则核无法可靠判定: no_collocation_hit {"winners": []}×1 |
