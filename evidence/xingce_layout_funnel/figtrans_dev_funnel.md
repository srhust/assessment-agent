# 布局证据八项漏斗（runtime ≠ gold）

样本：16 题（figure 类）

| 项 | True | False | None/不适用 |
|---|---|---|---|
| runtime_layout_verified | 0/16 | 16 | 0 |
| gold_grid_correct | 0/16 | 0 | 16 |
| complete_cell_recall | 0/16 | 0 | 16 |
| target_blank_localized | 0/16 | 16 | 0 |
| geometric_evidence_usable | 0/16 | 16 | 0 |
| solver_proposed | 13/16 | 3 | 0 |
| independently_verified | 10/16 | 6 | 0 |
| final_released | 10/16 | 6 | 0 |

## 逐题

| id | runtime | 金标→检测 grid | gold_ok | recall | blank定位 | 证据可用 | proposed | verified | released | 来源/备注 |
|---|---|---|---|---|---|---|---|---|---|---|
| ft-rot90-01 | False | [1, 4]→None | None | 0.0 | False | False | True | True | True | none ；no_stem_regions_or_image |
| ft-rot180-02 | False | [1, 3]→None | None | 0.0 | False | False | True | True | True | none ；no_stem_regions_or_image |
| ft-rot270-03 | False | [1, 4]→None | None | 0.0 | False | False | True | True | True | none ；no_stem_regions_or_image |
| ft-mirror-04 | False | [1, 3]→None | None | 0.0 | False | False | True | True | True | none ；no_stem_regions_or_image |
| ft-mirror-05 | False | [1, 3]→None | None | 0.0 | False | False | True | True | True | none ；no_stem_regions_or_image |
| ft-comp-06 | False | [1, 3]→None | None | 0.0 | False | False | True | True | True | none ；no_stem_regions_or_image |
| ft-trans-07 | False | [1, 4]→None | None | 0.0 | False | False | True | False | False | none ；no_stem_regions_or_image |
| ft-trans-08 | False | [1, 4]→None | None | 0.0 | False | False | True | False | False | none ；no_stem_regions_or_image |
| ft-scale-09 | False | [1, 4]→None | None | 0.0 | False | False | True | True | True | none ；no_stem_regions_or_image |
| ft-grid-10 | False | [3, 3]→None | None | 0.0 | False | False | True | True | True | none ；no_stem_regions_or_image |
| ft-grid-11 | False | [3, 3]→None | None | 0.0 | False | False | True | True | True | none ；no_stem_regions_or_image |
| ft-overlay-12 | False | [3, 3]→None | None | 0.0 | False | False | True | False | False | none ；no_stem_regions_or_image |
| ft-overlay-13 | False | [3, 3]→None | None | 0.0 | False | False | True | True | True | none ；no_stem_regions_or_image |
| ft-sym-14 | False | [1, 4]→None | None | 0.0 | False | False | False | False | False | none ；no_stem_regions_or_image |
| ft-conflict-15 | False | [3, 3]→None | None | 0.0 | False | False | False | False | False | none ；no_stem_regions_or_image |
| ft-canvas-16 | False | [1, 4]→None | None | 0.0 | False | False | False | False | False | none ；no_stem_regions_or_image |
