"""DSH / MCP 接入层测试：工具清单、MCP 形状调用、参数校验与脱敏。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assessment_agent.integration import (
    AssessmentAgentService,
    tool_manifest,
)
from assessment_agent.pipeline import Pipeline
from tests.conftest import make_settings


@pytest.fixture
def service(tmp_path: Path) -> AssessmentAgentService:
    settings = make_settings(tmp_path)
    return AssessmentAgentService(settings, pipeline_factory=lambda: Pipeline(settings))


def _text_of(result: dict) -> dict:
    assert result["content"], result
    return json.loads(result["content"][0]["text"])


def test_manifest_is_mcp_shaped_and_serializable():
    manifest = tool_manifest()
    assert manifest["protocolVersion"]
    names = {tool["name"] for tool in manifest["tools"]}
    assert names == {
        "assessment_solve",
        "assessment_list_skills",
        "assessment_list_tools",
        "assessment_capabilities",
    }
    for tool in manifest["tools"]:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"
    json.dumps(manifest)  # 必须可序列化


def test_solve_tool_schema_documents_all_options():
    solve = next(tool for tool in tool_manifest()["tools"] if tool["name"] == "assessment_solve")
    properties = solve["inputSchema"]["properties"]
    assert {"text", "image_path", "options", "skill", "save", "hint"} <= set(properties)
    assert properties["skill"]["enum"] and len(properties["skill"]["enum"]) == 8


def test_call_tool_lists_skills_and_tools(service: AssessmentAgentService):
    skills = service.call_tool("assessment_list_skills", {})
    assert skills["isError"] is False
    assert len(_text_of(skills)["skills"]) == 8

    tools = service.call_tool("assessment_list_tools", {})
    assert {tool["id"] for tool in _text_of(tools)["tools"]} >= {"python_math", "number_series", "z3_logic"}


def test_call_tool_solves_numeric_question(service: AssessmentAgentService):
    result = service.call_tool(
        "assessment_solve",
        {
            "text": "计算 (3.5 + 2.4) × 4 的结果是多少？",
            "options": {"A": "23.6", "B": "22.6"},
            "save": False,
        },
    )
    assert result["isError"] is False
    payload = _text_of(result)
    assert payload["solution"]["status"] == "solved"
    assert payload["solution"]["answer"] == "A"
    assert payload["verification"]["passed"] is True


def test_call_tool_unsupported_skill_is_not_an_error_but_has_no_answer(service: AssessmentAgentService):
    result = service.call_tool(
        "assessment_solve",
        {"text": "依次填入下列横线处最恰当的一项是（ ）", "skill": "verbal_understanding", "save": False},
    )
    assert result["isError"] is False
    payload = _text_of(result)
    assert payload["solution"]["status"] == "unsupported"
    assert payload["solution"]["answer"] is None


def test_call_tool_rejects_unknown_tool_and_bad_arguments(service: AssessmentAgentService):
    unknown = service.call_tool("no_such_tool", {})
    assert unknown["isError"] is True
    assert "未知工具" in _text_of(unknown)["error"]

    bad_options = service.call_tool(
        "assessment_solve",
        {"text": "计算 1+1", "options": [{"A": "2"}], "save": False},
    )
    assert bad_options["isError"] is True

    bad_save = service.call_tool("assessment_solve", {"text": "计算 1+1", "save": "yes"})
    assert bad_save["isError"] is True

    missing_image = service.call_tool("assessment_solve", {"image_path": "not-there.png"})
    assert missing_image["isError"] is True
    assert "截图不存在" in _text_of(missing_image)["error"]

    empty = service.call_tool("assessment_solve", {})
    assert empty["isError"] is True


def test_capabilities_are_sanitized(service: AssessmentAgentService, tmp_path: Path):
    payload = _text_of(service.call_tool("assessment_capabilities", {}))
    assert payload["settings"]["api_key_present"] is False
    assert "sk-" not in json.dumps(payload, ensure_ascii=False)
    assert payload["knowledge"]


def test_solve_does_not_write_runs_when_save_false(service: AssessmentAgentService, tmp_path: Path):
    service.solve({"text": "计算 12 × 8 的结果是多少？", "options": {"A": "96", "B": "97"}, "save": False})
    assert not (tmp_path / "runs").exists()
