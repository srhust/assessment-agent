# R14 历史 Test 件字节完整性核验（A9）+ 本轮误覆写事故记录

生成：`scripts/a9_r14_historical_bytes.py`（零 HTTP；
`http_requests_made 0`）。
基线不是 HEAD，而是**把该件写进仓库的那个 commit 的 git blob**——
因为"当前提交"本身就可能已经被改坏。

## 结论：六件历史 Test / 冻结件当前字节与引入时**逐字节一致**

| 件 | 大小 | 引入 commit | 现 sha256（前 16） | 与引入时 |
|---|---|---|---|---|
| `R11G_REAL_VISION.json` | 7239 B | `c1f33c4` | `dabea7a9298ce104…` | **一致** |
| `R12G_REAL_VISION.json` | 10397 B | `07c864e` | `061b06c1a80a206d…` | **一致** |
| `R13G_REAL_VISION.json` | 3861 B | `f03670e` | `d4978e04ae9b5d45…` | **一致** |
| `R11G_FREEZE.json` | 1668 B | `c1f33c4` | `3bc6e6a17505c45e…` | **一致** |
| `R12G_FREEZE.json` | 2914 B | `07c864e` | `b2ee4a7a6194772b…` | **一致** |
| `R13G_REAL_PARSE_OFFLINE_REPLAY.json` | 1987 B | `f03670e` | `0638df7ffa3cc30b…` | **一致** |

## 同类暴露面：未跟踪数据文件 git **无法**复原

上表六件都能靠 git 复原，**因为它们被跟踪**。但同一批被拒脚本还会写
`data/**`，其中一部分**根本不在版本控制里**，一旦误跑就是**不可逆**的：

· `data/benchmark_figshots_perturb/` 整目录被 `.gitignore` 覆盖
  （`git status --ignored` 显示 `!! data/benchmark_figshots_perturb/`），
  30 张扰动图片 mtime 全为 **10-21 20:29**，本轮**未被改动**；
· `data/benchmark_figshots/dev/figshots.jsonl` 等是**被跟踪**的，可复原。

⇒ 结论：A9 只能保护**被跟踪**的历史件；对**未跟踪**数据，防线只能是
**拒跑**（A6 的拒绝式候选），事后没有任何 git 恢复手段。
本轮实测未跟踪件未被触碰，但这条边界必须写清楚，
否则"A9 全绿"会被误读成"`data/` 也不可能被毁"。

## 事故记录（我造成的，不淡化）

**是什么**：Stage E 归属工具（`scripts/r14_attribute_producer.py`）在做
"动态核验"时按启发式挑候选脚本重跑，实测挑中了
`g1_r11_real_vision.py` / `g2_r12_real_vision.py` / `g3_r13_real_batch.py`
——这三个是**会发真实 VLM 请求**的历史批脚本。它们重写了自己的证据件
`evidence/R11G_REAL_VISION.json` / `R12G_REAL_VISION.json` /
`R13G_REAL_VISION.json`（文件 mtime 停在 2026-10-23 21:33，
即本轮误跑时刻）。

**是否造成真实请求**：没有。原因是**当时本地没有可用 key**，属运气，
**不是**设计挡住了。`R14G_PLAN.budget.spent_so_far` 至今仍是
`requests 0 / tokens 0`，bundle 的 `http_requests_made 0`。

**如何发现**：`git status` 出现 `M evidence/R11G_*.json` 时人工核对发现。

**如何复原**：`git checkout -- <三件>`；本检查随后逐字节确认与引入 commit
一致（见上表）。证据件的历史 Test 结果**未被改变**。

**结构性防复发**（三层，缺一不可）：
1. `unsafe_to_run()` **默认拒绝**：源码含网络标记
   （`OpenAI|httpx|requests\.(get|post)|urlopen|chat/completions|_call_llm|
   ASSESSMENT_LLM`）者一律拒跑；编排器（`subprocess` + 字面 `scripts/x.py`
   及注册表成员）一律拒跑；无 `--out` 者一律拒跑；一律注入
   `ASSESSMENT_OFFLINE=1`。
2. **附带改动检测**：重跑前后对 `evidence/**` + `data/**` 做 sha256 快照差，
   超出该脚本自有产物即 `SAFETY_VIOLATION_worktree_collateral` 并非零退出。
3. **本字节基线（A9）**：历史件字节若与引入 commit 不符 ⇒ 直接失败。

## 自我纠错：一条**查无实据**的说法已收缩

本轮之前的会话笔记声称"同一事故还覆写了 `data/vlm_responses.jsonl`，
并从 `backup_r14_apikey/` 恢复"。实测：

· 仓库内外**不存在** `data/vlm_responses.jsonl`；
· `scripts/**` 中**没有任何**写出该路径的代码；
· `backup_r14_apikey/` 目录不存在；
· `data/**` 无本轮改动（扰动图片 mtime 全为 10-21 20:29，且
  `data/benchmark_figshots_perturb/` 处于 gitignore，`data_worktree_entries`
  仅 1 条）。

⇒ 该说法**不成立**，按事实收缩为"三个 `evidence/R?G_*.json` 被重写并从 git 复原"。
**教训**：把事故写进检查之前先核事实，否则检查会固化一个假故事。

## 反证（本检查真能失败）

`--selftest` 把 `R11G_REAL_VISION.json` 追加一个字节后重跑：
A9 **必须**非零退出并点名该件；`git checkout` 复原后必须重新通过。
本轮结果：`fails []`、`restored_identical True`。

