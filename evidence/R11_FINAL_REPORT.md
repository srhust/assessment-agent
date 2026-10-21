# R11 最终报告：视觉真实性验证与图形证据—求解链闭合

基线 `38608e6`；本轮提交链见 §10。人工裁决授权范围：修复丢失的格子（输入表示契约），
**不降验证阈值、不放宽规律唯一性判定**。Dev 集成绩一律不作盲测/泛化宣称。

## 1. 视觉真实性（Stage A）
- 本轮由我本人通过 modlens 视觉桥**真实看图**（此前 Codex 开发段从未看图的事实已纠正）：
  ft-rot90-01（题面 3 个 L 括号+第 4 格无字形空白）、ft-mirror-05（题面仅 2 个右向箭头、
  间隙狭窄未粘连——程序过合并被肉眼揭穿）。overlay-13 桥两次 200s 超时，如实标注
  `bridge_timeout`，改用程序证据并说明。
- 网关阳性对照（`evidence/R11A_VISUAL_CONTROL.json`）：同文本 prompt × 两张不同图
  （3 圆 / 7 方）→ 各自正确且互不相同的回答，2 次 HTTP；每请求审计含
  sha256_16/bytes/size_wh/has_image_content_block/model_echo（`glm-5.3-flash`），
  无 base64/密钥入档。判定 **VISION_GATEWAY_VERIFIED**。

## 2. 输入契约修复（Stage C）
`tools/diagram_grid_evidence.py`：可见格等距节距外推补空格、布局多假设（打平=ambiguous
弃权）、cell 三态（visible / blank_or_undetected / unknown）、cell_pixel_sha、
padding-cell 画布（墨迹紧框居中，对齐金标 48px 几何）。接线：VLM 缺 stem 矩阵时
**像素重建题干**（`stem_crop_source=pixel_grid_evidence`），验证器独立复推画布哈希+
目标格（伪造=unverified）。图3 引擎显式 `target_cell` 契约（目标不在第 9 格：行/列规律
需左/上基格、叠加外推仅 xor 自反族——不可外推如实不足）。序列题 2 已知格守卫：
仅当显式行末空格+规则网格且全部规则同标签，否则弃权（非阈值放宽）。

## 3. 逐页错误源审计（Stage B，`R11B_VISUAL_AUDIT.json`）
15 页：chain_closed 1 / grid_contract_mismatch 4（全部被验证门拦截，0 污染成绩）/
verifier_rejected 1 / solver_rule_missing 9（**仅打在网格+目标位+像素内容三要素
全部核对正确的行上**，无视觉/像素依据不标此签）。

## 4. 六条件实验（Stage D，零 API，`R11_D_SIX_CONDITION.json`）
条件名对应实际消费的像素内容；缓存缺失的条件记 `cache_unavailable` 不冒名。

| 条件 | 输入组合 | 释放(门控口径) | 正确 | 错放 |
|---|---|---|---|---|
| A | 金标题干+金标选项 | 10（solver 层 13/13） | 10 | 0 |
| B | VLM 题干+金标选项 | 0 | 0 | 0 |
| C | 金标题干+VLM 选项 | 1 | 1 | 0 |
| D | VLM+VLM | 0 | 0 | 0 |
| E | 像素重建题干+金标选项 | 2 | 2 | 0 |
| F | 像素重建题干+像素重建选项 | 1 | 1 | 0 |

- **格契约正确率 11/15 与金标全等**（含 3×3 目标非末格全部命中）；
- 条件 A 双口径披露：solver 释放 13/13 正确，其中 3 题被独立复推判 unverified；
- 2 道条件不足题全条件弃权，false accepts 0。

## 5. 归因修正（对 R10 的"C/D 0/13 = 纯 Solver 边界"裁决的回应）
新证据支持**主因是输入契约**：真实截图模式下 VLM 自报格矩阵截断（1×2/1×3 vs 金标
1×3/1×4，Stage G 4/6 页复现），像素重建把契约修复（G 中 4/6 页 status=ok 且网格/
目标位全对）。修复契约后 D 的 E 条件从 0 → 2 正确释放、F → 1；剩余缺口才落在
solver：重建表示上的规律/变换引擎非唯一（9 行 solver_rule_missing）。归因链
"输入截断 → 契约修复 → 释放率部分恢复 → 能力边界仍在"每一步均有数据，
不再以单一天花板结论收口。

