# assessment-agent

面向 **北森 / SHL 类招聘测评**的练习与自测 Agent：支持文字题干与题目截图输入，
按题型调用确定性求解工具，独立校验后输出**带证据的答案与解析记录**。

> 定位：个人练习、模拟测试与解题方法研究。
> 不做真实考试作弊工具，不接入任何招聘系统，不保存他人题目与个人信息。

---

## 1. 架构设计

```
输入（文本 / 截图）
      │
      ▼
┌───────────────┐   截图时调用视觉模型，只抄写可见内容
│   Extraction  │   extraction.json（题干 + 选项 + 置信度 + 警告）
│  +区域(R2a)   │   VISION_PARSER=true：schema v2 区域坐标 + 题干矩阵/选项裁剪像素证据
└───────┬───────┘
        ▼
┌───────────────┐   规则打分优先，LLM 兜底；可显式指定题型
│    Router     │   route.json（skill / 来源 / 得分 / 是否歧义）
└───────┬───────┘
        ▼
┌───────────────┐   Skill = 题意归一 + 选工具 + 组织步骤
│    Solver     │──▶ tools: python_math (SymPy) / number_series / z3_logic / figure_tools
│  +适配层(R2a) │      图形题优先用"题干矩阵裁剪 + 选项裁剪"经 figure_adapter 喂入原六类规律引擎
└───────┬───────┘   solution.json（答案 / 步骤 / 置信度 / 工具调用轨迹）
        ▼
┌───────────────┐   结构校验 + 题型专属复核（独立重算 / 留出预测 / Z3 重建模）
│   Verifier    │   verification.json（逐条 check，可 accept / repair / reject）
└───────┬───────┘
        ▼
  runs/<run_id>/：report.md、trace.jsonl、原始题图、全部结构化记录
```

三层解耦（便于接入 DSH / MCP / 蒸馏）：

| 层 | 目录 | 职责 | 可替换点 |
| --- | --- | --- | --- |
| Skill | `src/assessment_agent/skills/` | 题意归一、选择工具、组织步骤 | 新增题型只实现 `BaseSkill` 并注册 |
| 知识库 | `knowledge/*.md` | 题型方法论、边界、常见坑 | 纯 Markdown，可按厂商框架替换 |
| 工具 | `src/assessment_agent/tools/` | 确定性计算与推理 | 每个工具可单独暴露为 MCP tool |

### 目录结构

```
assessment-agent/
├── src/assessment_agent/
│   ├── config.py          # 环境变量 / .env 配置（不在代码里存密钥）
│   ├── schemas.py         # 全局 Pydantic 数据结构
│   ├── llm.py             # OpenAI 兼容客户端（文本 + 视觉、重试、JSON 容错）
│   ├── prompts.py         # 提示词模板（与逻辑分离）
│   ├── vision.py          # 截图 -> ExtractionResult（schema v1 纯文本，兼容保留）
│   ├── vision_parser.py   # R2a：截图 -> 区域化 schema v2（坐标校验 + OpenCV/PIL 裁剪像素证据）
│   ├── router.py          # 题型路由（规则 + LLM 兜底）
│   ├── solver.py          # Skill 调度与异常归一
│   ├── verifier.py        # 独立校验（结构 + 题型复核 + 可选 LLM 交叉校验）
│   ├── pipeline.py        # 端到端编排与产物落盘
│   ├── artifacts.py       # 运行目录、trace、Markdown 报告
│   ├── text_utils.py      # 题干/选项拆分
│   ├── cli.py             # solve / eval / skills / doctor / inspect / mcp-manifest / call
│   ├── integration.py     # DSH / MCP 接入层（工具清单 + tools/call 形状调用）
│   ├── skills/            # 八个题型 Skill（4 个 MVP + 4 个声明式接口）
│   ├── tools/             # SymPy 计算、数列规律、Z3 约束、图形布局/规律引擎、图形布局适配层、
│   │                      #   figure_verify（R2c 候选代入独立复核）、word_problem（R2c 数量关系理解层）
│   ├── knowledge/         # 知识库加载器（Markdown）
│   └── evaluation/        # 数据集加载、指标（R2c 双维度口径）、评测执行器（oracle/auto/image 三模式）+ 视觉内容指标 + 失败归因（outcome×root_cause）+ mock 视觉后端
├── data/{train,dev,test}/ # 按题型分文件的 JSONL 数据集
├── data/screens/          # R2a 合成截图基准（regression）：images + regions 裁剪 + screens.jsonl 金标准
├── data/benchmark_real/   # R2b 独立泛化基准：dev + 冻结 holdout + manifest.json（sha256/防泄漏分组/来源授权）
├── knowledge/             # 八个题型的方法论笔记
├── tests/                 # pytest 单元测试（+1 真实模型用例，未配置时显式 skip）
├── runs/                  # 运行产物（gitignore）
├── results/               # 评测结果（gitignore）
├── .env.example
├── pyproject.toml
└── README.md
```

