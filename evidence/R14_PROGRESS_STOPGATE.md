# R14 本轮离线成果与 Stage G 门停点（供人工评审）

日期：2026-10-22（A–H 初版）→ 2026-10-23（Stage E 根修完成，本版）
零 HTTP（预算 6 次 / 100k token 全部未动用）· 全套测试 **669 passed / 2 skipped**
（R14-E 提交 `d715369` + 收尾 `eb47076`，基线 `5ab91dd` 之上）

## A–H 完成情况

| 阶段 | 状态 | 证据 |
|---|---|---|
| A P0 停机逻辑 | ✅ 已提交 | `9040337` + `test_r14a_stop_logic.py` |
| B CellGeometry 契约 | ✅ | `diagram_layout.py`：pitch 槽/边界/置信度三档；两已知格 ⇒ 单间距样本=medium，"规律唯一性由下游规则通道独立证明"写入 note |
| C pitch 因果消融 | ✅ | `evidence/R14C_PITCH_ABLATION.json`（双轴×3 画布×5 件全数字）+ 真实截图件端到端复放现行件 `R14E_REAL_PAGE_REPLAY_V8.json`（早期 `R14C_REAL_PARSE_REPLAY.json` 已被取代，只作历史，不作当前引用） |
| D 求解/验证强化 | ✅ 核心 | 绝对面积跨协议尺度、验证器复用坐标契约、节距槽统一、**退化常数序列弃权** |
| **E 选项证据完整性** | ✅ **根修完成**（本版）+ 回归**勘误 V3** | `R14E_STAGE_E_ACCEPTANCE_V8.json`（== V7，`checks_failed []`、`wrong_releases []`、`llm_calls_used 0`）、**新排版独立 Dev 回归 `R14E_NEWLAYOUT_REGRESSION_V3.json` + `R14E_NEWLAYOUT_REGRESSION.md`**（50 页三层计数 16/7/7、排版四不变量 50/50；V3 把"证据链单元"与"落盘裁剪份数"分列——V2 曾混同，使"4 页 tight 选项 0 份"表述过强，已在件内 `erratum_v3` 与两份文档更正，判定数字一字未变）、**缺口逐页成因 `R14E_TIGHT_GAP_DIAGNOSIS.json` + `.md`**（A 尺寸悬殊 / B 8px 真图形遇 10px 最小边门 / C 末带混合内容不可安全恢复 / D 对照）、`R14E_ANCHOR_SWEEP.json`、`R14E_REAL_PAGE_REPLAY_V8.json`、`tests/test_r14e_option_binding.py`（6 例） |
| F 六条件+扰动配对 | ✅（E 回归后**重跑**，并在 HEAD `b913813` 二次复验零漂移） | `R14E_SIX_CONDITION_V10.json` == `V9.json`（逐项 `identical_verdict_numbers true`、`differing_keys []`）：A 11 cand/10 rel、C 5/3、E **5/13**、F **5/13**，各层 wrong_releases `[]`。**血缘已件化** `R14E_LINEAGE_SIX_CONDITION.json`：现行协议四件（R14F=E 前基线 / R14E 首跑 / V9 / V10）F 恒为 5/5/5（5/13）且全层零错放；R12A/R13A 属**旧协议**件（E 1/13、F 0/13–1/13），差异源于协议，不得同列比较声称退化或进步。扰动审计的**实际件**是 `R14F_PERTURB_RELEASE_AUDIT.json`（`f1` 脚本忽略 `--out`、固定写该文件；此处此前写的 `R14E_PERTURB_RELEASE_AUDIT_V5.json` 并不存在，属**引用名错误**，已更正）：wrong_grid_releasable **[]**、wrong_accept_pert **[]**、contract **15/15**、perturb_loc 23/30——复验以 git HEAD 快照比对后**原样还原**，历史件未被本轮改动。汇总证据 `R14E_HEAD_REVERIFY.json`（`drift []`、`llm_calls_used 0`）。 |
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

