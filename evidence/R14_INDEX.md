# R14 交付索引（机器登记，零 API）

注：本轮**改了** STOPGATE / DELIVERABLES / NEWLAYOUT_REGRESSION 三份文档的措辞，也**重跑**了回归脚本（产物字节逐字一致），因此下表 commit 列是**入库时点**、与工作区未提交内容不同属**已知且如实**，不是漏记。

HEAD `e2f27a52f6c70bf1de0fab569696a8084f355950` · 件数 **56** · 自检失败 **0** 项 · 真实请求 **0**

## 当前件（可复现性 = 是否登记生产脚本）

（下表「生产脚本」已排除审计器/索引等**读取方**；「—」= 人工撰写的文档，不由脚本生成）

| 件 | 地位 | 生产脚本 | 首入库 | 最近改动 | sha256(12) |
|---|---|---|---|---|---|
| `R14_CLAIM_AUDIT.json` | 交付件一致性审计（含审计器自证） | e2e_r14_claim_audit.py | aa81b50 | 337dfd7 | `93f7c242ca87` |
| `R14_CLAIM_AUDIT.md` | 交付件一致性审计说明（含审计器自证） | **无** | aa81b50 | bf49734 | `030a7557e10e` |
| `R14_PROGRESS_STOPGATE.md` | 人工评审入口（本轮闸门文档） | d9_r14_stage_g_manifest.py, e2e_r14_stage_g_batc | 5ab91dd | e2ae3ed | `ebe76808e84b` |
| `R14_STAGE_E_DELIVERABLES.md` | Stage E 十项交付正文 | **无** | de14d1e | e2ae3ed | `affcdf7e5844` |
| `R14_VERIFICATION_BUNDLE.json` | 一键复现 bundle（18 步离线验证） | r14_verify_all.py | f9a2fc8 | e2f27a5 | `77fe5ef3c8ba` |
| `R14C_PITCH_ABLATION.json` | pitch 双轴消融（因果上限依据） | c2_r14_pitch_ablation.py | 59a244f | 59a244f | `df83fb21b136` |
| `R14C_PITCH_ABLATION_HEAD_DELTA.json` | 因果实验在现行 HEAD 的复现差异 | e2f_r14_causal_delta.py | f118cf9 | e2f27a5 | `d9a0ef16d80f` |
| `R14C_PITCH_ABLATION_HEAD_DELTA.md` | 因果实验在现行 HEAD 的复现差异（人工裁决项 2 材料） | e2f_r14_causal_delta.py | f118cf9 | e2f27a5 | `07869167fa65` |
| `R14E_ANCHOR_SWEEP.json` | 独立排版锚点普查 59 页 | e2e_r14_anchor_sweep.py | d715369 | 4fd5dc8 | `b96827fe92f9` |
| `R14E_ARTIFACT_ATTRIBUTION.json` | 证据件生产者归属（含安全闸门结论） | r14_attribute_producer.py | d48d97e | e2f27a5 | `439bbda3e260` |
| `R14E_ATTRIBUTION_SELFTEST.json` | 归属安全闸门的自证注入结果 | r14_attribute_producer.py | d48d97e | f118cf9 | `8812f2b72e12` |
| `R14E_HEAD_REVERIFY.json` | 三项离线基线 HEAD 复验（零漂移） | e2e_r14_head_reverify.py | b94041e | d48d97e | `905091829838` |
| `R14E_LINEAGE_SIX_CONDITION.json` | 六条件跨轮血缘（旧协议件分列不混比） | e2e_r14_six_condition_lineage.py | 8f4ca49 | 8f4ca49 | `51c6deb05227` |
| `R14E_NEWLAYOUT_REGRESSION.md` | 新排版独立 Dev 回归说明 | **无** | 32829d3 | c2b28ea | `c8fccf626af0` |
| `R14E_NEWLAYOUT_REGRESSION_V3.json` | 新排版独立 Dev 回归（含 V2 口径勘误） | e2e_r14_newlayout_regression.py | c2b28ea | c2b28ea | `9a6a48a7e9ad` |
| `R14E_REAL_PAGE_REPLAY_V8.json` | 真实截图解析件端到端复放 | e2d_r14_production_replay_v4.py | eb47076 | eb47076 | `89be0925dcbf` |
| `R14E_RERUN_SEMANTICS.json` | 补集复现核对（结论级/注解级差异分列） | r14_semantic_repro.py | f118cf9 | 139edc1 | `10fce6023ae5` |
| `R14E_SIX_CONDITION_V10.json` | 现行协议六条件（HEAD 复验重跑） | e2e_r14_head_reverify.py, e2e_r14_six_condition_ | b94041e | b94041e | `86dc65890f4e` |
| `R14E_STAGE_E_ACCEPTANCE_V8.json` | Stage E 验收 | e2e_r14_stage_e_acceptance.py | b94041e | b94041e | `d27ba4087d5c` |
| `R14E_TIGHT_GAP_DIAGNOSIS.json` | tight 覆盖缺口逐页成因 | e2e_r14_tight_gap_diagnosis.py | c2b28ea | c2b28ea | `13b771de58f9` |
| `R14E_TIGHT_GAP_DIAGNOSIS.md` | tight 缺口逐页成因说明 | **无** | c2b28ea | c2b28ea | `da8eaedf0a9e` |
| `R14F_PERTURB_RELEASE_AUDIT.json` | 扰动 30 页释放审计（f1 固定件） | f1_r14_perturb_release_audit.py | 4712825 | d715369 | `da176b1a274b` |
| `R14G0_GATE_READINESS_V5.json` | G 闸门行为级就绪审计（真实请求 0） | e2e_r14_stage_g_readiness.py | ce44cc7 | e2f27a5 | `4c5286c3b46e` |
| `R14G0_READINESS.md` | G 闸门零请求就绪说明 | **无** | ce44cc7 | ce44cc7 | `8c9049ac89f2` |
| `R14G_HISTORICAL_BYTES.json` | 历史 Test 件逐字节对引入 commit 的完整性核验（含本轮误覆写事故 | a9_r14_historical_bytes.py | e2ae3ed | e2f27a5 | `0ca5073c26e0` |
| `R14G_HISTORICAL_BYTES.md` | 历史 Test 件字节完整性核验正文（含本轮误覆写事故记录与自我撤回） | e2e_r14_stage_g_readiness.py | e2ae3ed | e2ae3ed | `93d7044ad462` |
| `R14G_HISTORICAL_BYTES_SELFTEST.json` | A9 字节基线检查的触发反证 | a9_r14_historical_bytes.py | e2ae3ed | 337dfd7 | `0f8a026f9b49` |
| `R14G_PLAN.json` | G 批 manifest（PLANNED_NOT_EXECUTED） | d9_r14_stage_g_manifest.py, d9b_r14_lock_manifes | ce44cc7 | e2f27a5 | `122b6d059576` |
| `R14H_KB_CHANNEL_AUDIT.json` | H KB 通道审计 | h1_r14_kb_channel_audit.py | 5ab91dd | 5ab91dd | `02b10e889753` |
| `R14H_KB_CHANNEL_HEAD_DELTA.json` | ('h1_r14_kb_channel_audit.py',) | h1_r14_kb_channel_audit.py | 未入库 | 未入库 | `b3980c267291` |

