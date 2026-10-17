"""图形推理 Skill（第一阶段）：截图 → 网格解析 → 规律推断 → 选项匹配。

能力边界（真实实现，可在 ``knowledge/figure_reasoning.md`` 查看）：
- 序列题：图形个数（等差数列）、单图形边数、墨迹面积、质心平移、镜像对称性；
- 九宫格：每行第三格 = 前两格的 异或 / 并集 / 交集；
- 布局：自动推断规则网格；排列不规则时可用显式 ``grid=(行, 列)``；
- 任何一条规律必须能解释**全部已知格**，且只与**一个**选项匹配，否则明确失败。

未实现（明确返回失败而不是猜测）：旋转/翻转匹配、曲线与立体图形、位置轮换类的
非叠加九宫格规律、多规律冲突时的仲裁。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..schemas import Question, Solution, Verification, VerificationCheck
from .base import BaseSkill, SkillContext

TOOL_ID = "figure_rules"


class FigureReasoningSkill(BaseSkill):
    id = "figure_reasoning"
    name_zh = "图形推理"
    description = "图形序列/九宫格：个数、边数、面积、平移、对称性、叠加规律"
    stage = 1
    supported = True
    requires = ("opencv-python-headless", "numpy")
    question_types = ("single_choice",)

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _grid(question: Question) -> Optional[Tuple[int, int]]:
        raw = (question.meta or {}).get("grid")
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            try:
                rows, cols = int(raw[0]), int(raw[1])
            except (TypeError, ValueError):
                return None
            if rows > 0 and cols > 0:
                return (rows, cols)
        return None

    def unavailable_reason(self) -> Optional[str]:
        try:
            import cv2  # noqa: F401
        except ImportError:
            return "图形推理需要 OpenCV：pip install opencv-python-headless"
        return None

    # ------------------------------------------------------------------ 求解
    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        if not question.image_path:
            return Solution(
                status="failed",
                skill=self.id,
                answer=None,
                confidence=0.0,
                method="figure_rules",
                error="图形推理需要题目截图：请用 --image 传入题图（纯文本无法还原图形）",
                meta={"stage": self.stage, "reason": "missing_image"},
            )
        grid = self._grid(question)
        invocation = ctx.toolbox.run(TOOL_ID, path=question.image_path, grid=grid)
        if not invocation.ok:
            return Solution(
                status="failed",
                skill=self.id,
                answer=None,
                confidence=0.0,
                method="figure_rules",
                error=f"图形分析失败：{invocation.error}",
                tool_calls=[invocation],
                meta={"stage": self.stage, "grid": list(grid) if grid else None},
            )

        payload: Dict[str, Any] = dict(invocation.result or {})
        layout = payload.get("layout") or {}
        meta: Dict[str, Any] = {
            "stage": self.stage,
            "image": question.image_path,
            "grid": list(grid) if grid else None,
            "mode": payload.get("mode"),
            "rule": payload.get("rule"),
            "agreement": payload.get("agreement") or [],
            "layout": layout,
            "predicted": payload.get("predicted"),
            "options_from_image": payload.get("options") or [],
            "candidates": payload.get("candidates") or [],
            "tool": TOOL_ID,
        }

        if payload.get("status") != "solved":
            return Solution(
                status="failed",
                skill=self.id,
                answer=None,
                confidence=0.0,
                method="figure_rules",
                error=payload.get("reason") or "图形规律未能确定唯一答案",
                steps=list(payload.get("steps") or []),
                tool_calls=[invocation],
                meta=meta,
            )

        steps = list(payload.get("steps") or [])
        bands = layout.get("cells_per_band") or []
        steps.insert(
            0,
            f"网格解析：{layout.get('mode')} 模式，{len(bands)} 行，各行列数 {bands}，"
            f"二值化阈值 {layout.get('threshold')}",
        )
        answer = payload.get("answer")
        return Solution(
            status="solved",
            skill=self.id,
            answer=answer,
            answer_text=(question.option_text(answer) if answer else "") or "",
            steps=steps,
            confidence=float(payload.get("confidence") or 0.85),
            method="figure_rules",
            tool_calls=[invocation],
            meta=meta,
        )

    # ------------------------------------------------------------ 独立复核
    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        if solution.status != "solved":
            return None
        meta = solution.meta or {}
        image = meta.get("image") or question.image_path
        grid = tuple(meta["grid"]) if meta.get("grid") else None
        layout = meta.get("layout") or {}
        threshold = layout.get("threshold")
        alternative = 127 if threshold == "otsu" else 160
        checks: List[VerificationCheck] = []

        if not image:
            return None
        invocation = ctx.toolbox.run(TOOL_ID, path=image, grid=grid, threshold=alternative)
        if invocation.ok:
            rerun = dict(invocation.result or {})
            same = rerun.get("status") == "solved" and rerun.get("answer") == solution.answer
            checks.append(
                VerificationCheck(
                    name="figure.rerun_consistent",
                    passed=same,
                    detail=(
                        f"阈值 {threshold} → {alternative} 独立复算："
                        f"{rerun.get('answer') or rerun.get('reason')}（Solver 记录 {solution.answer}）"
                    ),
                )
            )
            if same:
                checks.append(
                    VerificationCheck(
                        name="figure.rule_stable",
                        passed=rerun.get("rule") == meta.get("rule"),
                        severity="warning",
                        detail=f"两次阈值命中的规律：{meta.get('rule')} vs {rerun.get('rule')}",
                    )
                )
        else:
            checks.append(
                VerificationCheck(
                    name="figure.rerun_consistent",
                    passed=False,
                    detail=f"阈值 {alternative} 独立复算失败：{invocation.error}",
                )
            )

        checks.append(
            VerificationCheck(
                name="figure.unique_match",
                passed=len(meta.get("agreement") or []) >= 1,
                detail=f"能唯一匹配选项的规律：{meta.get('agreement')}（命中规律 {meta.get('rule')}）",
            )
        )
        if question.options and solution.answer:
            checks.append(
                VerificationCheck(
                    name="figure.answer_in_options",
                    passed=solution.answer in question.option_labels(),
                    detail=f"答案 {solution.answer} 是否在选项 {question.option_labels()} 内",
                )
            )

        passed = all(check.passed for check in checks if check.severity == "blocking")
        return Verification(
            passed=passed,
            action="accept" if passed else "reject",
            checks=checks,
            final_answer=solution.answer if passed else None,
            notes="换阈值独立复算 + 唯一匹配检查（不做答案修补，避免猜测）",
        )