## 6. Planner 选卡（Stage E，`R11E_PLANNER_SELECTION.json`）
检索与执行分离：planvar Recall@3 **6/6**、选中∈金标 3/3（检索链路本身可用）；
新语料 r10gen Recall@3 **1/5**（对未见句式的真实检索弱点，保留）；
wrong-card-but-right 3 例（同族卡功能等价、答案对——金标卡 id 精确口径过严，
功能适用率 4/4 并列报告）；依赖检查 3ok/1bad。负例 dev：跨题计划移植 0 例静默错值
（全部未绑定/安全失败）——选错卡风险定位于**选择阶段而非执行阶段**。未硬编码案例
ID 刷指标、未加关键词。

## 7. 失败分类与声明性弃权（Stage F）
稳定桶：`not_sent / sent_no_response / parse_or_protocol_fail / planned_but_exec_fail /
declared_insufficient`，写入 planner 失败 meta。条件不足弃权走**模型成功返回**路径：
合法 JSON + unknowns 指向目标 → 弃权不猜；干扰量写 unknowns 不阻断释放（卡面约定）。
4 项离线测试双向覆盖（`tests/test_r11_planner_failures.py`）。

## 8. 受控真实视觉（Stage G，`R11G_REAL_VISION.json`，清单先行冻结）
6 题（旋转/镜像/平移/缩放/3×3 叠加/新布局干扰）。6 请求 5 成功，**0 网络超时**
（止损规则未触发）；json_output_valid 5/5；每请求图像审计入档。VLM 自报网格在 4 页
截断（1×2/1×3/三个 1×1），像素重建在**同批次实跑中**修复 4/6 页网格与目标位
（含 overlay 3×3 目标 (2,2)）。释放 0、错放 0，全部弃权可解释——与离线 D 结论一致：
契约已闭合，释放瓶颈在 solver 对重建表示的唯一匹配。未跑 R5 87 题/60 题盲评/蒸馏微调。

## 9. 勘误（对历史产物的更正，不改冻结文件本体）
- **R10B"4/5"系三次实跑取优**：逐轮真实值为 run1 1/6、run2 3/6、run3 3/6 释放
  （`R10B_PLANNER_SLOTS.json` 三次运行可复核）；表述更正为单轮口径。
- **R10E"6/7"为修复后 dev 定向验证**，非盲测泛化证据；本报告沿此口径。
- R10 报告"C/D 0/13 = Solver 能力边界"为未定论假设，本报告 §5 以数据修正之。

## 10. 预算、回归与遗留
- 本轮网关请求：2(A)+14(G 四次实跑含 2 次脚本缺陷重跑)= **16/40**；网关未回传
  token 用量，故不报 token 数（以请求数为口径）。modlens 看图 3 次（免费桥）。
- 全量测试 **610 passed / 2 skipped**（本轮净增 9 项）；dev/variants 回归与冻结
  recheck SHA `81ec5663b5c34b4a` 口径不变（84.62%/77.27%）。
- 提交链：`8bc2bd9`(A) → `7e6bc7d`(C) → `1019516`+`a121594`(D) → `3ccaf26`(B) →
  `73f517b`(E) → `95452c0`(F) → `c1f33c4`(G)。
- 遗留（按影响排序）：① solver 在重建画布上的唯一匹配弱（候选方向：变换残差对
  padding-cell 几何的鲁棒化、L 形族的规则面扩充）；② 检索面对未见句式 1/5；
  ③ mirror-05 类"窄间隙过合并"定位缺陷（修复方案已在审计注记）；④ 真实现模式
  释放仍 0/6——**准入判定维持"尚不满足"，等待人工裁决下一步优先级**。

**停于此，等待人工 adjudication。**