**新排版独立回归（生产入口题面）**：`R14E_NEWLAYOUT_REGRESSION_V3.json`
50 页（真实 5 + dev15 + 扰动 30）三层分列 **候选 16 / 接受 7 / 释放 7**、
`wrong_releases []`、`checks_failed []`、`near_blank_consumed []`、
`band20_flagged []`；逐页另做**独立重推同源复核**（证据链单元 bbox+ink 与
页面重解全等，50/50）。tight 族 7 页按 `声明 / 证据链单元 / 落盘裁剪 /
页面末带列段` 四量分列入账：**3 页拿到真实完整选项证据**（含 1 页真实截图
tight：3 单元 168×3）。**此处曾有一句我自己的过强表述**（"4 页仍消费 0
单元"）已在 V3 与 `R14E_TIGHT_GAP_DIAGNOSIS.md` 更正：真实构成是
`ft-scale-09--tight`、`ft-trans-07--tight` **确为 0 份裁剪**，而
`ft-grid-10--tight`、`ft-overlay-13--tight` **已落盘 4 份裁剪**、但同一次
运行随后在**题干**侧弃权（`stem_evidence_ambiguous_layout`）使证据链为空，
且落盘格含并图（ink 296 / 349）与劈图（一个 w33 图形被劈成 80+227）。
这 4 页仍是 E 之后开着的**排版覆盖缺口**，不得表述成"已修好"；
列段数也**不是**选项数的代理（overlay-13--tight 末带 7 段 vs 声明 4），
只作对照。详见 `R14E_NEWLAYOUT_REGRESSION.md` 与 `R14E_TIGHT_GAP_DIAGNOSIS.md`。

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

**G0 零请求就绪审计（新增，真实请求 0）**：上面每一条约束已从"声称"变成
**可执行证明**——用 `httpx.MockTransport` 驱动真实 `OpenAICompatibleClient` /
`VisionParser`，`evidence/R14G0_GATE_READINESS_V5.json` 13 项 `ready:true /
failing:[]`（C2a/C2b 硬顶发前拒 + 在途入账、C1/C1b/C1c 重试同扣额不穿透、
C5 wrong_accept 触发位、C3/C3b `text=None`+裁剪落盘+解析器不补造、
C4 五件原图 SHA 复算 + Prompt/代码版本锁、C7 命名隔离、**C9 G 批入口子进程
实跑：无批准 exit 2、伪造批准仍被 model_lock 拦下、不生成结果件**）。
发批入口 `scripts/e2e_r14_stage_g_batch.py`，manifest
`evidence/R14G_PLAN.json`，说明 `R14G0_READINESS.md`。

G0 也**如实暴露了预算的脆弱面**（C1d）：计划 5 页 + 1 次余量 = 6，
一旦发生**两页**重试，第 6 次之后被原子闸拒绝 → 那 1 页只能记
`BudgetExhausted` 并计入端到端分母（实测 `sent=6 / succ=4 / tok=4080`、
`{429:2, 200:4}`）。届时**不得**用降低页数、放松闸或"只除成功件"来抹掉。

## 一键复现与**复现边界**（本轮新增，必读）

`scripts/r14_verify_all.py` → `evidence/R14_VERIFICATION_BUNDLE.json`：
从**干净工作区**按固定顺序**真跑** 14 步离线验证器（子进程、`PYTHONPATH=src`），
登记每步退出码/耗时/产物 sha256 与件内关键事实。当前 **`fails []`、
`all_steps_exit_zero true`**。附带两条强检查：**A4 预检**（跑之前就按 `--out`
形态算出"将写到哪个绝对路径"，不等于现行件即拒绝——这条抓出了会**覆盖历史件**
的写路径）与 **f1 固定件重跑后哈希不变**（漂移检查，不是重跑仪式）。

**必须如实的复现边界（不得说成"离线基线全部可复现"）**：
真实截图复放件 `R14E_REAL_PAGE_REPLAY_V8.json` 属 **NOT reproducible from HEAD**
——它含富化键（`chain / consumed_crop_shas / consumption_binding /
label_association_verified / option_sha256 / options_used / provenance`），
当前 HEAD 的重放脚本产不出，且其题面 `question.json` 已被就地重写。
分层结论见 `R14E_REAL_PAGE_REPLAY_V8_CHECK.json`：
· **判定层可复现**：9 个判定字段（含 `option_boxes`）与 V8 **逐字一致 5/5**
  ⇒ "真实截图端到端 2/5 正确释放"这一结论**未变**；