---

## 2. 八个题型与当前状态

| Skill ID | 题型 | 第一阶段 | 求解方式 |
| --- | --- | --- | --- |
| `numeric_calculation` | 数字计算 | ✅ 已实现 | 题干 → 表达式 → SymPy 精确求值 → 选项数值匹配 |
| `number_series` | 数字推理 | ✅ 已实现 | 7 类规律检测（等差/等比/差分/奇偶分组/递推/幂次偏移/常数），要求解释全部已知项 |
| `logical_reasoning` | 逻辑推理 | ✅ 已实现 | 排序比较语句 → Z3 约束 → 必然性/可能性判定 |
| `figure_reasoning` | 图形推理 | ✅ 已实现 | 网格解析 + 六类规律（个数/边数/面积/平移/对称/叠加）+ 选项匹配；失败时给出原因 |
| `verbal_understanding` | 言语理解 | 🚧 仅接口 | 返回 `unsupported`，不编造答案 |
| `language_reasoning` | 语言推理 | 🚧 仅接口 | 同上 |
| `data_analysis` | 资料分析 | 🚧 仅接口 | 同上 |
| `personality` | 性格测评解释 | 🚧 仅接口 | 同上 |

未实现题型的行为是**明确失败**：

```json
{
  "status": "unsupported",
  "skill": "verbal_understanding",
  "answer": null,
  "error": "言语理解尚未实现（第一阶段 MVP 范围之外），不提供推测性答案",
  "meta": {"stage": 2, "planned_capabilities": ["..."], "interface": "skills.verbal_understanding...solve(question, ctx) -> Solution"}
}
```

---

## 3. 快速开始

要求 Python ≥ 3.9。

```bash
cd assessment-agent
python -m venv .venv && . .venv/Scripts/activate    # Windows
pip install -r requirements.txt

copy .env.example .env         # 填入你自己的模型服务地址与密钥（.env 已被 gitignore）
```

即使**不配置任何模型**也能使用四个 MVP 题型（离线确定性模式）：

```bash
$env:PYTHONPATH="src"          # 或 pip install -e .

python -m assessment_agent doctor
python -m assessment_agent skills

# 数字计算
python -m assessment_agent solve --text "计算 (3.5 + 2.4) × 4 的结果是多少？" --options "A=23.6,B=22.6"

# 数字推理（选项可写在题干里）
python -m assessment_agent solve --text "数列 2, 5, 8, 11, ( ) 的下一项是？
A. 13
B. 14
C. 15
D. 16"

# 逻辑推理
python -m assessment_agent solve --text "甲乙丙三人参加比赛，名次各不相同。甲比乙高，乙比丙高。以下哪项一定为真？" --options "A=甲比丙矮,B=丙比甲矮,C=乙最高,D=丙最高"

# 截图输入（需要配置视觉模型）
python -m assessment_agent solve --image .\screenshot.png

# 没有 API Key 时，也能演示完整截图链路（注入脚本化假视觉模型）
python scripts/demo_screenshot.py
```

`scripts/demo_screenshot.py` 会生成一张示例题图，跑完
“截图 → 抽取 → 路由 → 数列求解 → 校验 → 产物落盘”，并把结果写到
`runs/demo_screenshot/`。它只用于本地验证链路，不代表真实视觉模型效果。

`doctor` 输出示例：

```
== 环境 ==
offline=True  llm_enabled=False  api_key_present=False
base_url=https://api.openai.com/v1  text_model=gpt-4o-mini  vision_model=None

== 工具 ==
python_math     stage=1  可用
number_series   stage=1  可用
z3_logic        stage=1  可用
figure_tools    stage=1  可用（布局与特征）
figure_rules    stage=1  可用（规律引擎）
```

