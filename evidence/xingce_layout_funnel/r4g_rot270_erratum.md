# R4g 布局自检勘误：runtime self-consistency ≠ 金标网格核对（任务D）

数据源：evidence/r4g_ablation/r4g-20261019T090349Z（C 臂 merge_pixel，同输入 15 题真实 VLM payload+原图，零 API 复算）

| case | 金标 grid | 重建 cols | runtime verified | gold_grid_correct(cols) | 释放 |
|---|---|---|---|---|---|
| ft-comp-06 | [1, 3] | 2 | True | False | None |
| ft-conflict-15 | [3, 3] | 3 | True | True | None |
| ft-grid-10 | [3, 3] | 3 | True | True | None |
| ft-grid-11 | [3, 3] | 3 | True | True | None |
| ft-mirror-04 | [1, 3] | 2 | True | False | None |
| ft-mirror-05 | [1, 3] | 2 | True | False | None |
| ft-overlay-12 | [3, 3] | 3 | True | True | None |
| ft-overlay-13 | [3, 3] | 3 | True | True | None |
| ft-rot180-02 | [1, 3] | None | False | None | None |
| ft-rot270-03 | [1, 4] | 3 | True | False | None |
| ft-rot90-01 | [1, 4] | 3 | True | False | None |
| ft-scale-09 | [1, 4] | 3 | True | False | None |
| ft-sym-14 | [1, 4] | 3 | True | False | None |
| ft-trans-07 | [1, 4] | 3 | True | False | None |
| ft-trans-08 | [1, 4] | 3 | True | False | None |

**runtime 通过但金标列数不符：9 题** → ['ft-comp-06', 'ft-mirror-04', 'ft-mirror-05', 'ft-rot270-03', 'ft-rot90-01', 'ft-scale-09', 'ft-sym-14', 'ft-trans-07', 'ft-trans-08']

重点样本 `ft-rot270-03`：金标 1×4（3 已知格 + 1 空白目标格），C 臂重建 `layout_cols=3` 且 `layout_verified=true`——自检语义是"已声明区域间像素互恰"；当 VLM 未声明空白格区域时，3 个已知格互洽照样通过。**空白格从未被几何定位**（target_blank_localized=False），complete_cell_recall=3/4。Solver 曾 proposed，被 unverified 拦住，released=null（零误放）。

八项漏斗对照（ft-rot270-03，C 臂）：

| runtime_layout_verified | gold_grid_correct | complete_cell_recall | target_blank_localized | geometric_evidence_usable | solver_proposed | independently_verified | final_released |
|---|---|---|---|---|---|---|---|
| true | **false**（1×3 vs 1×4） | 0.75 | **false** | 部分（声明内互洽/覆盖不全） | true（A） | false（unverified） | false |

结论（采纳评审裁定）：runtime_layout_verified 不得表述为"视觉结构完整"；scripts/layout_funnel.py 将 runtime 与 gold 两组指标分列，永不互推。后续 image_only 评测必须同时报告两栏，gold 栏仅离线计算。
