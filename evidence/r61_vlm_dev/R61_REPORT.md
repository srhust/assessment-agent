# R6.1 十二题真实 VLM Dev 诊断报告

- 运行：`xingce_dev` / **image_only**（金标题干、题型、网格、答案零注入）
- 模型：`glm-5.3-flash`（本机 DSH 网关 volceapi，OpenAI 兼容）；temperature=0；
  Prompt/Solver/Verifier 均为现有配置，未做任何调整
- 样本：**预先固定 12 题**（族内 id 升序取配额，未按可解性筛选）：
  nc×2 / ns×1 / da×2 / fig×2 / lg×2 / lang×1 / vu×2；未扩样、未触碰冻结 xingce_test
- 运行名 `r61_vlm_dev12`，用时 277.2s；LLM 审计（`run/llm_evidence.json`）：
  **共 14 次调用 = vision_parse 12 + solver_or_skill 2**（后者是 verbal 技能的
  既有 LLM 规划通道，属现有 Solver 配置，非本轮改动），全部 HTTP 200；
  **prompt 13,324 / completion 18,836，合计 32,160 tokens**
  （成本未计价：无定价依据，不编造）

## 一、裁决要求的四项指标

| 指标 | 结果 | 说明 |
|---|---|---|
| OCR/结构完整性 | **10/12 schema 有效** | 2 道图形题失败：xd-fig-19 模型输出非法 JSON（`Expecting ',' delimiter: line13 col114`）、xd-fig-20 **空返回** |
| Solver 候选覆盖率 | **0/12**（predicted_option 全空） | 全部在解析/规则层诚实拒绝，无一题产出候选 |
| 独立验证后正确释放率 | **0/12 = 0%** | 释放 0 题，正确释放 0 题 |
| 错误接受率 | **0** | 已知两条算术误放路径 + 对抗负例形态在真实视觉输入下同样未放行 |

弃权不等于白跑——**视觉层在 10/12 题上把题干与选项完整读对**（见下），
瓶颈不在 OCR，在 Solver 能力与路由。

## 二、逐题分层归因（视觉丢失 / 解析不足 / 规则不覆盖 / 验证拒答）

| case | 族 | HTTP | schema | 失败层 | 证据（原文摘录） |
|---|---|---|---|---|---|
| xd-nc-01 | 数字计算 | ✓ | ✓ | **规则不覆盖**（视觉完整） | OCR 题干全对、选项 A85.5/B85/C90.5/D94.5 全读入；solver 无两段折扣路径 → 退化表达式 `10*(1/100)`=0.1 → "计算结果与所有选项均不匹配" reject |
| xd-nc-02 | 数字计算 | ✓ | ✓ | 同上 | 先涨后折两段：同样单步退化 → 不匹配 reject。**正确题 gold 85.5 就在读取到的选项里**，丢分纯粹在 solver 能力 |
| xd-ns-17 | 数字推理 | ✓ | ✓ | 规则不覆盖 | 分数列分子分母双规律不在已知规律表 → "没有匹配到任何已知规律" |
| xd-da-11 | 资料分析 | ✓ | ✓ | **技能输入契约不覆盖** | OCR 完整（"23 亿元，增长 15%…41 亿元，增长 8%"全部读入）；data_analysis 硬要求表格/图形区域，**纯文字材料题被自身前置条件拒绝**——文字材料是 dev 新变体，规则缺口，非视觉丢失 |
| xd-da-12 | 资料分析 | ✓ | ✓ | 同上 | 同上 |
| xd-fig-19 | 图形推理 | ✓ | **✗** | **视觉丢失** | 模型输出非法 JSON（第 13 行分隔符错误）→ pipeline_error。注：解析失败时**模型原始文本未落盘**（仅 trace run_start + metrics 报错行号），归档改进项见 §四 |
| xd-fig-20 | 图形推理 | ✓ | **✗** | **视觉丢失** | 模型返回空串 → pipeline_error |
| xd-lg-14 | 逻辑推理 | ✓ | ✓ | **路由错位**（规则不覆盖的近亲） | 翻译推理题被路由进"比较对象"逻辑模板 → "未能识别出至少两个比较对象" reject。oracle 注入下该题走对通道可解——**image_only 路由把 lg 送错技能** |
| xd-lg-15 | 逻辑推理 | ✓ | ✓ | 同上 | 同上 |
| xd-lang-24 | 语言推理 | ✓ | ✓ | **路由错位** | 类比题在 image_only 下判 `unsupported_skill`；oracle 同题 accept 且答对（r61_dev_v4）——视觉文本没问题，路由没送进 language_reasoning |
| xd-vu-22 | 言语理解 | ✓ | ✓ | 验证阈值差一点 | OCR 正确（换行是原排版），主旨规则核 coverage：**B=0.89**（正确项），其余 0.11/0.0/0.32，未达覆盖阈值 → no_covering_option reject。**真实视觉链路下方向已识别正确，仅阈值判死** |
| xd-vu-23 | 言语理解 | ✓ | ✓ | 规则不覆盖 | 成语搭配词库无该条目 → no_collocation_hit |

全部 12 题 `verification_action=reject`、`predicted=None`：**无一无证据释放**。

## 三、结论

1. **纪律面通过**：真实视觉全错/半错的输入下，系统零误放（含裁决点名的两条
   算术误放路径与新增对抗形态在题面退化表达式下仍被 `unverified/reject` 拦住）；
2. **视觉面**：文本类题面 OCR 质量高（数字、选项、材料全量读对），
   **图形题是当前 VLM 输出协议的真实故障点**（2/2：非法 JSON 与空返回）；
3. **能力面**：本轮 0 作答的原因全部在 Solver/路由/契约层——
   两段折扣计算、文字材料资料分析、翻译推理与类比的路由、成语词库——
   与 Dev(oracle) 已知靶点一致，并新暴露**image_only 路由错位**这一接口缺陷；
4. vu-22（coverage 0.89）说明规则核在真实 OCR 文本上已接近可用，属阈值/
   换行归一化微调范畴，本轮未动参数。

## 四、下一轮修复清单（按证据排序，非调参）

1. `llm.py`：vision 解析失败时**落盘原始响应体**（本轮 fig-19/20 证据缺口）；
   输出协议加固（截断重试/JSON 模式，需裁决后实施）；
2. 路由：image_only 下语言类比 → language_reasoning、翻译推理 → 正确逻辑子技能
   （oracle 可解、视觉不可达的通道差）；
3. data_analysis 输入契约支持纯文字材料；numeric 两段折扣计算（dev-target 主线）；
4. 以上任何修复只以 dev 回归验证，R5 冻结头条 22.08%（17/77）依旧不回写。

## 五、证据索引

- 汇总：`results/xingce_dev/r61_vlm_dev12/{metrics.json,summary.md,predictions.jsonl}`
  （已复制归档于本目录 `run/`）
- 逐题：`run/<case>/{question,extraction,solution,verification,route}.json + trace_head.txt`
- 原始运行目录：`runs/e2e-r61_vlm_dev12-<case>/`（含送模截图副本，本地未入库）
- 运行包装（样本/纪律固化）：`scripts/r61_vlm_dev_run.py`、`scripts/r61_dsh_env.py`
  （凭据仅经进程环境传递，不入库不打印）
