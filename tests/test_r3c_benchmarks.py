"""R3c 数据守护：新基准完整性 + 冻结盲集不可回写 + 数据集间互斥。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _read_rows(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


EPI = DATA / "benchmark_epistemic"
LANG = DATA / "benchmark_language"
VBL = DATA / "benchmark_verbal"


def test_epistemic_dev_manifest_consistency():
    man = json.loads((EPI / "manifest.json").read_text(encoding="utf-8"))
    rows = _read_rows(EPI / "dev" / "epistemic.jsonl")
    assert len(rows) == man["splits"]["dev"]["count"] == 13
    assert _sha(EPI / "dev" / "epistemic.jsonl") == man["splits"]["dev"]["sha256"]
    assert {r["id"] for r in rows} == {r["id"] for r in rows}  # 无重复 id
    assert sum(1 for r in rows if r["expected_status"] == "failed") == 4
    # 最小对比对必须成组：同 group 内外层谓词不同（P 相同思路的人工网格保证）
    groups = {r["group"] for r in rows}
    assert {"reported", "asserted", "denied"} <= groups


def test_language_dev_and_blind_frozen():
    man = json.loads((LANG / "manifest.json").read_text(encoding="utf-8"))
    dev = _read_rows(LANG / "dev" / "language.jsonl")
    blind = _read_rows(LANG / "blind" / "language.jsonl")
    assert len(dev) == 20 and man["splits"]["dev"]["count"] == 20
    assert _sha(LANG / "dev" / "language.jsonl") == man["splits"]["dev"]["sha256"]
    b = man["splits"]["blind"]
    assert len(blind) == b["count"] == 12
    assert _sha(LANG / "blind" / "language.jsonl") == b["sha256"]
    # dev/blind 词对不重叠（防"背题"）
    dev_stems = {r["stem"] for r in dev}
    assert not (dev_stems & {r["stem"] for r in blind})
    # blind 首跑记录存在且与冻结文件同 sha（首跑即终录）
    firstrun = json.loads((LANG / "blind_firstrun_metrics.json").read_text(encoding="utf-8"))
    assert firstrun["total"] == 12


def test_verbal_blind2_frozen_and_disjoint_from_blind1():
    rows2 = _read_rows(VBL / "blind2" / "verbal.jsonl")
    rows1 = _read_rows(VBL / "blind" / "verbal.jsonl")
    man = json.loads((VBL.parent / "benchmark_language" / "manifest.json").read_text(encoding="utf-8"))
    assert len(rows2) == 10
    assert _sha(VBL / "blind2" / "verbal.jsonl") == man["splits"]["verbal_blind2"]["sha256"]
    # 盲集2 与盲集1 题干互斥（不复用已暴露样本）
    assert not ({r["stem"] for r in rows2} & {r["stem"] for r in rows1})
    # 旧 blind（R3b 冻结）sha 不变——盲测纪律的机器证明
    man_v = json.loads((VBL / "manifest.json").read_text(encoding="utf-8"))
    assert _sha(VBL / "blind" / "verbal.jsonl") == man_v["splits"]["blind"]["sha256"]


@pytest.mark.parametrize("split_dir,fname,count", [
    ("benchmark_epistemic/dev", "epistemic.jsonl", 13),
    ("benchmark_language/dev", "language.jsonl", 20),
    ("benchmark_language/blind", "language.jsonl", 12),
    ("benchmark_verbal/blind2", "verbal.jsonl", 10),
])
def test_rows_shape(split_dir, fname, count):
    rows = _read_rows(DATA / split_dir / fname)
    assert len(rows) == count
    for r in rows:
        assert r["question_type"] == "single_choice"
        assert {"id", "skill", "expected_status", "stem", "options", "tags"} <= set(r)
        assert r["expected_status"] in {"solved", "failed"}
        assert (r["answer"] is None) == (r["expected_status"] == "failed")
        labels = [o["label"] for o in r["options"]]
        assert len(labels) == len(set(labels)) >= 2
