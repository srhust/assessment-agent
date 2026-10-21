# R9-D 统一 8-metric × 消融（oracle_solver 离线两臂；full_pipeline 待 Stage E 预算）

## xingce_dev
| 指标 | rules_only | rules+templates |
| M1_solved_rate | 0.4231 | 0.8077 |
| M2_accuracy_all | 0.3077 | 0.8077 |
| M3_precision_answered | 0.7273 | 1.0 |
| M4_false_answer_rate | 0.2727 | 0.0 |
| M5_abstain_rate | 0.5769 | 0.1923 |
| M6_correct_abstain_rate | n/a(无条件不足类) | n/a(无条件不足类) |
| M7_verify_coverage | 0.7273 | 1.0 |
| M8_latency_p95_ms | 64.6 | 64.0 |

## dev_variants
| 指标 | rules_only | rules+templates |
| M1_solved_rate | 0.2045 | 0.7045 |
| M2_accuracy_all | 0.0 | 0.6364 |
| M3_precision_answered | 0.0 | 0.9032 |
| M4_false_answer_rate | 1.0 | 0.0968 |
| M5_abstain_rate | 0.7955 | 0.2955 |
| M6_correct_abstain_rate | 1.0 | 1.0 |
| M7_verify_coverage | 0.1111 | 0.9355 |
| M8_latency_p95_ms | 0.7 | 1.5 |

