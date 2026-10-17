"""Solver：把题目交给对应 Skill，并统一处理异常与工具轨迹。"""

from __future__ import annotations

import time
from typing import List, Optional

from .schemas import Question, Solution, ToolInvocation
from .skills import BaseSkill, SkillContext, SkillRegistry


class Solver:
    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def solve(self, question: Question, skill_id: str, ctx: SkillContext) -> Solution:
        started = time.perf_counter()
        skill: Optional[BaseSkill] = self.registry.get(skill_id)
        if skill is None:
            return Solution(
                status="unsupported",
                skill=skill_id,
                error=f"未知题型 {skill_id!r}；可用题型：{self.registry.ids()}",
                method="solver_dispatch",
                meta={"latency_ms": round((time.perf_counter() - started) * 1000.0, 3)},
            )
        ctx.toolbox.reset_trace()
        try:
            solution = skill.solve(question, ctx)
        except Exception as exc:  # noqa: BLE001 - 单题失败不应终止评测流程
            solution = Solution(
                status="failed",
                skill=skill.id,
                method=type(skill).__name__,
                error=f"{type(exc).__name__}: {exc}",
                tool_calls=list(ctx.toolbox.invocations),
            )
        if not solution.tool_calls:
            solution.tool_calls = list(ctx.toolbox.invocations)
        solution.skill = skill.id
        solution.meta.setdefault("stage", skill.stage)
        solution.meta.setdefault("latency_ms", round((time.perf_counter() - started) * 1000.0, 3))
        return solution

    @staticmethod
    def tool_trace(solution: Solution) -> List[ToolInvocation]:
        return list(solution.tool_calls)
