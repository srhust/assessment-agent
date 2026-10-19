# 布局证据八项漏斗（runtime ≠ gold）

样本：11 题（figure 类）

| 项 | True | False | None/不适用 |
|---|---|---|---|
| runtime_layout_verified | 0/11 | 0 | 11 |
| gold_grid_correct | 0/11 | 0 | 11 |
| complete_cell_recall | 0/11 | 0 | 11 |
| target_blank_localized | 0/11 | 0 | 11 |
| geometric_evidence_usable | 0/11 | 11 | 0 |
| solver_proposed | 4/11 | 7 | 0 |
| independently_verified | 4/11 | 7 | 0 |
| final_released | 4/11 | 7 | 0 |

## 逐题

| id | runtime | 金标→检测 grid | gold_ok | recall | blank定位 | 证据可用 | proposed | verified | released | 来源/备注 |
|---|---|---|---|---|---|---|---|---|---|---|
| xn-fig-01 | None | [1, 4]→None | None | None | None | False | True | True | True | None  |
| xn-fig-02 | None | [1, 4]→None | None | None | None | False | False | False | False | None  |
| xn-fig-03 | None | [3, 3]→None | None | None | None | False | False | False | False | None  |
| xn-fig-04 | None | [3, 3]→None | None | None | None | False | True | True | True | None  |
| xn-fig-05 | None | [1, 3]→None | None | None | None | False | True | True | True | None  |
| xn-fig-06 | None | [1, 4]→None | None | None | None | False | True | True | True | None  |
| xn-fig-07 | None | [1, 4]→None | None | None | None | False | False | False | False | None  |
| xn-fig-08 | None | [3, 3]→None | None | None | None | False | False | False | False | None  |
| xn-fig-09 | None | [1, 3]→None | None | None | None | False | False | False | False | None  |
| xn-fig-11 | None | [1, 4]→None | None | None | None | False | False | False | False | None  |
| xn-fig-12 | None | None→None | None | None | None | False | False | False | False | None  |
