"""数字计算 Skill：题干 -> 算术表达式 -> SymPy 精确求值 -> 选项匹配。

R2c：直接表达式抽取失败时，先过**自然语言数量关系理解层**
（``tools.word_problem``：百分比/折扣/速时距/单价/比例/逆百分比），
计算计划只是"关系→表达式"的结构化，数值一律由 Fraction/SymPy 确定性求值，
支持范围外如实失败——不允许猜答案。
"""

from __future__ import annotations

import math
import re as _re
from fractions import Fraction
from typing import List, Optional, Tuple

from ..schemas import Question, Solution, Verification, VerificationCheck
from ..tools.math_tool import fraction_eval, match_option, option_value
from ..tools.word_problem import evaluate_plan, extract_word_problem
from .base import BaseSkill, SkillContext


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-9)


def _close_approx(left: float, right: float) -> bool:
    """R7：行测"约为多少"类选项按四舍五入给值（19.0% vs 精确 19.0476%），
    仅当题干或选项文本含"约"时启用 0.5% 相对容差；多选项同时落入则视为歧义不匹配。"""
    return math.isclose(left, right, rel_tol=5e-3, abs_tol=1e-6)


def _rel_tol(stem: Optional[str]) -> float:
    """题面"约/大约/最接近"决定选项匹配容差（solve 与 verify 同一口径）。"""
    return 5e-3 if _re.search(r"约|大约|大概|最接近", stem or "") else 1e-6


