# R14-C 因果实验：提交件 vs 现行 HEAD 重跑（`R14C_PITCH_ABLATION_HEAD_DELTA.json`）

- HEAD `b9381e724`；提交件 sha 前缀 `df83fb21b1362156`（**未改动**），重跑件 `R14C_PITCH_ABLATION_RERUN2.json` 由 `c2_r14_pitch_ablation.py --out（新名，不覆盖提交件）`
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
