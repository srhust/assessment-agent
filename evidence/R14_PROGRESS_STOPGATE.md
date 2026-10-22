# R14 本轮离线成果与 Stage G 门停点（供人工评审）

日期：2026-10-22（A–H 初版）→ 2026-10-23（Stage E 根修完成，本版）
零 HTTP（预算 6 次 / 100k token 全部未动用）· 全套测试 **669 passed / 2 skipped**
（R14-E 提交 `d715369` + 收尾 `eb47076`，基线 `5ab91dd` 之上）

## A–H 完成情况

| 阶段 | 状态 | 证据 |
|---|---|---|
| A P0 停机逻辑 | ✅ 已提交 | `9040337` + `test_r14a_stop_logic.py` |
| B CellGeometry 契约 | ✅ | `diagram_layout.py`：pitch 槽/边界/置信度三档；两已知格 ⇒ 单间距样本=medium，"规律唯一性由下游规则通道独立证明"写入 note |
| C pitch 因果消融 | ✅ | `evidence/R14C_PITCH_ABLATION.json`（双轴×3 画布×5 件全数字）+ `R14C_REAL_PARSE_REPLAY.json` |
| D 求解/验证强化 | ✅ 核心 | 绝对面积跨协议尺度、验证器复用坐标契约、节距槽统一、**退化常数序列弃权** |
| **E 选项证据完整性** | ✅ **根修完成**（本版） | `R14E_STAGE_E_ACCEPTANCE_V6.json`（checks_failed `[]`、wrong_releases `[]`、`llm_calls_used 0`）、`R14E_ANCHOR_SWEEP.json`、`R14E_REAL_PAGE_REPLAY_V8.json`、`tests/test_r14e_option_binding.py`（6 例） |
| F 六条件+扰动配对 | ✅（E 回归后**重跑**） | `R14E_SIX_CONDITION_V8.json` == `V9.json`：A 11 cand/10 rel、C 5/3、E **5/13**、F **5/13**，各层 wrong_releases `[]`；`R14E_PERTURB_RELEASE_AUDIT_V5.json`：wrong_grid_releasable **[]**、wrong_accept_pert **[]**、contract **15/15**（与 E 前基线逐项相同，无漂移） |
| G 小批 HTTP | ⛔ **门停，等人工批准** | 见下 |
| H KB 审计 | ✅ | `R14H_KB_CHANNEL_AUDIT.json`（插桩通道证据 + 五态 + 4 卡归因 + 勘误附录） |

## Stage E 究竟修了什么（三条缺陷逐条对账）

1. **overlay-12 optC 近空白** → 消费裁剪 ink `16 → 64`。
   根因不是"字母没剥掉"，而是选项带切分把字母域与 8×8 小图混在同一单元：
   `strip_label_from_crop` 的 0.60×主高主判据在"图比字母矮"的版式上恒假。
   现在选项带按**页面字母域锚点**切分（`_option_band_cells`），字母框
   `label_bbox` 随单元同行、按像素确定性置零剥除；辅路（页级行线互证）
   只在锚点层不成立时使用。四件真页 optC 现逐像素等于页面同层真值。
2. **tight 页选项 0 份** → 现产出 **3 份真实选项裁剪**（ink 168/168/168）。
   页级实测结论（必须如实读）：该截图**整页没有任何字母像素**
   （选项带 3 单元 24×24、节距 74/72、每单元首段宽=单元宽），
   `has_letter_labels=false` 由定位器页级测得。**标签↔图形一一对应无从
   建立**，因此行序标签只记为 `labels_from_row_order_no_letter_evidence`，
   下游 label-association 校验必为 False → **该页仍是候选、不可释放**
   （实测 `rel=False`，弃权原因也从"选项 0 份"变成真实规则侧
   "九宫格目标空格不在行末，当前不支持该布局"）。伪造"标签已对应"是
   本项目红线，故此处止步于证据完整、不进释放。
3. **trans-07 选项框 82px 含双图** → 现 3 单元 `[33,53,20]`px、逐单元
   `label_assoc=True`、ink 160×3；82px 吞两图不再可能（切分由锚点域界
   决定，非 merge_gap 数量巧合）。该页失败于规则唯一性（三格逐像素全同），
   属**如实弃权**，与选项证据无关。

