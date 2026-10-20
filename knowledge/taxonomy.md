# R7 阶段A2：题型与考点分层分类

依据《2026 国考公共科目大纲》原文：行测分**六部分**——政治理论、常识判断、言语理解与
表达、数量关系、判断推理、资料分析（题量拆分仅第三方数据，不作金标）。本分类覆盖其中
四个可形式化域 + 言语域；personality 域保持仅拒答；政治理论/常识判断登记为能力缺口。

## 数量关系（numeric_calculation + number_series）
- 基础：百分比、比例分配、小数分数互化 → `nc.ratio-share-parts`、`nc.successive-discount`
- 变化：连续折扣/利润/复合变化 → `nc.successive-discount`、`nc.profit-margin-base`、`nc.compound-growth`
- 经典模型：行程（相遇/追及/流水）、工程（效率/公倍数）、浓度（溶质守恒）→ `nc.travel-meeting-pursuit`、`nc.work-rate-lcm`、`nc.concentration-conservation`
- 计数：排列组合、概率、容斥 → `nc.inclusion-exclusion`（排列组合/概率 2 批，来源改用大纲例题+MIT Ch14/16-18 定理级）
- 混合：十字交叉 → `nc.alligation-cross`
- 数列：数字推理（大纲保留题型，双分支建模：规则枚举 + LLM 规划）
- 缺口登记：几何最值、抽屉原理（大纲例题未涉，暂不建卡）

## 资料分析（data_analysis）
- 口径：基期/现期、同比/环比、时间口径（月均/日均/累计→累计权威定义 NO_PUBLIC_SOURCE，不建卡）→ `da.base-period`、`da.yoy-mom-period`
- 变化量：增长率、增长量、百分点 vs 百分数 → `da.growth-amount`、`da.pct-vs-points`
- 比值：比重、倍数、平均数及其增长率 → `da.two-period-share`、`da.average-growth`
- 复合：隔年增长、年均增长率、乘积/比值增速 → `da.interval-growth`（并入 compound）、`da.annualized`
- 输入形态：文字材料/表格/统计图 → **统一事实表表示**（`tools/text_material.py`），运算与证据绑定同一条链路
- 速算：直除、百化分、截位、n+1 法则（估算必须在有精确互验时才释放）

## 判断推理（logical_reasoning + figure_reasoning + language_reasoning）
- 图形：位置/属性/数量/样式，旋转/镜像/平移/缩放/叠加（figure_transform 域已有实现，R6 暂缓 87 题批量 image_only）
- 命题逻辑：蕴含方向、充要条件、矛盾/否一推一、真假话多主体、量词与集合 → `lg.implication-direction`、`lg.contradiction-transform`、`lg.truth-lie`
- 约束推理：排序、集合匹配 → `lg.arrangement-constraints`
- 论证：加强/削弱/前提 → `lg.strengthen-weaken`
- 类比：外延/逻辑/对应/语义关系类型 → `lg.analogy-relations`（词库证据绑定，无证据不释放）
- 定义判断：要素拆分与关键词核对（当前规则未覆盖，登记批次 2）

## 言语理解（verbal_understanding）
- 主旨意图（对策优先）→ `vu.main-idea-countermeasure`
- 细节判断（三类偷换）→ `vu.detail-shift`
- 逻辑填空（语境+八维）→ `vu.cloze-context-eight`
- 排序衔接（首句判定+标志词捆绑）→ `vu.sentence-ordering`
- 指代/复位/接下来/标题（大纲篇章 5 小题）→ 批次 2
- 论证结构（观点-证据）→ 与 `lg.strengthen-weaken` 共用结构抽取

## 语言推理（language_reasoning，类比/近反义）
- 词义关系：种属/组成/工具/场所/近反义 → 词库 `lexicon_r3c.json` 证据链（hyper/parts/tools/function 字段）

## 不实现域（诚实登记）
- **政治理论**（大纲六部分之一）：知识+方针依赖，无法形式化验证 → 缺口，不做
- **常识判断**：同上；现系统行为=无证据拒答，维持
- **personality**：设计即 reject-only，不参与解题升级

## 方法卡规范（A3）
`knowledge/methods/<method_id>.json`，12 必填字段：method_id/skill/problem_family/
concepts/applicability_conditions/required_quantities/reasoning_operations/
solution_procedure/verification_strategy/common_pitfalls/source_references/version。
来源必须在 `knowledge/sources.yaml` 登记（链接+访问日期+许可）；评测答案与逐题错误
**永不进入**方法库。生成/修订唯一入口 `scripts/gen_method_cards.py`。
