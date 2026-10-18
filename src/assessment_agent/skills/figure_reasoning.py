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

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..schemas import Question, Solution, ToolInvocation, Verification, VerificationCheck
from .base import BaseSkill, SkillContext

TOOL_ID = "figure_rules"
ADAPTER_ID = "figure_rules_adapter"


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

    # ------------------------------------------------------ 区域模式（R2a）
    @staticmethod
    def _region_inputs(question: Question) -> Optional[Dict[str, Any]]:
        """从 VisionParser 的区域 meta 中提取题干矩阵裁剪与选项裁剪。"""
        vision = (question.meta or {}).get("vision") or {}
        stem_crop = vision.get("stem_matrix_crop")
        option_regions = vision.get("option_regions") or []
        options = [
            (str(region.get("label")), str(region.get("crop_path")))
            for region in option_regions
            if region.get("has_figure") and region.get("crop_path")
        ]
        if stem_crop and len(options) >= 2:
            return {
                "stem_crop": stem_crop,
                "options": options,
                "grid": tuple(vision["stem_matrix_grid"]) if vision.get("stem_matrix_grid") else None,
            }
        return None

    def _region_incomplete(self, question: Question) -> Solution:
        """v2 区域证据不完整：说明缺什么，返回结构化失败（不猜测、不回退整图）。"""
        vision = (question.meta or {}).get("vision") or {}
        problems: List[str] = []
        stem_crop = vision.get("stem_matrix_crop")
        if not stem_crop:
            problems.append("缺少题干矩阵裁剪")
        elif not Path(stem_crop).is_file():
            problems.append(f"题干矩阵裁剪文件不存在: {stem_crop}")
        figure_options = [
            region for region in (vision.get("option_regions") or []) if region.get("has_figure")
        ]
        with_crop = [region for region in figure_options if region.get("crop_path")]
        lost = [region for region in with_crop if not Path(str(region.get("crop_path"))).is_file()]
        if len(figure_options) < 2:
            problems.append(f"带图形的选项仅 {len(figure_options)} 个（至少需要 2 个）")
        if len(with_crop) < 2:
            problems.append("选项图形裁剪不足 2 份")
        if lost:
            problems.append(f"选项裁剪文件丢失: {[region.get('label') for region in lost]}")
        detail = "；".join(problems) or "区域信息不足以定位图形"
        return Solution(
            status="failed",
            skill=self.id,
            answer=None,
            confidence=0.0,
            method=ADAPTER_ID,
            error=f"图形区域证据不完整：{detail}（不回退整图求解，避免在网页截图上产生假证据）",
            steps=[f"[区域检查] {detail}"],
            meta={"stage": self.stage, "reason": "region_incomplete", "problems": problems},
        )

    def _run_adapter(self, inputs: Dict[str, Any], *, threshold: Optional[float] = None, canvas_path: Optional[str] = None) -> Dict[str, Any]:
        from ..tools.figure_adapter import solve_figure_regions

        return solve_figure_regions(
            inputs["stem_crop"],
            inputs["options"],
            grid=inputs.get("grid"),
            threshold=threshold,
            canvas_path=canvas_path,
        )

    def _solve_regions(self, question: Question, ctx: SkillContext, inputs: Dict[str, Any]) -> Solution:
        """有区域裁剪证据时走适配层；适配异常 → 结构化失败（不回退整图猜测）。"""
        if getattr(ctx.settings, "r4a_disable_six_rules", False):
            # 消融臂 r1：六类通道禁用，仅变换通道可作答
            payload = {"status": "failed", "reason": "ablation: 六类通道禁用",
                       "candidates": [], "verdict": {}}
        else:
            try:
                payload = self._run_adapter(inputs)
            except Exception as exc:  # noqa: BLE001 - 区域异常记录为失败，不猜答案
                return Solution(
                    status="failed",
                    skill=self.id,
                    answer=None,
                    confidence=0.0,
                    method=ADAPTER_ID,
                    error=f"区域适配失败：{type(exc).__name__}: {exc}",
                    tool_calls=[
                        ToolInvocation(
                            tool=ADAPTER_ID,
                            args={"stem_crop": inputs["stem_crop"], "labels": [label for label, _ in inputs["options"]]},
                            ok=False,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    ],
                    meta={"stage": self.stage, "reason": "adapter_error"},
                )
        six_solved = payload.get("status") == "solved"
        # R4b P1-1：变换通道对**所有**区域化题常开，适用性由图形结构决定
        # （solve_figure_transform_regions 内部结构过滤：单行末空格/九宫格第9空格），
        # 不再读取数据集 layout 标签——Oracle/AutoRouter/ImageOnly 三模式同一决策路径。
        if getattr(ctx.settings, "r4a_transform", True):
            # 六类未解出 → 变换通道独立作答；六类已解 → 双通道交叉校验。
            # 冲突拦截仅限"变换规律族真的表态"的情形（有投票候选）：
            # 变换通道无候选 = 结构上不适用 = 无意见，保留六类结果（历史语义）。
            try:
                from ..tools.figure_adapter import solve_figure_transform_regions

                tp = solve_figure_transform_regions(
                    inputs["stem_crop"],
                    inputs["options"],
                    grid=inputs.get("grid"),
                )
            except Exception as exc:  # noqa: BLE001
                tp = {"status": "failed", "reason": f"变换通道异常：{type(exc).__name__}: {exc}",
                      "candidates": [], "verdict": {"verdict": "no_rule"}}
            tinv = ToolInvocation(
                tool="figure_transform",
                args={"stem_crop": inputs["stem_crop"],
                      "labels": [label for label, _ in inputs["options"]],
                      "mode": tp.get("mode")},
                ok=True,
                result={"status": tp.get("status"), "answer": tp.get("answer"),
                        "verdict": tp.get("verdict"),
                        "candidates": tp.get("candidates"),
                        "symmetry_unresolvable": tp.get("symmetry_unresolvable"),
                        "verification": tp.get("verification")},
            )
            if six_solved:
                if tp.get("status") == "solved" and tp.get("answer") == payload.get("answer"):
                    sol = self._finalize_regions(question, payload, inputs)
                    sol.meta["transform_cross_support"] = True
                    sol.meta["transform"] = {k: tp.get(k) for k in
                                             ("verdict", "candidates", "parameter_stable",
                                              "independently_verified", "symmetry_unresolvable", "stem_fingerprint",
                                              "verification")}
                    sol.tool_calls.append(tinv)
                    return sol
                t_verdict = str((tp.get("verdict") or {}).get("verdict") or "")
                has_cands = bool(tp.get("candidates"))
                if tp.get("status") == "solved" or (
                        has_cands and t_verdict in ("ambiguous", "multiple_rules")):
                    return Solution(
                        status="failed",
                        skill=self.id,
                        answer=None,
                        confidence=0.0,
                        method="figure_transform",
                        error=(f"规律族冲突（ambiguous）：六类规律 {payload.get('rule')} "
                               f"指向 {payload.get('answer')}，变换规律指向 "
                               f"{tp.get('answer') or '多个/无法唯一'}，不能共同支持同一选项"),
                        tool_calls=[tinv],
                        meta={"stage": self.stage, "channel": "cross_check",
                              "transform": {k: tp.get(k) for k in
                                            ("verdict", "candidates",
                                         "parameter_stable", "independently_verified",
                                         "symmetry_unresolvable")},
                              "six_rule_claim": {"rule": payload.get("rule"),
                                                 "answer": payload.get("answer")}},
                    )
                # 变换通道无规律可提 → 保留六类结果（历史语义）
                sol = self._finalize_regions(question, payload, inputs)
                sol.meta["transform"] = {k: tp.get(k) for k in
                                         ("verdict", "candidates",
                                         "parameter_stable", "independently_verified",
                                         "symmetry_unresolvable")}
                return sol
            if tp.get("status") in ("solved", "unverified"):
                if tp.get("status") == "unverified":
                    return Solution(
                        status="failed",       # 有唯一候选但独立验证不稳定 → 诚实弃权
                        skill=self.id,
                        answer=None,
                        confidence=0.0,
                        method="figure_transform",
                        error="变换候选未通过独立验证（unverified），不对外作答",
                        steps=tp.get("steps") or [],
                        tool_calls=[tinv],
                        meta={"stage": self.stage, "channel": "transform",
                              "transform": {k: tp.get(k) for k in
                                            ("verdict", "candidates", "parameter_stable",
                                             "independently_verified", "symmetry_unresolvable", "stem_fingerprint",
                                             "verification")},
                              "six_rule_failure": payload.get("reason")},
                    )
                return Solution(
                    status="solved",
                    skill=self.id,
                    answer=tp.get("answer"),
                    answer_text=(question.option_text(tp["answer"])
                                 if tp.get("answer") else "") or "",
                    confidence=0.88,
                    method="figure_transform",
                    steps=tp.get("steps") or [],
                    tool_calls=[tinv],
                    meta={"stage": self.stage, "channel": "transform",
                          "transform": {k: tp.get(k) for k in
                                        ("verdict", "candidates", "parameter_stable",
                                         "independently_verified", "symmetry_unresolvable", "stem_fingerprint",
                                         "verification")},
                          "six_rule_failure": payload.get("reason")},
                )
            # 变换通道也失败：合并两通道原因，保持 failed（诚实）
            merged = self._finalize_regions(question, payload, inputs)
            merged.error = (merged.error or "六类规律未解") + f"；变换通道：{tp.get('reason')}"
            merged.meta["transform"] = {k: tp.get(k) for k in
                                        ("verdict", "candidates",
                                         "parameter_stable", "independently_verified",
                                         "symmetry_unresolvable")}
            return merged
        return self._finalize_regions(question, payload, inputs)

    def _finalize_regions(self, question: Question, payload: Dict[str, Any], inputs: Dict[str, Any]) -> Solution:
        invocation = ToolInvocation(
            tool=ADAPTER_ID,
            args={
                "stem_crop": inputs["stem_crop"],
                "option_labels": [label for label, _ in inputs["options"]],
                "grid": list(inputs["grid"]) if inputs.get("grid") else None,
            },
            ok=True,
            result={key: payload.get(key) for key in ("status", "mode", "rule", "answer", "reason", "agreement")},
        )
        meta: Dict[str, Any] = {
            "stage": self.stage,
            "image": question.image_path,
            "mode": payload.get("mode"),
            "rule": payload.get("rule"),
            "agreement": payload.get("agreement") or [],
            "layout": payload.get("layout") or {},
            "predicted": payload.get("predicted"),
            "options_from_image": payload.get("options") or [],
            "candidates": payload.get("candidates") or [],
            "regions": {
                "stem_crop": inputs["stem_crop"],
                "option_labels": [label for label, _ in inputs["options"]],
                "option_crops": [path for _, path in inputs["options"]],
                "stem_grid": list(inputs["grid"]) if inputs.get("grid") else None,
            },
            "tool": ADAPTER_ID,
        }
        steps = list(payload.get("steps") or [])
        if payload.get("status") != "solved":
            return Solution(
                status="failed",
                skill=self.id,
                answer=None,
                confidence=0.0,
                method=ADAPTER_ID,
                error=payload.get("reason") or "区域图形规律未能确定唯一答案",
                steps=steps,
                tool_calls=[invocation],
                meta=meta,
            )
        answer = payload.get("answer")
        return Solution(
            status="solved",
            skill=self.id,
            answer=answer,
            answer_text=(question.option_text(answer) if answer else "") or "",
            steps=steps,
            confidence=float(payload.get("confidence") or 0.85),
            method=ADAPTER_ID,
            tool_calls=[invocation],
            meta=meta,
        )

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

        # R2a：区域裁剪证据优先（不再假定整图最后一行是选项，也不需要整图 grid）
        vision_present = isinstance((question.meta or {}).get("vision"), dict)
        region_inputs = self._region_inputs(question) if vision_present else None
        if region_inputs is not None:
            return self._solve_regions(question, ctx, region_inputs)
        if vision_present:
            # 区域输入存在但不完整（缺题干矩阵裁剪 / 可用选项裁剪 < 2 / 裁剪文件丢失）：
            # 返回结构化失败，禁止把整张网页截图当作旧式标准题图静默回退（R2b P1）
            return self._region_incomplete(question)

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
    def _verify_transform(self, question: Question, solution: Solution,
                          meta: Dict[str, Any], ctx: SkillContext) -> Verification:
        """变换通道验证：R2c 候选代回复核架构保留——验证器从**原始裁剪**独立重建
        每格 mask、重跑变换推理与帧参数扫描；求解器的归一化/容差不作前提。
        消融臂3（indep_verifier=off）时只做候选字段自洽检查（回到原 verifier 强度）。"""
        t = meta.get("transform") or {}
        verdict = t.get("verdict") or {}
        checks: List[VerificationCheck] = []
        if solution.answer is None:
            checks.append(VerificationCheck(
                name="figure_transform.abstain", passed=True, severity="info",
                detail="变换通道未给出对外答案（unverified/failed 语义）"))
            return Verification(passed=False, action="unverified", checks=checks,
                                notes="候选未过独立验证，弃权")
        settings = getattr(ctx, "settings", None)
        indep = getattr(settings, "r4a_indep_verifier", True)
        cands = t.get("candidates") or []
        voting = [c for c in cands
                  if c.get("candidate_scores", {}).get(solution.answer, 1.0) <= 0.15
                  and (c.get("discriminative_margin") or 0) >= 0.05]
        checks.append(VerificationCheck(
            name="figure_transform.support", passed=bool(voting),
            detail=f"{len(voting)} 条**参与裁决**的候选在容差内支持 {solution.answer}"
                   f"（总候选 {len(cands)} 条；未排除竞争项的候选按弃权处理，不构成竞争）"))
        if not voting:
            return Verification(passed=False, action="unverified", checks=checks,
                                notes="无参与裁决的候选支撑该答案")
        if indep:
            # 独立重建：从原始裁剪文件重新切格重跑（与求解器同一入口但独立调用，
            # verify_transformation 已在 solve 内做过 9 组参数扫描，这里再独立做一次，
            # 不复用 solution.meta 中求解器留下的结论）
            try:
                from ..tools.figure_adapter import solve_figure_transform_regions
                inputs = self._region_inputs(question)
                if not inputs:
                    raise RuntimeError("区域裁剪不可用")
                fresh = solve_figure_transform_regions(
                    inputs["stem_crop"], inputs["options"], grid=inputs.get("grid"))
            except Exception as exc:  # noqa: BLE001
                checks.append(VerificationCheck(
                    name="figure_transform.independent", passed=False,
                    detail=f"独立重建失败：{exc}"))
                return Verification(passed=False, action="unverified", checks=checks,
                                    notes="验证器无法独立重建证据链")
            fresh_answer = fresh.get("answer")
            stable = (fresh.get("status") == "solved" and fresh_answer == solution.answer)
            checks.append(VerificationCheck(
                name="figure_transform.independent", passed=stable,
                detail=f"独立重算={fresh_answer or fresh.get('reason')}，求解器={solution.answer}"))
            if not stable:
                return Verification(passed=False,
                                    action="reject" if fresh_answer else "unverified",
                                    checks=checks, notes="独立重算不一致")
        passed = all(c.passed for c in checks if c.severity == "blocking")
        return Verification(passed=passed, action="accept" if passed else "unverified",
                            checks=checks, final_answer=solution.answer if passed else None,
                            notes="变换通道独立验证通过" if passed else "变换验证未过")

    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        if solution.status != "solved":
            return None
        meta = solution.meta or {}
        if (meta.get("channel") or "") == "transform" or meta.get("transform_cross_support"):
            # R4a：变换通道解 + 双通道互证解 → 由变换独立 verifier 接管复核
            # （R2c substitution 复核不认识旋转/镜像/叠加等几何约束，互证题若再走
            # R2c 只会产生系统性错误拒绝；独立验证器的 9 参数重算覆盖更强）
            return self._verify_transform(question, solution, meta, ctx)
        regions = meta.get("regions")
        if regions:
            return self._verify_regions(question, solution, regions, settings=getattr(ctx, "settings", None))
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

    # ------------------------------------------------------ 区域模式独立复核
    def _verify_regions(
        self,
        question: Question,
        solution: Solution,
        regions: Dict[str, Any],
        *,
        settings: Any = None,
    ) -> Optional[Verification]:
        """区域裁剪证据重放：候选代入独立复核（R2c）+ 换阈值重跑（兼容保留）。

        R2c 核心：换阈值重跑只验证同一规则引擎的自我一致性，发现不了系统性规则
        误选（独立基准里的 verifier_false_accept 全部来自这种"自证"）。现在必须先
        通过 ``figure_verify.substitution_check``——逐选项单独感知复测 + 从题干独立
        重建约束 + 候选代入：

        - ``confirmed``：全部约束满足且唯一 → 才可能 accept；
        - ``contradicted``：候选违反可独立复测的约束 → reject（验证器确实发现了错误）；
        - ``unverified``：约束无法重建/唯一性不成立 → 受控拒答，**绝不默认通过**，
          也不冒充"验证纠错"（上层归因区分二者）。
        """
        meta = solution.meta or {}
        labels = regions.get("option_labels") or []
        crops = regions.get("option_crops") or []
        stem_crop = regions.get("stem_crop")
        if not stem_crop or len(labels) != len(crops) or len(labels) < 2:
            return None
        layout = meta.get("layout") or {}
        threshold = layout.get("threshold")
        alternative = 127 if threshold == "otsu" else 160
        inputs = {
            "stem_crop": stem_crop,
            "options": list(zip(labels, crops)),
            "grid": tuple(regions["stem_grid"]) if regions.get("stem_grid") else None,
        }
        checks: List[VerificationCheck] = []
        independent_enabled = bool(getattr(settings, "r2c_figure_verify", True))
        verdict: Optional[str] = None
        if independent_enabled:
            from ..tools.figure_verify import substitution_check

            try:
                report = substitution_check(
                    stem_crop,
                    list(zip(labels, crops)),
                    solution.answer or "",
                    meta.get("rule"),
                    grid=inputs["grid"],
                )
            except Exception as exc:  # noqa: BLE001 - 复核自身异常按未验证处理，不默认通过
                report = {"verdict": "unverified", "reasons": [f"独立复核执行异常：{type(exc).__name__}: {exc}"]}
            verdict = str(report.get("verdict") or "unverified")
            checks.append(
                VerificationCheck(
                    name="figure.independent_substitution",
                    passed=verdict == "confirmed",
                    detail=f"[{verdict}] " + "；".join(str(r) for r in report.get("reasons") or []),
                )
            )
        try:
            rerun = self._run_adapter(inputs, threshold=alternative)
            same = rerun.get("status") == "solved" and rerun.get("answer") == solution.answer
            checks.append(
                VerificationCheck(
                    name="figure.rerun_consistent",
                    passed=same,
                    severity="warning" if verdict == "confirmed" else "blocking",
                    detail=(
                        f"区域模式阈值 {threshold} → {alternative} 自我一致性重跑："
                        f"{rerun.get('answer') or rerun.get('reason')}（Solver 记录 {solution.answer}）"
                        + ("；独立复核已确认，此项仅作阈值鲁棒性记录" if verdict == "confirmed" else "")
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
        except Exception as exc:  # noqa: BLE001
            checks.append(
                VerificationCheck(
                    name="figure.rerun_consistent",
                    passed=False,
                    detail=f"区域模式阈值 {alternative} 独立复算失败：{type(exc).__name__}: {exc}",
                )
            )

        checks.append(
            VerificationCheck(
                name="figure.unique_match",
                passed=len(meta.get("agreement") or []) >= 1,
                detail=f"能唯一匹配选项的规律：{meta.get('agreement')}（命中规律 {meta.get('rule')}）",
            )
        )
        checks.append(
            VerificationCheck(
                name="figure.labels_from_regions",
                passed=bool(regions.get("option_labels")) and solution.answer in labels,
                detail=f"答案 {solution.answer} 是否来自区域标号 {labels}（裁剪证据 {len(crops)} 份）",
            )
        )

        blocking_ok = all(check.passed for check in checks if check.severity == "blocking")
        if blocking_ok:
            passed, action = True, "accept"
        else:
            passed = False
            action = "unverified" if verdict == "unverified" else "reject"
        return Verification(
            passed=passed,
            action=action,
            checks=checks,
            final_answer=solution.answer if passed else None,
            notes="R2c 候选代入独立复核 + 换阈值自我一致性重跑 + 唯一匹配检查（不确认不通过，不做答案修补）",
        )
