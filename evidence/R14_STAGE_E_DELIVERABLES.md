# R14 Stage E 交付材料汇总（10 项）+ 盲评准入结论

零 HTTP：本轮 **0/6 请求、0/100000 token** 动用。全套测试 **669 passed / 2 skipped**
（基线 663 + 新增 6 例）。提交：`d715369` → `eb47076` → `423d91d` → `d6cbfe0`
→ `de14d1e` → `32829d3`（新排版回归）→ `ce44cc7`（G0 就绪审计）→ `b913813`。
下文 10 项之后的「补充件」是 E 之后两轮新增的独立证据，不替换 10 项本身。

## 1. 选项带切分根修（`_option_band_cells`，页内字母域锚点）
`src/assessment_agent/tools/figure_page_localizer.py`。判据全部由页面像素导出：
连通域行层搜索（字母高 8–16、宽 5–18，取 tight 外接框）、上方文本行同高互证
（support 频次 ≥3）、恰 expect 个域、等距 0.6–1.5× 中位节距、锚点间与末锚后
带墨 ≥20、字母↔图形行线绑定 |Δcy| ≤ 0.6h+4、**多解即歧义 → 回退聚类**。
金标：无。case-id：无。

## 2. 标签一一对应的可验证记录
锚点单元携 `label_bbox=[cell_x0, band_y0, 字母域宽, 带高]` +
`provenance="localizer+label_anchor"`，消费前按该框**确定性置零**剥字母。
验收逐格 `label_association_verified=True`：mirror-04 3/3、rot90-01 4/4、
trans-07 3/3、overlay-12 4/4（`R14E_STAGE_E_ACCEPTANCE_V7.json`）。

## 3. overlay-12「optC 近空白」
消费裁剪 ink **16 → 64**；四格 ink 现 **256 / 320 / 64 / 160**，逐格等于页面
同层真值（不是阈值放宽，是切分正确后字母不再吞进图形域）。辅路
`strip_label_from_crop(band_mask=…)` 只处理"8×8 图比 13px 字母矮"的版式，
主判据（R13-G）原样保留。

## 4. tight 页「选项 0 份」→ 3 份真实裁剪，且**不释放**
页级实测（定位器 `has_letter_labels`）：该截图选项带 3 单元 24×24、
墨 168/168/168、节距 74/72，**整页无字母像素**（ASCII 行线转储确认每单元
首段宽 = 单元宽）。因此标签↔图形一一对应**无从建立**：行序标签只标注为
`labels_from_row_order_no_letter_evidence`，`anchored_cells=0`，
`label_association_verified=[None,None,None]`，`released=False`。
失败原因也从证据缺陷变为真实规则侧弃权
（"九宫格的目标空格不在行末，当前不支持该布局"）。
**这是有意的止步**：伪造"标签已对应"或放松 label-association 门都在禁止项内。

## 5. trans-07「选项框 82px 含双图」
现单元框 88 / 85 / 52 px，每格**恰一个字母域 + 一个图形域**，
ink 160×3 与页面真值逐像素对账通过（旧行为：merge_gap 数量巧合把两图并一格）。

## 6. OptionEvidence ↔ Solver 消费哈希绑定
消费裁剪与页面真值按 tight-ink 集合比对（逐像素相等 **或** 墨超集）；
陈旧/缺失 → 用唯一实现 `_rebuild_boxes` 从页面像素重建选项裁剪，题干/格证据
原样保留，状态 `consumption_binding:rebuilt` 并记 `consumed_crop_sha`；
无页面真值 → `no_page_truth`，**绝不静默**。五页 binding 全部
`rebuilt`（内联重复格盒公式已删，两处链路同源）。

## 7. 安全门与回归证明（零漂移）
- 全套 669 passed / 2 skipped；`test_r13a_safety_gates`、`test_r10d_wiring` 全绿。
- 扰动 30 页释放审计 `wrong_grid_releasable []`、`wrong_accept_pert []`、
  region 契约 **15/15**、`perturb_loc 23/30`（固定件
  `evidence/R14F_PERTURB_RELEASE_AUDIT.json`；文档旧引用的
  `R14E_PERTURB_RELEASE_AUDIT_V5.json` **并不存在**，已在此更正——见补充件 A）。
- 验收 `checks_failed []`、`wrong_releases []`、`llm_calls_used 0`。

## 8. E 回归后 F 六条件复评（与 E 前基线逐项相同）
`R14E_SIX_CONDITION_V8.json` 与 `V9.json` 数字一致：
A 11 候选 / 10 释放、B 0、C 5 / 3、D 1 / 0、**E 5/13**、**F 5/13**，
各层 `wrong_releases []`；F 释放集 rot90-01 / rot270-03 / mirror-04 /
comp-06 / grid-10。`gated_unverified [ft-trans-08]`、
`design_abstain_ok [ft-sym-14, ft-conflict-15]` 亦未变。

## 9. 独立排版锚点普查（59 页）
`R14E_ANCHOR_SWEEP.json`：总 59、锚点 **18** = 真实截图 4/5、dev15 14/15、
扰动 **0/30**、screens **0/9**。扰动/screens 为 0 是**设计结果**
（那些页没有可验证的字母带，锚点判据必须拒绝）；真实 5 页中唯一未锚定者
即 tight 页（无字母）。

