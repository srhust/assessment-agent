"""CLI 冒烟测试：solve / eval / mcp-manifest / call。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assessment_agent.cli import main


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ASSESSMENT_OFFLINE", "1")
    monkeypatch.setenv("ASSESSMENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ASSESSMENT_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("ASSESSMENT_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    yield


def test_cli_solve_json_output(capsys):
    code = main(["solve", "--text", "计算 12.5 × 8 - 36 ÷ 4", "--options", "A=90,B=91", "--no-save", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["solution"]["answer"] == "B"
    assert payload["verification"]["passed"] is True


def test_cli_solve_unsupported_returns_code_2(capsys):
    code = main(["solve", "--text", "依次填入横线处最恰当的一项", "--skill", "verbal_understanding", "--no-save"])
    assert code == 2
    assert "unsupported" in capsys.readouterr().out


def test_cli_skills_and_doctor(capsys):
    assert main(["skills"]) == 0
    assert "数字计算" in capsys.readouterr().out
    assert main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [skill["id"] for skill in payload["skills"]].__len__() == 8


def test_cli_mcp_manifest_writes_file(tmp_path: Path, capsys):
    out = tmp_path / "manifest.json"
    assert main(["mcp-manifest", "--out", str(out)]) == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["tools"][0]["name"] == "assessment_solve"


def test_cli_call_with_args_file(tmp_path: Path, capsys):
    args_file = tmp_path / "args.json"
    args_file.write_text(
        json.dumps(
            {"text": "数列 2, 5, 8, 11, ( ) 的下一项是？", "options": {"A": "13", "B": "14"}, "save": False},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code = main(["call", "--tool", "assessment_solve", "--args-file", str(args_file)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["solution"]["answer"] == "B"


def test_cli_call_tolerates_utf8_bom_args_file(tmp_path: Path, capsys):
    args_file = tmp_path / "args-bom.json"
    payload = json.dumps({"text": "计算 2+2 等于多少", "options": {"A": "4", "B": "5"}, "save": False}, ensure_ascii=False)
    args_file.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))
    code = main(["call", "--tool", "assessment_solve", "--args-file", str(args_file)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["solution"]["answer"] == "A"


def test_cli_call_reports_errors(capsys):
    assert main(["call", "--tool", "no_such_tool"]) == 1
    assert "未知工具" in capsys.readouterr().out
    assert main(["call", "--tool", "assessment_solve", "--args", "not-json"]) == 2


def test_cli_eval_runs_against_repository_dataset(capsys):
    code = main(["eval", "--split", "test", "--no-save", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 15
    assert payload["accuracy"] == pytest.approx(1.0)
