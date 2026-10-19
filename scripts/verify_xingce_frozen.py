# -*- coding: utf-8 -*-
"""R6 任务A：benchmark_xingce 冻结包完整性核验。

对 manifest.json 的 sha256_files 全清单逐文件重算 SHA-256。
原 manifest 键为 Windows 反斜杠路径，且题目表键写作 "xingce.jsonl"
（实际文件在 test/xingce.jsonl）。本脚本按下方勘误规则解析路径，
**不修改、不覆盖原 manifest**；正式的可移植清单见 MANIFEST_PATHS.json。

用法：python scripts/verify_xingce_frozen.py [--root <repo>] [--report <out.json>]
退出码 0 = 全部条目可定位且字节哈希一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BENCH_REL = Path("data") / "benchmark_xingce"

# 勘误解析：manifest 原键 → 仓内实际路径候选（顺序尝试）。不改原文件。
def resolve(bench: Path, key: str) -> Path | None:
    norm = key.replace("\\", "/")
    for cand in (bench / norm, bench / "test" / norm, Path(norm)):
        if cand.is_file():
            return cand
    return None


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="仓库根（干净克隆目录即可）")
    ap.add_argument("--report", default="", help="可选：核验报告 JSON 输出路径")
    a = ap.parse_args()

    bench = Path(a.root) / BENCH_REL
    manifest = json.loads((bench / "manifest.json").read_text(encoding="utf-8"))
    entries: dict = manifest["sha256_files"]

    ok, missing, mismatch = [], [], []
    for key, want in sorted(entries.items()):
        path = resolve(bench, key)
        if path is None:
            missing.append(key)
            continue
        got = sha256(path)
        (ok if got == want else mismatch).append(key)

    summary = {
        "manifest_entries": len(entries),
        "ok": len(ok),
        "missing": missing,
        "sha_mismatch": mismatch,
        "verdict": "PASS" if not missing and not mismatch else "FAIL",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if a.report:
        out = Path(a.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
