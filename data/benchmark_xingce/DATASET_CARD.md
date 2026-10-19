# 行测综合 Held-out 测试集 · benchmark_xingce（R5）

**版本**：v1（创建即冻结） · 创建日期：2026-10-19 · 基准名：`xingce_heldout`
**最终冻结 sha256（test/xingce.jsonl）**：`5058d485bdc64aac…`（完整清单见 `manifest.json.sha256_files`，共 143 个文件逐一哈希）
**用途**：仅本项目内部评测。**不参与任何训练、调参、阈值选择或题目筛选**。首次计分运行即 headline，禁止据结果回写题目。

---

## 1. 定位

验证 Solver+Verifier 整个系统在**真实行测全模块**上的性能：
- 8 个注册技能全覆盖：number_series / numeric_calculation / data_analysis / figure_reasoning / logical_reasoning / language_reasoning / verbal_understanding / personality；
- 文本 + 图像 oracle 双模态：每题都有整页渲染截图（`images/test/`），图形题附像素级金标区域（`regions/`，bbox+裁片+表格单元格），使**全系统在离线零 API** 下走完整链路；
- 87 题：客观可答 77（expected solved）+ 不可判定 6（answer=None，undecidable）+ 主观自评 4（personality）。

## 2. 分类学与题型来源

题型体系依据《中央机关及其直属机构考试录用公务员公共科目考试大纲》行测部分的公开模块划分，及主流培训机构公开题纲页面归纳（数量关系：数字推理/数学运算；资料分析：表格/文字/图形材料，增长率·比重·倍数·基期现期·间隔增长；判断推理：图形推理的位置/样式/属性/数量/空间重构，定义判断、类比推理（八类方向关系）、逻辑判断（排序、真假话、翻译推理、削弱加强）；言语理解与表达：成语填空、片段阅读、语句表达；性格测评：自评量表）。常识判断模块无对应注册技能，本集不覆盖（见 §7 局限）。

## 3. 命题方式与诚实纪律（关键）

官方从不公布真题与答案；网络流传均为机构/考生回忆版，且其答案表在付费/PDF 墙后（抓取验证时 PDF 下载实为反爬占位图）。据此本集采用**双轨命题**：

- **自建题（82 题）**：按上述真实考法分类学构造，**金标先于渲染固定**（数字规律/几何变换/词典关系由公式或变换定义直接确定），从未用 solver 输出筛选或修改答案。不可判定题（6）刻意保留多解/无证据结构，金标 answer=None，专测诚实弃权。
- **2023 国考副省级·资料分析回忆版组（5 题，`g-real2023-da`，xn-da-r1..r5）**：题干数字取自两个独立公开来源交叉核对的回忆文本（来源 URL 逐题记录于行内 `provenance.source_urls`）；**金标由本项目独立计算**（每行 `rationale` 附完整算式，`gold_method=independent-computation`），未采信任何机构答案表。因原卷为文字材料，本集将其数值**等价转写为结构化表格区域**（tag `structured-restatement`）供 oracle 注入——题干措辞保持真题原样，未按工具偏好简化（工具解不了真题句式即为真实缺口，见 §6）。

其他纪律：
- **泄漏检查**：文本题题干与 `data/` 下全部 241 行既有语料做归一化精确匹配 + ≥14 字包含比对，**0 命中**；带图题以（题干，图像字节 sha）联合键比对（模板化题干+图像不同不构成近重复）。首轮检查曾命中 2 处（分数乘法与表格导语撞既有 dev 行），已改题干（非改金标）。
- **构建迭代史**：冻结前发现并修正了多处**题面与技能输入契约不符**的构造 bug（选项序列化为嵌套数组、图形题干未采用系统声明的"规则提示+按规律"句法与空白格惯例、单字人名缺实体声明句、类比题词对分隔符含空格、同/反义引号格式、表格表头含年份数字被判缺表头、横移被画布钳位、对合变换导致选项全同）。这些修正**只动格式不动答案**。manifest `rebuild_log` 因脚本仅保留末条，记录不全，特此如实说明：共 --force 重建 6 次，全部发生在本 headline 运行之前。
- 泄漏、QA（选项标号/去重/图像存在/越界/裁片非空）在构建期**硬断言**，失败即中断。

## 4. 使用方式

```powershell
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"; $env:ASSESSMENT_R4G_LAYOUT="off"
python -X utf8 -m assessment_agent eval --split xingce_test --eval-mode oracle_solver
```
image_only（真实 VLM 感知链路）需在线 API 授权，本集图像与区域同样具备该模态的金标。