**OptionEvidence↔Solver 消费哈希绑定**（新增）：消费裁剪与页面真值按
tight-ink 集合逐像素对账（相等或墨超集）；陈旧/缺失 → 用**唯一实现**
`_rebuild_boxes` 从页面像素重建选项裁剪（题干/格证据原样保留），状态
`consumption_binding:rebuilt` 并记 `consumed_crop_sha`；无页面真值 →
`no_page_truth`，绝不静默。五页验收 `binding` 全为 `rebuilt/verified`。

**独立 Dev 回归**：`R14E_ANCHOR_SWEEP.json` 59 页（真实 5 / dev15 / 扰动 30 /
screens 9）锚点 **18**（真实 4/5、dev15 14/15、扰动 0/30、screens 0/9）。
扰动族 0 锚点是**设计结果**：那些页没有字母带，锚点判据必须拒绝。

## 为达到"零回归"付出的诚实代价（被否决的方案，全部留档在代码注释）

候选集一度扩成"自下而上逐带试"，实测**它就是缺陷本身**：扰动反例页
（`ft-grid-10--tight`、`ft-overlay-13--vertical`）因此把题干行选成选项带
→ 题干被截一行 → **1×4/1×5 假格阵且可释放** → `test_r13a_safety_gates`
失败。为堵它我先后试并**放弃**了 12 种页面不变量（岛数下界、字母占比、
`nc>2·expect`、图形簇尺寸、页内字母排他、选项带须末带、与文字混排、
题干分组连续性、覆盖度分布、opt 风格唯一性、簇内分裂许可、排布密度界
`gapmed < max(4,0.5·band_hmax)` 的窄化版），每种都有双向实测数据；
最终解法是**回到 HEAD 的候选集**（末带、末两带拼 2×2），锚点只在同一
候选集内**优先替换切分方式**，并加一条消费门：无锚点的**单行**聚类若
其下方还有 >40px 页面墨迹则不消费（tight 双行簇候选不受限，否则退回
"选项 0 份"）。另有两次自我纠正也已入库：`opt_span` 曾一律写单带高
（会让 tight 页 legacy 切分裁空）、聚类候选消费端曾一度整页否决
（`test_r10d_wiring` 立即复现"选项 0 份"）。

## 为什么停在 G 前

1. 工单条件"G 仅在 A–F 离线因果评审后"——材料已就绪，**评审权在人工**；
2. E 的三条缺陷已闭合，但**真实截图端到端只有 2/5 正确释放**
   （mirror-04、rot90-01；trans-07/overlay-12/tight 均如实弃权、0 错放）。
   G 批的边际信息量在"付费通道能否复现离线释放"，不改变离线结论；
3. 预算仍完整：**0/6 请求、0/100000 token**。

G 闸门代码已备：原子请求预算（R13-G）+ token 硬顶
（`ASSESSMENT_LLM_MAX_TOKENS` / `max_tokens=`，发前拒绝、在途如实计入，
3 个测试锁定）。批准即启用 ≤6 请求顺序
mirror-04→rot90-01→trans-07→overlay-12→余量新排版、`text=None`、`save=True`、
模型/Prompt/版本/原图 SHA 固定、错误接受即停（R14-A 逻辑）、异常全计入分母、
证据 `evidence/R14G_*`。

## 需要人工裁决的点

1. 接受"尺度协议是主因、pitch 非因果必需"这一**弱于预设**的结论
   （`R14C_PITCH_ABLATION.json` 双轴表）。
2. **批准或否决 Stage G 小批 HTTP**（E 已闭合，F 已重跑无漂移）。
3. r4a 两个历史测试断言按收紧方向更新（退化恒等序列不再放行；
   disclose 于 commit `4712825`），请确认该方向。
4. tight 页的诚实边界请确认：证据已完整（3 份真裁剪）但**标签不可验证
   → 不释放**。若人工认为"行序标签 + 无字母证据"应可进入释放候选，
   需要的是**新规则**而非放松现有 label-association 门（后者属禁止项）。

## 盲评（blind eval）准入结论

**仍 NOT READY**。支持该结论的当前证据：离线 F 链 5/13 全对零错放
（`R14E_SIX_CONDITION_V9.json`），真实截图解析件仅 **2/5** 端到端正确释放
（`R14E_REAL_PAGE_REPLAY_V8.json`，0 错误接受），扰动 30 页释放审计
`wrong_grid_releasable []`。**不得**把 F 5/13 或 dev15 数字表述成"真实
截图端到端已突破"。解锁需 G 批实测达到人工设定门槛。
