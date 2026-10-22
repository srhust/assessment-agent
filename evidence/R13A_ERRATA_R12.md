# R13-A 对 R12 报告的口径勘误（原件保留，不改 SHA）

1. **R12-G 预注册批的正确口径是 0/6 完成任务**（其中 4 题通过 vision_parser
   形成合格图形输入、0 题释放；2 题新排版在题干抽取阶段失败）。R12 报告把
   "eligible 4/4" 与"释放 0/4"并列展示但分母口径未突出 6，本文件为准。
2. **23/30 不能解释为纯保守弃权**：逐题翻转=4 改善 5 退化（净 -1）。其中 3 页
   以 `ok`/`assumed_target` 状态向后传递了错误网格，属安全性缺陷，R13-A4 已修：
   `ft-overlay-13--vertical`（延续性怀疑门）、`ft-grid-10--tight`
   （rows_hint 一致门）、`ft-overlay-13--distractor`（assumed→不可释放资格）。
   修复后 30 页扰动集"错误网格仍可释放"= **0**（tests/test_r13a_safety_gates.py）。
3. **"15/15" 仅指网格尺寸**：分项为尺寸 15/15、空白格 13/15、目标位 13/15、
   选项对应 8/15、内容完整 15/15（evidence/R13A_SUBMETRICS.json）。
   `ft-overlay-13` 尺寸正确但目标未定位（ambiguous），此前叙述有混淆口径之嫌。
4. **Functional Recall 3/5 作废**：`前缀同族即命中`不能证明检索卡可解题
   （r10g-03 组合数题被记为 `nc.inclusion-exclusion` 功能命中属误判）。
   R13-F 按 Exact/Skill-level/Operator-coverage/Applicability/Exec-validity
   五层重做，并区分 `not_invoked / invoked_empty / failed`。
5. **释放证据**：row_layer 现严格要求脚本保存的 Verifier `final_answer`
   （缺字段不再回退 Solver answer）；R13A 重跑中 ev_missing=0，
   E=1/13、F=0/13 与 R12 勘误基线一致，且 mirror-04 E 的 accept 由
   Verifier 真实返回 `"final_answer": "A"` 背书。
