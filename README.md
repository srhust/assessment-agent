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
└───────┬───────┘
        ▼
┌───────────────┐   规则打分优先，LLM 兜底；可显式指定题型
│    Router     │   route.json（skill / 来源 / 得分 / 是否歧义）
└───────┬───────┘
        ▼
┌───────────────┐   Skill = 题意归一 + 选工具 + 组织步骤
│    Solver     │──▶ tools: python_math (SymPy) / number_series / z3_logic / figure_tools
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
│   ├── vision.py          # 截图 -> ExtractionResult
│   ├── router.py          # 题型路由（规则 + LLM 兜底）
│   ├── solver.py          # Skill 调度与异常归一
│   ├── verifier.py        # 独立校验（结构 + 题型复核 + 可选 LLM 交叉校验）
│   ├── pipeline.py        # 端到端编排与产物落盘
│   ├── artifacts.py       # 运行目录、trace、Markdown 报告
│   ├── text_utils.py      # 题干/选项拆分
│   ├── cli.py             # solve / eval / skills / doctor / inspect / mcp-manifest / call
│   ├── integration.py     # DSH / MCP 接入层（工具清单 + tools/call 形状调用）
│   ├── skills/            # 八个题型 Skill（4 个 MVP + 4 个声明式接口）
│   ├── tools/             # SymPy 计算、数列规律、Z3 约束、图形布局与规律引擎
│   ├── knowledge/         # 知识库加载器（Markdown）
│   └── evaluation/        # 数据集加载、指标、评测执行器
├── data/{train,dev,test}/ # 按题型分文件的 JSONL 数据集
├── knowledge/             # 八个题型的方法论笔记
├── tests/                 # 114 个 pytest 单元测试
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
data/train/  data/dev/  data/test/
```

样本为一题一行的 JSONL，字段见 [`data/README.md`](data/README.md)。评测强制路由到样本声明的题型，
并统计多维度指标：

```bash
python -m assessment_agent eval --split test
```

**评测指标说明：**

| 指标 | 定义 | 说明 |
| --- | --- | --- |
| **Answer Accuracy** | 正确解答数 / 有标准答案的题目数 | 衡量解题能力 |
| **Coverage** | 提供通过校验答案的题目数 / 全部可评分题目数 | 衡量系统覆盖率 |
| **Selective Accuracy** | 已通过校验的答案中，真正正确的比例 | 衡量答案可信度 |
| **Abstention Accuracy** | 应当拒答的样本中，正确拒答的比例 | 衡量诚实拒答能力 |
| **Router Accuracy** | 自动选择正确 Skill 的比例 | 衡量路由准确率 |

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

---

## 7. 测试

```bash
python -m pytest -q
# 114 passed
```

覆盖范围：

- 配置与密钥安全（`.env` 不入库、无密钥字面量、`describe()` 不泄露 key）
- 路由（7 类规则命中、显式指定、离线回退、LLM 兜底、未知技能拒绝）
- 工具（SymPy 精确计算与非法输入、Fraction 独立求值、选项匹配、7 类数列规律、Z3 蕴含/否定/矛盾/带数值差拒绝、图形布局解析、六类规律推断、Skill 端到端与诚实失败）
- 校验（数值篡改 → repair、数列篡改 → repair、逻辑复核、unsupported → reject、图形换阈值复算）
- Skill（8 个注册、4 个 MVP、声明式 Skill 绝不返回答案）
- 流水线（文本端到端、内联选项解析、截图 + 假视觉模型端到端、缺视觉模型报错、产物完整性）
- 评测（数据集加载、指标统计、失败归因、结果落盘）
- 图形工具接口（布局解析、特征提取、规律引擎、Skill 端到端、诚实失败、换阈值复核）
- DSH / MCP 接入（清单结构、`tools/call` 形状、未知工具与非法参数、能力输出脱敏、`save=false` 不落盘）
- CLI（solve / eval / skills / doctor / mcp-manifest / call 的返回码与输出）

截图流程的测试使用 `FakeChatModel` 注入，无需真实 API Key。

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
