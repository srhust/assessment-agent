# R9 Stage C+D 阶段报告（截至本轮，Stage E 前）

生成：2026-10-21 · 代码基线 `0268df7`（链：`8ea29ba A → 34c92be/1cb4d9e B → bef5be9/c97be26/6c3416c/84cb9a6/a2f8a37/8db3b8d C → 90af3b1/0268df7 D`）· 全量测试 **567 passed / 2 skipped**（零 API）

## 1. 六态矩阵（真实探测，非愿望标记）

| 态 | 计数/47 | 探测方式（全部代码实证） |
|---|---|---|
| documented | 47 | `MethodKB.validate_all()` 无该卡错误 |
| retrievable | 47 | `kb.search(family+concepts)` top-3 自命中 |
| plannable | 26 | 逐技能确定性 API 实构 |
| executable | 42 | 卡面算子文本过 `planner.scan_missing_operators` + 卡声明缺算子 |
| verified | 35 | **live `solve(llm=None,真实toolbox)→verify`**，要求 ≥1 独立检查 |
| dev_passed | 8 | dev 题金标命中（tag 或**运行证据 method_refs**：模板实测抽取且算对才建映射） |

六态全真卡（8）：`da.growth-amount, nc.compound-growth, nc.concentration-conservation, nc.profit-margin-base, nc.ratio-share-parts, nc.successive-discount, nc.travel-meeting-pursuit, nc.work-rate-lcm`。
其余缺口：lg/vu/lr/fig 的 verified 失败原因逐卡记录于 `evidence/R9C_SIX_STATE_MATRIX.json`（如 vu `no_covering_option`＝规则核离线无词汇证据通道，属 LLM 解析域，非谎报）。

## 2. Stage C 交付（执行算子 + 事实核）

- **executor**（`planner.py`）新增：`C(n,k)/P(n,k)/n!(0..1000)/整数幂(右结合,负指数)/sqrt(含分数)/ceil/floor/pow/abs`；一元负号在幂前（`-2^2=-4`）。**显式弃权**：开 n 次方根、log、三角（`MISSING_OPERATORS` 具名）。
- **word_problem 确定性模板**新增 11 族（浓度守恒/相遇/追及含领先式/多阶段百分复合/净率链/恢复原价/工程合作/单位数量/地图比例尺/两步计划/中间份额），全部 Fraction 精确、缺条件即 None（绝不猜）。
- **rule_regions**（封闭区域数列）：常数洞**不声称**；verify 侧仅非常数等差登记且 **advisory**（确认性证据，不参与否决扫描）——面数等差≠规律，教训固化。
- **fig 卡显式限制**：solid-view（折叠无确定性引擎）、count-elements（交点/笔画/直角无拓扑提取）按实际算子面逐项声明。
- 测试：executor/regions/templates 三套 **5 类样本**（正常/反向边界/典型错答/不足条件/独立复核）共 54 个新用例。

## 3. Stage D 交付（独立变体 corpus + 统一 8-metric + 消融）

`xingce_dev_variants`：44 题（5 类/算子族，参数化生成、金标 Fraction 复算、**与 blind_v2 完全无关**）。

**指标集为本轮显式声明**（工单八指标原文未能从记忆库恢复，人工裁定前以下列定义为准）：M1 解题率 / M2 全题准确(弃权计错) / M3 已答精度 / M4 误答率 / M5 弃权率 / M6 正确弃权率(不足条件类) / M7 独立复核覆盖率 / M8 p95 延迟。

| corpus | 臂 | M2 | M4 误答 | M6 | M7 |
|---|---|---|---|---|---|
| xingce_dev(26) | rules_only | 0.308 | **0.273** | n/a | 0.73 |
| xingce_dev(26) | rules+templates | **0.808** | **0.000** | n/a | **1.00** |
| variants(44) | rules_only | 0.000 | **1.000**(9/9 全错) | 1.0 | 0.11 |
| variants(44) | rules+templates | **0.682** | **0.032**(1/31) | 1.0 | **1.00** |

关键结论（均有逐题预测文件支撑）：
1. **模板的净效应主要是消灭误答**，不是刷准确率：variants 臂 rules_only 答出去的 9 题全错；模板把误答率压到 0.032。
2. 变体集立刻暴露 9 处表面形式缺口（先走/加盐N克/连续两次各/与配对/较多/裸分/图上N厘米/恢复原成本/混合升降）——修的是**通用表面规则**，非单题补丁。
3. 两个真缺陷被变体集抓到并修复：(a) 原始表达式通道"碰巧等值"抢跑（改让位语义模板）；(b) degenerate_copy ×100 等值类把比例尺"1"误当百分搬运（收紧到带%数）。
4. 剩余缺口如实保留：5 道 expr-operators 题——executor 会算 `C(6,2)` 但**旧表达式抽取器**把括号切坏（`'2)+2**4'`），已定位为抽取层缺口非能力谎报；1 道 wrong_answer（dv-net-growth 问句变体）。9 道 insufficient 全部正确弃权但 runner 口径计错（M6 单独呈现）。

## 4. 纪律自查

- 未动任何历史冻结产物（blind_v2 零触碰；`R9B_FOUR_CONDITION.json` 一次误覆盖已 `git checkout` 复原，SHA 校验一致）。
- dev 增益≠泛化：全部结论限定在 dev/variants 口径。
- 未为准确率放松任何核验：本轮两处 verify 改动均为**语义收紧**；false_accept 仅存 1 例且如实入档。
- 正确弃权与答对分开计数（M2 vs M6）。

## 5. Stage E 计划（待执行，预算先声明）

多模块截图 Dev 对照 text oracle：**复用 R8 已缓存 extraction**（`runs/e2e-r8_e_img-*/`，零新 VLM 调用）做四条件配对的 solver+verify 侧；新 VLM 抽取仅对无缓存题，预算上限 **LLM_CALL_BUDGET=30**，单轮执行，模型/prompt 冻结自 `scripts/r61_dsh_env.py` 当前配置。若缓存覆盖率不足则先报告覆盖数再决定是否动用预算。