def _format_fraction(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{float(value):g}"


# ------------------------------------------------------------------ R6 任务B：操作数覆盖复核
# 通用不变式（与题型/答案无关）：**解必须是对全部题干数量的计算**。计划若遗漏题干
# 数量（如"先打 8 折、再降价 10%"只用了 8 折；"60 克加 20 克水"只用了浓度数），
# 无论其表达式内部算术多么自洽，都缺少完整题意证据——禁止释放，返回 unverified。
#
# R6.1 加固（评审对抗样本）：**步骤描述文本中的数字不算消费**——只有实际参与
# 计算的表达式操作数（含除法项值）才能绑定题干数量。"描述里提到 10% 但表达式
# 没有 10/0.1/0.9/1.1 任何形态"仍须拦截。长期正确方向是数量→运算节点的
# 结构化依赖图；本检查是其数值近似（必要条件，不是语义充分证明）。

_YEAR_RE = _re.compile(r"20\d{2}\s*年")  # 年份标签是时间标识而非可运算数量（通用豁免）
_DIV_RE = _re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
_RATIO_RE = _re.compile(r"\d+(?:[∶:]\d+)+")


def _stem_numbers(stem: str) -> List[float]:
    text = _YEAR_RE.sub("", stem or "")
    out: List[float] = []
    for tok in _re.findall(r"\d+(?:\.\d+)?", text):
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def _ratio_groups(stem: str) -> List[List[float]]:
    """比例式数字组（2∶3∶4）：结构项，其成员共享绑定命运（分母/总份数参与计算即可）。"""
    groups = []
    for m in _RATIO_RE.finditer(stem or ""):
        groups.append([float(x) for x in _re.split(r"[∶:]", m.group(0))])
    return groups


def _expr_values(plan: Optional[dict], expression: Optional[str]) -> set:
    """**仅表达式操作数**的值集：数字 token + 除法项值（a/b）。
    步骤描述文本（source）一律不参与——描述提到数字 ≠ 数字参与计算。"""
    srcs: List[str] = [expression or ""]
    if plan:
        for step in plan.get("steps", []):
            if len(step) >= 2:
                srcs.append(str(step[1]))
    vals: set = set()
    for src in srcs:
        for tok in _re.findall(r"\d+(?:\.\d+)?", src):
            try:
                vals.add(float(tok))
            except ValueError:
                continue
        for m in _DIV_RE.finditer(src):
            try:
                a, b = float(m.group(1)), float(m.group(2))
            except ValueError:
                continue
            if b:
                vals.add(a / b)
    return vals


def _bound(x: float, vals: set, has_zhe: bool, group: Optional[List[float]]) -> bool:
    """题干数量 x 是否被表达式绑定：允许 百分/折扣换算值与派生差值（360-300=60）。"""
    cands = {x, x / 100.0, 1.0 - x / 100.0, 1.0 + x / 100.0}
    if has_zhe and 0 < x < 10:
        cands |= {x / 10.0, 1.0 - x / 10.0, 1.0 + x / 10.0}
    if any(_close(v, c) for v in vals for c in cands):
        return True
    for a in vals:
        for b in vals:
            if any(_close(a - b, c) for c in (x, x / 100.0)):
                return True
    if group:  # 比例组结构豁免：任一成员或总份数进入表达式即整组绑定
        tot = sum(group)
        if any(any(_close(v, g) for g in group) or _close(v, tot) for v in vals):
            return True
    return False


def _plan_numbers(plan: Optional[dict], expression: Optional[str]) -> set:
    return _expr_values(plan, expression)


def _degenerate_percent(expression: str, stem: str) -> bool:
    """R7 §6 归因结论：直接抽取通道把"浓度变为多少/需提价多少"抽成了
    **原样换算**（如 12*(1/100)），完全没有建模题干的变化过程——伪计划。
    判定：表达式除常数换算外只用到 ≤1 个题面数字，且题干明含变化词。"""
    if not expression:
        return False
    if not _re.search(r"加入|加水|加盐|加糖|混合|注入|倒入|变为|变成|恢复|升高|降低|增加|减少",
                      stem or ""):
        return False
    nums = [float(n) for n in _re.findall(r"\d+(?:\.\d+)?", expression)]
    core = [n for n in nums if n not in (1.0, 100.0, 1000.0)]
    if len(core) >= 2:
        return False
    return True


class NumericCalculationSkill(BaseSkill):
    id = "numeric_calculation"
    name_zh = "数字计算"
    description = "算术、百分比、比例类计算题：解析题干算式，用 SymPy 精确求值并匹配选项"
    stage = 1
    requires = ("sympy",)

    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        options = self.option_pairs(question)
        invocation = ctx.toolbox.run("python_math", text=question.stem, options=options)
        if not invocation.ok:
            # 直接表达式抽取失败 → 自然语言数量关系理解层（R2c），计算仍走 Fraction
            word_solution = self._solve_word_problem(question, ctx, options)
            if word_solution is not None:
                word_solution.tool_calls = [invocation] + list(word_solution.tool_calls or [])
                return word_solution
            planner_solution = self._solve_planner(question, ctx, options)
            if planner_solution is not None:
                return planner_solution
            return Solution(
                status="failed",
                skill=self.id,
                method="sympy_exact",
                error=invocation.error,
                tool_calls=[invocation],
            )
        payload = invocation.result
        value = float(payload["value"])
        exact = str(payload["exact"])
        expression = payload["expression"]
        steps = [
            f"从题干抽取算式：{expression}",
            f"SymPy 精确求值：{exact}",
            f"数值结果：{value:g}",
        ]
        if question.options:
            raw_label = payload.get("matched_label")
            degenerate = raw_label is not None and _degenerate_percent(expression, question.stem)
            label = None if degenerate else raw_label
            # R9-D：raw 匹配成功也要先让位确定性语义模板——"碰巧等值"的
            # 表面抽取（如把"增长50%"读成 50*(1/100)）即使命中选项也不构成
            # 计算证据；模板计划绑定了全部题面数量，是更强的推理凭证。
            if label is not None:
                word_solution = self._solve_word_problem(question, ctx, options)
                if word_solution is not None:
                    word_solution.tool_calls = [invocation] + list(word_solution.tool_calls or [])
                    return word_solution
            if label is None:
                # 直接抽取的算式可能本身抽错（如把"比…便宜20%"读成减 20、
                # 或把"浓度变为多少"原样换算成 12%）：先走数量关系建模层（R2c/R7）
                word_solution = self._solve_word_problem(question, ctx, options)
                if word_solution is not None:
                    word_solution.tool_calls = [invocation] + list(word_solution.tool_calls or [])
                    return word_solution
                planner_solution = self._solve_planner(question, ctx, options)
                if planner_solution is not None:
                    return planner_solution
                if not degenerate:
                    return Solution(
                        status="failed",
                        skill=self.id,
                        method="sympy_exact",
                        error="计算结果与所有选项均不匹配",
                        tool_calls=[invocation],
                        meta={"expression": expression, "value": value, "exact": exact},
                    )
                # 退化表达式但建模层也无产出：保留原匹配（由 verify 独立把关），
                # 记录诊断标记供归因
                label = raw_label
                steps.append("注：直接抽取为原样换算，建模层无更优计划（保留待验证）")
            steps.append(f"与选项比对，匹配到 {label}. {question.option_text(label) or ''}".strip())
            return Solution(
                status="solved",
                skill=self.id,
                answer=label,
                answer_text=question.option_text(label) or "",
                steps=steps,
                confidence=0.95,
                method="sympy_exact",
                tool_calls=[invocation],
                meta={"expression": expression, "value": value, "exact": exact, "matched_label": label},
            )
        answer_text = exact if payload.get("is_integer") else f"{value:g}"
        steps.append("题目无选项，直接输出计算结果")
        return Solution(
            status="solved",
            skill=self.id,
            answer=answer_text,
            answer_text=answer_text,
            steps=steps,
            confidence=0.85,
            method="sympy_exact",
            tool_calls=[invocation],
            meta={"expression": expression, "value": value, "exact": exact},
        )

    # ------------------------------------------------ R7 路径二：知识增强规划
    @staticmethod
    def _stale_target_probe(plan, final, stem: str) -> str:
        """R7 §6 结构诊断：题干明说变化（加入/变为/混合…）而计划结果恰等于某个
        给定原始量——目标量没有随情境更新（典型如稀释后仍报原浓度）。返回反馈文本
        或空串。不含任何答案信息，只指出结构缺陷。"""
        if not _re.search(r"加入|加水|加盐|增加|减少|变为|混合|注入|倒入|恢复|升高|降低", stem or ""):
            return ""
        for g in plan.givens or []:
            try:
                gv = Fraction(str(g.value))
            except (ValueError, ZeroDivisionError):
                continue
            if gv == final:
                return (f"你的 target 计算结果 {g.value} 恰等于题干引用的原始量"
                        f"（{g.stem_span[:40]}），但题目描述了变化过程，"
                        "所问量应是变化后的新值：变化后的总量/新基准必须进入 target 表达式"
                        "（例如稀释后浓度=溶质/新溶液总量，恢复原价的提价率以现价为基准）。"
                        "请重新规划。")
        return ""

    def _solve_planner(self, question: Question, ctx: SkillContext, options) -> Optional[Solution]:
        """规则通道无法建模时，用方法卡 + LLM 结构化规划 + Fraction 执行。
        产出候选必须带完整依赖图（structured_plan），由 verify() 独立复核后才可释放。"""
        if ctx.llm is None or not question.options:
            return None
        try:
            from ..planner import ReasoningPlanner, execute_plan
            from ..knowledge.methods import MethodKB
        except ImportError:  # pragma: no cover
            return None
        planner = ReasoningPlanner(getattr(self, "method_kb", None) or MethodKB(), ctx.llm)
        opts = [{"label": o.get("label") if isinstance(o, dict) else getattr(o, "label", ""),
                 "text": o.get("text") if isinstance(o, dict) else getattr(o, "text", "")}
                for o in (question.options or [])]
        plan, _cards, why = planner.plan_llm(question.stem, opts, self.id)
        if plan is None:
            return Solution(
                status="failed", skill=self.id, method="reasoning_planner",
                error=f"结构化规划未成立: {why}",
                meta={"planner_failure": str(why)[:200]})
        ex = execute_plan(plan)
        if ex.final is not None:
            stale = self._stale_target_probe(plan, ex.final, question.stem)
            if stale:
                plan2, _c2, why2 = planner.plan_llm(question.stem, opts, self.id,
                                                    feedback=stale)
                if plan2 is not None:
                    ex2 = execute_plan(plan2)
                    if ex2.final is not None and ex2.final != ex.final:
                        plan, ex = plan2, ex2
        if ex.final is None:
            return Solution(
                status="failed", skill=self.id, method="reasoning_planner",
                error=f"计划执行失败: {ex.error}",
                meta={"structured_plan": plan.to_dict()})
        value = float(ex.final)
        exact = (f"{ex.final.numerator}/{ex.final.denominator}"
                 if ex.final.denominator != 1 else str(ex.final.numerator))
        label = None
        approx_asked = bool(_re.search(r"约|大约|大概|最接近", question.stem or ""))
        best: Optional[Tuple[float, str]] = None
        for opt in (question.options or []):
            otext = opt.get("text") if isinstance(opt, dict) else getattr(opt, "text", "")
            ov = option_value(otext or "")
            if ov is None:
                continue
            olabel = opt.get("label") if isinstance(opt, dict) else getattr(opt, "label", "")
            if _close(ov, value):
                label = olabel
                break
            if approx_asked and _close_approx(ov, value):
                gap = abs(ov - value)
                if best is None or gap < best[0]:
                    best = (gap, olabel)
        if label is None and best is not None:
            label = best[1]
        meta = {"structured_plan": plan.to_dict(), "solver_source": "llm_planner",
                "expression": ex.expression, "value": value, "exact": exact,
                "planner_method": plan.method_id}
        if label is None:
            meta.update({"planner_failure": "plan_result_not_in_options"})
            return Solution(status="failed", skill=self.id, method="reasoning_planner",
                            error=f"计划结果 {exact} 与所有选项均不匹配", meta=meta)
        steps = [f"方法卡 {plan.method_id}（{plan.family}）：{plan.rationale}"[:160]]
        steps += [f"{s.var} = {s.expr}（依据：{s.source[:40]}）" for s in plan.steps]
        steps.append(f"Fraction 精确求值 = {exact}（数值 {value:g}），匹配选项 {label}")
        return Solution(
            status="solved", skill=self.id, answer=label,
            answer_text=question.option_text(label) or "", steps=steps,
            confidence=0.8, method="reasoning_planner", meta=meta)

    # ------------------------------------------------ 应用题理解层（R2c）
    def _solve_word_problem(self, question: Question, ctx: SkillContext, options) -> Optional[Solution]:
        if not getattr(ctx.settings, "r2c_word_problem", True):
            return None
        plan = extract_word_problem(question.stem)
        if plan is None:
            return None  # 支持范围外：由上层返回原有诚实失败
        env, error = evaluate_plan(plan)
        if error or env is None:
            return Solution(
                status="failed",
                skill=self.id,
                method=f"word_problem:{plan['kind']}",
                error=f"数量关系抽取成功但确定性求值失败：{error}",
                meta={"word_problem": plan},
            )
        final = env[plan["final_var"]]
        # R7-C1：规则计划若未穷尽题干数量（如两段折扣只建模第一段），且规划层可用，
        # 则让位给知识增强路径；无 LLM 时维持既有行为（候选交由 verify 拦截）。
        if ctx.llm is not None:
            _vals = _expr_values(plan, None)
            _zhe = "折" in (question.stem or "")
            _groups = _ratio_groups(question.stem)
            _miss = [n for n in _stem_numbers(question.stem)
                     if not _bound(n, _vals, _zhe,
                                   next((g for g in _groups if any(_close(n, m) for m in g)), None))]
            if _miss:
                return None
        # 把中间变量代入最后一步表达式，得到可独立复核的单一算式
        expression = plan["steps"][-1][1]
        for name, value in env.items():
            if name != plan["final_var"]:
                expression = _re.sub(rf"\b{name}\b", f"({value.numerator}/{value.denominator})", expression)
        steps = [f"关系抽取（{plan['kind']}）：{desc}" for _var, _expr, desc in plan["steps"]]
        steps.append(f"Fraction 精确求值 = {_format_fraction(final)}（数值 {float(final):g}）")
        if question.options:
            label = match_option(float(final), options, rel_tol=_rel_tol(question.stem))
            if label is None:
                return Solution(
                    status="failed",
                    skill=self.id,
                    method=f"word_problem:{plan['kind']}",
                    error=f"应用题计算结果 {_format_fraction(final)} 与所有选项均不匹配",
                    steps=steps,
                    meta={"word_problem": plan, "expression": expression, "value": float(final),
                          "exact": _format_fraction(final)},
                )
            steps.append(f"与选项比对，匹配到 {label}")
            return Solution(
                status="solved",
                skill=self.id,
                answer=label,
                answer_text=question.option_text(label) or "",
                steps=steps,
                confidence=0.9,
                method=f"word_problem:{plan['kind']}",
                meta={"word_problem": plan, "expression": expression, "value": float(final),
                      "exact": _format_fraction(final), "matched_label": label},
            )
        answer_text = _format_fraction(final)
        return Solution(
            status="solved",
            skill=self.id,
            answer=answer_text,
            answer_text=answer_text,
            steps=steps,
            confidence=0.85,
            method=f"word_problem:{plan['kind']}",
            meta={"word_problem": plan, "expression": expression, "value": float(final), "exact": answer_text},
        )

    # ------------------------------------------------------------ 独立复核
    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        expression = solution.meta.get("expression")
        if not expression:
            return None
        checks: List[VerificationCheck] = []
        repaired: Optional[str] = None
        plan = solution.meta.get("word_problem")
        # R6-B/R6.1 操作数覆盖（blocking）：题干数量必须全部被**表达式操作数**绑定
        stem_nums = _stem_numbers(question.stem)
        consumed = _expr_values(plan, solution.meta.get("expression"))
        # R7-B1：planner 路径独立复核——Verifier 重放依赖图（来源绑定+拓扑+重算），
        # 不信任 Solver 自报结果；任何缺陷即 unverified，禁止释放
        if solution.meta.get("solver_source") == "llm_planner":
            from ..planner import StructuredPlan as _SP
            from ..planner import execute_plan as _ep
            from ..planner import validate_plan as _vp
            ok_graph, p_detail = False, "structured_plan 缺失"
            try:
                p2 = _SP.from_dict(solution.meta.get("structured_plan") or {})
                problems = _vp(p2, question.stem)
                ex2 = _ep(p2)
                replay_ok = (not problems) and ex2.final is not None and _close(
                    float(ex2.final), float(solution.meta.get("value") or float("nan")))
                ok_graph = replay_ok
                p_detail = ("依赖图独立重放通过：来源绑定全部为题干原文、拓扑完整、重算值与候选一致"
                            if replay_ok else
                            "重放失败: " + "; ".join((problems or [])[:3]) +
                            ("" if not problems else f"; 执行={ex2.error}; 与候选一致="
                             + str(_close(float(ex2.final),
                                          float(solution.meta.get('value') or -1))
                                   if ex2.final is not None else "无值")))
            except Exception as exc:  # noqa: BLE001
                p_detail = f"重放异常: {exc}"
            checks.append(VerificationCheck(
                name="planner.dependency_graph", passed=ok_graph,
                severity="blocking", detail=p_detail[:300]))
            if not ok_graph:
                return Verification(
                    passed=False, action="unverified", checks=checks, final_answer=None,
                    notes="planner 依赖图独立复核未通过：禁止释放")
        has_zhe = "折" in (question.stem or "")
        groups = _ratio_groups(question.stem)
        uncovered = [n for n in stem_nums
                     if not _bound(n, consumed, has_zhe,
                                    next((g for g in groups if any(_close(n, m) for m in g)), None))]
        if uncovered:
            checks.append(
                VerificationCheck(
                    name="numeric.operand_coverage",
                    passed=False,
                    severity="blocking",
                    detail=(f"题干数量 {sorted(set(uncovered))} 未出现在计算计划的操作数/步骤依据中"
                            f"（已消费 {sorted(consumed)}）——缺少完整题意证据，不释放"),
                )
            )
            return Verification(
                passed=False,
                action="unverified",
                checks=checks,
                final_answer=None,
                notes="操作数覆盖核验未通过：计划遗漏题干数量，禁止无完整计算证据的释放",
            )
        checks.append(
            VerificationCheck(
                name="numeric.operand_coverage",
                passed=True,
                severity="blocking",
                detail=f"题干 {len(stem_nums)} 个数量均被计划操作数绑定（{sorted(consumed)}）",
            )
        )
        # R6-B 退化拷贝（blocking）：若计算结果只是某个题干数量的单位换算搬运
        # （值 ∈ {s, s/100, 100·s}），且计划没有多步运算，则"计算"并未回答问题
        # ——没有超出题干已知量的计算证据，禁止释放（典型：问"需提价多少"却把
        # 题干的 20% 原样搬回）。同样是语义无关的数值不变式，不是题型模板。
        value = solution.meta.get("value")
        single_step = not plan or len(plan.get("steps", [])) <= 1
        # R7：planner 路径的步骤数看 structured_plan；且表达式含加/减复合运算时
        # 即为真实计算证据（如 240/(60+40) 的 2.4 并非题干 240 的搬值），
        # 不得被等值类误扣——保守拒答不能替代能力（工单 R7 总目标）。
        sp_meta = solution.meta.get("structured_plan")
        if sp_meta and sp_meta.get("steps"):
            single_step = len(sp_meta["steps"]) <= 1
        expr_txt = str(solution.meta.get("expression") or "")
        real_computation = ("+" in expr_txt or "-" in expr_txt)
        # R9-D：×100/÷100 等值类只对题面带 % 号的数值生效（百分数搬运语义）；
        # 无 % 的裸数（如比例尺 1∶2000000 的 1）不构成"百分搬运"证据，
        # 否则 5×2000000/100000=100 会被 1×100 误扣（收紧语义范围，非放松核验）。
        _pct_nums = {float(m) for m in _re.findall(r"(\d+(?:\.\d+)?)\s*[%％]", question.stem or "")}
        if value is not None and single_step and not real_computation:
            degenerate = next(
                (s for s in stem_nums
                 if _close(float(value), s)
                 or (s in _pct_nums and (_close(float(value) * 100, s) or _close(float(value), s * 100)))),
                None,
            )
            if degenerate is not None:
                checks.append(
                    VerificationCheck(
                        name="numeric.degenerate_copy",
                        passed=False,
                        severity="blocking",
                        detail=(f"结果 {float(value):g} 只是题干数量 {degenerate:g} 的同值搬运"
                                "（无多步计算证据）——不释放"),
                    )
                )
                return Verification(
                    passed=False,
                    action="unverified",
                    checks=checks,
                    final_answer=None,
                    notes="退化拷贝核验未通过：答案未超出题干已知量，缺少真实计算证据",
                )
            checks.append(
                VerificationCheck(
                    name="numeric.degenerate_copy",
                    passed=True,
                    detail="结果超出题干数量等值类（存在真实计算证据）",
                )
            )
        if plan:
            # R2c：应用题计划链独立重放（关系步骤 → Fraction 重算），
            # 与下面的单一表达式 Fraction 求值是两条独立计算路径
            env, chain_error = evaluate_plan(plan)
            chain_ok = (
                chain_error is None
                and env is not None
                and solution.meta.get("value") is not None
                and _close(float(env[plan["final_var"]]), float(solution.meta["value"]))
            )
            checks.append(
                VerificationCheck(
                    name="word_problem.chain_recompute",
                    passed=chain_ok,
                    severity="blocking",
                    detail=(
                        f"计划链重放 {'=' if chain_ok else '≠'} Solver 结果（{plan['kind']}，"
                        f"{len(plan['steps'])} 步）" + ("" if chain_ok else f"：{chain_error or '数值不一致'}")
                    ),
                )
            )
            if not chain_ok:
                return Verification(
                    passed=False,
                    action="reject",
                    checks=checks,
                    final_answer=None,
                    notes="应用题计划链复核未通过，拒绝输出（不猜测）",
                )
        try:
            recomputed = float(fraction_eval(str(expression)))
            checks.append(
                VerificationCheck(
                    name="math.independent_recompute",
                    passed=True,
                    detail=f"Fraction 独立求值 = {recomputed:g}（不依赖 SymPy 路径）",
                )
            )
        except Exception as exc:  # noqa: BLE001 - 交叉验证失败即视为未通过
            recomputed = float("nan")
            checks.append(
                VerificationCheck(
                    name="math.independent_recompute",
                    passed=False,
                    detail=f"独立求值失败: {exc}",
                )
            )

        claimed = solution.meta.get("value")
        if claimed is not None and not math.isnan(recomputed):
            matched = _close(float(claimed), recomputed)
            checks.append(
                VerificationCheck(
                    name="math.claim_consistent",
                    passed=matched,
                    detail=f"Solver 结果 {float(claimed):g} vs 复核 {recomputed:g}",
                )
            )
            if not matched:
                pairs = [(option.label, option.text) for option in question.options]
                repaired = match_option(recomputed, pairs, rel_tol=_rel_tol(question.stem)) or f"{recomputed:g}"
        else:
            checks.append(
                VerificationCheck(name="math.claim_consistent", passed=False, detail="缺少 Solver 数值结果")
            )

        if question.options and solution.answer:
            labels = question.option_labels()
            in_options = solution.answer in labels
            checks.append(
                VerificationCheck(
                    name="numeric.answer_in_options",
                    passed=in_options,
                    detail=f"答案 {solution.answer} {'在' if in_options else '不在'}选项 {labels} 中",
                )
            )
            if in_options and not math.isnan(recomputed):
                value = option_value(question.option_text(solution.answer) or "")
                if value is not None:
                    same = _close(value, recomputed) or (
                        _rel_tol(question.stem) > 1e-5 and _close_approx(value, recomputed))
                    checks.append(
                        VerificationCheck(
                            name="answer.matches_recompute",
                            passed=same,
                            detail=f"选项 {solution.answer} 数值 {value:g} vs 复核 {recomputed:g}",
                        )
                    )
                    if not same:
                        repaired = match_option(recomputed, [(o.label, o.text) for o in question.options],
                                rel_tol=_rel_tol(question.stem)) or repaired

        passed = all(check.passed for check in checks if check.severity == "blocking")
        if passed:
            action = "accept"
        elif repaired and repaired != solution.answer:
            action = "repair"
        else:
            action = "reject"
        return Verification(
            passed=passed,
            action=action,
            checks=checks,
            final_answer=solution.answer if passed else repaired,
            repaired_answer=repaired if action == "repair" else None,
            notes="SymPy 求值 + Fraction 独立求值 + 选项数值一致性校验",
        )
