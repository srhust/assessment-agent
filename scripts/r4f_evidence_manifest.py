"""R4f 阶段A：为可复现证据包生成 SHA-256 清单（干净克隆逐字节校验用）。

范围：两次真实 Smoke 的 evidence 冻结副本、同输入回放报告目录、勘误文件。
只新增 `manifest.sha256.json`，绝不改动任何既有文件。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "evidence/real_vlm_first_run/smoke-20261019T022717Z",
    "evidence/real_vlm_second_run/smoke-20261019T051724Z",
    "evidence/r4e_replay/pre_fix",
    "evidence/r4e_replay/post_fix",
    "evidence/r4e_replay/post_fix_second_run",
    "evidence/r4e_replay/r4f_first_run",
    "evidence/r4e_replay/r4f_second_run",
    "evidence/metric_errata",
    "evidence/real_vlm_dev15/dev15-20261019T065016Z",
    "evidence/r4g_ablation",
]


def digest_tree(rel: str) -> dict:
    base = ROOT / rel
    files = {}
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.name != "manifest.sha256.json":
            files[p.relative_to(base).as_posix()] = {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "bytes": p.stat().st_size,
            }
    return {"dir": rel, "file_count": len(files), "files": files}


def main() -> int:
    bundles = []
    for t in TARGETS:
        d = ROOT / t
        if not d.is_dir():
            print(f"跳过（不存在）：{t}")
            continue
        man = {
            "kind": "assessment-agent-evidence-manifest",
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hash": "sha256",
            **digest_tree(t),
        }
        (d / "manifest.sha256.json").write_text(
            json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        bundles.append((t, man["file_count"]))
        print(f"{t}: {man['file_count']} files manifest 写入")
    print("共", len(bundles), "个目录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
