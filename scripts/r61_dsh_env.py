# -*- coding: utf-8 -*-
"""R6.1：从本机 DSH 配置取网关凭据，注入环境后执行 12 题真实 VLM Dev 诊断。

密钥只经内存传递（不打印、不落盘、不写入任何工件）；baseURL/模型来自
DSH settings，vision 模型固定 glm-5.3-flash（人工裁决指定）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

HOME = Path(os.environ.get("DSH_HOME", r"E:\DSH-upgrade-test\migration-home"))
ROOT = Path(__file__).resolve().parents[1]
VISION_MODEL = "glm-5.3-flash"


def load_creds() -> tuple[str, str]:
    cr = yaml.safe_load((HOME / ".credentials.yaml").read_text(encoding="utf-8"))
    refs = cr.get("refs") or {}
    key = refs.get("HS_API_KEY") or refs.get("hs_api_key")
    if not key:
        raise SystemExit("未找到 HS_API_KEY")
    st = (HOME / "settings.yaml.imported").read_text(encoding="utf-8")
    m = re.search(r"baseURL:\s*(\S+)", st)
    if not m:
        raise SystemExit("未找到 baseURL")
    return str(key), m.group(1).strip("'\"")


def main() -> int:
    key, base = load_creds()
    env = dict(os.environ)
    env.update({
        "LLM_API_KEY": key,
        "LLM_BASE_URL": base,
        "LLM_VISION_MODEL": VISION_MODEL,
        "LLM_TEMPERATURE": "0",
    })
    print(f"[r61-dsh] base={base} vision={VISION_MODEL} temperature=0")
    if len(sys.argv) > 1:
        # 通用注入模式：r61_dsh_env.py <cmd> [args...]（凭据只进子进程环境，不落盘）
        proc = subprocess.run([sys.executable, "-X", "utf8"] + sys.argv[1:],
                              cwd=str(ROOT), env=env)
        return proc.returncode
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "scripts/r61_vlm_dev_run.py"],
        cwd=str(ROOT), env=env)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