---

## 4. 配置

全部通过环境变量（或项目根目录 `.env`）配置，**代码与仓库中不保存任何密钥**。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | 任意 OpenAI Chat Completions 兼容服务 |
| `LLM_API_KEY` | 无 | 未设置则自动进入离线模式 |
| `LLM_TEXT_MODEL` | `gpt-4o-mini` | 路由兜底、交叉校验 |
| `LLM_VISION_MODEL` | 无 | 截图抽取；留空则截图输入不可用 |
| `LLM_JSON_MODE` | `true` | 服务不支持 `response_format` 时自动降级 |
| `ASSESSMENT_OFFLINE` | 有 key 时 false | 强制离线，完全不发请求 |
| `ASSESSMENT_RUNS_DIR` / `ASSESSMENT_RESULTS_DIR` / `ASSESSMENT_DATA_DIR` | `runs` / `results` / `data` | 目录 |
| `ROUTER_RULE_THRESHOLD` | `1.0` | 规则得分低于该值时回退 LLM 分类 |
| `VERIFIER_USE_LLM` | `false` | 是否追加一次 LLM 交叉校验 |

---

## 5. 运行产物

每次求解生成 `runs/<时间戳>_<随机>_run/`：

```
run.json            # 汇总（答案、校验、耗时、脱敏配置）
question.json       # 规范化题目（含原始题图路径）
extraction.json     # 截图抽取结果（仅截图输入）
route.json          # 路由决策与得分
solution.json       # 答案、步骤、置信度、工具调用轨迹
verification.json   # 逐条校验 check
trace.jsonl         # 事件时间线（可直接用于蒸馏数据整理）
report.md           # 人类可读报告
image/original.png  # 原始题图，原样保存
```

`report.md` 片段：

```
- 题型：numeric_calculation（路由来源 rules，置信度 0.95）
- 求解状态：solved
- 最终答案：A
- 校验结论：通过（action=accept）

## 解题步骤
- 从题干抽取算式：(3.5 + 2.4) * 4
- SymPy 精确求值：59/2
- 数值结果：23.6
```

---

## 6. 数据集与评测

```
data/train/  data/dev/  data/test/     # 文本/整图样本
data/screens/                          # R2a 合成截图基准（截图 + 区域金标准）→ 定位为 regression 回归集
data/benchmark_real/                   # R2b 独立泛化基准（dev + 冻结 holdout + manifest.json）
```

**数据五分类（口径必须分开，不可混用）：**

| 类别 | 位置 | 用途与限制 |
| --- | --- | --- |
| regression 回归集 | `data/screens/`、`data/train|dev|test` | 允许根据求解结果调参/自校验，代表"别弄坏基线"，不代表真实场景能力 |
| 独立合成基准 | `data/benchmark_real/` | 样本按内容表独立选择，**未按当前 Solver 是否解对筛选**；dev 可调参，holdout 冻结 |
| 授权真实截图 | （本轮无） | 只有拿到授权真实测评页面截图后才存在此类结论；当前一切指标都来自合成页面 |
| mock-vlm | 评测/报告中的 mock 行 | payload 由金标准区域构造，仅验证解析→适配→求解→验证**链路机制**，不代表真实识别能力 |
| real-vlm | 配置 `LLM_VISION_MODEL` 后的 image_only | 唯一能证明真实截图识别能力的证据；未执行时必须明确写"未执行" |

样本为一题一行的 JSONL，字段见 [`data/README.md`](data/README.md)。评测强制路由到样本声明的题型，
并统计多维度指标：

```bash
python -m assessment_agent eval --split test
```

**评测指标说明：**