**自指说明**：本索引写完后其自身的最终字节无法被自己登记，因此 `R14_INDEX.json` / `R14_INDEX.md` 地位标为 `self` 并**排除**哈希自检（A3）——这是诚实边界，不是漏检。

## 非当前件（不得作当前引用）

- `R14C_REAL_PARSE_REPLAY.json` · **superseded** · 同名高版本件为当前引用
- `R14E_REAL_PAGE_REPLAY.json` · **superseded** · 同名高版本件为当前引用
- `R14E_REAL_PAGE_REPLAY_V4.json` · **superseded** · 同名高版本件为当前引用
- `R14E_REAL_PAGE_REPLAY_V5.json` · **superseded** · 同名高版本件为当前引用
- `R14E_REAL_PAGE_REPLAY_V6.json` · **superseded** · 同名高版本件为当前引用
- `R14E_REAL_PAGE_REPLAY_V7.json` · **superseded** · 同名高版本件为当前引用
- `R14E_SIX_CONDITION_V2.json` · **superseded** · 同名高版本件为当前引用
- `R14E_SIX_CONDITION_V3.json` · **superseded** · 同名高版本件为当前引用
- `R14E_SIX_CONDITION_V6.json` · **superseded** · 同名高版本件为当前引用
- `R14E_SIX_CONDITION_V7.json` · **superseded** · 同名高版本件为当前引用
- `R14E_SIX_CONDITION_V8.json` · **superseded** · 同名高版本件为当前引用
- `R14E_SIX_CONDITION_V9.json` · **superseded** · 同名高版本件为当前引用
- `R14E_STAGE_E_ACCEPTANCE.json` · **superseded** · 同名高版本件为当前引用
- `R14E_STAGE_E_ACCEPTANCE_V4.json` · **superseded** · 同名高版本件为当前引用
- `R14E_STAGE_E_ACCEPTANCE_V5.json` · **superseded** · 同名高版本件为当前引用
- `R14E_STAGE_E_ACCEPTANCE_V6.json` · **superseded** · 同名高版本件为当前引用
- `R14E_STAGE_E_ACCEPTANCE_V7.json` · **superseded** · 同名高版本件为当前引用
- `R14E_SIX_CONDITION.json` · **historical** · E 首跑件（C/D 候选口径与后续不同，逐条说明见血缘件）
- `R14F_SIX_CONDITION.json` · **historical** · E **前**现行协议基线
- `R14E_NEWLAYOUT_REGRESSION_V2.json` · **erratum_kept** · 把证据链单元数标为消费单元，与落盘裁剪份数混同（V3 已勘误，保留不删）

