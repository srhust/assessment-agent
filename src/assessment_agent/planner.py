"""R7 阶段B：ReasoningPlanner——知识增强的结构化解题规划层。

目标架构（评审工单 §二）：
  题目 → 题型/考点识别 → 检索方法知识 → 构建可执行计划 → 工具求解 → 独立验证 → 输出

两条路径（§四，共用证据与验证框架，`solver_source` 标记区分）：
  路径一 `rule`      ：现有确定性 Solver（如 word_problem）可靠处理时直接用；
  路径二 `llm_planner`：规则失败时 → MethodKB 检索 → LLM 产出**结构化计划**
                       → 转 Fraction/SymPy 可执行形式 → 精确计算候选。

结构化计划纪律（A3/B1）：
- 每个已知量必须绑定原题文字片段（stem_span，校验为题干子串且含该数值）；
- 每个步骤必须声明依赖（givens 标签或前序变量），形成 原始数量→语义关系→
  中间量→目标量 的运算依赖图；
- 计划必须显式声明不确定/缺失信息（unknowns），不允许"我认为答案是 A"式输出；
- LLM 输入只含题目与允许检索的方法卡，**绝不含金标/失败归因/标准答案**。

多路径推理（B2）：最多 2 个候选计划；结果一致仍检查依赖图是否独立
（共享同一错误来源片段则不加成）；冲突时比较题干证据覆盖度而非投票。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from .schemas import Question
from .knowledge.methods import MethodCard, MethodKB

# ------------------------------------------------------------------ 数据结构


@dataclass
class Given:
    label: str                 # 变量名，如 "price0"
    value: str                 # 数值字符串（分数或小数）
    stem_span: str             # 原题中的文字片段（来源绑定，必须为题干子串）
    unit: str = ""


@dataclass
class PlanStep:
    var: str
    expr: str                  # 可执行表达式：数字/运算符/其它 var 或 given label
    source: str                # 该步骤语义关系的原题文字片段（可为多段拼接的子串）
    deps: List[str] = field(default_factory=list)


@dataclass
class StructuredPlan:
    family: str                                   # 子题型假设，如 "successive-discount"
    method_id: str                                # 引用的方法卡
    givens: List[Given]
    steps: List[PlanStep]
    target: str                                   # 最终变量
    rationale: str = ""                           # 选该方法的原因
    unknowns: List[str] = field(default_factory=list)   # 缺失/不确定信息
    solver_source: str = "llm_planner"            # rule | llm_planner
    question_hypotheses: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family, "method_id": self.method_id,
            "givens": [g.__dict__ for g in self.givens],
            "steps": [s.__dict__ for s in self.steps],
            "target": self.target, "rationale": self.rationale,
            "unknowns": self.unknowns, "solver_source": self.solver_source,
            "question_hypotheses": self.question_hypotheses,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredPlan":
        return cls(
            family=str(data.get("family") or ""), method_id=str(data.get("method_id") or ""),
            givens=[Given(label=str(g["label"]), value=str(g["value"]),
                          stem_span=str(g.get("stem_span") or ""),
                          unit=str(g.get("unit") or ""))
                    for g in data.get("givens", [])],
            steps=[PlanStep(var=str(s["var"]), expr=str(s["expr"]),
                            source=str(s.get("source") or ""),
                            deps=[str(d) for d in (s.get("deps") or [])])
                   for s in data.get("steps", [])],
            target=str(data.get("target") or ""),
            rationale=str(data.get("rationale") or ""),
            unknowns=[str(u) for u in (data.get("unknowns") or [])],
            solver_source=str(data.get("solver_source") or "llm_planner"),
            question_hypotheses=[str(h) for h in (data.get("question_hypotheses") or [])])


@dataclass
class PlanExecution:
    values: Dict[str, Fraction]
    final: Optional[Fraction]
    expression: Optional[str]      # target 的展开表达式（仅数字与运算符，供复核）
    error: Optional[str] = None


# ------------------------------------------------------------------ 执行器（Fraction 精确）

_NUM = r"\d+(?:\.\d+)?(?:/\d+)?"
_TOKEN_RE = re.compile(rf"[A-Za-z_][A-Za-z0-9_]*|{_NUM}|[+\-*/() ]")


def _tokenize_expr(expr: str) -> List[str]:
    pos, toks = 0, []
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            raise ValueError(f"表达式含不支持字符: {expr[pos]!r} @ {pos}")
        toks.append(m.group(0))
        pos = m.end()
    return toks


class _ExprParser:
    """递归下降：+ - * / 括号，标识符解析为已绑定值。"""

    def __init__(self, toks: List[str], env: Dict[str, Fraction]):
        self.toks, self.i, self.env = toks, 0, env

    def peek(self) -> Optional[str]:
        # 跳过空白 token（LLM 表达式普遍含空格：'P0 * (1 - r1)'）
        while self.i < len(self.toks) and self.toks[self.i].strip() == "":
            self.i += 1
        return self.toks[self.i].strip() if self.i < len(self.toks) else None

    def eat(self, want: str) -> None:
        got = self.peek()
        if got != want:
            raise ValueError(f"期望 {want!r} 得到 {got!r}")
        self.i += 1

    def parse(self) -> Fraction:
        v = self.additive()
        if self.i != len(self.toks):
            raise ValueError("表达式尾部有多余内容")
        return v

    def additive(self) -> Fraction:
        v = self.multiplicative()
        while self.peek() in ("+", "-"):
            op = self.peek()
            self.i += 1
            rhs = self.multiplicative()
            v = v + rhs if op == "+" else v - rhs
        return v

    def multiplicative(self) -> Fraction:
        v = self.atom()
        while self.peek() in ("*", "/"):
            op = self.peek()
            self.i += 1
            rhs = self.atom()
            v = v * rhs if op == "*" else v / rhs
        return v

    def atom(self) -> Fraction:
        t = self.peek()
        if t == "(":
            self.i += 1
            v = self.additive()
            self.eat(")")
            return v
        self.i += 1
        if t is None:
            raise ValueError("表达式意外结束")
        if re.fullmatch(rf"{_NUM}", t):
            if "/" in t:
                a, b = t.split("/")
                return Fraction(a) / Fraction(b)
            return Fraction(t)
        if t in self.env:
            return self.env[t]
        raise ValueError(f"未绑定变量: {t}")


def execute_plan(plan: StructuredPlan) -> PlanExecution:
    """按依赖顺序精确求值；每个变量只允许定义一次（无环依赖自然成立）。"""
    env: Dict[str, Fraction] = {}
    for g in plan.givens:
        try:
            env[g.label] = Fraction(g.value)
        except (ValueError, ZeroDivisionError):
            return PlanExecution(env, None, None, error=f"given {g.label} 值非法: {g.value}")
    expr_of: Dict[str, str] = {}
    for step in plan.steps:
        if step.var in env:
            return PlanExecution(env, None, None, error=f"变量重复定义: {step.var}")
        try:
            env[step.var] = _ExprParser(_tokenize_expr(step.expr), env).parse()
        except Exception as exc:  # noqa: BLE001
            return PlanExecution(env, None, None, error=f"步骤 {step.var} 执行失败: {exc}")
        expr_of[step.var] = step.expr
    if plan.target not in env:
        return PlanExecution(env, None, None, error=f"目标量未产生: {plan.target}")
    # 展开为纯数值表达式（供独立复核与 operand_coverage 检查）
    def expand(var: str) -> str:
        e = expr_of.get(var)
        if e is None:
            return str(env[var]) if var in env else var
        out, toks = "", _tokenize_expr(e)
        for t in toks:
            ts = t.strip()
            out += expand(ts) if (ts and not re.fullmatch(rf"{_NUM}|[+\-*/() ]", ts)) else t
        return "(" + out + ")"
    return PlanExecution(env, env[plan.target], expand(plan.target))


# ------------------------------------------------------------------ 计划校验（B1 来源绑定）


def validate_plan(plan: StructuredPlan, stem: str) -> List[str]:
    """返回缺陷列表（空=计划满足来源绑定与依赖完整性）。"""
    problems: List[str] = []
    norm_stem = re.sub(r"\s+", "", stem or "")
    labels = {g.label for g in plan.givens}
    if len(labels) != len(plan.givens):
        problems.append("givens 标签重复")
    for g in plan.givens:
        span = re.sub(r"\s+", "", g.stem_span or "")
        if not span or span not in norm_stem:
            problems.append(f"given {g.label} 的 stem_span 不是题干原文")
        else:
            # 绑定值必须出现在其来源片段（数值或其百分/折扣/增减换算形态）
            nums = {float(x) for x in re.findall(r"\d+(?:\.\d+)?", span)}
            try:
                v: Optional[float] = float(Fraction(g.value))
            except (ValueError, ZeroDivisionError):
                problems.append(f"given {g.label} 值非法: {g.value}")
                v = None
            if v is not None and nums and not any(
                    _close(n, v) or _close(n / 100.0, v) or _close(n / 10.0, v)
                    or _close(1.0 - n / 100.0, v) or _close(1.0 + n / 100.0, v)
                    or _close(1.0 - n / 10.0, v) or _close(1.0 + n / 10.0, v)
                    for n in nums):
                problems.append(f"given {g.label} 的值 {g.value} 与其来源片段数值不符: {g.stem_span[:30]}")
    seen = set(labels)
    vars_defined: set = set()
    for step in plan.steps:
        if step.var in vars_defined:
            problems.append(f"步骤变量重复: {step.var}")
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", step.expr):
            if tok not in seen:
                problems.append(f"步骤 {step.var} 引用未绑定标识符 {tok}")
        for d in step.deps:
            if d not in seen:
                problems.append(f"步骤 {step.var} 依赖 {d} 不存在")
        span = re.sub(r"\s+", "", step.source or "")
        if span and span not in norm_stem:
            problems.append(f"步骤 {step.var} 的 source 不是题干原文")
        vars_defined.add(step.var)
        seen.add(step.var)
    if plan.target not in seen:
        problems.append(f"目标量 {plan.target} 无定义")
    if not plan.givens:
        problems.append("计划缺少任何原题已知量绑定")
    return problems


def _close(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


# ------------------------------------------------------------------ LLM 规划通道

PLAN_SYSTEM = """你是行测解题规划器（只做规划，不给选项答案）。
输出 JSON：
{"family":"子题型假设","method_id":"所用方法卡id","rationale":"为什么用这个方法",
 "givens":[{"label":"变量名","value":"数值(分数或小数)","stem_span":"原题文字片段","unit":""}],
 "steps":[{"var":"中间或最终变量","expr":"仅含数字、+ - * / 括号、givens与更早step的var","source":"该关系的原题文字片段","deps":["引用的标识符"]}],
 "target":"最终变量","unknowns":["无法从题面确定的信息，可为空"],
 "question_hypotheses":["题型假设"]}
