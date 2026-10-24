"""**崩溃路径必须能自己证明它会落盘**（否则第 4 次复发还是靠肉眼）。

`_crash_record()` 只是我在 `__main__` 里加的 except 分支；
"加了"不等于"会触发"。这里把模块的 `EV` 指向 tmp 目录，
**真的让 `main()` 抛异常**，要求结论件变成
`status REFUSED / reason crashed / n_steps 0` ——
即"本轮没有判定"是**可见事实**，不是静默留着上一轮的 `RAN`。
"""
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util as iu  # noqa: E402

_spec = iu.spec_from_file_location("rva_crash", ROOT / "scripts"
                                   / "r14_verify_all.py")
rva = iu.module_from_spec(_spec)
_spec.loader.exec_module(rva)


def test_crash_record_marks_the_conclusion_refused(tmp_path, monkeypatch):
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "R14_VERIFICATION_BUNDLE.json").write_text(
        json.dumps({"status": "RAN", "n_steps": 21, "fails": []}),
        encoding="utf-8")
    monkeypatch.setattr(rva, "EV", ev)
    monkeypatch.setattr(rva, "ROOT", tmp_path)
    rva._crash_record("UnboundLocalError: local variable '_a13_seen' "
                      "referenced before assignment")
    now = json.loads((ev / "R14_VERIFICATION_BUNDLE.json")
                     .read_text(encoding="utf-8"))
    assert now["status"] == "REFUSED"
    assert now["reason"] == "crashed"
    assert now["n_steps"] == 0
    assert now["fails"][0].startswith("FATAL")
    assert "_a13_seen" in now["detail"]["traceback_tail"]
    assert now["run_mark"]                      # 新鲜度可见


def _write_tmp(entry: str, tmp_path):
    p = tmp_path / "entry_under_test.py"
    io.open(p, "w", encoding="utf-8").write(entry)
    return str(p)


def test_main_crash_goes_through_the_same_path(tmp_path):
    """`__main__` 的兜底不是装饰：**逐行执行入口原文**再断言落盘。

    前两版都是假测试：
    · 一版 `monkeypatch` 后**直接调 `rva.main()`** ⇒ 入口那三行没执行；
    · 一版把 `def main()` **追加在入口之后** ⇒ 追加永远不会生效，
      真跑的是原 `main()`，reason 成了 `missing_artifacts`
      （顺带证明**主动拒绝**分支确实落盘）。
    ⇒ 现在把源码**按入口标记切开**：
      `head` 原样执行（只把 EV/ROOT 指向 tmp），
      然后**替换命名空间里的 `main`** 为必然抛 `NameError` 的实现，
      再执行 `tail`（入口 `try/except BaseException` **原文**）。
    """
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "R14_VERIFICATION_BUNDLE.json").write_text(
        json.dumps({"status": "RAN", "n_steps": 21, "fails": []}),
        encoding="utf-8")
    src = (ROOT / "scripts" / "r14_verify_all.py").read_text(encoding="utf-8")
    MARK = 'if __name__ == "__main__":'
    assert src.count(MARK) == 1, "入口标记形态变了"
    # **先核注入锚点，再断言**（上版断言"源码里有绝对路径字面量"，
    # 可源码其实是 `ROOT = Path(__file__).resolve().parents[1]`
    # ⇒ 断言**永假**、测试**永不跑**，这就是"没有反证的检查"）。
    ANCHOR = 'ROOT = Path(__file__).resolve().parents[1]\nEV = ROOT / "evidence"'
    assert src.count(ANCHOR) == 1, "入口锚点形态变了"
    src = src.replace(ANCHOR,
                      f'ROOT = Path(r"{tmp_path.as_posix()}")\n'
                      f'EV = Path(r"{ev.as_posix()}")')
    head, tail = src.split(MARK, 1)
    ns = {"__name__": "entry_under_test", "__file__":
          str(ROOT / "scripts" / "r14_verify_all.py")}
    exec(compile(head, "head", "exec"), ns)          # 模块原文
    assert callable(ns["_crash_record"]), "兜底函数不在这个命名空间"

    def boom():
        raise NameError("name '_a13_seen' is not defined")

    ns["main"] = boom                                # 只换实现
    with pytest.raises(SystemExit):
        exec(compile("if True:" + tail, "tail", "exec"), ns)
    now = json.loads((ev / "R14_VERIFICATION_BUNDLE.json")
                     .read_text(encoding="utf-8"))
    assert now["status"] == "REFUSED" and now["reason"] == "crashed"
    assert now["n_steps"] == 0
    assert "_a13_seen" in now["detail"]["traceback_tail"]


def test_refuse_path_lands_even_in_a_sandbox_run(tmp_path):
    """主动拒绝（`missing_artifacts`）同样必须改写结论件——
    上面入口测试的**第一次真跑**就是撞在这条分支上被看见的。"""
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "R14_VERIFICATION_BUNDLE.json").write_text(
        json.dumps({"status": "RAN", "n_steps": 21, "fails": []}),
        encoding="utf-8")
    monkey = rva
    monkeypatch_ev(monkey, ev)
    rc = monkey.refuse_and_record("missing_artifacts",
                                 {"missing": ["R14_X.json"]})
    assert rc == 2
    now = json.loads((ev / "R14_VERIFICATION_BUNDLE.json")
                     .read_text(encoding="utf-8"))
    assert now["status"] == "REFUSED" and now["reason"] == "missing_artifacts"
    assert now["steps"] == [] and now["run_mark"]


def monkeypatch_ev(mod, ev):
    mod.EV = ev
    mod.ROOT = ev.parent


def test_entry_source_actually_has_the_guard():
    src = (ROOT / "scripts" / "r14_verify_all.py").read_text(encoding="utf-8")
    tail = src[src.index('if __name__ == "__main__":'):]
    assert "except BaseException" in tail and "_crash_record(" in tail


def test_refuse_branch_writes_even_when_worktree_dirty(tmp_path, monkeypatch,
                                                       capsys):
    ev = tmp_path / "evidence"
    ev.mkdir()
    monkeypatch.setattr(rva, "EV", ev)
    monkeypatch.setattr(rva, "ROOT", tmp_path)
    rc = rva.refuse_and_record("dirty_worktree", {"dirty": ["x"]})
    assert rc == 2
    now = json.loads((ev / "R14_VERIFICATION_BUNDLE.json")
                     .read_text(encoding="utf-8"))
    assert now["status"] == "REFUSED" and now["steps"] == []
    assert "REFUSED" in capsys.readouterr().out


def test_a13_seen_is_initialized_before_the_rewrite_loop():
    """本轮第 4 次复发的**具体形状**：判据在用之前必须已赋值。"""
    src = (ROOT / "scripts" / rva.__name__.split("_")[0] + "_verify_all.py"
           if False else (ROOT / "scripts" / "r14_verify_all.py")
           ).read_text(encoding="utf-8")
    assert src.index("_a13_seen = []") < src.index("len(_a13_seen)")
