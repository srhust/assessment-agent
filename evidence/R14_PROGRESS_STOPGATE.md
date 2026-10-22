# R14 本轮离线成果与 Stage G 门停点（供人工评审）

日期：2026-10-22 · 零 HTTP（预算 6 次 / 100k token 全部未动用）· 全套测试 663 passed / 2 skipped

## A–F/H 完成情况

| 阶段 | 状态 | 证据 |
|---|---|---|
| A P0 停机逻辑 | ✅ 上轮已提交 | `9040337` + `test_r14a_stop_logic.py` |
| B CellGeometry 契约 | ✅ | `diagram_layout.py`：pitch 槽/边界/置信度三档；两已知格 ⇒ 单间距样本=medium，"规律唯一性由下游规则通道独立证明"写入 note |
| C pitch 因果消融 | ✅ | `evidence/R14C_PITCH_ABLATION.json`（双轴×3 画布×5 件全数字）+ `R14C_REAL_PARSE_REPLAY.json` |
| D 求解/验证强化 | ✅ 核心 | 绝对面积跨协议尺度、验证器复用坐标契约、节距槽统一、**退化常数序列弃权** |
| E 选项证据完整性 | ⚠️ 根因已定位 | overlay-12 opt C 近空白、tight 页选项 0 份、trans-07 选项框 82px 含双图——同一"选项带切分"缺陷，待下轮修 |
| F 六条件+扰动配对 | ✅ | `R14F_SIX_CONDITION.json`（F 5/13 全对零错放，基线 1/13 之上）、`R14F_PERTURB_RELEASE_AUDIT.json`（wrong_grid_releasable **[]**，wrong_accept_pert **[]**） |
| G 小批 HTTP | ⛔ **门停** | 见下 |
| H KB 审计 | ✅ | `R14H_KB_CHANNEL_AUDIT.json`（插桩通道证据 + 五态 + 4 卡归因 + 勘误附录） |

## 最重要因果结论（诚实表述，弱于"pitch 根因"假设）

- 真实解析件 mirror-04/rot90-01 现**端到端正确释放 A**（Solver→Verifier→final_answer 三层，0 错误接受）。
- 消融证明：失败充分原因是**跨协议尺度惩罚**（legacy 相对面积把选项全部罚饱和 1.0），不是格宽 pitch；
  A(旧 max+2)/B(pitch)/C(金标) 三种画布在修复后打分协议下**全部** solved——`pitch_causal_fixed` 标签**未触发**。
- pitch/CellGeometry 的价值在几何证据层（槽边界、空格槽、墨偏移），不在打分通路。
- trans-07：三格逐像素全同（sha e1dbadd7×3）——旋转/镜像族现为**如实弃权**（旧行为是 I/FH/FV/R180 四条退化候选共同**假证错误 B 并通过独立验证释放**；本轮真实解析件曾复现该错误 accept，已按 P0 拦截修复并固化反例测试）。
- dev15 金标区上 trans-07 由 translate(+0.12,0) 唯一正确判 A——两布尔字段语义仍分离（overlay-12 为 (True,False)）。

## Stage H 关键勘误（工单 r10g-08 要求）

插桩证明 r10g-07 历史 solved 走的是 **python_math 字符串抽取主链**，不是 `_solve_ast` 结构化 AST；R13F"direct-AST"表述不准确。r10g-08 离线不可复现 solved（其历史成功属 LLM planner 通道）——`ast_expr=null` 本就证明不了通道。r10g-03 在当前代码态被结构化 AST 离线解出（缺卡但执行器覆盖）。

## 为什么停在 G 前（而非"没做完所以跳过"）

1. 工单条件"G 仅在 A–F 离线因果评审后"——因果评审材料已就绪，**评审权在人工**；
2. E 未完成：5 件中 2 件（overlay-12、tight）的失败根因是选项裁剪，与 pitch/尺度**正交**——现在跑 G 只是把已知离线失败重复到付费通道；
3. 计划 G 顺序含这两件；E 修完后 G 的边际信息量最大。

G 的闸门代码已备：原子请求预算（R13-G）+ **token 硬顶**（`ASSESSMENT_LLM_MAX_TOKENS` / `max_tokens=`，发前拒绝，在途如实计入，3 个新测试锁定）。批准即在下一轮启用 ≤6 请求 + 100,000 token 上限批次。

## 需要人工裁决的点

1. 接受"尺度协议是主因、pitch 非因果必需"这一**弱于预设**的结论（数据在 R14C_PITCH_ABLATION.json 双轴表）。
2. 批准下一轮顺序：**E（选项带切分根修）→ E 回归 → G 批次**；或坚持先跑 G 拿带外证据。
3. r4a 两个历史测试断言按收紧方向更新（退化恒等序列不再放行；disclose 于 commit 4712825），请确认该方向。
