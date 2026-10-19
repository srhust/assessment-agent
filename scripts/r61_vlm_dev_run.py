# -*- coding: utf-8 -*-
"""R6.1 真实 VLM Dev 诊断运行包装（人工裁决第四节，预先固定）。

纪律（脚本层面固化，防止误操作）：
- 样本 = 预先固定的 **12 题**（族内 id 升序取配额，未按可解性筛选）：
  数字计算 2 / 数字推理 1 / 资料分析 2 / 图形推理 2 / 逻辑推理 2 / 语言推理 1 / 言语理解 2
- 只跑 xingce_dev，绝不触碰冻结 xingce_test；
- image_only：仅原始整页截图进链路，不注入金标题干/题型/网格/答案；
- temperature=0、现有 Solver/Verifier、Prompt 不改；
- --no-overwrite，证据完整落盘（请求/Token/原始 Vision 结果/裁剪/候选/验证动作）。

用法：
  设置环境变量 LLM_API_KEY、LLM_BASE_URL、LLM_VISION_MODEL=glm-5.3-flash 后：
  python -X utf8 scripts/r61_vlm_dev_run.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# —— 预先固定的 12 题（裁决配额，构建顺序取族内最小 id；不看任何结果）——
CASES = [
    "xd-nc-01", "xd-nc-02",          # 数字计算（连续折扣 ×2）
    "xd-ns-17",                       # 数字推理（分数列）
    "xd-da-11", "xd-da-12",           # 资料分析（文字材料 ×2）
    "xd-fig-19", "xd-fig-20",         # 图形推理（计数 / 复合变换）
    "xd-lg-14", "xd-lg-15",           # 逻辑推理（翻译推理 ×2）
    "xd-lang-24",                     # 语言推理（唯一答案类比）
    "xd-vu-22", "xd-vu-23",           # 言语理解（主旨 / 成语搭配）
]

def main() -> int:
    assert len(CASES) == 12, "样本数纪律：恰好 12 题"
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    vision_model = os.environ.get("LLM_VISION_MODEL")
    if not (api_key and base_url and vision_model):
        print("[r61] 缺少 LLM_API_KEY / LLM_BASE_URL / LLM_VISION_MODEL 配置，未发起任何调用")
        return 2
    print(f"[r61] model={vision_model} base={base_url} cases={len(CASES)}（image_only，金标零注入）")
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONIOENCODING": "utf-8",
        "ASSESSMENT_R4G_LAYOUT": os.environ.get("ASSESSMENT_R4G_LAYOUT", "merge"),
        "LLM_TEMPERATURE": os.environ.get("LLM_TEMPERATURE", "0"),
    })
    cmd = [
        sys.executable, "-X", "utf8", "-m", "assessment_agent", "eval",
        "--split", "xingce_dev", "--eval-mode", "image_only",
        "--case-ids", ",".join(CASES),
        "--run-name", "r61_vlm_dev12", "--no-overwrite", "--json",
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
    out = ROOT / "results" / "xingce_dev" / "r61_vlm_dev12"
    if proc.returncode == 0 and (out / "metrics.json").is_file():
        m = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
        usage = m.get("usage") or {}
        print("[r61] 完成。calls:", usage.get("llm_calls", "?"),
              "tokens:", usage.get("tokens", "?"),
              "| 报告见", out)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
