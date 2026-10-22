# R12 最终验收报告 — 图形表示保真、验证一致性与真实截图复测

基线 HEAD `871d903`；本轮提交链 `76ebfec`(A) → `dd7c531`(B) → `cdccec7`(C) → `0251a7a`(D) → `f927bcd`(E) → `b3e9957`(F) → G/H（本报告所在提交）。
全量测试：**635 passed / 2 skipped**。零 API 阶段（A–F）未消耗任何 HTTP；受控真实视觉（G）恰好消耗预算 8/8。

## 1. R11 勘误与真实 HTTP 审计
- 释放口径修正后（`evidence/R11_ERRATA_SCORING.json`，原件未覆盖）：R11 E = **1/13** 最终释放、F = **0/13**（原判 E=2/F=1 中把 solved+reject 计为释放，属 P0 计分缺陷）。
- "网关零超时"不成立：R11G 审计 `error_types.ReadTimeout=1`；脚本原 `network_timeouts` 统计外层异常（恒 0）。R12 起超时一律取 `audit_requests_sent/audit_network_timeouts`（`release_scoring.py`，含重试）。
- 第 6 题因传 `extra_text` 跳过 VisionParser，不合格 image_only，已从指标剔除。
- 预算检查原读 `by_phase` 不存在的 `calls/requests` 键 → 改读真实 `requests_sent`。
- 设计性不可唯一题 `ft-sym-14/ft-conflict-15` 从 dev-gap 分母剔除（9→7），本轮六条件中两题均正确弃权、0 错误接受。

## 2. 双重表示证据（B）
- `render_canvas(mode="shape")`：墨迹紧框居中 + padding（中心平移变换，隐式记录）；`mode="position"`：槽位并集原样裁剪（绝对位移逐像素保留，测试用 `np.array_equal` 钉死）。
- 两画布哈希同栏记录（`canvas_sha` / `canvas_position_sha`），验证器复推**同时比对两者**；meta 标注 `representation_pair="shape_normalized+position_preserving"` 与 `consumed_representation`。
- ft-trans-07 三同哈希：ink-pitch 坐标系内位移本就为 0，同哈希不是抹除；平移真实损失在**选项页带为墨迹紧框 → 选项格内坐标不可观测**，归因类 `position_info_lost` 已按数据信号（格 sha 全同 + 非零 `ink_deltas` + 选项框未知）标注，非 case-id 分支。
- 消融（R12E）：mirror-04 在 Shape 画布 solved/A，在 Position 画布诚实 failed——两种表示互补而非冗余。

## 3. narrow-gap 过合并修复（C）
- 分裂门（全为测量量，禁金标列数挑选）：≥2px 纯白列 + 两侧墨量≥30 + 宽≥8 + 边距 6 + 平衡门 宽比≥0.8 ∧ 墨比≥0.6。
- 反例实测：mirror-05 真缝 0.95/0.99 通过；头/杆伪缝 0.75/0.54、环弧 0.5/0.16 被拦截。合成电池 gap∈{2,4,6,8}px × 尺寸×笔画矩阵 + 噪点 2×2 不产生幻影格，`tests/test_r12c_narrow_gap.py` 6 项全过。
- **冻结 15 页干板网格契约：11/15 → 15/15**（`evidence/R12E_SIX_CONDITION.json` fifteen_page_grid_contract，零 API）。

## 4. 可视化对照（原图 + 分割叠加 + Solver 消费像素）
- 原题：`evidence/R12C_mirror05_split_crop.png`、`R12C_mirror05_solver_canvas.png`（人眼复核：两段独立箭头，无切字）。
- 新排版扰动页三联：`evidence/R12H_ft-mirror-04--tight_{split,shape_canvas,pos_canvas}.png`（[1,3] ok）、`R12H_ft-rot90-01--small_{...}.png`（[1,4] ok，`ink_deltas=[[41,0],[35,5]]` 显示微扰后位移通道仍在）。
- 桥读如实披露：13px 微图上 modlens 对 split 条带报"0 图形"（尺度不足），对 shape_canvas 读出"两个等高同笔画、左右镜像的直角形"——与 mirror 语义一致但无箭头头部细节；大图合成板 200s 超时（bridge_timeout，R12C 文档已录）。

