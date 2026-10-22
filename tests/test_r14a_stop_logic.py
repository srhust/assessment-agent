# -*- coding: utf-8 -*-
"""R14-A（P0）：错误接受停机逻辑反例组——零 API。

R13-G 的停机条件 `row_layer()==3 ∧ final_answer≠gold` 定义上不可达
（row_layer 只有 final_answer==gold 才返回 3），误 accept/repair 无法停批。
本组测试固化修复后的语义：判定**只**依赖 Verifier 原始 action/final_answer，
并用 g3 主循环 stub 证明误放发生后的下一个样本不再被执行。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.tools.release_scoring import release_status, row_layer  # noqa: E402


def test_wrong_accept_is_caught_even_though_row_layer_says_1():
    # Solver 答错、Verifier 照错 accept：R13 旧条件的死分支演示
    rs = release_status("accept", "C", gold="A")
    assert rs["released"] is True and rs["wrong_accept"] is True
    rec = {"status": "solved", "answer": "C", "gold": "A",
           "verify": "accept", "final_answer": "C"}
    assert row_layer(rec) == 0  # 候选即错：层 0
    assert not (row_layer(rec) == 3 and rec["final_answer"] != rec["gold"])
    # 候选正确但 Verifier repair 改错：layer 1（同样永不到 3）——旧条件依旧漏停
    rec2 = {"status": "solved", "answer": "A", "gold": "A",
            "verify": "repair", "final_answer": "B"}
    assert row_layer(rec2) == 1
    assert not (row_layer(rec2) == 3 and rec2["final_answer"] != rec2["gold"])


def test_solver_correct_but_repair_wrong_still_stops():
    rs = release_status("repair", "B", gold="A")
    assert rs["wrong_accept"] is True


def test_accept_repair_correct_release():
    assert release_status("accept", "A", "A") == {
        "released": True, "wrong_accept": False,
        "release_anomaly_missing_final": False}
    assert release_status("repair", "A", "A")["released"] is True


def test_missing_or_empty_final_answer_is_anomaly_not_release():
    for fa in (None, ""):
        rs = release_status("accept", fa, "A")
        assert rs["released"] is False
        assert rs["release_anomaly_missing_final"] is True
        assert rs["wrong_accept"] is False


def test_reject_and_unverified_are_silent_not_released():
    for act in ("reject", "unverified"):
        rs = release_status(act, None, "A")
        assert rs == {"released": False, "wrong_accept": False,
                      "release_anomaly_missing_final": False}


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeClient:
    max_requests = 6

    def __init__(self):
        self._requests_sent = 0

    def usage_snapshot(self):
        return {"calls": self._requests_sent, "total_tokens": 111 * self._requests_sent}

    def audit_snapshot(self):
        return {"requests_sent": self._requests_sent, "error_types": {}}


class _FakePipeline:
    """按预置表逐题返回；记录被执行的题，以证明停机后不再消费样本。"""

    def __init__(self, table):
        self.table = table
        self.executed = []

    def run(self, image_path=None, text=None, save=True, run_id=""):
        pid = run_id.replace("r13g-", "")
        self.executed.append(pid)
        act, fa, sol_ans = self.table[pid]
        rd = Path(image_path).parent / f"run_{pid}"
        rd.mkdir(parents=True, exist_ok=True)
        q = _NS(stem="题干", options=[_NS(label="A"), _NS(label="B")])
        return _NS(question=q, route=_NS(skill="figure_reasoning"),
                   run_dir=rd,
                   solution=_NS(status="solved", answer=sol_ans,
                                meta={}, error=None),
                   verification=_NS(action=act, final_answer=fa, checks=[]))


def test_batch_halts_immediately_after_wrong_accept(tmp_path, monkeypatch):
    import importlib
    g3 = importlib.import_module("scripts.g3_r13_real_batch") \
        if (ROOT / "scripts" / "__init__.py").exists() else None
    if g3 is None:
        sys.path.insert(0, str(ROOT / "scripts"))
        g3 = importlib.import_module("g3_r13_real_batch")
    # 假图片（sha 读取需要）
    for _, rel, _gold in g3.ORDER:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"fakepng")
    (tmp_path / "evidence").mkdir(exist_ok=True)
    table = {
        # mirror-04：Solver 候选正确，Verifier 却 repair 成错误项 → 必须停机
        "ft-mirror-04": ("repair", "B", "A"),
        "ft-rot90-01": ("accept", "A", "A"),
    }
    pipe = _FakePipeline(table)
    client = _FakeClient()
    monkeypatch.setattr(g3, "ROOT", tmp_path)
    monkeypatch.setattr(g3, "build_client", lambda *a, **k: client)
    monkeypatch.setattr(g3, "Pipeline", lambda *a, **k: pipe)
    monkeypatch.setattr(sys, "argv", ["g3"])
    rc = g3.main()
    assert rc == 0
    out = json.loads((tmp_path / "evidence" / "R13G_REAL_VISION.json")
                     .read_text(encoding="utf-8"))
    assert out["stop_reason"].startswith("WRONG ACCEPT/REPAIR on ft-mirror-04")
    assert pipe.executed == ["ft-mirror-04"]          # 下一题未被消费
    assert out["denominator"]["executed"] == 1
    r0 = out["results"][0]
    assert r0["release_status"] == {"released": True, "wrong_accept": True,
                                    "release_anomaly_missing_final": False}
    assert r0["release"] == "final"