## 5. Headline 结果（oracle_solver，离线零 API）

`results/xingce_test/xingce_headline_r5/`（2026-10-19T20:29+08 左右）

| 指标 | 值 |
| --- | --- |
| 总体/已支持题准确率 overall/supported_answer_accuracy | **22.08%**（17/77） |
| 作答覆盖 answered_coverage | 24.68%（19/77） |
| 选择性准确率（通过校验的答案中正确比例） | 89.47% |
| **诚实弃权（6 不可判定 + 4 主观）** | **100.00%（10/10）** |
| 误放 false_accept | 2；误杀 false_reject 0；越权作答 over_answer 0 |
| 失败归因 | solver_error 55 / unsupported_skill 4 / verifier_false_accept 2 |

## 6. 模块能力画像（headline 实测）

| 模块 | 答对/期望 | 已证明能力 | 真实缺口（本集暴露） |
| --- | --- | --- | --- |
| number_series | 5/11 | 等差·等比·差分·幂次·分组 | 分数列、素数列、机械分组（"没有匹配到任何已知规律"×6） |
| numeric_calculation | 3/11 | 直接公式化简 | 行程/工程/数字谜应用题句式（"未能从题干解析出算术表达式"×5）、答对 2 题被验证器误放（false_accept 来源） |
| data_analysis | 2/16 | 表格直读/简单同比（行×列矩阵表） | **文字材料完全不认**、真题基期/比重/顺差句式 operation_unsupported、跨行 ambiguous_reference、间隔增长率——14/15 题失败 |
| figure_reasoning | 5/11 | 平移·旋转·水平镜像·周期2·九宫格镜像 | 数量类（个数/边数外推）、等比缩放墨迹判定、异或/并集九宫格、组合律（对合）、空间重构（立方体展开）0 分 |
| logical_reasoning | 2/10 | 传递链排序、"不是最高"排除式 | 真假话（陈述句解析不出约束×2）、翻译推理/蕴含类**全部**"识别不出比较对象"×6——z3 核仅排序域 |
| language_reasoning | 0/8 | （无——词典仅 dev 词对覆盖） | 七类关系词典覆盖极窄（unknown_pair），且 2 题**过度自信答错**（含 1 题对不可判定类比强行作答）——诚实性反面样本 |
| verbal_understanding | 0/8 | （无——4 题 unsupported） | 成语搭配零命中、覆盖式片段阅读 no_covering_option、论证 neutral 全不判定；但**全部如实拒答**，无乱答 |
| personality | 4/4 正确弃权 | subjective→解释性读数，不假装有答案 | — |

**读数结论**：系统"知道自己不会"的弃权纪律良好（10/10），但客观题面实际能力窄（24.68% 作答覆盖），语言/资料分析两模块与真实行测句式差距最大；numeric 与 language 的 4 例 wrong_answer（2 例漏网为 false_accept）是最应优先修补的诚实性风险。

## 7. 局限

1. 自建题不能代表整卷难度分布，是**能力边界探针**：每个模块含"已证可解"与"缺口提示"两类题（tag：capability-gap-hint 标注预期缺口）。
2. 常识判断、定义判断无注册技能，未覆盖。
3. 回忆版组题干未逐字对官方原卷校验（官方从不公布）；已双源交叉核对数字并独立复算金标。
4. 图形/资料的空间重构与文字材料为预期内缺口题，其失败是**测量目标本身**而非标注噪声。

## 8. 合规声明

本数据集**仅限本项目内部评测使用**；其中回忆版题目文本来源于公开转载，尊重原出处版权，**禁止再分发、公开传播或商业使用**。自建题目与全部图像由生成脚本 `scripts/make_xingce_benchmark.py`+`make_xingce_content.py` 程序化产出，可复现。

## 9. 文件清单

- `test/xingce.jsonl` — 87 行，冻结（rebuild_log 见 manifest）
- `images/test/*.png` — 87 张整页渲染
- `regions/xn-fig-*/` — 11 题的格/选项裁片（xn-fig-12 无区域，缺口题）
- `manifest.json` — 全部文件 sha256、计数、泄漏统计、policy、frozen 时间戳
- 生成器：`scripts/make_xingce_benchmark.py`（框架）/ `scripts/make_xingce_content.py`（题目表）
- 注册点：`src/assessment_agent/evaluation/runner.py`（split=xingce_test）、`src/assessment_agent/cli.py`（--split 选项）