| 指标 | 定义 | 说明 |
| --- | --- | --- |
| **Answer Accuracy** | 正确解答数 / 有标准答案的题目数 | 衡量解题能力 |
| **Answerable Coverage** | 给出通过校验答案的可答题数 / 全部可答题数 | ≤100%（错误接受的答案不计入分子） |
| **Selective Accuracy** | 已通过校验的答案中，真正正确的比例 | 衡量答案可信度 |
| **Abstention Accuracy** | 应当拒答的样本中，正确拒答的比例 | 衡量诚实拒答能力 |
| **Router Accuracy** | 自动选择正确 Skill 的比例 | oracle_solver 模式下显示 N/A |
| **Extraction Accuracy** | image_only 下解析题干与金标准一致的比例 | 衡量 Vision Parser |
| **Region IoU** | 题干矩阵识别区域与标注框的平均 IoU | 衡量区域定位质量 |
| **Option Mapping Accuracy** | 选项标号（A/B/C/D/E）映射正确的比例 | 衡量选项布局解析 |
| **Answer Accuracy (E2E)** | image_only 下**可答题样本**的正确答案率，分母=有标准答案样本 | R2b：不与拒答样本混分母 |
| **Answerable Coverage** | 给出通过校验答案的可答题数 / 全部可答题数 | E2E 与总口径均给出 |
| **Abstention Accuracy** | 应拒答样本（unsupported/failed/undecidable）中正确弃权的比例 | 与 Answer Accuracy 分开计算 |
| **End-to-End Accuracy (mixed)** | 旧 R2a 混合口径（可答+弃权同一分母） | 仅历史对照，报告中显式标注 mixed 口径 |
| **Overall Answer Accuracy**（R2c） | 正确解答数 / **所有有唯一客观标准答案的样本** | R2c 主口径：分母含当前未实现但客观有答案的题型（答不出如实计错） |
| **Supported Answer Accuracy**（R2c） | 正确 / 客观可答**且**当前已支持题型样本 | ≈ 旧 Answer Accuracy 口径，保留兼容 |
| **Capability Coverage**（R2c） | 已支持题型样本 / 客观可答样本 | 静态能力覆盖，与运行结果无关 |
| **Honest Abstention**（R2c） | 正确弃权 / （undecidable + subjective）样本 | 与"有答案未实现的安全拒答"（能力缺口）**分开统计**，不再混为同一 abstention 指标 |
| **verification_outcome / root_cause**（R2c） | 逐题正交记录：验证表现 vs 最初失效环节 | 误放 false_accept ≠ Verifier 的错；先问答案为什么错 |

### 6.1 截图基准与三模式评测（R2a，定位为 regression 回归集）

`data/screens/` 由 `scripts/make_screenshot_benchmark.py` 生成——**合成评估页面截图**
（纯文字题、数字序列、图形序列/两行选项/九宫格、低分辨率缩放、干扰页眉页脚、诚实失败、
未实现题型弃权），每题带像素坐标金标准（`regions` 字段 + 题干/选项裁剪图）。
生成时即用生产适配层自校验，杜绝"意外构造的坏基准"。
R2b 起该集合**明确为回归数据**：可以用来自校验与调参，其满分只代表基线未退化，
真实场景泛化结论一律以 `data/benchmark_real/`（下文）为准。

评测按输入信息量分三级（`--eval-mode`）：

| 模式 | 提供给系统的内容 | 考察范围 |
| --- | --- | --- |
| `oracle_solver` | 标准题干 + 金标准区域/标号 + 标注题型 | 仅 Solver/Verifier |
| `auto_router` | 标准题干 + 金标准区域/标号，题型未知 | Router + Solver |
| `image_only` | **仅原始截图**（无任何标注注入） | Vision + Router + Solver + Verifier 全链路 |

```bash
python -X utf8 scripts/make_screenshot_benchmark.py          # 重建基准（自校验）
python -m assessment_agent eval --split screens --eval-mode oracle_solver
python -m assessment_agent eval --split screens --eval-mode image_only   # 需配置视觉模型
python -X utf8 scripts/run_screenshot_modes_report.py        # 三模式对照报告（results/screens/）
```

`image_only` 未配置视觉模型时会逐题记录 `pipeline_error`（诚实失败，不猜测）；
配置真实模型后截图会走 VisionParser → 区域裁剪 → 图形适配层 → 独立复核，
`runs/e2e-<case>/` 保存原图、抽取 JSON、区域裁剪图与警告。
未配置 `LLM_API_KEY`/`LLM_VISION_MODEL` 时，真实模型端到端测试自动跳过并**明确报告"未执行"**——
mock 视觉后端的结果只用于验证链路机制，绝不代表真实识别能力。

