"""R8 阶段A：ProblemValidityVerifier —— 题目金标有效性的四层独立审查。

设计原则（对应工单 §A.5）：
1. **算术层**：金标值必须由声明条件的表达式用 Fraction 独立重放得到；
2. **依赖层**：表达式引用的每个数字必须是题干声明条件的等价类
   （n、n/100、1±n/100…），出现"延续假设"（把 Y 年率用于 Y+1 年）即失败；
3. **充分性层**：条件必须**唯一确定**目标——
   - 资料分析：年份可达性不动点（值+归属同年的率才能推进一年）；
   - 命题推理：前提模型枚举非空且恰一个选项在所有模型中为真；
4. **选项唯一层**：容差带内恰一个选项命中；舍入语义必须由命题方**显式声明**
   （approx_declared），否则 33⅓% vs 33.3% 判不唯一。

自动命题**不得仅凭生成公式**标记 has_unique_answer：生成器必须先经本验证器。
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .planner import _ExprParser, _tokenize_expr  # 同一执行核，防止双口径


def _F(text: Any) -> Optional[Fraction]:
    m = re.search(r"(-?\d+(?:\.\d+)?)(?:/(\d+))?", str(text).replace(",", ""))
    if not m:
        return None
    v = Fraction(m.group(1))
    if m.group(2):
        v = v / Fraction(m.group(2))
    return v / 100 if "%" in str(text) else v


@dataclass
class DeclaredConditions:
    """命题方显式声明的条件（评测数据不含此结构，由生成器/人工命题提供）。"""
    kind: str                                   # data_analysis | propositional | numeric
    facts: Dict[str, Fraction] = field(default_factory=dict)    # da: {"2023": F(1100)}
    rates: Dict[str, Fraction] = field(default_factory=dict)    # da: {"2023": F(1,5)} 归属年
    ask_year: Optional[str] = None              # da: 目标年
    ask_delta: bool = False                     # da: 问增量（需要目标年与上年均可达）
    premises: Sequence[str] = ()                # 逻辑：公式串（可带 rule:/fact: 前缀）
    options_logic: Sequence[Tuple[str, str]] = ()   # (label, formula)
    numbers: Sequence[str] = ()                 # 声明的数字条件原文
    gold_value: Optional[Fraction] = None       # 金标数值（da 直给值矛盾检查用）
    stem: Optional[str] = None                  # series：题面原文
    declared_next: Optional[str] = None         # series：金标下一项


@dataclass
class ValidityReport:
    ok: bool
    layers: Dict[str, Tuple[bool, str]]
    verdict: str            # valid | flawed | invalid
    marker: str             # has_unique_answer | underdetermined | ambiguous_options | rounding_undeclared

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "verdict": self.verdict, "marker": self.marker,
                "layers": {k: {"passed": p, "why": w} for k, (p, w) in self.layers.items()}}


class ProblemValidityVerifier:

    # ---------------- 层1 算术 ----------------
    def check_arithmetic(self, expr: str, gold: Fraction,
                         approx_declared: bool = False) -> Tuple[bool, str]:
        try:
            val = _ExprParser(_tokenize_expr(expr), {}).parse()
        except Exception as exc:  # noqa: BLE001
            return False, f"表达式重放失败: {exc}"
        v = Fraction(val)
        if v == gold:
            return True, "Fraction 独立重放一致"
        if approx_declared and abs(v - gold) <= abs(gold) * Fraction(5, 1000) + Fraction(1, 10 ** 6):
            return True, "重放在已声明的近似容差内一致"
        return False, f"重放 {float(v):g} ≠ 金标 {float(gold):g}"

    # ---------------- 层2 依赖 ----------------
    def check_dependency(self, expr: str, cond: DeclaredConditions) -> Tuple[bool, str]:
        """表达式数字必须能溯源到声明条件（值或 1±率 形态），否则是隐含假设。"""
        allowed: List[Fraction] = [Fraction(1), Fraction(100), Fraction(1000)]   # 结构常量（单位 1 / 百分换算）
        for v in list(cond.facts.values()) + [x for x in (_F(n) for n in cond.numbers) if x is not None]:
            allowed += [v, v / 100, 1 + v / 100, 1 - v / 100]
        for r in cond.rates.values():
            allowed += [r, 1 + r, 1 - r, r * 100]
        nums = [Fraction(m.group(0)) for m in
                re.finditer(r"(?<![\d./])\d+(?:/\d+)?(?:\.\d+)?(?![\d/])", expr)]
        bad = [n for n in nums if not any(self._equiv(n, a) for a in allowed)]
        if bad:
            return False, f"表达式含未声明数字 {bad}（疑似隐含假设）"
        return True, "全部操作数可溯源到声明条件"

    @staticmethod
    def _equiv(n: Fraction, a: Fraction) -> bool:
        if n == a or n == 1:
            return True
        if a == 0:
            return False
        return (n == a / 100 or n == a * 100 or n == 1 + a / 100 or n == 1 - a / 100
                or n == 1 / a)  # 倒数：效率↔时间

    # ---------------- 层3 充分性 ----------------
    def check_sufficiency(self, cond: DeclaredConditions) -> Tuple[bool, str]:
        if cond.kind == "data_analysis":
            return self._sufficiency_da(cond)
        if cond.kind == "propositional":
            return self._sufficiency_logic(cond)
        if cond.kind == "series":
            from .tools.series import parse_sequence, solve_series
            seq = parse_sequence(cond.stem or "")
            if not seq:
                return False, "数列无法解析"
            res = solve_series(seq)
            names = [c.name for c in res.candidates]
            if len(names) != 1:
                return False, f"规律候选 {names} 不唯一（归纳歧义）"
            if str(res.next_value) != str(_F(cond.declared_next or "")):
                return False, f"唯一规律给出 {res.next_value} ≠ 金标 {cond.declared_next}"
            return True, f"规律唯一（{names[0]}）且外推与金标一致"
        # numeric：依赖层已保证操作数全部声明过；确定性算式即充分
        return True, "确定性算式，操作数均已声明"

    @staticmethod
    def _sufficiency_da(cond: DeclaredConditions) -> Tuple[bool, str]:
        """年份可达性不动点：facts{年:值}, rates{年: 该年对上一年的率}。
        已知 y 值 + 率(y) → y−1 可导；已知 y 值 + 率(y+1) → y+1 可导。"""
        known = {y for y, v in cond.facts.items() if v is not None}
        changed = True
        while changed:
            changed = False
            for y in list(known):
                r_next = cond.rates.get(str(int(y) + 1)) if y.isdigit() else None
                if y.isdigit() and r_next is not None and str(int(y) + 1) not in known:
                    known.add(str(int(y) + 1)); changed = True
                r_self = cond.rates.get(y) if y.isdigit() else None
                if y.isdigit() and r_self is not None and str(int(y) - 1) not in known:
                    known.add(str(int(y) - 1)); changed = True
        if cond.ask_year is None:
            return True, "无年份类目标"
        if cond.ask_year not in known:
            return False, (f"目标年 {cond.ask_year} 不在条件可达闭包 {sorted(known)} 内"
                           "（缺少覆盖该年的值或以其归属年为锚的率）")
        if cond.ask_delta:
            prev = str(int(cond.ask_year) - 1) if cond.ask_year.isdigit() else None
            if prev is not None and prev not in known:
                return False, f"增量需要上年 {prev} 可导，实际不可达"
        direct = cond.facts.get(cond.ask_year) if not cond.ask_delta else None
        if direct is not None and cond.gold_value is not None and direct != cond.gold_value:
            return False, f"目标年直接给值 {float(direct):g} 与金标 {float(cond.gold_value):g} 矛盾"
        return True, "目标年（及上年）均在可达闭包内"

    def _sufficiency_logic(self, cond: DeclaredConditions) -> Tuple[bool, str]:
        """规则/实例两层语义：
        - `rule:F` = 全局方向约束（对所有个体成立的蕴含式）；
        - `fact:F`（或裸公式）= 特定实例断言。
        模型 = (规则方向集 R 满足前提规则, 实例赋值 v 与 R 闭合一致)。
        选项"必然为真"= 在所有模型为真。'…都…'型选项按 rule 判，
        不会被实例级空真错误放行（∀(A→B) 不由 {∀(B→A), Inst(B)} 推出）。"""
        from .tools.prop_logic import FormulaError, atoms_of, parse_formula

        def feval(f, env):
            if f.kind == "atom":
                return env[f.name]
            if f.kind == "not":
                return not feval(f.children[0], env)
            if f.kind == "and":
                return all(feval(c, env) for c in f.children)
            if f.kind == "or":
                return any(feval(c, env) for c in f.children)
            if f.kind == "imp":
                return (not feval(f.children[0], env)) or feval(f.children[1], env)
            if f.kind == "iff":
                return feval(f.children[0], env) == feval(f.children[1], env)
            raise FormulaError(f"未知节点 {f.kind}")

        def load(fs):
            out = []
            for f in fs:
                f = str(f)
                if f.startswith("rule:"):
                    out.append(("rule", parse_formula(f[5:])))
                elif f.startswith("fact:"):
                    out.append(("fact", parse_formula(f[5:])))
                else:
                    out.append(("fact", parse_formula(f)))
            return out
        try:
            sigma = load(cond.premises)
            opts = [(lb, load([fx])[0]) for lb, fx in cond.options_logic]
        except FormulaError as exc:
            return False, f"公式解析失败: {exc}"
        all_fx = [fx for _, fx in sigma] + [fx for _, (_k, fx) in opts]
        atoms = sorted({a for fx in all_fx for a in atoms_of(fx)})
        if not atoms or len(atoms) > 4:
            return False, "原子数需在 1..4 的枚举范围"
        pairs = [(x, y) for x in atoms for y in atoms if x != y]
        models = []
        for rmask in range(1 << len(pairs)):
            R = {p for i, p in enumerate(pairs) if rmask >> i & 1}
            bad_rule = False
            for kind, fx in sigma:
                if kind != "rule":
                    continue
                if fx.kind != "imp":
                    return False, "规则前提需为蕴含式"
                if (fx.children[0].name, fx.children[1].name) not in R:
                    bad_rule = True
            if bad_rule:
                continue
            for v in itertools.product((False, True), repeat=len(atoms)):
                env = dict(zip(atoms, v))
                if not all(feval(fx, env) for kind, fx in sigma if kind == "fact"):
                    continue
                if any(env[x] and not env[y] for x, y in R):
                    continue
                models.append((R, env))
        if not models:
            return False, "前提集无模型（不可满足或超出表达力）"

        def holds(fx, kind, R, env):
            if kind == "fact":
                return feval(fx, env)
            if fx.kind == "imp":
                return (fx.children[0].name, fx.children[1].name) in R
            for v in itertools.product((False, True), repeat=len(atoms)):
                e = dict(zip(atoms, v))
                if any(e[x] and not e[y] for x, y in R):
                    continue
                if not feval(fx, e):
                    return False
            return True
        must = [lb for lb, (kind, fx) in opts
                if all(holds(fx, kind, R, env) for R, env in models)]
        if len(must) != 1:
            return False, f"必然为真的选项数={len(must)}（{must}），不唯一"
        return True, f"模型 {len(models)} 个，恰一选项必然为真：{must[0]}"

    # ---------------- 层4 选项唯一 ----------------
    def check_option_uniqueness(self, options: Sequence[Tuple[str, str]], gold: Fraction,
                                approx_declared: bool) -> Tuple[bool, str]:
        tol = Fraction(5, 1000) if approx_declared else Fraction(1, 10 ** 9)
        hits = [lb for lb, text in options
                if (v := _F(text)) is not None and abs(v - gold) <= abs(gold) * tol + Fraction(1, 10 ** 6)]
        if len(hits) == 1:
            return True, f"唯一命中 {hits[0]}"
        if not hits:
            return False, ("无选项命中金标（舍入/近似未声明）" if not approx_declared
                           else "无选项命中金标")
        return False, f"容差带内多选项命中 {hits}，歧义"

    # ---------------- 汇总 ----------------
    def verify_item(self, *, gold_expr: str, gold: Fraction,
                    options: Sequence[Tuple[str, str]], cond: DeclaredConditions,
                    approx_declared: bool = False,
                    gold_label: Optional[str] = None) -> ValidityReport:
        layers: Dict[str, Tuple[bool, str]] = {}
        layers["arithmetic"] = self.check_arithmetic(gold_expr, gold, approx_declared)
        if cond.kind == "series":
            layers["dependency"] = (True, "数列归纳题：数值依赖由充分性层的唯一规律判定")
        else:
            layers["dependency"] = self.check_dependency(gold_expr, cond)
        layers["sufficiency"] = self.check_sufficiency(cond)
        if cond.kind == "propositional":
            layers["option_unique"] = (True, "逻辑题选项唯一性由模型层判定")
        else:
            layers["option_unique"] = self.check_option_uniqueness(options, gold, approx_declared)
        _ = gold_label  # 金标 label 供上层对答案表；本验证器只审数值关系
        ok = all(p for p, _ in layers.values())
        if not layers["sufficiency"][0] or not layers["dependency"][0]:
            verdict, marker = "invalid", "underdetermined"
        elif not layers["option_unique"][0]:
            verdict = "flawed"
            marker = "rounding_undeclared" if not approx_declared else "ambiguous_options"
        elif not layers["arithmetic"][0]:
            verdict, marker = "invalid", "gold_mismatch"
        else:
            verdict, marker = "valid", "has_unique_answer"
        return ValidityReport(ok=ok, layers=layers, verdict=verdict, marker=marker)
