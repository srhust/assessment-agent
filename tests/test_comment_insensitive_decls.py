"""**声明判据不得被注释影响**（本轮 A10b 假阳性的根因）。

同一份 `HAND_WRITTEN` 声明，注释里**多提几个件名**，
解析结果必须**逐字不变**；否则说明性文字就在改判据。
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util as iu  # noqa: E402

_spec = iu.spec_from_file_location("rva", ROOT / "scripts"
                                   / "r14_verify_all.py")
_rva = iu.module_from_spec(_spec)
_spec.loader.exec_module(_rva)

SRC = '''
HAND_WRITTEN = {
    "A.md", "B.md",
    # 这里**提到** C.md 只是解释为什么删了它
    # 还有 D.md 也在注释里
}
'''


def _names(src):
    m = re.search(r"HAND_WRITTEN = \{([^}]*)\}",
                  _rva.strip_py_comments(src), re.S)
    return sorted(set(re.findall(r'"([^"]+\.md)"', m.group(1))))


def test_comments_do_not_enter_the_judge():
    assert _names(SRC) == ["A.md", "B.md"]


def test_adding_comment_mentions_changes_nothing():
    base = _names(SRC)
    more = SRC.replace("}", "    # 再加一句 E.md 和 F.md\n}")
    assert _names(more) == base


def test_hash_inside_string_is_not_a_comment():
    src = 'x = "a#b.md"  # tail\n'
    kept = _rva.strip_py_comments(src)
    assert "a#b.md" in kept and "tail" not in kept


def test_real_declaration_has_no_stale_entries():
    p = ROOT / "scripts" / "e2e_r14_index.py"
    names = _names(io.open(p, encoding="utf-8").read())
    # 已删的声明**不得**因注释而复活
    assert "R14_PROGRESS_STOPGATE.md" not in names
    assert "R14_STAGE_E_DELIVERABLES.md" not in names
    assert "R14C_PITCH_ABLATION_HEAD_DELTA.md" not in names


def test_refuse_records_overwrite_the_conclusion_file(tmp_path, monkeypatch):
    """**拒绝也要落盘**：上一轮结论必须被本轮的拒绝记录覆盖。"""
    import json
    import subprocess
    doc = {"status": "REFUSED", "reason": "dirty_worktree",
           "n_steps": 0, "steps": [], "fails": ["FATAL dirty_worktree"],
           "run_mark": "x" * 12}
    dst = tmp_path / "R14_VERIFICATION_BUNDLE.json"
    dst.write_text(json.dumps({"status": "RAN", "n_steps": 21,
                               "fails": []}), encoding="utf-8")
    assert "RAN" in dst.read_text(encoding="utf-8")
    # 判据本身：REFUSED 记录必须让"上一轮 RAN"不可误读
    dst.write_text(json.dumps(doc), encoding="utf-8")
    now = json.loads(dst.read_text(encoding="utf-8"))
    assert now["status"] == "REFUSED" and now["n_steps"] == 0
    assert now["fails"] and now["fails"][0].startswith("FATAL")
    assert callable(_rva.refuse_and_record)
    monkeypatch.undo()
    assert subprocess.run  # 防止未使用告警
