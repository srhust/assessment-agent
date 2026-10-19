# R6 验收材料（提交人工裁决）

日期：2026-10-19（本地时区）。裁决范围 = 工单 P0-A / P0-B / P1。
本轮**未运行**完整 image_only、figshots_test、蒸馏；R5 冻结集仅做一次性
完整性复现与逐题行为 diff（诊断用途），**未做任何阈值/规则调参**。

## 0. 声明：R5 headline 未被回写

- R5 冻结头条 **oracle_solver = 22.08%（17/77）**、answered 19/77、false_accept 2、
  诚实弃权 10/10 的历史记录（`evidence/xingce_headline_r5/`）**一字未改**；
- `data/benchmark_xingce/` 的 87 行金标、题图、manifest 冻结字节全部保持原样
  （143/143 SHA-256 与冻结清单一致，见 §1）；
- 本轮修复使 xn-nc-05/08 两题在**新运行**中变为受控弃权——这是行为修复，
  **不重算、不重命名** R5 分数；17/77 永久冻结。

## 1. 任务A（P0-1）R5 冻结包自包含 — ✅

提交：`103929a`（入库）+ `2c7e65e`（克隆验收）。

- 根因确认：`.gitignore` 的 `data/**/*.png` 缺少 benchmark_xingce 例外 →
  87 张整页 PNG + 55 张区域裁剪从未入库（评审 404 复现路径成立）；
- 修复前逐文件核验：**入库前本地原字节 143/143 SHA-256 与冻结 manifest 一致**
  （`evidence/xingce_frozen_verify/local_precommit.json`，verdict=PASS）。
  142 张 PNG 为 R5 首次生成的原始文件，**零重渲染**；
- 原 manifest 冻结字节不改动；新增路径勘误件
  `data/benchmark_xingce/MANIFEST_ERRATA.md` 与 POSIX 规范键可移植清单
  `MANIFEST_PATHS.json`（143 项，值与原 `sha256_files` 完全相同，仅修正
  反斜杠分隔与 `xingce.jsonl`→`test/xingce.jsonl`）；
- 干净克隆验收（`git clone file:///…` 只含已提交字节）：
  - `scripts/verify_xingce_frozen.py --root .` → **143/143 PASS**
    （`evidence/xingce_frozen_verify/clean_clone_verify.json`）；
  - 克隆内零 API oracle_solver 全量复现（`r6_clone_repro`）：
    **17/77（22.08%）、answered 19/77、honest_abstention 10/10、
    false_accept_total=2、failure_classes={solver_error:55, unsupported_skill:4,
    verifier_false_accept:2}** —— 与冻结 headline 逐数一致
    （metrics/summary/predictions 全量归档于 `evidence/xingce_frozen_verify/`）。

复现命令（任意平台克隆后）：

```text
python -X utf8 scripts/verify_xingce_frozen.py --root .
PYTHONPATH=src ASSESSMENT_R4G_LAYOUT=off python -X utf8 -m assessment_agent eval \
  --split xingce_test --eval-mode oracle_solver --run-name repro --no-overwrite --json
```

## 2. 任务B（P0-2）两例 false_accept 逐层根因 + 通用修复 — ✅

提交：`1a6f952`。中间产物取自管线复算（`word_problem` 计划、表达式、逐检查项
detail 全文见下方表格；验证检查名为真实字段输出）。

### xn-nc-05（金标 D=216，释放 A=240）

