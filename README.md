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

当前注册表 8 个 Skill、7 个已实现（numeric/series/logical/figure/**data_analysis**/**verbal_understanding**/**language_reasoning**）；
`capability_status` 一律由 `pipeline.registry.supported()` 派生（R3a），不再有手写名单。

| Skill ID | 题型 | 第一阶段 | 求解方式 |
| --- | --- | --- | --- |
| `numeric_calculation` | 数字计算 | ✅ 已实现 | 题干 → 表达式 → SymPy 精确求值 → 选项数值匹配 |
| `number_series` | 数字推理 | ✅ 已实现 | 7 类规律检测（等差/等比/差分/奇偶分组/递推/幂次偏移/常数），要求解释全部已知项 |
| `logical_reasoning` | 逻辑推理 | ✅ 已实现 | 排序比较语句 → Z3 约束 → 必然性/可能性判定 |
| `figure_reasoning` | 图形推理 | ✅ 已实现（R4a 扩展） | 网格解析 + 六类规律（个数/边数/面积/平移/对称/叠加）+ **R4a 变换通道**：离散旋转 90/180/270、H/V/对角镜像、组合（D4 群）、帧内相对平移、等比缩放+相对面积、3×3 行列一致变换、xor/or/and 叠加（画布级判别）；候选规律须共同支持同一选项，两律冲突/对称不可区分即诚实弃权；独立验证器 9 组归一化参数重推，不信任求解器前提；失败时给出原因 |
| `verbal_understanding` | 言语理解 | ✅ 已实现（R3b，R3c 增强） | 选词填空（搭配表）/主旨概括（结论句覆盖）/阅读推断（否定极性·情态·量词核对 + **R3c 命题归属层**：asserted/reported/believed/hypothetical/uncertain/denied 分层，传闻/假设/归属否认不得作事实断言）/加强削弱假设（字面条件链 + Z3）；每条证据必须为原文逐字区间，越界/伪造引用直接拒绝；子集外诚实 failed |
| `language_reasoning` | 语言推理 | ✅ 已实现（R3c，词典关系子集） | 词语类比（七类方向关系：种属/整体部分/材料成品/功能用途/地点对象/工具行为/因果，方向显式）+ 近义/反义（仅人工词典证据）+ 英文类比子集；每个关系判定绑定 `lexicon_r3c` 词条，多重关系要求判别性覆盖，词典外一律 unknown——不硬猜 |
| `data_analysis` | 资料分析 | ✅ 已实现（R3a） | 表格/统计图 → `DataCalculationPlan` → Fraction 精确求值 → 六步独立验证；缺数/歧义/单位冲突如实失败 |
| `personality` | 性格测评解释 | 🚧 仅接口 | 同上 |

未实现题型的行为是**明确失败**（如 `personality`）：

```json
{
  "status": "unsupported",
  "skill": "personality",
  "answer": null,
  "error": "性格测评解释尚未实现（第一阶段 MVP 范围之外），不提供推测性答案",
  "meta": {"stage": 2, "planned_capabilities": ["..."], "interface": "skills.personality...solve(question, ctx) -> Solution"}
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

即使**不配置任何模型**也能使用七个已实现题型（离线确定性模式）：

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

# 言语理解（R3b 离线证据子集：答案必须绑定原文逐字区间）
python -m assessment_agent solve --text "他的发言获得了与会者一致的（ ），会场气氛由此变得热烈起来。" --options "A=反应,B=反映,C=批示,D=注视"

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

### 6.4 资料分析 Skill 正式实现（R3a）

`data_analysis` 从"声明未实现"升级为**可验证的真实能力**：
表格/统计图 → 数据结构化 → 问题语义解析 → 计算计划 → 确定性求值 → 独立验证 → 最终答案。

**数据结构化**（`tools/table_tool.py`）：复用 `ExtractionResult.table_regions`/金标 `regions.table`；
行/列/单元格标准化并保留来源坐标与原文；多层表头按"时间·指标"合并（不丢失年份或统计对象）；
显式区分金额/数量/百分比/百分点/倍数；表头单位与格内 inline 单位量级冲突逐列标记；
空值/占位符、OCR 歧义数字（`l80`、多小数点、错误千分位）、一格多数值全部返回结构化失败码，
**每个数字保留原始证据**（raw 文本 + bbox + 单位）。

**计算计划**（`tools/data_plan.py`，schemas 中 `DataCalculationPlan`/`DataOperand` 可复用结构）：
求和/均值/最大最小与排序/两实体差值、同比环比增长率与增长量、比重（含无合计行时的总量推定）、
倍数（"是多少倍"与"多几倍"区分）、复合/年均增长率（精确有理开方，非精确时显式标近似）、
基期/现期推算（`现期/(1+r)`、`现期×(1+r)`，题干内联速率同样要求逐字证据）、百分点差、
单位换算（万元/亿元/万亿）与近似比较。求值为分像素级 Fraction 精确算术（自带递归下降解析器），
结果按显示单位换算回表头量纲后匹配选项；**数值重算正确 ≠ 题意理解正确**——
操作数必须绑定真实单元格原文（行×列或题干内联文本）。

**LLM 可选入口**：规则解析失败时才允许 LLM 提交结构化计划（只含 operation/operands/expression），
且**所有引用数字必须逐字命中真实单元格原文，否则整盘拒绝**；LLM 永远不能直接给出最终选项。

**统计图纪律**：柱状/折线数值只取图上**标注文本证据**（如"甲组17件"）；对无标注、读数冲突或
无法可靠提取的图表（饼图/堆叠图）一律 `unsupported/unverified`——**不允许仅凭柱形长度猜精确数值**。
`chart_tool.py` 的柱高像素测量仅用于**比例一致性复核**（与文本读数矛盾 → 阻断；测不了 → warning，
不作为确认依据）。

**六步独立验证**（与 Solver 不共享最终决策）：① 重读源单元格（原文/数值/单位逐字一致，篡改即 `reject`）；
② 核对行列索引、时间与统计对象（题面线索仍在，双层表头标签含年份+指标）；③ 核对单位、
百分比 vs 百分点；④ **另一条算术路径**独立复算（不重放表达式字符串；无法复算 → `unverified`）；
⑤ 答案与选项**唯一**匹配（两个选项同值 → 拒绝接受）；⑥ 任何不能确认的情形返回 `unverified`
受控弃权，**绝不默认 accept**。

**口径与残留修复**：`capability_status` 改为从 `pipeline.registry.supported()` 实际注册能力派生
（不再依赖固定"已实现名单"，`metrics.capability.capability_source="skill_registry"`）；
主观量表即使带人工填写的 A/B/C/D 也优先派生 `subjective`（修复 2）；
`_grid_substitution` 非方形裁剪 bbox 宽高序修复（修复 1）；e2e 指标块新增 `primary_*` 主口径字段并
显式标注 `legacy_fields`/`legacy_note`（修复 3），消融基线更名 `flags_off_current_code` 与历史提交
对照分开报告。消融升级为七臂（+`da_only`、+`r2c_full`），并用 `ASSESSMENT_R2C_DISABLE=…,data_analysis`
单独关闭资料分析。

**R3a Dev 实验结果（2026-10-18，`results/modes_20261018T112929/` 与 `results/r2c_ablation_20261018T032936/`）**：
独立基准 Dev 扩充至 35 样本（客观分母 15→30，全部新增 DA 样本为 dev-only，Holdout 字节级不变）：

| 指标（oracle_solver / auto_router 同值） | R2c | R3a |
|---|---|---|
| 总体答题准确率（客观分母） | 53.3%（8/15） | **80.0%（24/30）** |
| 资料分析准确率 | 0/1（未实现） | **16/16（全部有答案样本可验证作答）** |
| 能力覆盖（已支持/客观可答） | 86.7% | **96.7%（29/30）** |
| 选择性准确率 | 100% | **100%（误放 0）** |
| 误放 / 越权 | 0 / 0（Dev） | **0 / 0** |
| unverified（受控弃权） | 1 | 1 |
| honest abstention（undecidable+subjective） | 100% | 100%（5/5，含 4 个新"真无唯一解"样本） |

消融净增量：`r2c_full` 8/30 → `full` 24/30（+16 全部来自资料分析），`da_only` 臂单独 +16
且其 4 起图形误放恰好证明**覆盖率提升与误放治理是两个正交机制**，同时开启后误放归零。
mock-vlm image_only 机制行 22/30、路由 91.4%——该行 payload 由金标区域构造，只验证
VisionParser→Router→Solver→Verifier 链路机制，不代表真实识别能力。**真实 VLM：NOT EXECUTED**（本机未配置 `LLM_API_KEY`/`LLM_VISION_MODEL`）——
DA 的"真实截图端到端作答能力"在配置视觉 API 前仍未验证，且应尽早单独跑一轮 image-only。

### 6.5 言语理解 Skill 与 LLM 计划执行（R3b）

R3b 含两部分：**先修 R3a 遗留可靠性问题，再实现言语理解离线证据子集**（不含图形规律扩展、蒸馏、语言类比与性格评分）。

**Part 1：LLM Plan Fallback 从"预留入口"变为"真正执行"（资料分析）**

- 规则解析失败后，LLM 提交受限 Schema 计划（operation/operands/expression/question_focus），随后走
  **与规则通道完全相同的确定性路径**：逐操作数原文证据绑定（行×列网格坐标或题干内联逐字命中）→
  操作白名单 + 表达式模板一致性 → Fraction 求值 → 独立验证；证据不足则**拒绝并记录 llm_rejection**，
  绝不"忽略模型计划再重跑同一个失败的规则解析器"。
- `verify()` 强化：每个 `DataOperand` 必须**且只能**命中一种有效证据来源，任何无法解析或越界的引用
  阻断接受（`data.source_recheck`）；新增 `data.plan_semantics`（操作↔表达式模板互证）与
  `data.expr_replay`（用当前表达式重放求值对账存档值——堵住"改了表达式但没改存档值"这类
  数值重算正确但计划被篡改的情况）；`data.intent` 如实降级为"表层字符串线索检查，不构成题意正确性证明"。
- 回归：`tests/test_r3b_reliability.py`（11 项）覆盖 篡改引用/表达式-计算语义不一致/合法计划被执行/伪造计划被拒绝。

**Part 2–4：言语理解 Skill（`verbal_understanding`）**

覆盖四类任务的**可形式化子集**，输出 `VerbalReasoningPlan`（任务类型、提问锚点、原文跨度、前提/结论、
逐选项分析、无依据 claims、候选答案、置信度、不确定原因）：

- 选词填空：人工编写搭配表命中/冲突（单空；"依次填入"多空诚实 failed）；
- 主旨概括：结论线索句 + 内容词覆盖率与新颖度排除（重表述改写不在子集）；
- 阅读推断（可以推出/无法推出/说法不正确）：句级证据对齐 + **否定奇偶扫描、情态强度、量词范围**核对，
  选项强于原文记过度引申、极性翻转记矛盾；真歧义（多候选）拒绝挑选；
- 加强/削弱/必要前提：字面条件链（如果/只要/只有才/除非/所有…都(不)）→ **Z3 命题蕴含**，
  削弱=结论直接反驳、加强=补上论证缺口、假设=否定后论证崩溃；主张权衡型/肯后式推断不在子集。

纪律：答案只能来自证据绑定 + 确定性判定，**不允许凭候选字母、样本 ID、数据集文件名走捷径**；
每条证据引用必须是原文有效字符区间（`stem[start:end] == text` 逐字核对），伪造/越界/截断改义 → 拒绝。
LLM 通道（规则失败后可选）必须给**逐选项分析与原文区间**；接受条件是独立审计（与求解不同实现的
包含度+奇偶重算）`confirmed`——搭配表/Z3 这类"与求解共享实现"的**自洽复核永远不能给 LLM 通道
确定性通过**（cloze/argument 的 LLM 计划最多 `unverified`）。模型 confidence 不作为任何接受依据。

**verbal 基准（`data/benchmark_verbal/`，人工按 任务类型×语言特征 网格编写）**：
dev 20 题（可调参）、blind 12 题（一次生成即冻结，manifest 记 sha256）；覆盖短/长文、否定、转折、
双重否定、量词、条件、近义并列、信息不足与真歧义，并含标注为 failed/undecidable 的构造性难点——
**期望状态在任何模型运行之前按语言学标准确定，不按模型是否解对筛题**。

**R3b 实验结果（2026-10-18，`results/r3b_report_20261018T054049/` 与
`results/r2c_ablation_20261018T054105/`，全部为离线规则通道）**：

| 实验 | R3a | R3b | 误放 |
|---|---|---|---|
| bench_dev oracle/auto（客观分母 30） | 80.0%（24/30） | **83.3%（25/30）** | 0 → 0 |
| bench_holdout oracle（客观分母 14，冻结） | 5/14 | **5/14（逐行仅 verbal-30 判定口径变化）** | 0 → 0 |
| screens auto | 9/9 | 9/9（与 HEAD 逐行一致） | 0 |
| verbal_dev 20（oracle 与 auto） | — | 判定 20/20；期望可解 12/12；引用逐字有效率 100%；弃权 8/8 | **0** |
| verbal_blind 12（**首次运行即定版**） | — | 判定 8/12；期望可解 3/5；引用有效率 100%；弃权 6/7 | **1**（见下） |

- 消融九臂（`r2c_full → r3a_full → full`）：+16（DA）→ +1（verbal-16 由"弃权正确"变"作答正确"），
  `verbal_only` 单独臂 +1；误放在 r3a_full/full 臂均为 0。
- **盲集首跑暴露的真实误放**：`vbv-inf-09`"有报道称…"把传闻内容当作事实接受（选择性准确率 1.0 的含义：
  有金标准答案的接受 3/3 全对，越权作答 1 例计入误放）。按冻结纪律**未依据盲集结果修改题目或规则**，
  该缺陷作为 R3c 首要修复项：转述/传闻谓语句应降为不确定证据（不得直接支持其内容）。
- 能力声明变化的口径效应：holdout `bi-verbal-read-30`（期望不支持+金标 A）由"弃权判对"转为
  "按金标准判未答对"，属能力声明升级后的判定分支变化，不是新误放（该项 FA 计数 0）。

**已知限制（如实声明）**：搭配表为人工子集；主旨靠结论线索句+字面覆盖，重表述改写不覆盖；
阅读推断不处理情态不确定反转（肯后式）；论证题只认字面条件链；无 LLM/VLM 配置时**真实模型通道
NOT EXECUTED**——言语理解与资料分析对真实模型的表现均未验证，配置 API 后应最先补跑真实 VLM 的
image-only 实验与 verbal LLM 审计通道实验。

---

### 6.6 R3c：命题事实性层、指标口径重构与语言推理

**Part 1（P0）：传闻当事实的修复——命题级区分，而不是词表**。
R3b 盲集首跑暴露 `vbv-inf-09`（"有报道称… ⇒ 报道内容为真"被接受）。修复不是扩词黑名单，
而是给推断核引入命题归属层 `epistemics()`：`asserted(P) / reported(P) / believed(P) /
hypothetical(P) / uncertain(P) / denied(归属谓词)`，规则：

- `reported(P) ⊬ P`，但 `reported(P) ⊢ "存在关于 P 的报道"`（归属对齐选项仍可支持）；
- 原文否定的是"有人声称 P"时，**不得**转换成"P 不成立"（双向裸断言均不可推出）；
- 嵌套范围（"报道称某机构否认 P"）逐层记录 nested markers，只支持归属层面复述；
- 来源不可靠/无合法候选 → `insufficient`/诚实 `failed`，**不允许仅凭词组重叠度给 confirmed**；
- 证据粒度为**小句级**（句内按逗号切分）：归属引导小句（"有报道称"）与内容小句合并、
  条件连词小句（"如果 p，q"）不得把后句当独立断言——修复了逗号复句的归属越权。
  主实现（结构锚点：句首"据…"、主语+报道动词、对象式否认）与审计层（独立轻量词表扫描）
  双实现互相制约，方向不一致时保守结果胜出。

不针对任何题干/选项/样本 ID 特判；已暴露的旧盲集样本转为**历史回归锚**（机制变体测试），
不再作为未见盲测成绩（新规则下旧 blind 为 9/12，该数字不再具有盲测意义）。

**Part 2（P1）：指标口径重构**。R3b 的"verbal_dev 20/20"与"blind 8/12"是
**policy_compliance**（遵守子集策略：该答答对+该弃权弃权），不是客观正确率。
`scripts/run_r3c_report.py` 分离八个指标：`objective_answer_accuracy`（全部有金标准样本，
含规则不可解）、`supported_subset_accuracy`（预声明可解子集）、`policy_compliance`、
`answered_coverage`、`selective_accuracy`、`false_accept`（有金标准且接受错项）、
`over_answer`（无金标准/越权给确定答案）、`unverified_abstention`（产生候选但验证未过、
未对外作答）；"对外作答"与内部 `failed` 状态分账。Router Accuracy 分母=全部样本
（unknown/超时/路由失败计入并列出 misses）。复算口径：verbal_dev objective 12/19=63.2%、
历史 blind objective 3/11=27.3%。

**Part 3–4：语言推理 Skill（`language_reasoning`，词典关系子集）**。
`tools/language_tool.py` + `tools/lexicon_r3c.json`（人工词条：释义 def + 关系字段，
是**唯一世界观来源**）：七类关系方向标签（GENUS_UP/DOWN、PART_OF/HAS_PART、
MADE_FROM/PRODUCES、FUNCTION、PLACE、TOOL、CAUSE/EFFECT、SYN/ANT）。
输出 `LanguageRelationPlan`（词对、关系签名、逐词条证据绑定、候选覆盖与排除、冲突、置信度）。
规则：方向显式（反序=partial 不算满足）；多重关系要求选项**覆盖题干全部签名**（判别性不足→
ambiguous 拒答）；近义/反义仅词典证据；英文类比子集。验证六检查：词条 source_text 对账、
方向一致、粒度/方向偷换、竞争项同等判定（未登录竞争项如实声明不积极排除）、世界知识来源、
唯一性；`audit_relation` 独立重推导。LLM（如配置）只有**提议权**：提议词对必须被词典逐对
重算一致，同一模型自评不构成验证。

**Part 5：新基准（`data/benchmark_epistemic/`、`data/benchmark_language/`、
`data/benchmark_verbal/blind2/`）**。事实性对照组 13 题（最小对比对：同一命题 P 只换外层
谓词，期望行为随之改变）；language dev 20 题（oracle/auto 均 20/20）；
language blind 12 题与 verbal_blind2 10 题在 dev 完成后生成、**首跑即冻结**：
language_blind 首跑 policy 11/12（objective 8/8，1 例 over_answer——ANT 字段单向引用被
盲集暴露，如实记录不回写）；verbal_blind2 首跑 policy 6/10（objective 4/8，误放 0，
4 例为覆盖缺口诚实弃权）。全部盲集 sha 入 manifest + 守护测试。

**Part 6：消融四臂**（`results/r3c_ablation_*`，同一代码按开关切换）：

| 臂 | verbal_dev obj | epistemic dev obj/over_answer | language dev obj | router auto |
|---|---|---|---|---|
| r0 R3b 基线 | 12/19 | 5/9 · **3 越权** | 0/18（不支持） | 53/53 |
| r1 仅命题事实性 | 12/19 | **9/9 · 0 越权** | 0/18 | 53/53 |
| r2 仅语言推理 | 12/19 | 5/9 · 3 越权 | **18/18** | 53/53 |
| r3 完整 R3c | 12/19 | 9/9 · 0 | 18/18 | 53/53 |

主基准无退化：bench_dev 25/30（oracle/auto，误放 0）、bench_holdout 5/14（误放 0）、
screens 9/9；旧 Dev/盲集数据未回写。**真实 LLM/VLM 未配置：NOT EXECUTED**（词典语言核、
命题归属层与 LLM 提议通道的真实模型表现均未验证）。

### 6.7 R4a：对称闭包、开放世界词典修正与图形变换推理扩展

**Part 1：语言关系对称闭包与开放世界口径**（`tools/language_tool.py`，已入库 `cb5d1dc`）：
SYN/ANT 词典边按 `syn@a/syn@b/ant@a/ant@b` 源标签双向补全；候选三态判定
（match/partial/contradicted/unknown_pair）区分"词典未记录"与"真实矛盾"——
存在未排除且可能匹配的竞争项时降为 `unverified`，不再把"唯一已知匹配"当"唯一正确答案"。
由此 lngb-gen-01、lngb-en-12 从"不可判"改判为**客观可答的能力缺口**（词典缺项），
客观分母 8→10；盲集原始数据与首跑报告**不回写**，审计单独存
`data/benchmark_language/annotation_audit_r4a.json`、`data/benchmark_verbal/annotation_audit_r4a.json`
（含 vb2-inf-10 预报口径争议的双口径复述）。命题事实性层新增 `predicted` 状态
（预报/预测/预计：Forecast(P) ⊬ P，与 reported 同律），最小对比对测试防逐样本特判。

**Part 2-4：图形变换独立模块与通道**（`tools/figure_transform.py` +
`figure_transform_tools.py`，`solve_figure_transform_regions`）：
离散旋转 90/180/270、水平/垂直/对角镜像、稳定相对平移、等比缩放+相对面积、D4 组合表
（12×12 非对称探针实测推导）、3×3 行列一致变换与去同存异/并集/交集叠加。
标准表示输出原始裁剪/连通域/轮廓/相对坐标/候选变换/几何残差/判别裕度；
**答案决策不使用选项绝对位置、文件名、标签顺序或人工 grid**。
每候选十字段（rule_type…unresolved_competitors/confidence）；旋转/镜像候选守
**保面积纪律**（bbox 面积比 |log|>0.35 即不可由二面体解释，交缩放律处理）；
叠加律用**画布级归一化**（bbox 归一会把 xor/or 的中心重叠差异抹平——dev 实测暴露）；
平移律的判别维度是帧内相对位置（位置惩罚 0.30 曼哈顿标度）。
仲裁：多个候选规律必须共同支持同一选项；**绝不做多数投票**，两律指向不同选项即
ambiguous 弃权；旋转对称图形（非方形矩形等）经稳定子披露"唯一角度不可判定"。
独立验证器从裁剪重推：`FRAME×tol` 9 组参数扫描重跑全流程，求解器的归一化/容差
**不作为不可质疑前提**；参数间答案漂移→unverified，与求解答案矛盾→reject；
画布尺寸失配（异常裁剪）全局降级。`figure_reasoning` 集成为**回退+交叉校验通道**：
六类未解出时变换通道独立作答；`layout=figtrans` 新题型则双通道交叉校验，
两规律族冲突即诚实弃权（原六类题历史语义不变 → 299+ 测试零回归）。
错误放行/错误拒绝回归用例：`tests/test_r4a_figtrans_regression.py` 固化 test 首评
暴露的三个真实失误（FV 对称等价类、R180 自对称步进不可区分、容差贴边不放宽）。

**Part 5：新变换基准与消融**（`data/benchmark_figtrans/`，dev 16 / test 10，生成器
模板先于求解器定义期望，不按 Solver 结果筛题）：
dev **16/16**（含 3 个歧义/降级诚实 failed 全弃权，误放 0）；
**test 首评即终评：7/10，误放 1**——三个失误全部诚实记录在
`test_firstrun_metrics.json`（comp-08 金标复合式书写错误、rot90-01 阶梯 R180 自对称
致选项内容同化、scale-04 残差 0.1505 贴容差边界），不回写、不重评、不放宽容差。
消融四臂（`data/benchmark_figtrans/ablation_r4a.json`）：

| 臂 | figtrans_dev | bench_dev（原六类） | 误放 |
|---|---|---|---|
| r0 仅原六类 | 3/16（仅诚实 failed 题弃权得分） | 25/30 · obj 0.8276 | 0 |
| r1 仅新变换 | 16/16 | 23/30（六类图题 3→1 正确） | 0 |
| r2 完整+原 Verifier | 16/16 | 25/30 · 0.8276 | 0 |
| r3 完整+独立 Verifier | 16/16 | 25/30 · 0.8276 | 0 |

变换通道带来 figtrans 0/13 → 13/13 客观增益，原六类口径逐项不变。

**Part 6：真实 VLM image_only 冒烟**：环境未配置 `LLM_API_KEY`/`LLM_VISION_MODEL`
→ `data/benchmark_figtrans/phase6_vlm_smoke.json` 显式 **NOT_EXECUTED**（含执行时
应记录的字段清单：model/run_ref/usage/输入图/区域定位/答案/验证状态），无 mock 冒充。

### 6.8 R4b：图形验证可靠性与真实截图闭环（2026-10-18）

R4a 交付后复核发现两个 P0 洞，R4b 全部封堵并新增**完整页面截图基准**：

**P0-1 完整截图基准 `benchmark_figshots`**：R4a 的 figtrans 题图只渲染题干矩阵，
选项单独存放且标注 bbox 超出主图——`image_only` 端到端选择题在物理上不成立。
figshots 把题干矩阵、全部选项格、字母标号（A. B. C.）与中性说明文字
（"根据题目图形中蕴含的规律…"，**无旋转/叠加等规律名提示**）渲染到同一页面；
全部 bbox 严格位于图片内；regions 金标裁剪从页面像素**再导出**（像素一致性由
测试锁定 `np.array_equal(crop, page[bbox])`）。canvas-16（异常裁剪考点）在整页
统一网格结构下无法表达，如实剔除并记入 manifest.deviations——该降级题保留在
figtrans Oracle 回归集。原 figtrans/test 冻结集与首评记录**未动**（sha 复验在测试内）。

**P0-2 扫描计数洞**：R4a `unique={A,None,…}` 去 None 后当成"全部一致"放行（1/9 确认
可 accept）。现拆 `confirmed/contradicted/unresolved` 三计数，政策显式
`all_confirm`（unresolved>0 → unverified），锁死测试
`test_partial_confirm_is_unverified_not_accept`（构造部分确认场景）。扫描同时从
3×3 全因子改为**单因子帧组 {48,64,80}**（48帧×0.08容差这类交叉病态组合检验的是
双重极端劣化而非求解器前提，曾把真实唯一规律误判为不稳定）；打分容差不再混入
扫描维度，由验证器自有 VTOL+双侧扰动承担。`ft.FRAME` 全局突变路径删除，改为
`group_for(frame)` 参数贯穿——并发 12 线程结果逐位一致、中断/顺序调换测试在
`test_r4b_reliability.py` 锁定。

**P0-3 parameter_stable ≠ independently_verified**：9 组同参重跑只是参数稳定性。
新 `verify_geometry` 在验证器**自有帧 72×72** 下枚举几何候选做替代验证六项：
规律重放（blocking）、外推代入（blocking）、等价选项互距、稳定子披露、
同等合理替代规律指向别处、离散化双侧扰动（±1px 膨胀/腐蚀同时作用于题干与
选项、声明规律按原参数重放、排序不翻转且裕度不塌缩）。两布尔经
`solve_figure_transform_regions` 顶层字段 → solution.meta → 验证记录**分开透出**，
测试锁定"参数稳定但代入未过 → unverified 不作答"的真实样本
（trans-07/overlay-12：dev 中 3 道弃权根因即在此）。

**P1-1 layout 决策解耦**：`question.meta["layout"]=="figtrans"` 不再参与求解决策——
变换通道对所有区域化题常开，适用性由图形结构判定（空格位置/已知格数/网格形状），
交叉冲突拦截仅限"变换规律族真的表态"。测试 `test_layout_label_does_not_change_solver_decision`
证明只改 layout 字段时答案/候选/验证逐位不变；Oracle/auto_router/image_only 同一路径。

**P1-2 分账审计 `annotation_audit_r4b.json`**：comp-08（疑 gold 生成错误）、
rot90-01（等价选项无唯一解）、scale-04（gold 残差贴容差）逐题记录原始金标/预测/
评测结论/是否存在唯一合法解/复核结论与证据。首评 7/10 **永不回写**；Adjudicated
Score 标 PENDING_HUMAN_REVIEW。`code_commit=cb5d1dc` 取证结论：该字段只记录了
HEAD 指针，变换模块当时未入库（首次 commit 是 ca89a6c），首评代码态**不可复原**，
如实记录不猜测——R4b 起所有指标持久化附**模块内容 sha256 双凭证**。
R4b 代码对 test 集一次性诊断重放：误放 1→0（comp-08 封堵为弃权），代价是
trans-03 由首评答对转为保守弃权——覆盖损失如实入账，不粉饰为正确。

**P1-3 严格 image_only**：runner image_only 分支只传原始整页图片（输入合同由源码
断言测试锁定），无 VLM 环境 15/15 诚实 `model_api_error`；冒烟报告迁移至
`benchmark_figshots/phase6_vlm_smoke.json`（NOT_EXECUTED，含 8 项产物清单）。

**R4b 数字**（误放全 0；真实识别能力主张仍为零）：

| 数据集 | R4a 口径 | R4b 口径 | 说明 |
|---|---|---|---|
| figtrans_dev（Oracle 回归集） | 16/16 | **13/16** | 3 题位置/叠加判别由离散化维持→弃权（FR 分账） |
| figshots_dev（完整页面，Oracle） | — | **12/15** | 与 figtrans 等价题集判定逐题一致 |
| figtrans_test 首评（历史） | 7/10 · FA1 | 诊断重放 6/10 · FA0 | 首评记录不回写 |
| figshots_test **首评即终评** | — | **6/10 · FA0 · FR0** | obj 4/8；trans-03 口径收紧直接可见 |
| bench_dev（原六类） | 25/30 · obj 0.8276 | **不变** | layout 解耦与验证收紧零回归 |

R4b 消融（`ablation_r4b.json`，四臂 × figtrans_dev/figshots_dev/bench_dev）：
r0 仅六类 figshots 2/15；r1 仅变换 12/15（bench 六类图题 3→1）；r2/r3 完整
13/16 与 12/15、bench 25/30 obj 0.8276 误放 0——变换通道增益与验证器代数差
同表可查。

**行尾凭证事故与修复（R4b 过程中发现的仓库级缺陷）**：本机 `core.autocrlf=true`
且无 `.gitattributes`，`git checkout` 把冻结 jsonl 重写为 CRLF，多个 manifest sha
出现"假性漂移"；同时取证确认 R2b/R3b 时代部分 manifest 记录的是发布机 CRLF 字节
哈希而 git blob 一直存 LF（凭证与库内容**历史性地**不字节相等，JSONL 行内容恒等
已逐行验证）。修复：`.gitattributes` 强制 `*.jsonl/*.json -text`（禁一切转换），
四个受害文件按 manifest 凭证字节恢复（行内容零改动，`rows-equal True` 证明），
新增 `test_frozen_figtrans_untouched` 等测试锁定。

### 6.9 R4c：真实 VLM 端到端评测与错误归因基础设施（2026-10-18）

R4b 静态验收后发现的 P0——**冒烟脚本用 CLI 退出码/local run_ref 判 EXECUTED**，
在"已配置但 401/模型不存在/全部请求失败"场景下会产生虚假执行凭证——本轮封堵；
范围严格限于端到端测量，**未扩任何新 Skill/规则，未动冻结金标，未开始蒸馏**。

**执行状态五分类（调用审计为唯一依据）**：
`OpenAICompatibleClient` 新增审计层（`llm.py`）：每次真实 HTTP 尝试计数、状态码分布、
错误类型（auth_401_403/not_found_404/rate_limit_429/server_5xx/请求异常名）、服务端回显
模型、request id 采样、注入 transport 检测、offline 标志——`audit_snapshot()` 不落密钥。
评测每次落 `output_dir/llm_evidence.json`（runner），冒烟与实验脚本按
`NOT_CONFIGURED / CONFIGURED_NOT_CALLED / API_FAILED / PARTIAL_EXECUTED / EXECUTED`
五态判定；`classify_status` 为纯函数，11 项测试锁定，其中反例直接构造
"requests_succeeded=0 + predictions 全有 run_ref → 绝不 EXECUTED"。
真实执行报告只写 `results/real_vlm/<run_id>/`；Git 跟踪的
`data/benchmark_figshots/phase6_vlm_smoke.json` 保持离线凭证不可变（源码断言测试：
configured 分支无任何仓库写路径）；what_would_run 旧 `figtrans_dev` 残留已修。

**固定实验基础设施**（`scripts/run_real_vlm_experiment.py`）：manifest 锁定 git
commit+dirty 摘要、provider/model/temperature/max_tokens、VISION_PARSE_SYSTEM prompt
sha256、15 张图逐图 sha256、选择规则**运行前声明**（冒烟 3 题显式 case_ids /
全量按数据集行序 / bench_dev 全部 35 截图不按题型文件筛更不按解析结果筛）。
三模式矩阵（oracle_solver / auto_router / image_only）×（冒烟→全量）+ 跨题型
自动执行；`Δ_vision = Acc(auto) − Acc(image_only)` 只在 image_only 有真实调用凭证
时输出数值，否则记 `not_meaningful（未真实执行）`——当前环境产出的诚实状态是
**NOT_CONFIGURED**，未声称任何真实识别能力。
**（R4d 收尾纪律修正：本脚本为自动分阶段执行器，健康门通过后连跑 15/35，
首次真实运行请只跑 `scripts/phase6_vlm_smoke.py`，见 §6.11。）**

**逐题归因**（attribution_*.json）：11 字段（stem OCR/CER、选项标号映射、选项文本、
stem bbox IoU、逐选项 bbox IoU、grid 行列、裁剪完整性、路由、solver 提出正解、
verifier 接受、最终作答）+ 错误类型优先级分类（api_failure→ocr→bbox→option_map→
grid→router→solver→verifier）；未执行=None ≠ 零值。`compare_extraction_detail`
补 grid_parse_correct / crops_complete（漏检记 0 留在分母）。oracle 的 200px 裁剪
IoU 差异按 R2c 口径归一化说明，不误报 bbox 错误。

**分层指标**：extraction/region IoU/option map/router/solver-with-gold-extraction/
端到端 obj/coverage/selective/FA/over-answer/unverified/API 失败率/解析成功率/
P50-P95 延迟/tokens——全部分子+分母齐备，`not_applicable`/`missing`/真零三态区分；
per_skill 分组含 false_accept 及最上游归因链；报告显式声明小调用量不支撑
"已证明部署误放概率"的宣称。

**新盲测（仅计划，未构建）**：`blind_test_plan.status=PLANNED_NOT_BUILT`，前提=
真实模型 Dev 实验完成且模型/算法配置冻结；三层来源（自编合成/独立人工/授权真实
截图，第三类当前缺失不得以合成冒充）。

当前离线回归：`373 passed, 2 skipped`（新增 R4c 11 项 + 真实路径审计凭证 1 项
skip；原 362 全保留，真实端到端用例仍未配置时双双显式 skip）。

### 6.10 R4d：真实模型首跑前的指标修复与分阶段执行（2026-10-19，当前 HEAD）

R4c 静态验收后，评审点出三个首跑前必须修的洞，R4d 全部落地——**本环境无
API 密钥，真实 VLM 实验仍未执行**，以下全部是"预检"证据；
**不新增 Skill、不扩大图形规则、不蒸馏、不修改冻结基准**。

- **统一真实执行状态（P0-1）**：五态判定唯一实现移入包内
  `assessment_agent.evaluation.execution_status`（`classify_status/health_gate/
  vision_stage_summary`），冒烟与实验脚本同源导入（测试从源码层面禁止第二套
  `classify_status` 存在）。客户端审计新增**分阶段计数** `by_phase`
  （vision_parse/router/verifier/solver_or_skill：http_succeeded/http_failed/
  json_output_valid/json_output_invalid）；`requests_succeeded` 是全部阶段
  HTTP 2xx 总数，**明确不得冒充视觉成功数**（测试断言分阶段计数与总数区分）。
  逐题三段记录写进 `predictions.jsonl` 的 `vision` 明细：
  `vision_http_success / vision_schema_valid / vision_extraction_valid`，
  外加 `evidence_complete`。**EXECUTED = 指定样本全部完成真实视觉解析**：
  HTTP 全成功但 JSON 结构错误 → PARTIAL（"API 成功不等于视觉解析成功"写进
  reason）；1/15 成功 → PARTIAL；mock/注入 transport/离线一律不得 EXECUTED。
  预检探针（注入 transport + 显式披露 mock 标记，只验判定链，不是模型能力
  证据）实证三种链路形态逐级区分：合法 v2 → 三段 True；HTTP 200 但输出
  非 JSON → schema False；旧版纯文本 payload → 结构化 False +
  `legacy_payload_fallback`。结构化判据要求 v2 **区域数组真实非空**，不接受
  `parser` 字段默认值——schemas 默认值就是 `vision_parser`，v1 产物同样带
  该值，这个口径洞正是探针暴露的。
- **统一客观准确率与最终答案口径（P0-2）**：`end_to_end_objective_accuracy`
  只取 `overall_answer_accuracy` 的 value/hit/denominator 三元组（同分母，
  字段附 `caliber` 标注；旧 `answer_accuracy` 改名
  `legacy_answer_accuracy_for_reference_only` 仅供对照，禁止混用）；
  Δ_vision 与 `verified_answer_yield` 同源，且 image_only 未达 EXECUTED 时
  `image_only_objective=None`——未执行的 0.0 禁止进入差值。严格三分：
  `solver_proposed_answer`（=predicted_option，Solver 内部提议）、
  `final_answer`（=predicted，Verifier accept/repair 后实际对外输出）、
  `answered_out_loud`（final_answer 非空且动作∈{accept,repair}）；
  覆盖率/选择性/归因/false_accept 全部只用最终答案。两个反例进测试：
  ①Solver 提议 A + unverified + 无最终答案 → 已作答 False；②旧口径 5/12 与
  新口径 5/14 并存 → 主指标 value==5/14 且 value=hit/denominator 一致。
  bench_holdout 真实数据恰好是该形态（新 6/16 vs 旧 5/14），修复前 value 与
  分子分母来自两套口径。
- **运行隔离与配置一致性（P1）**：每次实验生成唯一
  `experiment_id = UTC 时间戳-pid-摘要`，**贯穿所有子运行的 run_name 与
  eval_batch**（结果目录、`e2e-<batch>-<case>` runs 产物、报告互不重叠）；
  runner/CLI 新增 `no_overwrite / --no-overwrite`：输出目录已存在且非空
  **报错**而非静默覆盖（多次运行保留全部历史凭证）；实验目录同样拒绝覆盖
  （FileExistsError）。manifest 新增 9 个核心代码文件 sha256 + 配置环境
  **变量名清单**（只记哪些存在、绝不记值）；父/子进程配置事实源统一：
  `OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL` 别名随 `LLM_*` 一起透传
  （测试实证别名生效且密钥值不落任何产物）。
- **分阶段熔断（P1）**：Stage1 Smoke（3 题，选择标准运行前声明并锁进
  manifest）→ API+Schema 健康检查 → Stage2 figshots_dev 全量 15 题
  （成功与失败全保留）→ 健康检查 → Stage3 bench_dev 跨题型 35 题
  （不按技能/解析结果/准确率筛样）。任一门失败 → **后续真实调用不发起**，
  报告记录 `BLOCKED_BY_CIRCUIT_BREAKER` 与诊断信息；零样本分母视为
  不可判定，同样熔断；`--force-continue` 是唯一显式越级方式。离线模式
  （oracle/auto）零 API 成本，熔断时仍运行做基础设施回归，但不会被称作
  "真实验证完成"。
- **首跑三个关注指标**入报告顶线：Vision Parse Success（三段全通题数/指定
  样本）、Evidence Completeness（schema 合法之外，金标必需区域全命中——
  漏两个选项或 3×3 识别成一行**不算**成功）、Verified Answer Yield
  （客观可答题中得到正确且通过验证**最终答案**的比例，分母=overall 客观
  分母）。三者决定首跑后的优先级判断（VisionParser / 图形 Solver /
  Verifier），本轮不做任何该层面的调参。
- **预检实跑（本环境，离线）**：`results/real_vlm/20261018T170237Z-23644-5268/`
  ——status=**NOT_CONFIGURED**、audit requests_sent=0、stage1 熔断、stage2 的
  image_only 与 stage3 全部 BLOCKED、Δ_vision=`None/not_meaningful`；
  oracle/auto 的 10/13 等只是 Solver 层离线回归证据。仓库
  `data/benchmark_figshots/phase6_vlm_smoke.json` 重新生成，仍为 NOT_CONFIGURED
  五态语义（what_would_run 指向真实链路口径）。
- 测试：`tests/test_r4d_status_metrics.py` 14 项——同源源码锁、HTTP 全成功
  +schema 非法≠EXECUTED、1/15=PARTIAL、14/15≠EXECUTED、健康门语义（含零
  分母熔断）、三段分子分母与嵌套字段、两反例（unverified 不作答 / 5-12 vs
  5-14）、MockTransport 分阶段计数（200 非 JSON → http_succeeded=1 且
  json_output_invalid=1，且 ≠ requests_succeeded）、no-overwrite 报错、
  隔离源码锁（run_name/eval_batch 贯穿 exp_id、别名透传、熔断标记）、
  manifest 无密钥值有代码哈希、runner 逐题三段字段离线实跑全 False，以及
  **判定链探针**（注入 transport，审计自动披露 mock）实证三种链路形态分层：
  合法 v2=三段 True、HTTP200 非 JSON=schema False、纯文本 payload=结构化
  False+`legacy_payload_fallback`；探针自身永远被判 CONFIGURED_NOT_CALLED，
  不构成任何真实执行证据。
  全量 **387 passed / 2 skipped**（373 项 R4c 基线零退化 + 14 新增；
  1 项 R4b 契约测试的源码窗口从 2000 字符扩到 3200 以覆盖新增审计快照行）。

### 6.11 R4d 收尾：首跑纪律修正 + HTTP 错误脱敏（评审建议，当前 HEAD）

评审 R4d 静态验收通过后的两项收尾（主脚本 health_gate **按评审要求未改动**）：

- **首跑预期纠正（纪律）**：明确 `python scripts/run_real_vlm_experiment.py`
  是**自动分阶段执行器**——stage1 健康门（1/3 结构化成功即过）通过后会连续
  自动跑完 15 与 35 题；`--force-continue` 只是跳过失败熔断的越级开关，
  **不是人工继续按钮**。因此首次真实运行只允许独立入口
  `python scripts/phase6_vlm_smoke.py`（`--case-ids` 固定三题：ft-rot90-01
  离散旋转 / ft-grid-10 九宫格 / ft-overlay-12 图形叠加；输出
  smoke_report.json + llm_evidence.json + predictions.jsonl 三件套供逐题
  人工审查）。评审扩大标准已写入 roadmap：**0/3 停止修 API/Schema；
  1–2/3 保留报告、定位失败、不自动扩大；3/3 先查证据完整率/选项映射/裁剪
  质量再议 15 题；3/3 结构化成功 ≠ 答题正确 ≠ 误放率可靠**。
- **HTTP 错误诊断脱敏（评审建议）**：`llm.py` 不再把响应正文原文写入
  `LLMError`（正文会经 failure_reason / stderr_tail / 报告进入持久化）。
  新增 `_safe_error_summary`：只落状态码、服务端 `error.type` / `error.code`、
  ≤120 字符**打码后**的 message 摘要与 server request id；`redact_secrets`
  屏蔽 `sk-*` / `Bearer …` / `api_key=…` 形态。顺带修复降级判据：
  json_mode 降级只在错误文本明确含 `response_format`/`json_object` 时触发，
  不再把普通 400（鉴权/参数错误）猜测性地去掉 response_format 重试。
  测试 `tests/test_r4d_error_redaction.py` 6 项：正则打码、4xx 异常含
  type/code 不含密钥、非 JSON 正文打码、5xx 重试后仍干净、降级判据正反例、
  注入 transport 端到端 predictions.jsonl 的 failure_reason 无密钥但保留
  `authentication_error` 可诊断信息。
- 当前环境仍无任何 API 密钥：离线重跑 smoke 依旧 **NOT_CONFIGURED**，
  真实 HTTP 联调**尚未执行**；上述纪律与脱敏是为拿到第一份可信真实运行
  证据做准备，不构成任何能力宣称。
- 全量回归：**393 passed / 2 skipped**（387 基线零退化 + 6 新增）。

---

## 7. 测试

```bash
python -m pytest -q
# 393 passed, 2 skipped（真实模型端到端用例在未配置 LLM_VISION_MODEL 时明确跳过）
```

覆盖范围：

- 配置与密钥安全（`.env` 不入库、无密钥字面量、`describe()` 不泄露 key）
- 路由（7 类规则命中、显式指定、离线回退、LLM 兜底、未知技能拒绝）
- 工具（SymPy 精确计算与非法输入、Fraction 独立求值、选项匹配、7 类数列规律、Z3 蕴含/否定/矛盾/带数值差拒绝、图形布局解析、六类规律推断、Skill 端到端与诚实失败）
- 校验（数值篡改 → repair、数列篡改 → repair、逻辑复核、unsupported → reject、图形换阈值复算、Skill 复核异常 → blocking 失败）
- Skill（8 个注册、7 个已支持（R3a 资料分析、R3b 言语理解、R3c 语言推理）、声明式 Skill 绝不返回答案、数列歧义标记 ambiguous）
- 流水线（文本端到端、内联选项解析、截图 + 假视觉模型端到端、缺视觉模型报错、产物完整性、拒答不暴露原始答案）
- 评测（数据集加载、指标统计、失败归因、结果落盘、oracle/auto 路由模式、新指标计算、failed 状态严格匹配、answerable_coverage 不被错误拒答虚高）
- 图形工具接口（布局解析、特征提取、规律引擎、Skill 端到端、诚实失败、换阈值复核）
- Vision Parser（区域协议、越界/过小/空白/重复标号丢弃、裁剪像素证据、旧 payload 兼容、结构化失败）
- 图形布局适配层（题干矩阵+选项裁剪→原六类规律引擎、标号映射保持、题干专属 grid、空白选项诚实失败）
- 截图基准（oracle_solver/auto_router 全离线通过；image_only 用 mock 视觉后端验证全链路机制与产物落盘；真实模型用例未配置时显式 skip）
- R2b 独立基准（region_incomplete 拦截静默整图回退、旧输入兼容、失败归因映射与最上游次序、CER/IoU 单元、漏检留在分母、批次 run_id 隔离、用量快照、benchmark_real 结构/防泄漏/manifest sha256、undecidable 判分链路）
- R2c 可靠性（标注派生与冻结分母、误放/误杀/unverified/over-answer 计数、图形候选代入复核、非方形格框 bbox 锚、消融五臂→七臂→九臂口径）
- R3a 资料分析（39 项：Dev 全部 DA 样本 solved/failed 双套参数化、计划可追溯性、六步验证、单元格/图上文本篡改拦截、缺数/OCR 歧义/单位冲突/同名列歧义结构化失败、年均增长率精确开方、双层表头、统计图像素比例复核、LLM 只能供证据绑定计划、注册表派生 capability_status、开关消融、primary/legacy 口径字段）
- R3b 可靠性（11 项：LLM 合法计划被真正执行并 accept、越界/篡改引用与表达式-存档值不一致阻断、操作↔表达式模板互证、intent 表层检查措辞、伪造计划拒绝）
- R3b 言语理解（14 项：搭配/推断/主旨/论证四类规则核端到端、原文区间逐字验证与篡改/越界/提问区引用拦截、LLM 逐选项分析+区间绑定执行通道、伪造证据/漏分析/坏标签拒绝、cloze/argument 自洽
- R3c 命题归属（18 项：六状态 epistemics 判别、嵌套归属范围、最小对比对（P 固定换外层谓词）、否认双向阻断、旧盲集 inf-09 机制锚通用变体、审计层独立拦截 supported 越权、Skill 端到端拒传闻/接归属对齐）
- R3c 语言推理（15 项：七类关系方向显式、反序 partial、多重匹配拒答、词典外诚实 failed、source_text 篡改 reject、LLM 提议三态（一致绑定/不一致拒绝/词典外拒绝）、消融开关复现 R3b 误放、路由优先语言）
- R3c 数据守护（7 项：epistemic/language/blind/blind2 数量与 sha 对账、盲集间题干互斥、旧 blind 冻结不变、首跑记录存在、行格式与 answer/expected 一致性）复核不得给 LLM 确定性通过、真歧义拒选、开关消融、verbal 基准结构+盲集 sha256 冻结+防泄漏守护）
- R4a 语言闭包（9 项）+ 命题预测状态（21 项：predicted 六态、最小对比、审计层、开放世界词典守卫）
- R4a 图形变换（15 项：标准表示相对量、D4 组合表封闭、离散旋转/镜像/平移/缩放、九宫格行列冲突 ambiguous、叠加 xor/or、标签位置盲、对称竞争项披露、画布失配降级）
- R4a 变换回归锁定（4 项：FV 等价类不择一、R180 自对称步进不可区分、容差贴边不放宽、稳定子披露）+ figtrans 基准守护（6 项：manifest sha、诚实 failed 保留、test 冻结+首评对账、split 注册、Phase6 NOT_EXECUTED 语义、消融四臂底线）
- R4b 可靠性（13 项：完整页面 bbox 有界+crop 像素再导出+字母/说明文字像素存在、figtrans 冻结集字节不变、部分确认→unverified 反例、all_confirm 政策断言、无全局 FRAME 突变、并发/中断/顺序调换一致、parameter_stable 与 independently_verified 分离且代入未过不作答、layout 标签零影响（四取值逐位一致）、image_only 输入合同源码断言、Phase6 figshots NOT_EXECUTED）
- R4c 真实 VLM 凭证与归因（11 项：五状态分类纯函数全分支（含"退出码 0+run_ref 非空≠EXECUTED"与"全 401 绝不 EXECUTED"反例）、mock transport 拒绝、客户端审计结构与非敏感（密钥不落审计）、runner 恒产 llm_evidence（离线 requests_sent=0）、仓库冒烟报告 configured 分支零写盘（源码断言）、冒烟 case_ids/limit 运行前声明、归因字段消费 compare_extraction_detail 的 grid/裁剪新字段（None≠0）、冻结报告 NOT_CONFIGURED+无 figtrans_dev 残留）
- R4d 指标修复与分阶段执行（14 项：smoke/实验**同源**判定源码锁（包内唯一实现，脚本禁私有 classify_status）、HTTP 全成功+JSON 非法=PARTIAL（"API 成功不等于视觉解析成功"）、1/15 与 14/15 均不得 EXECUTED、健康门（含零样本分母=不可判定熔断）、三段分子分母与嵌套 vision 字段消费、反例①unverified 提议不作答、反例②5/12 旧口径 vs 5/14 新口径 value 同分母、MockTransport 分阶段计数与 requests_succeeded 区分、no-overwrite 二次运行报错、隔离源码锁（exp_id 贯穿 run_name/eval_batch、OPENAI_* 别名透传、熔断/越级标记）、manifest 无密钥值 + 代码/环境变量名哈希锁、runner 离线逐题三段全 False、注入探针三形态分层（v2=三段 True/非 JSON=schema False/纯文本=结构化 False+降级标志，探针自身必判 CONFIGURED_NOT_CALLED 不构成真实执行证据））
- R4d 收尾错误脱敏（6 项：redact_secrets 打码 sk-*/Bearer/api_key= 形态且不误伤正常诊断、4xx 异常含 error type/code 与 server_id 但不含正文密钥、非 JSON 错误正文只留打码短摘要、5xx 重试链全程干净、json_mode 降级判据正反例（明确格式拒绝才去 response_format，普通 400 不猜）、注入 transport 端到端 predictions 的 failure_reason 无密钥但 authentication_error 可诊断）
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
| 图形推理 | ✅ R2~R4b：网格解析 + 六类规律 + **离散旋转/镜像/平移/缩放/组合/九宫格/叠加变换通道**（D4 群 + 画布级叠加判别 + 单因子 all_confirm 扫描 + 几何候选替代独立验证 + parameter_stable/independently_verified 分账 + **完整页面截图基准 figshots**）；R4b 已封堵扫描计数洞、layout 决策耦合与题图缺选项三项 P0；待实现：曲线/交点数量律、多步复合链规律、真实截图 VLM 端到端（Phase6 仍 NOT_EXECUTED） | OpenCV + NumPy |
| 下一步顺序（评审确认，2026-10-19 更新） | ① **首跑只允许 3 题独立 Smoke**：配置密钥后运行 `python scripts/phase6_vlm_smoke.py`（内部已 `--case-ids` 固定 ft-rot90-01 / ft-grid-10 / ft-overlay-12 三题，运行前声明、不按结果筛样）；**注意 `run_real_vlm_experiment.py` 是自动分阶段执行器**（stage1 健康门 1/3 结构化成功即自动扩到 15/35，`--force-continue` 只是跳过失败熔断的越级开关，不是人工继续按钮），按当前纪律首跑**不得**直接运行主脚本。跑完提交 `smoke_report.json` + `llm_evidence.json` + `predictions.jsonl` 三件套人工逐题审查 → ② **扩大标准（评审）**：0/3 结构化成功=停止先修 API/模型参数/Schema；1–2/3=保留报告定位失败原因、**不自动扩大**；3/3=先查证据完整率、选项映射与实际裁剪质量，再决定进入 15 题 Dev。**3/3 结构化成功 ≠ 3/3 答题正确 ≠ 真实场景误放率可靠** → ③ 首跑按三顶线指标（Vision Parse Success/Evidence Completeness/Verified Answer Yield）的归因决定优先修 VisionParser、图形 Solver 还是 Verifier → ④ 冻结模型与算法配置后构建新跨题型盲测（合成/人工/授权真实三层，第三类缺失如实标注）→ ⑤ 才评估 R5 蒸馏（先回答：真实视觉解析是否破坏题干信息/表格结构/选项映射、错误解题轨迹会不会被验证器误收）。**R5 与新增 Skill 均未启动**；当前状态=NOT_CONFIGURED（本机无密钥，HTTP 联调尚未执行） | 真实 VLM 密钥配置（用户提供）+ 首跑人工逐题核对 |
| 资料分析 | ✅ R3a 已实现：表格/柱状图结构化 + 计算计划 + Fraction 求值 + 六步独立验证；待实现：折线/多图复合表格、更复杂表头合并与真实截图 OCR 端到端验证 | OpenCV + 表格校验器 +（真实 VLM 待配置） |
| 言语理解 | ✅ R3b+R3c：选词搭配、主旨结论覆盖、推断否定/量词/情态核对 + **命题归属层**（传闻/假设/否认不升格为事实）、字面条件链 Z3；待实现：跨句指代、近义重表述主旨、预设/蕴含（"否认说法⇒说法存在"）形式化、肯后式推断 | 带证据的 LLM 输出 + 校验器 |
| 语言推理 | ✅ R3c：词典七类方向关系 + 判别性覆盖 + 六项验证；待实现：词库规模化（当前 ~120 中文词条）、程度关系、词性/语义角色校验、真实 LLM 提议通道验证 | 知识库检索 + LLM |
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