```
split=test  样本 19  准确率 100.00%  用时 1.02s
题型                      正确/总数    准确率    平均ms   p95ms   失败原因
figure_reasoning        5/5        100.00%   38.5    74.4    -
logical_reasoning       3/3        100.00%   72.2    96.4    -
number_series           5/5        100.00%   1.1     1.5     -
numeric_calculation     5/5        100.00%   120.5   479.7   -
verbal_understanding    1/1        100.00%   1.0     1.0     -
```

使用 `--json` 参数可输出完整指标（含 answerable、coverage、abstention 等）：

```bash
python -m assessment_agent eval --split test --json
```

结果写入 `results/<split>/<时间戳>/`：`metrics.json`、`predictions.jsonl`、`summary.md`。

失败原因分类：`unsupported_skill`、`wrong_answer`、`verification_rejected`、
求解器自身错误（如 `未能从题干中解析出算术表达式`）、`pipeline_error: ...`。

> 注意：`test` 集只用于最终评估。当前样本量小，准确率不代表真实测评难度上限；
> 请按 `data/README.md` 的约束自行补充样本（不要提交企业真题与个人信息）。

### 6.2 独立泛化基准与真实 VLM 验证（R2b）

目标不是把 9 张合成图的满分继续刷高，而是**测量真实截图下的视觉解析与端到端解题能力**。
`data/benchmark_real/`（由 `scripts/make_independent_benchmark.py` 生成）是与回归集**严格隔离**的独立集：

- **独立选择**：样本内容与标准答案写在手工内容表里（百分比/折扣/幂次/斐波那契/差分等由构造算术确定），
  生成过程**不调用任何求解器**决定样本去留。已知超出现有六类图形规则的题型（旋转、翻转、半径外推、
  缺口九宫格、质数）与未实现题型（资料分析、言语理解、性格）都保留——它们的失败正是基准要测的信号。
- **dev / holdout**：`dev/`（16）可调参；`holdout/`（16）冻结，`manifest.json` 记录其 `sha256`、冻结日期与"冻结后不得按结果增删"政策。
- **防泄漏分组**：同题/同模板（含重排、缩放变体）共享 `group`，整组只进一个 split；脚本与测试双重断言。
- **来源与授权**：全部为**自编模拟测评页面（independent-synthetic），非真实招聘平台截图**；
  每条记录带 `source`（provenance/license/构造方法）与 `image_sha256`。本轮无授权真实截图，故不声称真实平台结论。

**新增样本字段**：`expected_status ∈ {solved, unsupported, failed, undecidable}`。`undecidable` 表示信息不足以
唯一确定答案（构造时即如此，`answer=null`）——系统**必须弃权**，作答即判错并归因，不得强答。

**区域完整性（P1）**：VisionParser v2 图形题若区域裁剪不完整（缺题干矩阵 / 带图选项 < 2 / 裁剪文件丢失），
`figure_reasoning` 返回结构化 `region_incomplete` 失败并列出丢失项，**禁止静默回退整张网页截图的旧图形求解器**。
无 `vision` 元数据的旧输入仍走兼容整图路径。

**扩展视觉指标**（`image_only`，`--json` 的 `metrics.vision`，全部注明分母、漏检计入分母不剔除）：
Stem OCR Exact Match / CER、Option Label Recall、Option Text Accuracy/CER、Option Region IoU（每题+合并）、
Diagram Region Recall/IoU、Table Cell Accuracy、Region Detection Failure Rate。

**逐题失败归因**（`metrics.failure_classes`，按"最上游失效环节"归类，不再统一记 `pipeline_error`）：
`model_api_error` / `vision_parse_error` / `extraction_text_error` / `region_missing_or_shifted` /
`option_mapping_error` / `routing_error` / `unsupported_skill` / `solver_error` / `verifier_false_reject` /
`verifier_false_accept` / `pipeline_error`（兜底，必须显式可见）。每题独立诊断记录与红（金标准）绿（预测）
区域叠加图写入 `results/<split>/<run>/cases/` 与 `overlays/`。

**批次隔离**：`eval --eval-batch NAME` 使 `image_only` 的 `runs/e2e-<batch>-<case>/` 按批次命名，
重复评测不覆盖历史证据；真实模型的模型名 / token 用量 / 调用延迟在可获取时逐题记录（`predictions.jsonl` 的 `usage`）。