| 层 | 实际发生 | 证据 |
|---|---|---|
| 题意解析 | `word_problem` 只命中 discount 关系并抽取"原价 300 × 折扣 8折"，**丢弃"再降价 10%"第二条件**（提取器现仅支持单段折扣） | 计划 steps=`[["result","300*4/5","原价 300 × 折扣 8折"]]`，1 步 |
| Solver 公式 | `300*4/5` | meta.expression |
| 中间运算 | =240，**算术本身正确** | steps"Fraction 精确求值 = 240" |
| 选项映射 | 240→A，映射正确 | matched_label=A |
| Verifier 独立检查依据 | `word_problem.chain_recompute`＝**重放 Solver 自身计划**（计划残缺则重放同样残缺，自确认）；`math.independent_recompute`＝对**同一表达式**做 Fraction 独立求值（证明"240 算对了"，不证明"240 是题目要的"）；`math.claim_consistent`、选项数值一致性 | 检查项 detail 原文 |
| 为何 accept | 全链内部自洽 → blocking 全过 → accept。**没有任何一环核对"题干数量是否被计划消费"**（题干 {300,8,10}，计划消费 {300,8}） | 修复前 actions=accept |

### xn-nc-08（金标 A=11.25%，释放 D=15%）

| 层 | 实际发生 |
|---|---|
| 题意解析 | `word_problem` 无浓度模型→失败；**退化到表达式通道**从题干抽字面数 `15*(1/100)`，"60 克""20 克"零绑定 |
| Solver 公式 | `15*(1/100)` |
| 中间运算 | =0.15，算术正确 |
| 选项映射 | 0.15→D"15%"，映射正确 |
| Verifier 依据 | 同一表达式独立求值 0.15 ✓ + "选项 D 数值 0.15 vs 复核 0.15" ✓ → accept |
| 为何 accept | 验证维度只有算术自洽；**答案恰为题干原始浓度的搬运**却无异常标记。加水稀释的语义（新浓度=盐/新总量）从未被检查 |

### 修复（通用不变式，无 case-id 规则、无 216/11.25% 模板）

`skills/numeric_calculation.py::verify()` 新增两个 blocking 检查，失败即
`action=unverified、final_answer=None`（**禁止无完整计算证据的释放**，与评审要求
"独立复算不可行则返回 unverified"一致）：

1. `numeric.operand_coverage`：题干全部数字 token（通用豁免 4 位年份）必须出现
   在计算计划的操作数/步骤依据文本中；遗漏 → unverified；
2. `numeric.degenerate_copy`：单步计划且结果只是某题干数量的同值/百分数换算
   搬运（值 ∈ {s, s/100, 100·s}）→ unverified（拦截"数字搬运冒充计算"，
   Dev 上抓出的第 3 类误放 xd-nc-09 即此型）。

回归与零误杀证据：

- R5 numeric 12 题逐题 diff：**仅 xn-nc-05/08 由 accept→unverified**，其余 10 题
  判定逐字不变（17 个既有正确释放未受影响）；
- benchmark_real dev numeric 2/2 仍 100%（无退化）；
- xingce_dev 13 道 numeric：误放路径全部拦截，**false_accept_total=0**；
  正控制题 xd-nc-19（单段折扣应释放）正常释放且答对（误杀哨兵）；
- 单元测试 `tests/test_numeric_operand_coverage.py` 6 项，用例数字全部与 R5 不同；
- 全量 `pytest tests` → **462 passed / 2 skipped**（456 基线 + 6 新增，零回归）。

修复后两题行为=受控弃权（不再错误释放）。**在 Solver 获得多段折扣/浓度计算能力
之前，这两题不会被答对——本轮目标是不再错误释放**；能力开发已列入 Dev 靶点。

## 3. 任务C `xingce_dev` 开发集 — ✅

提交：`22c6da8`。split 已注册（runner + cli），23 题：

- 覆盖工单要求的全部变体：**连续折扣**（nc-01/02 两段/先涨后折）、**浓度变化**
  （nc-03 加水、nc-04 加盐）、**行程**（nc-05 相遇、nc-06 追及）、**比例**
  （nc-07 分配、nc-08 地图尺度）、反百分比/复合增长（nc-09/10）、两步链（nc-21）、
  **资料分析文字材料**（da-11/12 无表材料、da-13 增长量）、**多解类比**
  （ana-16，answer=None/undecidable）、翻译推理（lg-14/15）、分数/素数数列
  （ns-17/18）、计数图形（fig-19 三角形数）、复合变换九宫格（fig-20 双参数旋转律）、
  **正控制误杀哨兵**（nc-19/20，当前规则必须能释放）。
