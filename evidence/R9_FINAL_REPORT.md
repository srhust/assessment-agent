# R9 总报告：解题能力与真实截图链路专项突破（Stage A–F 全链，停于人工裁决点）

基线 `19e2cae` · 终态 SHA **`8cee8e3`** · 全量回归 **570 passed / 2 skipped**（零 API）· 生成 2026-10-21

## 一、八项交付物

| # | 交付物 | 路径/位置 |
|---|---|---|
| 1 | Stage A：v2-60 逐题静态审计 + 双独立检查（material_fact_consistency / proof_option_alignment） | `evidence/GOLD_VALIDITY_ERRATA_R9.md`（valid 41 / flawed 4 / invalid 2 / uncertain 13；raw 15/60 永久保留不改） |
| 2 | Stage A：R8 首评原始脱敏入库 + budget.exhausted 方向修复 | predictions/metrics/逐题验证/用量审计/输入 SHA（冻结 SHA `8aea5255603652de`）；修复在 B 阶段提交内 |
| 3 | Stage B：四条件配对诊断（A 13/13 · B 12/13 · C 0/13 · D 0/13，A/B/C 仅离线诊断） | `evidence/R9B_FOUR_CONDITION.json`；残差定性=Solver 规则非唯一性，非布局；部署维持路径 D(image_only) |
| 4 | Stage C：47 卡六态矩阵（真实探测）+ executor 算子 + 11 确定性模板族 + 图形能力显式限制声明 | `evidence/R9C_SIX_STATE_MATRIX.json`：documented 47 / retrievable 47 / **plannable 39** / executable 42 / **verified 35** / **dev_passed 8**；六态全真卡 8 张 |
| 5 | Stage D：独立变体 corpus（44 题 ×5 类，金标 Fraction 复算，与 blind_v2 无关）+ 工单口径统一报告 + 三消融臂 | `data/benchmark_xingce_dev/variants/`、`evidence/R9D_UNIFIED_METRICS.json`、`evidence/R9D_METRICS_ABLATION.{json,md}` |
| 6 | Stage E：独立 Dev 六模块 26 张真页截图 ↔ 文本 oracle 配对**单跑**（预算先声明 120） | `evidence/R9E_PAIRED_EVAL.json` + `results/xingce_dev/r9e_dev_imageonly/`（predictions/metrics/llm_evidence/逐题 runs+overlays） |
| 7 | 核验诚实性突破：最后一起错误接受根因封堵（题面直读第二独立通道） | `src/assessment_agent/skills/numeric_calculation.py` `numeric.stem_direct_parse` + `tests/test_r9d_stem_direct_parse.py`（3 例锁定） |
| 8 | 纪律自查 + 成本审计 + 下轮建议（本报告） | 本文档 |

## 二、各阶段关键数字（全部限定 dev/variants 口径，非独立泛化）

**Stage C/D 能力提升**：xingce_dev 38.46%→**84.62%**（numeric 1/13→13/13）；变体集 deterministic 可答正确释放率 0→**0.8824**。

**消融（工单八项指标原文，本轮已从工单恢复并逐一对应）**：

| corpus/臂 | 可答正确释放率 | 候选覆盖率 | 候选正确率 | 错误接受数 | 正确弃权 | LLM 调用/tokens/重试 |
|---|---|---|---|---|---|---|
| xingce_dev rules_only | 0.3077 | 0.4231 | 0.7273 | 0 | — | 0 |
| xingce_dev rules+templates | 0.8077 | 0.8077 | **1.0** | 0 | — | 0 |
| xingce_dev rules+planner | **0.8462** | 0.8846 | 0.9565 | 0 | — | 3 call / 8.8k tok / 0 重试 |
| variants rules_only | 0.0 | 0.2647 | 0.0 | 1* | 10/10 | 0 |
| variants rules+templates | 0.8824 | 0.9118 | 0.9677 | **0** | 10/10 | 0 |
| variants rules+planner | 0.8824 | **0.9706** | 0.9091 | 1*(修复前产物) | 10/10 | 19 call / 62.5k tok / 0 重试 |
| full_pipeline（=Stage E 截图端到端单跑） | 0.80(20/25 可答) | 0.8 | 1.0 | **0** | 1/1 | 32 成功/36 发送/4 ReadTimeout / 64.6k tok |