硬规则：
1. 每个 givens.value 必须来自其 stem_span 原文（原文数字或其百分/折扣换算）；
2. 题目叙述出现的每个**参与计算的量**都必须进入 givens 并被步骤使用；
   不参与计算的干扰量要写进 unknowns/或说明；
3. 多次变化（连续折扣/复合增长/分步操作）必须逐步建模，每步基准量可能不同，
   百分比变化是乘法复合不是相加；
4. 不允许输出选项字母或最终选项，只输出结构化计划；
5. 只依据题干与选项文本与给出的方法卡。"""


class ReasoningPlanner:
    def __init__(self, kb: MethodKB, llm: Any = None):
        self.kb = kb
        self.llm = llm

    # ---------- 路径二：知识增强 LLM 规划 ----------
    def plan_llm(self, stem: str, options: List[Dict[str, Any]],
                 skill: str, cards: Optional[List[MethodCard]] = None
                 ) -> Tuple[Optional[StructuredPlan], List[MethodCard], Optional[str]]:
        """返回 (plan, 使用的cards, 失败原因)。"""
        if self.llm is None:
            return None, [], "llm_unavailable"
        cards = cards or self.kb.search(stem, skill=skill, top_k=2)
        if not cards:
            return None, [], "no_method_card"
        card_blocks = "\n\n".join(c.prompt_block() for c in cards)
        user = (f"方法卡:\n{card_blocks}\n\n题干:\n{stem}\n\n选项:\n"
                + json.dumps(options, ensure_ascii=False)[:1200])
        try:
            payload = self.llm.complete_json(PLAN_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001
            # R7：输出协议失败（空返回/坏 JSON）可重试——格式提醒后命中率提升。
            try:
                payload = self.llm.complete_json(
                    PLAN_SYSTEM,
                    user + "\n\n输出格式硬要求：只输出一个合法 JSON 对象；"
                    "method_id 必须是上方方法卡的 id 原文；字符串值内不得出现未转义引号。")
            except Exception:  # noqa: BLE001
                return None, cards, f"llm_error: {exc}"
        plan = self._bind_payload(payload, cards)
        if plan is None:
            return None, cards, "plan_unbound"  # 结构不可解析/方法卡未绑定：确定性拒绝
        problems = validate_plan(plan, stem)
        if not problems:
            return plan, cards, None
        # R7：一次修正重试——把确定性校验器的缺陷清单反馈给模型。
        # source/stem_span 必须是**题干逐字子串**是模型最常违反的硬规则，
        # 明确报错内容后命中率显著提升（预算+1 次调用，仍受 max_plans 约束）。
        retry_user = (user + "\n\n上一次输出未通过校验，缺陷如下：\n- "
                      + "\n- ".join(problems[:6])
                      + "\n请修正后重新输出完整 JSON（source/stem_span 必须逐字摘录题干）。")
        try:
            payload2 = self.llm.complete_json(PLAN_SYSTEM, retry_user)
        except Exception as exc:  # noqa: BLE001
            return None, cards, f"llm_error_retry: {exc}"
        plan2 = self._bind_payload(payload2, cards)
        if plan2 is not None and not validate_plan(plan2, stem):
            return plan2, cards, None
        return None, cards, "plan_invalid: " + "; ".join(problems[:4])

    @staticmethod
    def _bind_payload(payload: Any, cards: List[MethodCard]) -> Optional[StructuredPlan]:
        if not isinstance(payload, dict):
            return None
        try:
            givens = [Given(label=str(g["label"]), value=str(g["value"]),
                            stem_span=str(g.get("stem_span") or ""),
                            unit=str(g.get("unit") or ""))
                      for g in payload.get("givens", [])]
            steps = [PlanStep(var=str(s["var"]), expr=str(s["expr"]),
                              source=str(s.get("source") or ""),
                              deps=[str(d) for d in (s.get("deps") or [])])
                     for s in payload.get("steps", [])]
            method_id = str(payload.get("method_id") or "")
            valid_ids = {c.method_id for c in cards}
            if method_id not in valid_ids:
                return None  # 必须引用检索到的方法卡（可追溯）
            return StructuredPlan(
                family=str(payload.get("family") or ""), method_id=method_id,
                givens=givens, steps=steps, target=str(payload.get("target") or ""),
                rationale=str(payload.get("rationale") or "")[:200],
                unknowns=[str(u) for u in (payload.get("unknowns") or [])][:8],
                solver_source="llm_planner",
                question_hypotheses=[str(h) for h in (payload.get("question_hypotheses") or [])][:4])
        except (KeyError, TypeError):
            return None

    # ---------- 多路径（B2）：最多两计划，冲突比证据 ----------
    def plan_multi(self, stem: str, options: List[Dict[str, Any]], skill: str,
                   max_plans: int = 2) -> List[Tuple[StructuredPlan, PlanExecution]]:
        results: List[Tuple[StructuredPlan, PlanExecution]] = []
        seen_values: Dict[str, str] = {}
        cards = self.kb.search(stem, skill=skill, top_k=2)
        for _round in range(min(max_plans, 2)):
            plan, used, _why = self.plan_llm(stem, options, skill, cards=cards)
            if plan is None:
                break
            ex = execute_plan(plan)
            if ex.final is None:
                break
            # 同一结果不同方法：要求 givens 来源片段互不相同才算独立（防同错共票）
            key = str(ex.final)
            if key in seen_values:
                spans_prev = {g.stem_span for g in results[0][0].givens}
                spans_now = {g.stem_span for g in plan.givens}
                if spans_now == spans_prev:
                    break  # 同一错误前提衍生，不加成
            results.append((plan, ex))
            seen_values[key] = plan.method_id
            cards = [c for c in used if c.method_id != plan.method_id] or cards
            if len(results) >= 1:
                break  # 默认单计划即进入验证；第二计划仅在需要仲裁时由调用方触发
        return results