```bash
python -X utf8 scripts/make_independent_benchmark.py                                   # 重建独立基准
python -m assessment_agent eval --split bench_dev     --eval-mode image_only --json     # 开发集
python -m assessment_agent eval --split bench_holdout --eval-mode image_only --eval-batch r2b --json  # 冻结集
python -X utf8 scripts/run_screenshot_modes_report.py                                   # 回归+独立 三模式对照报告
```

> 真实 VLM 实验状态：本机未配置 `LLM_API_KEY`/`LLM_VISION_MODEL` 时，报告与测试都**明确报告"真实模型实验未执行"**，
> 绝不允许用金标准生成的 Fake/mock 响应冒充真实识别结果。mock-vlm 只验证解析→适配→求解→验证链路机制。

### 6.3 错误接受治理与真实可答口径（R2c）

方向从"让 Agent 支持更多功能"转向"**扩大真实可答覆盖率，同时降低错误答案被接受的概率**"。

**标注双维度（口径修复的根基）**——由 loader 派生，**不修改任何已有 JSONL 题目/答案**
（Holdout 冻结哈希不受影响；数据格式保留显式 `answerability`/`capability_status` 字段入口供未来标注）：

- `answerability`（客观属性）：`has_unique_answer`（有唯一客观标准答案，哪怕题型未实现）/
  `undecidable`（信息不足）/ `subjective`（无客观答案）/ `objective_unlabeled`（客观可答但本数据集无标注，单独上报）；
- `capability_status`（系统声明）：`supported` / `unsupported`。
- 由此 **Dev 客观分母 13→15、Holdout 12→14**；"有答案但没实现"从旧 abstention 指标里剥离，
  计为能力缺口；诚实弃权（undecidable+subjective）单独度量。

**图形候选代入独立复核**（`tools/figure_verify.py`，Verifier 与 Solver 不共享最终规则决策）：
对题干独立重建约束（个数等差/等比、边数、对称、墨迹、真实平移、九宫格格框归一化叠加算子），
把**被选选项单独复测**后代入——候选违反约束 `contradicted`（拒）；约束无法重建、只有弱恒定量、
唯一性不成立或裁剪不完整一律 `unverified`（受控弃权，**绝不默认通过**）；全部满足且判别性唯一才 `confirmed`。
"换阈值重跑同一规则"降级为 warning 记录（自我一致性，不能发现共享的系统性错误）。
九宫格目标格 `？` 印字改为**目标格标记检测**（感知修复，不新增规律类别）。

**自然语言数量关系层**（`tools/word_problem.py`，numeric_calculation 前置兜底）：
百分比/折扣/速时距（含分钟↔小时换算）/单价数量/比例分配/逆向百分比 → **结构化计算计划**
（中间变量分步），数值一律 Fraction 精确求值并由计划链独立重放复核；支持范围外/信息不足返回 None，
**不允许猜答案**。未来资料分析 Skill 复用同一确定性闸门。

**Router 冲突修复**：图形结构证据（≥2 个含图选项裁剪+题干矩阵）优先于"按规律"关键词；
表格/图表区域 → data_analysis；空括号+词语选项不再误判 number_series；ambiguous 置信度封顶 0.6
（**不允许把不确定分类伪装成高置信度**）；无规则命中返回 `unknown` 且 `classified=False`
（unknown≠unsupported）。

**归因拆分**：`verification_outcome`（correct_accept / false_accept / false_reject / correct_reject /
unverified_answerable / correct_abstention / over_answer / …）与 `root_cause`（solver_rule_selection /
visual_feature / word_problem_parse / router / capability_gap / verifier / …）逐题**正交记录**；
误放/误杀计数进 `metrics.capability`，任何被接受的错误答案不再笼统记在 Verifier 名下。

**消融开关**（`ASSESSMENT_R2C_DISABLE=word_problem,figure_verify,router_structure`）：
`scripts/run_r2c_ablation.py` 跑 baseline/+word/+router/+verifier/full 五臂 × Dev 双模式，
输出分题型准确率、能力/作答覆盖、selective accuracy、false-accept rate 与 outcome/root_cause 分布——
验证的是**失败机制**（如：关复核时 Dev 图形误放 4 起、开复核后 0 起且 screens 不受影响）。