\* 唯一残留错接 dv-expr-03（`-2^4+2^4` 被抽取器丢负号→32，复核复算了 Solver 自己的错误抽取=工单禁止的"同一计划算两遍"）。已封堵：Verifier 现对纯算术题面**独立再解析**（0≠32→unverified），deterministic 臂实测错误接受=0；planner 臂产物为修复前单跑存盘，不重跑触 API，跨臂一致性由确定性复放证明（该题走 sympy_exact，planner 为未触发回退）。

**Stage E 单跑结果（固定样本 26 全量/模型 glm-5.3-flash/temp=0/生产 prompt/预算 120）**：
OCR 条件完整率 8/26=0.3077（题干文本精确抽取；注意分母口径）｜路由准确率 23/26=0.8846（runner 对口径 23/25=0.92）｜方法检索命中率 **N/A**（产物未持久化检索清单，不造数）｜规划执行成功率 21/26=0.8077｜正确释放率 20/25 可答=0.80｜错误接受率 **0**｜文本 oracle→截图**净损失 2 题**（xd-lang-24 路由 unsupported_skill；xd-nc-05 planner 拒收 `mode=相向`）；截图侧净增益 1（xd-vu-23，偶然项不作能力宣称）。figure 模块 0/2：变换通道空格不在行末不适用 + 1 次 VLM ReadTimeout——布局/规律错误区分如实入档。

## 三、工单禁止项自查

- 未修改任何历史冻结成绩（blind_v2 零触碰；四条件产物误覆盖已复原校验）；未跑 R5 87 题 image_only。
- 正确弃权从未计入答对（M 口径分列 10/10 与 1/1）。
- 两处核验改动均为**收紧**（×100 等值类限定带%数；新增题面直读阻断）；无任何"为正确率放松无证据验证"。
- dev 增益未外推：本轮全部结论限定 dev/variants；未启动下轮盲评（等 R9 冻结后设计，命题与 Solver 隔离）。
- 无微调/蒸馏；LLM 只做解析/检索/规划，执行与检查全本地。

## 四、如实保留的缺口（不粉饰）

1. **表达式抽取器括号失真**：`C(6,2)` 类题面被切坏（`'2)+2**4'`）——executor 会算，抽取层表面缺口；stem_direct_parse 已保证此类不再被误放，但覆盖仍缺。
2. **planner 计划语言无分类值槽位**：`相向/追及` 只能被 LLM 塞进数值 given 而被验证器正确拒收——schema 扩展需下轮带预算验证增益。
3. OCR 条件完整率 0.3077 说明**视觉抽取仍是主瓶颈**（20 题正确释放大多靠语义模板容错了不精确抽取——净损失/净增益逐题表在 `R9E_PAIRED_EVAL.json`）。
4. degenerate_copy 对"单步且结果恰等题面裸数"类仍会 withhold（保守拒答，如 3^4÷9=9）——宁漏不滥，未放松。
5. 方法检索命中率不可从单跑产物重建（下一轮在管线内落盘检索清单）。
6. Stage B 残余项（定向二次观察接线、OCR 截断修复、check_option_crop 在 C 通道接线）未在本轮闭环，工单允许其作为下轮项。
7. 指标口径两处并示（我的全 26 分母 vs runner 可答 25 分母）供人工裁定取哪套。

## 五、R9 成本审计累计（工单第 7 项）

- Stage B 期间：57 发送 / 29 成功 / 28 ReadTimeout（历史入档）。
- Stage D planner 臂：19 调用 / 62,482 tokens / 0 重试（预算 30 内）。
- Stage E 单跑：36 发送 / 32 成功 / 4 ReadTimeout / 64,591 tokens（预算 120 内，模型回执全为 glm-5.3-flash，temperature 0）。
- 本轮其余全部评估为零 API（oracle_solver/deterministic/matrix/tests）。

## 六、Git 链与回归

`19e2cae(基线) → 8ea29ba(A) → 34c92be/1cb4d9e(B) → bef5be9/c97be26/6c3416c/84cb9a6/a2f8a37/8db3b8d(C) → 90af3b1/0268df7/5304dcc/06be264/3c0b011/8cee8e3(D/E 全部)`。
终态回归：**570 passed / 2 skipped**（skip=真实模型端到端测试，未配置密钥环境正常跳过）。

**停于此，等待人工裁决**：本报告第四节 7 项缺口的优先级裁定、第五节指标口径裁定、以及"planner 臂 pre-fix 产物是否要求补一次带预算重跑"的决定。