## 人工阅读顺序

- 1. R14_PROGRESS_STOPGATE.md（进度 + 6 个待裁决点）
- 2. R14_STAGE_E_DELIVERABLES.md（10 项材料 + 补充件 A–E + 盲评准入）
- 3. R14E_NEWLAYOUT_REGRESSION.md / R14E_TIGHT_GAP_DIAGNOSIS.md（回归与缺口成因，含 V2 口径勘误）
- 4. R14G0_READINESS.md / R14_CLAIM_AUDIT.md（G 闸门就绪与交付自审）
- 5. R14E_LINEAGE_SIX_CONDITION.json（数字血缘，旧协议不混比）

**盲评准入**：NOT READY：真实截图端到端 2/5 释放正确；离线 F 5/13 零错放不构成真实截图突破


## 复现需要 `--out`（默认名与现行件名不同）

- R14E_REAL_PAGE_REPLAY_V4.json ← e2_r14_optionband_replay.py, e2b_r14_anchor_option_recovery.py, e2d_r14_production_replay_v4.py, r14_attribute_producer.py（需 --out 复现）
- R14E_REAL_PAGE_REPLAY_V6.json ← e2_r14_optionband_replay.py, e2b_r14_anchor_option_recovery.py, e2d_r14_production_replay_v4.py, r14_attribute_producer.py（需 --out 复现）
- R14E_REAL_PAGE_REPLAY_V7.json ← e2_r14_optionband_replay.py, e2b_r14_anchor_option_recovery.py, e2d_r14_production_replay_v4.py, r14_attribute_producer.py（需 --out 复现）
- R14E_SIX_CONDITION_V2.json ← e2e_r14_six_condition_lineage.py（需 --out 复现）
- R14E_SIX_CONDITION_V3.json ← e2e_r14_six_condition_lineage.py（需 --out 复现）
- R14E_SIX_CONDITION_V6.json ← e2e_r14_six_condition_lineage.py（需 --out 复现）
- R14E_SIX_CONDITION_V7.json ← e2e_r14_six_condition_lineage.py（需 --out 复现）
- R14E_SIX_CONDITION_V8.json ← e2e_r14_six_condition_lineage.py（需 --out 复现）
- R14E_STAGE_E_ACCEPTANCE_V4.json ← e2e_r14_stage_e_acceptance.py（需 --out 复现）
- R14E_STAGE_E_ACCEPTANCE_V5.json ← e2e_r14_stage_e_acceptance.py（需 --out 复现）
- R14E_STAGE_E_ACCEPTANCE_V6.json ← e2e_r14_stage_e_acceptance.py（需 --out 复现）
