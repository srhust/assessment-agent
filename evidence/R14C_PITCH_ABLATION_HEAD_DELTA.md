# R14-C 因果实验：提交件 vs 现行 HEAD 重跑（`R14C_PITCH_ABLATION_HEAD_DELTA.json`）

- HEAD `bf49734d5`；提交件 sha 前缀 `df83fb21b1362156`（**未改动**），重跑件 `R14C_PITCH_ABLATION_RERUN2.json` 由 `c2_r14_pitch_ablation.py --out（新名，不覆盖提交件）`
- 起因：`r14_semantic_repro.py` 修好一个**恒真比较**缺陷（恢复原样发生在语义比较**之前**，等于『原件 vs 原件』）后，本件暴露 9 处结论级差异。

| 页 | J1 pitch 不变(旧→新) | J2 尺度充分(旧→新) | 状态改变臂 | margin 旧→新 |
|---|---|---|---|---|
| ft-mirror-04 | True→True | True→True | — | A:0.538→0.538 / B:0.538→0.538 / C:0.538→0.538 |
| ft-mirror-04--tight | True→False | False→False | C_gold | A:None→None / B:None→None / C:None→0.538 |
| ft-overlay-12 | True→True | False→False | — | A:None→None / B:None→None / C:None→None |
| ft-rot90-01 | True→True | True→True | — | A:0.7546→0.6566 / B:0.7546→0.6566 / C:0.7546→0.6566 |
| ft-trans-07 | True→True | False→False | — | A:None→None / B:None→None / C:None→None |

- **结论方向保持**的页：['ft-mirror-04', 'ft-overlay-12', 'ft-rot90-01', 'ft-trans-07']
- **状态发生变化**的页：['ft-mirror-04--tight']
- **仅数值漂移**的页：['ft-rot90-01']

> 人工锁定表述为「尺度协议修复是充分原因、pitch 非因果必需」。本表只说明该表述在当前 HEAD 的**成立范围**是否改变，并**不**据此扩大或收紧结论。

预算：HTTP 0 / LLM 0。


## 提交件里 `causal_conclusion` 的**字面**需要限定的两处（不改写提交件，勘误记在此）

提交件手写附注 `causal_conclusion.scale_protocol` 原文含
「legacy 相对尺度惩罚下 A/B/C 三种画布**全部饱和 1.0 失败**」。

现行 HEAD 重跑（`R14C_PITCH_ABLATION_RERUN2.json`）给出的 legacy 值是
**margin 0.0153 / 顶分 0.9847**（mirror-04），rot90-01 则是
**margin 0.0 / 顶分未变但 protocol margin 0.7546→0.6566**。
⇒ 「**饱和 1.0**」这一**字面**只在提交件那次运行成立，在现行 HEAD 上
应读作「**近饱和**（顶分 0.9847、margin 0.0153）」；
**结论方向不变**：legacy 三臂在 mirror-04/rot90-01 仍**全部 failed**，
protocol-aware 三臂仍**全部 solved**（J2 两边同为 True）。

## 新增边界（如实，不据此扩大也不据此收紧人工结论）

`ft-mirror-04--tight`（扰动反例页）在现行 HEAD 上 **C_gold 由 failed 变 solved**，
于是该页 A/B/C 三臂状态不再一致 ⇒ **J1（pitch 不变性）在该页不再成立**。

· 人工锁定结论的适用范围本来就只写了 **mirror-04 / rot90-01**，
  该页不在其中，所以这**不构成对人工结论的反驳**；
· 但它**反驳**任何被推广成「pitch 从不影响判定」的说法——
  tight 页（无字母锚点、几何更紧）上 gold pitch 现在确实单独可解。
  这与本轮另一条发现相互印证：tight 覆盖缺口（成因 A/B/C/D）里
  有一类就是**几何间距**而非选项带切分。
· 因此交付表述维持上限：**尺度协议修复是充分原因；pitch 非因果必需**
  （仅就 mirror-04/rot90-01 的 R13-G live 失败而言），
  **不**写成「pitch 对任何页都无关」。