## 5. 零 API 六条件三层指标（勘误基线 vs 本轮）
| 条件 | 正确候选 | 验证接受 | 最终释放 | 备注 |
|---|---|---|---|---|
| A 金标 | 13 | 10 | 10 | 不变 |
| B VLM题干 | 0 | 0 | 0 | cache_unavailable 如实 |
| C 像素选项 | 1 | 1 | 1 | |
| D 全 VLM | 0 | 0 | 0 | 错误候选 trans-07→C 被 unverified 拦下 |
| E 像素题干+金标选项 | 1 | 1 | **1**（mirror-04） | R11 幻影 [1,4] 上的"候选 B"消失 |
| F 全像素 | 1 | 0 | **0** | mirror-04 solved 但复验 unverified |
- **诚实发现**：R11 E"2 正确候选"含 mirror-05 在幻影网格上的侥幸；表示修正后 Solver 对"两同形+空格"正确弃权（规律不唯一），干净证据下候选覆盖 1 而非 2——旧数字是被坏表示抬高的。
- 错误释放：全部条件 0；错误候选全部被 reject/unverified 门控（清单见 R12A/R12E json）。

## 6. 失败归因与残差（D/E）
`evidence/R12E_SIX_CONDITION.json.failure_attribution`：shape_mismatch 20、position_info_lost 2（trans 家族，数据信号）、solved 3；`classify_failure` 为纯函数。
残差保留：`candidate_scores` 含金标与全部干扰项残差、`geometric_residual`、`discriminative_margin`（合成电池断言金标残差严格低于干扰项且 margin>0；padding/resize 触发保守 `canvas_mismatch` 降级而非假唯一）。阈值未动（tol 0.15 / margin_min 0.05）。

## 7. Planner 泛化（F，P1，零 API）
未见表达 7 题：Exact Method-ID 1/7；有检索记录的 5 题 Exact 1/5、Functional 3/5；**2 题检索记录缺失本身是管线缺陷**。彻底缺席同族：`da.growth-amount`（增长量↔nc.* 卡无词面桥）。选卡层已把功能同族命中转成 3 题正确作答（wrong_card_but_right）。任何检索策略替换须先过独立 Dev；金标方法 ID 未动。`evidence/R12F_PLANNER_RECALL.json`。

## 8. 小批量真实截图（G，≤8 含重试，用满 8）
- 清单先于 HTTP 冻结（`evidence/R12G_FREEZE.json`，含六文件源码 sha）；模型/温度不变。
- **4 题真 image_only 合格**（`extraction_parser==vision_parser`，无文本旁路），像素证据网格 **4/4 正确**（[1,4]/[1,3]/[1,4]/[3,3]），双哈希入 meta。
- 但**最终正确释放 0/4**：四题均止步 `status=failed → reject`（候选未形成，无错误释放、无停止规则触发）。归因主嫌疑为 VLM 选项区域质量——首轮运行 `save=False` 未缓存解析件，无法离线复盘，**这是流程缺陷**：g2 已改为强制落盘 + 记录 `vlm_option_regions/vlm_stem_grid`（本提交内）。
- 2 题新布局扰动页在视觉抽题干即失败（PipelineError，未达解题层，合格性 False）。
- 审计：sent 8（含重试）、HTTP 层超时 2、`usage_snapshot.total_tokens=16946`（网关本轮真实回显，保守估算 16394 误差 <4%）、预算逻辑经此实测可用。

## 9. 复现与哈希
pytest 635/2；冻结件 sha：R12G_FREEZE `b2ee4a7a6194772b`、R12G_REAL_VISION `061b06c1a80a206d`、R12E `2ee6967df4f126e5`、R11_ERRATA `b532f2af2167feb0`；运行 HEAD 见文首提交链。R11 原件（`R11_D_SIX_CONDITION.json` 等）逐字节未动（测试钉死）。

## 10. 盲评就绪判断：**未就绪**
盲评前置（真实截图、无金标区域辅助的正确释放 + 独立 Dev 泛化）两条均未满足：G 批 0 释放、F 层 0/13。缺口排序：
1) VLM 选项区域质量与解析件复盘（下轮先跑已落盘的重放，再定修复）；
2) F 条件 mirror-04 复验 unverified 的复推链一致性；
3) 新布局页题干抽取失败；
4) 检索词面覆盖（P1）。
建议下一轮先做"零新增 HTTP 的解析件复盘 + 选项区域质量门"，再申请 ≤8 的定向预算复测同清单。