```bash
python -X utf8 scripts/run_r2c_ablation.py        # 五臂消融矩阵（results/r2c_ablation_*/）
python -m assessment_agent eval --split bench_dev --eval-mode auto_router --json   # 含 capability 块
```

> 数据卫生修复（自查披露）：R2b 提交中曾把 adapter 合成画布 `*_adapter_canvas.png` 写入 `data/`
> （17 个文件）。R2c 已把画布默认落盘移到系统临时目录（内容寻址文件名）并删除污染文件，
> 测试断言 `data/` 不再出现画布。

---

## 7. 测试

```bash
python -m pytest -q
# 197 passed, 1 skipped（真实模型端到端用例在未配置 LLM_VISION_MODEL 时明确跳过）
```

覆盖范围：

- 配置与密钥安全（`.env` 不入库、无密钥字面量、`describe()` 不泄露 key）
- 路由（7 类规则命中、显式指定、离线回退、LLM 兜底、未知技能拒绝）
- 工具（SymPy 精确计算与非法输入、Fraction 独立求值、选项匹配、7 类数列规律、Z3 蕴含/否定/矛盾/带数值差拒绝、图形布局解析、六类规律推断、Skill 端到端与诚实失败）
- 校验（数值篡改 → repair、数列篡改 → repair、逻辑复核、unsupported → reject、图形换阈值复算、Skill 复核异常 → blocking 失败）
- Skill（8 个注册、4 个 MVP、声明式 Skill 绝不返回答案、数列歧义标记 ambiguous）
- 流水线（文本端到端、内联选项解析、截图 + 假视觉模型端到端、缺视觉模型报错、产物完整性、拒答不暴露原始答案）
- 评测（数据集加载、指标统计、失败归因、结果落盘、oracle/auto 路由模式、新指标计算、failed 状态严格匹配、answerable_coverage 不被错误拒答虚高）
- 图形工具接口（布局解析、特征提取、规律引擎、Skill 端到端、诚实失败、换阈值复核）
- Vision Parser（区域协议、越界/过小/空白/重复标号丢弃、裁剪像素证据、旧 payload 兼容、结构化失败）
- 图形布局适配层（题干矩阵+选项裁剪→原六类规律引擎、标号映射保持、题干专属 grid、空白选项诚实失败）
- 截图基准（oracle_solver/auto_router 全离线通过；image_only 用 mock 视觉后端验证全链路机制与产物落盘；真实模型用例未配置时显式 skip）
- R2b 独立基准（region_incomplete 拦截静默整图回退、旧输入兼容、失败归因映射与最上游次序、CER/IoU 单元、漏检留在分母、批次 run_id 隔离、用量快照、benchmark_real 结构/防泄漏/manifest sha256、undecidable 判分链路）
- DSH / MCP 接入（清单结构、`tools/call` 形状、未知工具与非法参数、能力输出脱敏、`save=false` 不落盘）
- CLI（solve / eval / skills / doctor / mcp-manifest / call 的返回码与输出）

截图流程的离线测试使用注入的脚本化模型（`FakeChatModel`/mock 视觉后端），无需真实 API Key；
mock 结果只证明链路机制，不用于宣称模型识别能力。

> **重要说明**：离线测试与评测数据集仅使用**小规模合成数据**（19 道文本题 + 9 张合成截图），
> 主要用于验证系统架构、模块接口和核心逻辑的正确性。**这些测试结果不代表系统在真实招聘测评场景下的表现**。
> 真实场景需要：
> - 真实视觉模型（而非 mock payload）：配置 `LLM_VISION_MODEL` 后 `eval --split screens --eval-mode image_only` 才产生有效 E2E 指标
> - 获授权的真实测评截图（当前基准全部为程序合成，`screens.jsonl` 的 `source.provenance` 字段有显式标注）
> - 更大规模、更多样化的测试数据集
> - 按失败归因分层改进：VLM 感知错误 ≠ 区域布局错误 ≠ Solver 规律错误 ≠ Verifier 误接受
>
> R2a 已打通"截图 → 区域 → 裁剪证据 → 适配层 → 求解 → 独立复核"机制；R2b 起进行真实截图泛化基准。