- 纪律：全部独立场景/句式/数字设计，**不是 R5 换数字**；构建时对**全语料
  （含 benchmark_xingce 测试集）**做内容级防泄漏检查，**0 命中**（命中即拒绝构建，
  过程中实际抓到并修正了 1 处真题对复用与模板句误报）；金标全部 Fraction
  复算断言先于渲染固定。
- 非 held-out：dev 可反复用于规则开发与回归（`dev_info.json` 声明）。
- Dev 首跑诊断（oracle_solver，`results/xingce_dev/r6c_dev_v2`）：**false_accept_total=0**；
  行为分层如实——xd-nc-19 唯一正确释放（accept 且答对，误杀哨兵通过）；
  解析失败不释放（reject）与证据不足（unverified）分层清晰；
  多解类比 ana-16 受控弃权计为合规。

## 4. 任务D 布局指标拆分 + ft-rot270-03 勘误 — ✅

提交：`136f26b`（漏斗）+ `370cd8b`（勘误）。

- 八项指标全部落字段（`scripts/layout_funnel.py` + runner 诊断组）：
  runtime_layout_verified / **gold_grid_correct（离线-only）** / complete_cell_recall /
  target_blank_localized / geometric_evidence_usable / solver_proposed /
  independently_verified / final_released，逐题两栏并列输出，**永不互推**；
- runner 在 oracle/auto 分支现记录 Solver **实际消费**的重建
  （`meta.diagram_layout` → detected_rows/cols/units + runtime 自检组，金标无关）；
- `ft-rot270-03` 复核（R4g C 臂历史证据 `evidence/r4g_ablation/`）：
  金标 1×4；重建 `layout_cols=3` 且 `layout_verified=**true**`——评审断言成立。
  自检语义=已声明区域间像素互洽，空白格未声明时 3 已知格互洽照样放行；
  **target_blank_localized=false，complete_cell_recall=0.75**；
- 且为**系统性**：序列类 9/15 题 runtime 通过而金标列数不符
  （`evidence/xingce_layout_funnel/r4g_rot270_erratum.md` 全表）；
- 结论采纳：任何"视觉结构完整"声明必须引用 gold_grid 离线栏；
  runtime self-consistency 单独报告。

## 5. 提交链（本轮 R6 全部 commit）

| SHA | 内容 |
|---|---|
| `103929a` | 任务A：142 PNG 原字节入库 + .gitignore 例外 + 勘误清单 |
| `2c7e65e` | 任务A：干净克隆 143/143 PASS + 零 API 复现归档 |
| `1a6f952` | 任务B：operand_coverage + 根因 + 回归 + 单测 |
| `22c6da8` | 任务C：xingce_dev 23 题 + degenerate_copy + dev 验证 |
| `136f26b` | 任务D：八项漏斗 + runner 重建字段 |
| `370cd8b` | 任务D：R4g rot270 勘误（9/15 系统性少列） |
| （本篇） | 任务E：验收汇总 |

## 6. 已知边界（如实声明）

- Dev 上 Solver 多段折扣/浓度**仍不会算**（本轮只修验证释放纪律）；
- oracle 分支的 stem regions 注入形态与 R4g ablation（真实 VLM payload）不同，
  figtrans dev 漏斗里 runtime 组多为 false+`no_stem_regions_or_image`——如实呈报，
  image_only 全量评测留待下一阶段（工单第 7 节流程）；
- R5 manifest 的 rebuild_log 只记最后一次的既有局限不变（DATASET_CARD §3）；
- 本轮未运行：完整 image_only、figshots_test、蒸馏、R5 任何重评分。
