# R14-E 新排版独立 Dev 回归（零 API）

脚本：`scripts/e2e_r14_newlayout_regression.py`
产物：`evidence/R14E_NEWLAYOUT_REGRESSION_V3.json`（50 页逐页行 + 汇总）。
V2 为**同名早期版**，其 tight 汇总存在口径混同（见下文勘误），V2 件保留不删、
但**不得**再作为当前引用；当前引用一律 V3。
成本：**0 HTTP / 0 token**。与 F 六条件互相独立（不复用其分数，也不被其分数替代）。

## 覆盖与三层计数（互不代称）

| 层 | 数量 | 说明 |
|---|---|---|
| 页面 | **50** | real 5（R13-G 真实截图，含 1 张 `--tight`）+ dev 15 + perturb 30（含 6 张 `--tight`） |
| 候选 candidates_solved | **16** | Solver 产出候选 |
| 接受 accepted | **7** | Verifier 动作 accept/repair |
| 释放 released | **7** | `release_status` 判定，其中真实截图件 2 个（`ft-mirror-04[real-screenshot]`、`ft-rot90-01[real-screenshot]`） |

`wrong_releases []`、`checks_failed []`。真实截图件与 dev 母版同 id 时行键加
`[real-screenshot]` 后缀消歧——两者题面/选项声明不同（例如真实 tight 页由
OCR 声明 3 选项，dev 版同名 tight 变体沿用母版声明 4），**绝不合并计数**。
下表逐页 `kind` 字段即该页的真实来源（`real` = runs/r13g-* 原样题面）。

## 排版层四条不变量（逐页断言，50/50 通过）

1. **消费裁剪非近空白** `near_blank_consumed []`：每份被消费的 `option_*.png`
   墨量均 ≥20。真实 overlay-12 四格 256/320/64/160（optC 缺陷前为 16）；
   tight 页 168/168/168。
2. **标签通道自洽** `letterless_anchor_claims []`、`letterless_released []`：
   无字母页（`has_letter_labels=False`）既不自报锚点也不被释放；
   `label_association_verified=True` 只出现在 `localizer+label_anchor` 单元。
3. **释放必具体系**：released ⇒ 答案==金标 ∧ 单元全锚点 ∧
   `binding_state ∈ {verified, rebuilt}`。
4. **同源复核不采信自报**：消费单元与本轮**独立重推**同 n 次 `localize_page`
   的解逐格 bbox+ink 全等（`cells_match_page_reeval` 50/50）。

## tight 族：声明 / 证据链单元 / 落盘裁剪 / 页面真值（实测逐行，7 页）

> **V3 勘误（本轮）**：本文件与 V2 件此前把下表"证据链单元"一列标作
> "消费单元"，与**落盘裁剪份数**混同，导致"4 页 tight 消费 0 单元"的表述
> **过强**。真实构成是 **2 页 0 份裁剪（scale-09、trans-07）+ 2 页 4 份裁剪
> 已落盘、但那次运行随后在题干侧弃权使证据链为空（grid-10、overlay-13）**。
> `R14E_NEWLAYOUT_REGRESSION_V3.json` 已分列两个量，其余数字一字未变
> （层计数 16/7/7、`wrong_releases []`、`checks_failed []`、`band20_flagged []`、
> 同源复核 50/50）。逐页成因见 `R14E_TIGHT_GAP_DIAGNOSIS.md`。

| 页（kind） | 题面声明 | 证据链单元 | 落盘裁剪 | 页面末带列段 | 释放 | Solver |
|---|---|---|---|---|---|---|
| `ft-mirror-04--tight`（real） | 0（OCR 空） | **3**（168×3） | 3 | 3 | False | failed |
| `ft-mirror-04--tight`（perturb） | 3 | **3** | 3 | 3 | False | solved |
| `ft-rot90-01--tight`（perturb） | 4 | 4（168×4） | 4 | 4 | False | solved |
| `ft-grid-10--tight`（perturb） | 4 | **0** | **4**（168/**296**/168/168） | 5（非一致排布） | False | failed |
| `ft-overlay-13--tight`（perturb） | 4 | **0** | **4**（107/227/**349**/160） | 7 | False | failed |
| `ft-scale-09--tight`（perturb） | 3 | 0 | **0** | 3 | False | failed |
| `ft-trans-07--tight`（perturb） | 3 | 0 | **0** | 3 | False | failed |

必须如实读的三点：

* **列段数不是选项数的代理**：同一行里"字母段+图形段"各算一列段
  （overlay-13--tight 7 段 vs 声明 4），字母被裁掉的紧页又会少一段。故本
  脚本只记录对照、**不据此立断言**，也不许被引为"选项数已证"。
* **3 页 tight 拿到真实完整证据**（含 1 页真实截图 tight），标签一一对应
  无从建立 → 一律不释放；其弃权理由从"选项 0 份"变为规则/布局侧的真实
  判定（例如真实 tight 页"九宫格目标空格不在行末，当前不支持该布局"）。
* **4 页 tight 仍不产出可核对证据**，但成因**分两类且不可混说**：
  scale-09 / trans-07 是**真的 0 份裁剪**（前者 `options_band_not_detected`，
  后者第三格 `min(h,w)=8 < MIN_CELL_PX=10` 完整性未过）；grid-10 / overlay-13
  是**裁剪已写盘但未进证据链**（`option_cells_not_label_anchored` +
  `stem_evidence_ambiguous_layout` 题干侧先弃权），且落盘格中含并图
  （296 / 349）与劈图（同一 w33 图形被劈成 80+227）——所以既不能说
  "证据完整"，也不能说"选项 0 份"。
  **这不能算已修好**，是 E 之后仍开着的排版覆盖缺口，也是 G 批前必须让人工
  知晓的边界；零错放、零释放的结论不受本次勘误影响。

## 页面尺度自审（`page_band20_audit`）

逐页记录 mask 尺寸、总墨量、内容带高/宽、上下留白；`band20_flagged []`
= 50 页内容带高与宽均 ≥20（无页小于 20px 墨量判据所假设的排版尺度）。
真实 tight 页实测：240×264、ink 840、内容带 134×168、下留白 56。
