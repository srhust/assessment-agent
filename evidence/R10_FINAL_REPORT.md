# R10 最终报告：真实解题能力闭环与泛化准入

基线 `47f2cd916f41526c8c55f9c58af66fb54463a568`；本轮提交链
`22b451c`(归档)→`321b9b3`(A)→`69d756d`/`7c14863`(B)→`e834e0d`(C)→`e1ad08d`(B 证据)→`e178b88`(D1/D2)→`2f6398d`(D 接线)→E/F（本报告提交）。
全套测试：**601 passed, 2 skipped**；确定性复验 SHA16 `81ec5663b5c34b4a`。

## A 零 API 勘误与归档
- 归档 manifest 370 文件（`R10A_ARCHIVE_MANIFEST.json`）。
- 金标 answerability 口径勘误：可答分母 25/34（`R10A_METRICS_ERRATA.json/md`）；弃权与错误分列；OCR 拆分：文字精确 0.3077 vs 关键条件完整 0.9615。
- Planner 检索持久化（methods_retrieved/selected_method/rejection_reason）端到端在 B 臂实跑验证。
- 六态矩阵重出（新路径 `R10_SIX_STATE_MATRIX.json`，R9 冻结件未动）：
  documented 47 / retrievable 47 / plannable **7**（接口 39∩逐卡证据 7，da.growth-amount 降为仅接口）/ executable 42 / verified 35（技能级实解，口径注记保留）/ dev_passed 8 / full_pass 7。
- 验证器零 API 重放：dv-expr-03 历史误放在新 AST 三重校验下**确定性拦截**；截断伪影分类不入行为变更。

## B 语义计划可执行性（typed slots）
- Given(kind, mode) 类型槽位；REL(a,b,mode) 执行前重写为纯算术（相向/背向/相遇/反向→和，同向/追及→绝对差）。
- planvar 独立语料 6 题三次实跑（`R10B_PLANNER_SLOTS.json`）：**可答 5 题中 4 题在 run2/run3 端到端正确释放**（pv-01 三次全对、pv-03/04/05 各至少一次），0 错误释放；未释放全部归因网络 ReadTimeout（run3 审计 16 请求/7 超时）或验证器拦截的无效 plan（正确方向）。
- 真实缺陷修复两处并锁定测试：中英混排变量名 ASCII 截断误拒；travel 卡检索面 v2（pv-02 类题 top1 恢复）。

## C 结构化 AST 通道
- `tools/expr_ast.py`：节点+题干 span+依赖；Solver AST 优先；Verifier 三重交叉（后缀栈重放独立实现 / span 锚定题干 / 独立再解析）。
- variants 44 题：**77.27%（34/44，+4 全为 expr 族含历史 dv-expr-03），0 回归、0 误放**；degenerate-copy 仅在无结构化证据时拦截（3^4÷9=9 合法受控放行）；insufficient 10/10 保持不放。
- dev 26 题 84.62% 不变；U+2212 负号映射补齐（E 语料暴露的真实覆盖缺口）。

## D 图形：区域定位与内容完整性（未扩 Solver 规则）
- `figure_page_localizer`：带分离/节距聚类/标签剪除/超宽分裂/文本带否决/空白格定位/三坐标+像素哈希关联。
- 零 API 定位评分：**原生 figshots 选项槽 42/48、题干槽 60/66、空白格 4/4**；**30 页全新排版扰动集**（缩放/竖排/干扰元素/极紧间距，金标组装时程序化记录）：**81/105 + 103/141**；自洽 45/45 **单列报告、明确不计为真实定位**。
- 接线：VLM 选项区缺失→像素回退（题干裁剪仍强制来自解析通道——不回退整图红线）；`check_option_crop` 完整性接入；验证器**独立像素哈希复推**（伪造 pixel_sha→unverified，测试锁定）；变换通道独立重建走同一回退入口；定向二次观察实现为单次、默认关（本周视觉网关超时高发）。
- 四条件实跑（冻结件 git 复原，新文件留存）：A 13/13 B 12/13 C 0/13 D 0/13（off 与 merge 档一致）。**C/D 根因是题干序列格数的 Solver 能力边界而非选项定位**；工单禁止扩规则，故本轮不翻转 C/D。完整性检查在 C 条件实弹命中（scale-09 空白选项裁剪被拒参与匹配）。0 误放，弃权 2/2。
- 端到端新排版释放示例：ft-mirror-04--tight solved=A=gold，复推通过；`test_r10d_wiring.py` 3 项。

## E 新独立泛化 Dev（测前冻结）
- `xingce_dev_r10gen` 8 题（7 可答+1 条件不足），全部区别于卡示例/R9 变体/planvar；`R10E_FREEZE.json` 冻结模型、Prompt SHA `4b7802…`、样本、预算。
- 主指标（金标 answerability 口径）：**可答题正确释放率 rules_only 1/7 → rules+planner 6/7**；planner 释放精度 **6/6（0 误放）**；insufficient 题 rules 臂安全不释放，planner 臂因超时未评估（基建归因，如实标注）。
- 检索命中率首次可审计：选卡∩金标 refs **1/4**——两处“选卡偏了但算式对”的真实泛化缺口已记录（g01→compound-growth、g04→successive-discount）。
- image_only 臂不适用（无图形输入）；templates 臂与 rules_only 等价路径，不重复烧预算。

## F 盲评准入结论与设计（本轮不启动）
- 设计见 `R10F_BLIND_EVAL_DESIGN.json`。
- **准入判定：尚不满足**。缺口清单（按影响排序）：① 图形 C/D 条件正确作答率 0（Solver 能力边界，规则面本轮冻结）；② planner 检索选卡 1/4 命中；③ 视觉链路网络稳定性（三次 planner 实跑共 21+ 超时）；④ insufficient 定向弃权未走 unknowns 通道（g6 若 LLM 正常返回，走的是 plan_invalid 而非声明式弃权）。
- R5 87 题 image_only、蒸馏、微调：继续暂缓（未授权、未执行）。
- 历史冻结产物（v2 SHA `8aea5255603652de`、raw 15/60、R9 全套）本轮零改动。

## 交付物索引
`evidence/R10A_*`（归档/勘误/重放）、`R10_SIX_STATE_MATRIX.json`、`R10B_PLANNER_SLOTS.json`、
`R10D_LOCALIZER_EVAL.json`、`R10D_PERTURB_CORPUS/EVAL.json`、`R10D_FIGURE_LOCALIZATION.json`、
`R10D_FOUR_CONDITION{,_MERGE}.json`、`R10E_FREEZE.json`、`R10E_GENERALIZATION_DEV.json`、
`R10F_BLIND_EVAL_DESIGN.json`、本报告。代码：`expr_ast.py`、`figure_page_localizer.py`、
planner 类型槽位、figure 接线。测试 +9 于本段（localizer 5 + 接线 3 + U+2212 1），R10 全轮新增 20 项，全量 601 passed。
