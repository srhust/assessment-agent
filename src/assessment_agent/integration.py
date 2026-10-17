"""DSH / MCP 接入层。

目标：把已有的稳定入口（``Pipeline.run`` / ``Pipeline.capabilities``）暴露成
**无需改动核心代码**的调用面：

- ``tool_manifest()``：MCP 风格的工具清单（``{"tools": [...]}``），可直接给 MCP server
  或 DSH 的能力注册使用；
- ``AssessmentAgentService.call_tool(name, arguments)``：MCP ``tools/call`` 形状的返回
  （``{"content": [{"type": "text", "text": ...}], "isError": bool}``）；
- ``AssessmentAgentService.solve(payload)``：给 DSH / 其它 Agent 用的纯 JSON 入口。

约定：
- 只做参数校验与结果包装，不复制求解逻辑；
- 工具失败返回 ``isError: true`` 与原因，绝不返回伪造答案；
- 返回内容不含任何密钥（``Settings.describe()`` 已脱敏）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from .config import Settings
from .pipeline import Pipeline, PipelineError

PROTOCOL_VERSION = "2024-11-05"

SOLVE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "题干文本；含 “A. …” 选项时可自动拆分",
        },
        "image_path": {
            "type": "string",
            "description": "题目截图绝对路径（需要配置 LLM_VISION_MODEL）",
        },
        "options": {
            "description": "选项：{\"A\": \"12\", \"B\": \"13\"} 或 \"A=12,B=13\" 或 [\"12\", \"13\"]",
            "oneOf": [
                {"type": "string"},
                {"type": "object"},
                {"type": "array", "items": {"type": "string"}},
            ],
        },
        "skill": {
            "type": "string",
            "enum": [
                "verbal_understanding",
                "language_reasoning",
                "numeric_calculation",
                "data_analysis",
                "number_series",
                "figure_reasoning",
                "logical_reasoning",
                "personality",
            ],
            "description": "显式指定题型；省略则自动路由",
        },
        "save": {
            "type": "boolean",
            "description": "是否把产物写入 runs/（默认 true）",
            "default": True,
        },
        "hint": {"type": "string", "description": "截图抽取的补充提示"},
    },
    "additionalProperties": False,
}

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "assessment_solve",
        "title": "求解单道测评题",
        "description": (
            "输入题干文本或题目截图，返回答案、解题步骤、校验结论与产物路径。"
            "第一阶段支持数字计算、数字推理、逻辑推理；其余题型返回 unsupported，不编造答案。"
        ),
        "inputSchema": SOLVE_SCHEMA,
    },
    {
        "name": "assessment_list_skills",
        "title": "列出题型 Skill",
        "description": "返回八个题型的 ID、中文名、阶段（MVP / 接口）与依赖。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "assessment_list_tools",
        "title": "列出求解工具",
        "description": "返回工具层（python_math / number_series / z3_logic / figure_tools）及其可用状态。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "assessment_capabilities",
        "title": "能力自检",
        "description": "返回脱敏配置、Skill 状态、工具可用性与知识库清单（等价于 CLI doctor）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def tool_manifest() -> Dict[str, Any]:
    """MCP 风格工具清单。"""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {"name": "assessment-agent", "version": "0.1.0"},
        "tools": [dict(tool) for tool in TOOL_DEFINITIONS],
    }


def _text_content(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str)}],
        "isError": False,
    }


def _error_content(message: str) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps({"error": message}, ensure_ascii=False)}],
        "isError": True,
    }


def _require_str(arguments: Mapping[str, Any], key: str) -> Optional[str]:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"参数 {key} 必须是字符串")
    return value


class AssessmentAgentService:
    """无状态服务封装；``pipeline_factory`` 便于注入假模型做离线测试。"""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        pipeline_factory: Optional[Callable[[], Pipeline]] = None,
    ):
        self.settings = settings or Settings.from_env()
        self._pipeline_factory = pipeline_factory or (lambda: Pipeline(self.settings))
        self._pipeline: Optional[Pipeline] = None

    @property
    def pipeline(self) -> Pipeline:
        if self._pipeline is None:
            self._pipeline = self._pipeline_factory()
        return self._pipeline

    # ------------------------------------------------------------------ 清单
    def list_tools(self) -> Dict[str, Any]:
        return tool_manifest()

    def list_skills(self) -> Dict[str, Any]:
        return {"skills": self.pipeline.registry.describe()}

    def list_solver_tools(self) -> Dict[str, Any]:
        return {"tools": self.pipeline.toolbox.describe()}

    def capabilities(self) -> Dict[str, Any]:
        return self.pipeline.capabilities()

    # ------------------------------------------------------------------ 调用
    def solve(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """DSH / 其它 Agent 直接调用的纯 JSON 入口。"""
        text = _require_str(payload, "text")
        image_path = _require_str(payload, "image_path")
        skill = _require_str(payload, "skill")
        hint = _require_str(payload, "hint")
        options = payload.get("options")
        if isinstance(options, list) and any(isinstance(item, (dict, list)) for item in options):
            raise ValueError('options 数组只接受字符串；带标号请传 {"A": "..."}')
        if isinstance(options, dict):
            for key in options:
                if not isinstance(key, str):
                    raise ValueError("options 的键必须是字符串标号")
        save = payload.get("save", True)
        if not isinstance(save, bool):
            raise ValueError("save 必须是布尔值")
        if image_path and not Path(image_path).is_file():
            raise ValueError(f"截图不存在: {image_path}")
        try:
            run = self.pipeline.run(
                text=text,
                image_path=image_path,
                options=options,
                skill=skill,
                save=save,
                hint=hint,
            )
        except PipelineError as exc:
            raise ValueError(str(exc)) from exc
        return run.model_dump(mode="json")

    def call_tool(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """MCP ``tools/call`` 形状的调用。"""
        args: Mapping[str, Any] = arguments or {}
        handlers = {
            "assessment_solve": self.solve,
            "assessment_list_skills": lambda _: self.list_skills(),
            "assessment_list_tools": lambda _: self.list_solver_tools(),
            "assessment_capabilities": lambda _: self.capabilities(),
        }
        handler = handlers.get(name)
        if handler is None:
            return _error_content(f"未知工具 {name!r}；可用工具: {sorted(handlers)}")
        try:
            return _text_content(dict(handler(args)))
        except (ValueError, TypeError, KeyError) as exc:
            return _error_content(f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ 关闭
    def close(self) -> None:
        self._pipeline = None
