"""工具层基类与注册表。

Tool 提供确定性的、可独立测试的求解能力；Skill 只负责“理解题意 + 组装配方”，
二者通过 ``ToolBox`` 解耦，便于后续把工具暴露为 MCP tool。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional

from ..schemas import ToolInvocation


class ToolError(RuntimeError):
    """工具执行失败（参数错误、无法解析等）。"""


class ToolUnavailable(ToolError):
    """工具依赖缺失（如未安装 z3-solver / opencv）。"""


class Tool(ABC):
    id: str = "tool"
    description: str = ""
    requires: tuple = ()
    stage: int = 1

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> Optional[str]:
        return None

    @abstractmethod
    def run(self, **kwargs: Any) -> Any:
        """执行工具，返回可 JSON 序列化的结果。"""


def json_safe(value: Any) -> Any:
    """把常见非 JSON 类型（Fraction / Decimal / set / 异常）转成可序列化结构。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    try:
        from fractions import Fraction

        if isinstance(value, Fraction):
            return str(value) if value.denominator != 1 else value.numerator
    except ImportError:  # pragma: no cover
        pass
    return str(value)


class ToolBox:
    """工具注册表 + 调用记录（每次 Skill 求解都带自己的调用轨迹）。"""

    def __init__(self, tools: Optional[Iterable[Tool]] = None):
        self._tools: Dict[str, Tool] = {}
        for tool in tools or ():
            self.register(tool)
        self.invocations: List[ToolInvocation] = []

    def register(self, tool: Tool) -> None:
        self._tools[tool.id] = tool

    def get(self, tool_id: str) -> Tool:
        tool = self._tools.get(tool_id)
        if tool is None:
            raise ToolError(f"未注册的工具: {tool_id}")
        return tool

    def has(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def describe(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": tool.id,
                "description": tool.description,
                "requires": list(tool.requires),
                "available": tool.available(),
                "unavailable_reason": tool.unavailable_reason(),
                "stage": tool.stage,
            }
            for tool in self._tools.values()
        ]

    def reset_trace(self) -> None:
        self.invocations = []

    def run(self, tool_id: str, **kwargs: Any) -> ToolInvocation:
        """执行并记录一次调用；工具异常被记录为 ok=False，不抛出。"""
        started = time.perf_counter()
        tool = self.get(tool_id)
        invocation = ToolInvocation(tool=tool_id, args=json_safe(kwargs))
        if not tool.available():
            invocation.ok = False
            invocation.error = tool.unavailable_reason() or f"工具 {tool_id} 不可用"
        else:
            try:
                invocation.result = json_safe(tool.run(**kwargs))
            except ToolError as exc:
                invocation.ok = False
                invocation.error = str(exc)
            except Exception as exc:  # noqa: BLE001 - 工具层统一兜底成结构化失败
                invocation.ok = False
                invocation.error = f"{type(exc).__name__}: {exc}"
        invocation.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        self.invocations.append(invocation)
        return invocation
