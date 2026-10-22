# R11 计分勘误（R12-A 产出；原件保留不覆盖）

| 条件 | R11 报告释放 | 正确候选 | 验证接受 | **最终释放** |
|---|---|---|---|---|
| A | 10 | 13 | 10 | **10** |
| B | 0 | 0 | 0 | **0** |
| C | 1 | 1 | 1 | **1** |
| D | 0 | 0 | 0 | **0** |
| E | 2 | 2 | 1 | **1** |
| F | 1 | 2 | 0 | **0** |

- E 真实最终释放 **1/13**（原报 2）；F **0/13**（原报 1）。mirror-05 在 E/F 均提交正确候选 B 但 Verifier 明确 reject——不计释放。
- mirror-04：E accept（计入最终释放），F unverified（不计）。 gated_unverified=['ft-mirror-04', 'ft-trans-08']
- 网关审计：requests_sent=6，error_types ReadTimeout=1；R11 报告“0 网络超时”表述更正为“外层脚本未捕获到异常，HTTP 层实际 1 次 ReadTimeout”。
- 第 6 题（新排版干扰）：传了 extra_text，Pipeline 按文本路径跳过 VisionParser，从 image_only 合格样本中剔除；合格视觉解析端到端样本=5（其中 1 次 HTTP 超时后未重放）。
- 口径澄清：R11 A=10 为最终 Verifier 门控释放；R9 A=13/13 为 Solver 层正确候选数，两者口径不同，不构成退化对比。
- solver_rule_missing 剔除设计性题 ['ft-conflict-15', 'ft-sym-14'] 后，真实研发缺口 7 项：['ft-rot90-01', 'ft-rot180-02', 'ft-rot270-03', 'ft-comp-06', 'ft-trans-07', 'ft-grid-10', 'ft-overlay-12']
