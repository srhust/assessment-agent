# manifest.json 路径勘误（R6 任务A，不修改原冻结记录）

原 `manifest.json` 的 `sha256_files` 存在两处键名缺陷（构建脚本以 Windows
反斜杠与相对目录名写入所致）：

1. 键分隔符为 `\`（如 `images\test\xn-xl-03.png`），跨平台不可直接使用；
2. 题目表键写作 `xingce.jsonl`，实际文件在 `test/xingce.jsonl`。

**处理**：原 manifest 保持冻结字节不变；本目录新增
[`MANIFEST_PATHS.json`](MANIFEST_PATHS.json) —— 与 `sha256_files` 逐条对应的
**POSIX 规范键**可移植清单（143 项，值完全相同，仅路径规范化）。
核验工具 `scripts/verify_xingce_frozen.py` 同时支持两种键形，并优先按
勘误解析。

**R6 核验记录**：入库前本地原字节 143/143 SHA-256 一致
（`evidence/xingce_frozen_verify/local_precommit.json`，verdict=PASS）；
PNG 为 R5 首次生成的原始文件，未做任何重渲染。干净克隆复验见
`evidence/xingce_frozen_verify/`（R6 提交后追加）。
