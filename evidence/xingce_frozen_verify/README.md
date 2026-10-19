# R6 任务A 验收记录（2026-10-19）

- local_precommit.json：入库前对 142 张 R5 原始 PNG **逐文件 SHA-256 与冻结 manifest 比对：143/143 一致**，零重渲染。
- clean_clone_verify.json：干净克隆（git clone file://，仅含已提交字节）核验 143/143 PASS。
- metrics.json/summary.md/predictions.jsonl：干净克隆零 API oracle_solver 复现，与冻结 headline 一致：
  primary_answer_accuracy 17/77（22.08%）、answered_coverage 19/77、honest_abstention 10/10、
  false_accept_total=2、failure_classes=（solver_error 55 / unsupported_skill 4 / verifier_false_accept 2）。
- 结论：R5 冻结包自此自包含、跨平台可核验。原 manifest 字节未改动；路径勘误见 data/benchmark_xingce/MANIFEST_ERRATA.md。