· **证据层不可复现**：可核对性退化（不是数字变化）。
bundle 对此的处理是 **skip 形态**：不重跑该件，并断言其在运行期间**未被改动**
（`v8_untouched true`，前后哈希相同）——把重跑产物写回 V8 就是目标②禁止的
"改历史 Test 结果"。若要恢复完整复现，需要把富化步骤纳入可重跑脚本，
**属新脚本、须人工批准，本轮未做**。

## 需要人工裁决的点

1. 接受"尺度协议是主因、pitch 非因果必需"这一**弱于预设**的结论
   （`R14C_PITCH_ABLATION.json` 双轴表）。
2. **批准或否决 Stage G 小批 HTTP**（E 已闭合，F 已重跑无漂移）。
3. r4a 两个历史测试断言按收紧方向更新（退化恒等序列不再放行；
   disclose 于 commit `4712825`），请确认该方向。
4. tight 页的诚实边界请确认：证据已完整（3 份真裁剪）但**标签不可验证
   → 不释放**。若人工认为"行序标签 + 无字母证据"应可进入释放候选，
   需要的是**新规则**而非放松现有 label-association 门（后者属禁止项）。
5. 新排版回归暴露的**排版覆盖缺口**请裁决是否纳入 R14 范围。
   **先更正我自己的口径错误**：此前写"4 张 tight 变体消费 0 单元"是
   **过强**表述——`R14E_NEWLAYOUT_REGRESSION_V2` 把"证据链单元数"标成了
   "消费单元"。V3 已分列两个量（其余数字一字未变），真实构成是
   **2 页确实 0 份裁剪（scale-09、trans-07）+ 2 页有 4 份裁剪落盘但该次
   运行随后在题干侧弃权、证据链为空（grid-10、overlay-13）**；零错放不变。
   逐页成因见 `R14E_TIGHT_GAP_DIAGNOSIS.md`（零 API、不调阈值、不加规则）：
   · **A 类**（scale-09--tight）三选项**故意**尺寸悬殊（ink 378/38/168），
     "宽高/墨一致"正则性谓词**按设计**必须拒绝——放松它就是拆防假格阵的门；
   · **B 类**（trans-07--tight）真图形本就是 8px 横/竖条（实心度 1.0），
     `min(h,w)=8 < MIN_CELL_PX=10` 不过；锚点路径有 tight 内容基（最小边
     4px）**但无字母版式走不到它**——这是**规则不对称**，需新规则而非调阈值，
     且该页节距 46/88 不等距，第二道阻碍独立存在；
   · **C 类**（grid-10 / overlay-13--tight）末带是**混合内容**（5 段 vs 声明 4；
     7 段 vs 4），落盘裁剪里含并图格（ink 296 / 348）与劈图格——页面像素
     **不含**"哪几段才是选项"的信息，属**不可安全恢复**，不是可修缺陷。
   建议：留待人工定优先级；本轮**没有**自行加规则，也**未**动
   tol=0.15 / margin_min=0.05 / MIN_CELL_PX=10。
6. 若批准 G：还须给出**模型名**。本地配置 `vision_model=null` 且无 key，
   manifest 的 `model_lock.status = UNRESOLVED_MUST_BE_CONFIRMED_BY_HUMAN`；
   发批入口在模型名未锁定时**拒绝发起**（G0 C9 已实跑验证），脚本不会
   自选模型。批准凭据经 `--approve` 传入，人工需同时把
   `model_lock.status` 改为 `CONFIRMED_BY_HUMAN` 并填 `fixed.vision_model`。

## 盲评（blind eval）准入结论

**仍 NOT READY**。支持该结论的当前证据：离线 F 链 5/13 全对零错放
（`R14E_SIX_CONDITION_V10.json`，HEAD 复验与 `V9.json` 逐项相同），真实截图解析件仅 **2/5** 端到端正确释放
（`R14E_REAL_PAGE_REPLAY_V8.json`，0 错误接受），扰动 30 页释放审计
`wrong_grid_releasable []`。**不得**把 F 5/13 或 dev15 数字表述成"真实
截图端到端已突破"。解锁需 G 批实测达到人工设定门槛。