---

## 8. 设计原则

1. **不虚构能力**：没有实现的题型返回 `unsupported`；工具缺失依赖返回 `ToolUnavailable`；
   解析不完整时失败并说明原因，绝不猜测答案。
2. **可复算**：所有数值/逻辑结论都能被 Verifier 用**另一条路径**重算
   （SymPy vs `ast+Fraction`；数列留出预测；Z3 重建模）。
3. **可追溯**：每个答案都有步骤、工具调用、校验条目与原始题图。
4. **可替换**：模型接口只依赖 `ChatModel` 协议；工具可单独暴露为 MCP；Skill 可按题型替换。
5. **最小依赖**：核心只依赖 `pydantic` + `httpx` + `sympy`，`z3-solver` / `Pillow` / `numpy`
   按需安装，缺失时给出明确提示。

---

## 9. 第二阶段路线图

| 题型 | 计划实现 | 依赖 |
| --- | --- | --- |
| 图形推理 | ✅ 已实现：网格解析 + 六类规律（个数/边数/面积/平移/对称/叠加）；待实现：旋转/翻转匹配、曲线图形、非叠加九宫格规律 | OpenCV + NumPy |
| 资料分析 | 截图表格 → Markdown 表 + 行列/合计校验；指标公式复用 `python_math` | 视觉模型 + 表格校验器 |
| 言语理解 | 选词填空四维排除、语句排序关联词链、主旨论题-论点提取 | 带证据的 LLM 输出 + 校验器 |
| 语言推理 | 词关系类型知识库 + 关系一致性/方向/词性校验 | 知识库检索 + LLM |
| 性格测评解释 | 维度释义模板 + 组合解释 + 合规边界检查 | 量表知识库 |
| 通用 | 真假话/条件推理的 Z3 编码、DSH/MCP 适配、SFT 蒸馏 | — |

---

## 10. 接入 DSH / MCP 与蒸馏

**已实现**（[`integration.py`](src/assessment_agent/integration.py)）：

- `tool_manifest()` → MCP 风格清单 `{"protocolVersion", "serverInfo", "tools": [...]}`，
  含四个工具：`assessment_solve`、`assessment_list_skills`、`assessment_list_tools`、
  `assessment_capabilities`，每个都带完整 `inputSchema`。
- `AssessmentAgentService.call_tool(name, arguments)` → MCP `tools/call` 形状返回
  `{"content": [{"type": "text", "text": ...}], "isError": bool}`；参数非法或工具未知时
  `isError=true` 并给出原因，不会返回伪造答案。
- `AssessmentAgentService.solve(payload)` → 纯 JSON 结果，供 DSH / 其它 Agent 直接调用。

```bash
# 输出工具清单（可交给 MCP server 或 DSH 注册）
python -m assessment_agent mcp-manifest --out manifest.json

# 按 MCP tools/call 形状调用（Windows PowerShell 会吞内层双引号，建议用 --args-file）
python -m assessment_agent call --tool assessment_solve --args-file args.json

# 参数文件示例 args.json
# {"text": "数列 2, 5, 8, 11, ( ) 的下一项是？", "options": {"A": "13", "B": "14"}, "save": false}
```

接入 DSH 时只需把 `call_tool` 包成本地工具处理器，或直接把 `TOOL_DEFINITIONS`
注册到你的能力表；核心求解逻辑无需改动。

**蒸馏**：`trace.jsonl` 记录路由、求解、工具调用与校验结论，`solution.json` 含结构化步骤；
`predicted`/`expected` 配对后可筛选“校验通过 + 与标注一致”的样本作为 SFT 数据。
建议只在自有或授权数据上做蒸馏。

---

## 11. 安全与合规

- 密钥只从环境变量/`.env` 读取；`.env`、`runs/`、`results/` 已在 `.gitignore` 中。
- 默认不把题干发送到模型：只有配置了 `LLM_VISION_MODEL` 且使用截图输入时才会调用视觉模型；
  离线模式（`ASSESSMENT_OFFLINE=1`）完全不发请求。
- 题图与解析记录只保存在本地 `runs/` 目录。
- 本项目不提供绕过监考、代替真实笔试的能力，也不包含任何企业真实题库。
