# 数据集目录

```
data/
├── train/   训练集（可用于提示词示例、蒸馏样本筛选）
├── dev/     验证集（调参、回归）
├── test/    测试集（只用于最终评估，不参与调参）
└── figure/images/   图形推理题图（由 scripts/generate_figure_dataset.py 生成）
```

每个 split 下按题型分文件：`<skill_id>.jsonl`（`mvp.jsonl` 是多个文字题型的合集），
一行一条样本：

```json
{"id": "num-test-001", "skill": "numeric_calculation", "stem": "计算 12.5 × 8 - 36 ÷ 4", "options": {"A": "90", "B": "91", "C": "92", "D": "93"}, "answer": "B", "tags": ["四则运算"]}
{"id": "fig-test-count", "skill": "figure_reasoning", "stem": "观察图形的变化规律，选出括号处应填入的图形。", "image": "figure/images/count_test.png", "answer": "B", "expected_status": "solved"}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 样本唯一 ID |
| `skill` | 是 | 期望题型（评测时强制路由到该 Skill） |
| `stem` | 是 | 题干文本 |
| `options` | 否 | `{"A": "..."}` 或 `["...", "..."]`；无选项表示填空题 |
| `answer` | 否 | 期望答案（选项标号或数值文本）；`unsupported` / `failed` 样本可为空 |
| `expected_status` | 否 | `solved`（默认）/ `unsupported`（题型未实现）/ `failed`（必须明确失败，不猜答案） |
| `image` | 否 | 题图路径，相对 `data/` 目录；图形推理必填 |
| `grid` | 否 | 显式网格 `[行, 列]`：整图按该行列等分，空白格也会被识别；用于位置/叠加类图形题 |
| `tags` | 否 | 标签，便于失败归因 |
| `answerability` | 否（R2c） | 客观可答性显式标注：`has_unique_answer` / `undecidable` / `subjective` / `objective_unlabeled`；**缺省由 loader 派生**（有 `answer`→客观可答；`expected_status∈{undecidable,failed}`→信息不足；性格/自评→主观；无答案且非上述→客观可答未标注），**已有数据集无需回填**，冻结集不受影响 |
| `capability_status` | 否（R2c） | 当前支持声明 `supported` / `unsupported`；**R3a 起缺省由 `pipeline.registry.supported()` 实际注册能力派生**（当前：numeric_calculation / number_series / logical_reasoning / figure_reasoning / data_analysis / verbal_understanding），不再有手写名单 |

## 独立基准 `benchmark_real/`（R2b/R3a）

- `dev/`（35 样本，R3a 扩充资料分析后可重生成）与 `holdout/`（16 行 / 客观分母 14，**冻结**）：
  `independent.jsonl` + `images/` + `regions/`（选项图形裁剪、统计图柱形裁剪）。
- `regions.table = {"bbox", "cells"}`：表格金标（cells 为字符串二维数组，行 0 起为表头，
  支持双层表头）；**dev-only** 新增 `regions.text_regions`（含 `id="chart"` 的图上标注文本）与
  `regions.chart = {"bbox", "text", "crop_path"}`（统计图金标 + 柱状图裁剪，供像素比例复核）。
- 资料分析样本纪律：有唯一答案的表格/标注柱状图 `expected_status=solved`；
  "真无唯一解"样本（缺数、OCR 歧义、无标注柱状图、同名单位列歧义）`answer=null` +
  `expected_status=failed`——期望系统**如实失败**，禁止为其编写特殊规则。
- 生成器完全确定性：`source.created` 按 split 固定（holdout 恒为 2026-10-17，重生成字节级不变，
  由 `tests/test_r2b_benchmark.py` 的 sha256 断言守护）。**禁止根据冻结 Holdout 的具体答案修改规则。**

## 言语理解基准 `benchmark_verbal/`（R3b）

- `dev/`（20 题，允许在此调规则）与 `blind/`（12 题，**一次生成即冻结**，manifest 记录 sha256）：
  `verbal.jsonl` 一行一题；字段与上表一致，`group` ∈ cloze / main_idea / inference / argument，
  `tags` 标注语言特征（否定、转折、双重否定、量词、条件、近义并列、信息不足、真歧义、长短文）。
- 期望状态纪律：`expected_status` 按**语言学标准在模型运行之前**确定——
  `solved`=设计上属离线规则子集且有人工金标准；`failed`=构造性难点（成语色彩、重表述主旨、
  肯后式推断、权衡型论证等），期望系统如实失败；`undecidable`=真歧义/信息不足，`answer=null`，
  系统给出任何通过校验的答案都算越权。**禁止按模型是否解对筛题，禁止依据盲集结果回改题目或规则。**
- 评测：`eval --split verbal_dev|verbal_blind`；指标聚合见 `scripts/run_r3b_report.py`
  （Verbal Accuracy、Selective、False Accept、Abstention、Evidence Citation Validity、Latency 等）。

## 图形推理子集

- 图片：`data/figure/images/*.png`，由 `scripts/generate_figure_dataset.py` **可复现生成**
  （`python scripts/generate_figure_dataset.py` 会重写 JSONL 与图片）。
- 布局约定：白底黑图；题干区在上（序列题一行 3 格 + 空白末格，九宫格 3 行 × 3 格），
  选项区在下方独立一行、从左到右为 A、B、C…。
- `grid` 字段：等分网格下才能可靠定位单元格，因此位置类（平移）与叠加类题目显式给出；
  其余题目走自动推断，用来覆盖“自动布局解析”这条路径。
- `fig-test-unresolved` 是**合规性样本**：图形个数为 1、3、2，任何受支持的规律都无法
  唯一确定答案，期望系统返回 `failed` 而不是猜一个选项。

## 数据边界

- 当前样本覆盖第一阶段已实现的题型（数字计算 / 数字推理 / 逻辑推理 / 图形推理 / 资料分析 R3a /
  言语理解 R3b 的离线证据子集）。
- 未实现题型只保留少量 `expected_status=unsupported` 样本，用于验证系统如实返回
  “未实现”，而不是编造答案。
- 全部样本为自编的合成题，不含任何真实企业测评原题；请勿提交含真实题目、个人信息或
  受版权保护的整卷内容。