## 10. 被否决方案留档（诚实成本）
候选集一度扩为"自下而上逐带试"，实测**它本身即缺陷**：扰动反例页因此把
题干行选成选项带 → 题干截一行 → 1×4/1×5 假格阵且可释放。为堵它先后试并
放弃 12 种页面不变量（岛数下界、字母占比、`nc>2·expect`、图形簇尺寸、
页内字母排他、选项带须末带、与文字混排、题干分组连续、覆盖度分布、
opt 风格唯一、簇内分裂、密度界窄化版），以及两次自我纠正
（`opt_span` 一律写单带高 → tight 页 legacy 切分裁空；聚类候选整页否决 →
`test_r10d_wiring` 立刻复现"选项 0 份"）。全部以双向实测数据写入代码注释，
最终解法是**回到 HEAD 候选集** + 一条消费门（无锚点的**单行**聚类若其下方
仍有 >40px 页面墨迹则不消费；跨两行的 2×2 簇候选不受限）。
阈值一字未改：tol 0.15 / margin_min 0.05 / BASE_TOL 0.12 / SCAN 48-64-80 /
all_confirm。

---

## 补充件（E 之后两轮新增的独立证据，不改 10 项结论）

### A. HEAD 复验：三项离线基线零漂移（`R14E_HEAD_REVERIFY.json`）
在当前提交状态 `b913813` 重跑 d1（六条件）、验收脚本、f1（扰动审计），
逐项 `identical_verdict_numbers: true`、`differing_keys: []`、`drift: []`、
`llm_calls_used 0`。两点如实记录：

* **引用名错误已更正**：本文件与 STOPGATE 此前把扰动审计写成
  `R14E_PERTURB_RELEASE_AUDIT_V5.json`，该件**不存在**；`f1` 忽略 `--out`，
  固定写 `evidence/R14F_PERTURB_RELEASE_AUDIT.json`（数字与既报一致：
  contract 15/15、perturb_loc 23/30、`wrong_grid_releasable []`、
  `wrong_accept_pert []`）。
* **历史件字节未被复验改动**：f1 走"取 git HEAD 快照 → 重跑 → 比对重跑
  产物 → 用快照还原工作区"，还原后 `git status` 对该件为 clean。

### B. 新排版独立 Dev 回归（`R14E_NEWLAYOUT_REGRESSION_V2.json` / `.md`）
50 页（真实 5 + dev15 + perturb30）走**生产入口题面**，三层分列
候选 16 / 接受 7 / 释放 7，排版四不变量 50/50，`wrong_releases []`。
tight 族按 `声明/消费/页面末带列段` 三元组入账：**3 页拿到完整真实选项
证据**（含 1 页真实截图 tight），**4 页仍消费 0 单元**（grid-10 /
overlay-13 / scale-09 / trans-07 变体）→ 如实缺证据、不释放，是 E 之后
**仍开的排版覆盖缺口**（STOPGATE 裁决点 5），不得说成已修好。

### C. G0 零请求闸门就绪审计（`R14G0_GATE_READINESS_V5.json` / `R14G0_READINESS.md`）
用 `httpx.MockTransport` 驱动**真实** `OpenAICompatibleClient` / `VisionParser`
把每条 G 约束变成可执行证明，13 项 `ready:true / failing:[]`；G 批入口
`scripts/e2e_r14_stage_g_batch.py` + manifest `R14G_PLAN.json`（5 件原图 SHA
复算一致、代码版本==HEAD、Prompt 源哈希锁）。C9 实跑入口：无 `--approve`
在任何 socket 之前 exit 2；伪造批准仍被 `model_lock` 拦下（模型名
`UNRESOLVED_MUST_BE_CONFIRMED_BY_HUMAN`，本地 `vision_model=null` 且无 key，
脚本禁止自选模型）。C1d 实测预算脆弱面：5 页 + 1 次余量 = 6，若两页重试
则第 6 次后被拒、那 1 页记 `BudgetExhausted` 并入分母。
**真实请求 0 次，预算仍 0/6、0/100000 token。**

---

## 盲评准入结论：**NOT READY**

支持该结论的当前证据（严格分三层，不混同）：
- 离线 F 链：**5/13 全对、零错放**（候选 5、接受 5、释放 5；HEAD 复验
  `R14E_SIX_CONDITION_V10.json` 与 V9 逐项相同）。
- 真实截图解析件端到端：**2/5 释放且正确**（mirror-04、rot90-01），
  trans-07 / overlay-12 / tight 均如实弃权，0 错误接受
  （`R14E_REAL_PAGE_REPLAY_V8.json`）。
- 扰动 30 页：无可释放假格阵（`wrong_grid_releasable []`，
  `R14F_PERTURB_RELEASE_AUDIT.json`，HEAD 复验零漂移）。

**F 5/13 与 dev15 数字不得表述成"真实截图端到端已突破"**；真实截图上的
可释放率仍是 2/5，且 tight 排版页的证据虽已完整，其标签通道不可验证。
解锁盲评需要 **Stage G 付费小批实测**达到人工设定门槛——G 仍处门停状态，
等待 `evidence/R14_PROGRESS_STOPGATE.md` 的人工批准。
