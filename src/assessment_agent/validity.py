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


def _tok_frac(t: str) -> Fraction:
    if "/" in t:
        a, b = t.split("/", 1)
        return Fraction(a) / Fraction(b)
    return Fraction(t)


def _F(text: Any) -> Optional[Fraction]:
    m = re.search(r"(-?\d+(?:\.\d+)?)(?:/(\d+))?", str(text).replace(",", ""))
    if not m:
        return None
    v = Fraction(m.group(1))
    if m.group(2):
        v = v / Fraction(m.group(2))
    return v / 100 if "%" in str(text) else v


def check_proof_option_alignment(proof_text: str, options: Sequence[Tuple[str, str]],
                                 answer: str) -> Tuple[bool, str]:
    """R9-A 独立检查②：证明文本的字母↔数值绑定必须与冻结选项一致。

    提取 proof 中"字母 + 数值簇"声明（如 "D 答 1584"、"A 1875 为基期"），
    与冻结选项文本数值簇比对：某字母绑定的 ≥3 位数出现在**另一字母**选项而
    不在自身选项 → 错位（常见成因：证明写于打乱之前）。金标字母行与 <3 位
    数字（年份尾段/序号）不作证据；未做字母数值声明 → 不适用即通过。"""
    opt_nums = {lb: set(re.findall(r"\d+(?:\.\d+)?", txt)) for lb, txt in options}
    claims = []
    for seg in re.split(r"[；;。\n]", proof_text):
        labs = re.findall(r"\b([ABCD])\b", seg)
        nums = set(re.findall(r"\d+(?:\.\d+)?", seg))
        if len(labs) == 1 and nums:
            claims.append((labs[0], nums, seg.strip()[:40]))
    problems = []
    for lab, nums, _seg in claims:
        own = opt_nums.get(lab, set())
        for n in nums:
            if n == answer or len(n.split(".")[0]) < 3:
                continue
            if n in own:
                continue
            for lb2, nums2 in opt_nums.items():
                if lb2 != lab and n in nums2:
                    problems.append(f"证明把 {n} 绑到 {lab}，但 {n} 实为选项 {lb2}")
                    break
    if problems:
        return False, "proof 字母错位：" + "；".join(sorted(set(problems))[:4])
    return True, f"{len(claims)} 条字母数值声明与冻结选项一致"


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
    negative: bool = False                      # 定义/细节负设问（"不属于/不符"）
    semantic_gold: bool = False                 # 金标为文字选项（非数值）
    declared_next: Optional[str] = None         # series：金标下一项
    defs: Dict[str, str] = field(default_factory=dict)      # 逻辑：缩写（如 T1 → "J2"）
    derivations: Dict[str, str] = field(default_factory=dict)   # 中间量→推导式（如 "24": "4*3*2*1"）
    exclusive_groups: Sequence[Sequence[str]] = ()           # 恰一为真的原子组（作案者唯一）
    elements: Sequence[str] = ()                # definition：定义要件清单
    options_elements: Sequence[Tuple[str, Sequence[str]]] = ()  # definition：选项满足要件
    material_formulas: Sequence[tuple] = ()     # 材料数量声明（R9-A 检查①，见 _material_consistency）


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
        allowed: List[Fraction] = [Fraction(1), Fraction(2), Fraction(100), Fraction(1000)]   # 结构常量：单位1/配对对称除2/百分换算
        vals = [v for v in cond.facts.values() if isinstance(v, Fraction)]
        for v in vals + [x for x in (_F(n) for n in cond.numbers) if x is not None]:
            allowed += [v, v / 100, 1 + v / 100, 1 - v / 100]
        for r in cond.rates.values():
            allowed += [r, 1 + r, 1 - r, r * 100]
        # 显式推导声明（中间量）：固定点迭代，推导式数值仍须溯源到声明集
        for _round in range(4):
            base = list(allowed)
            progressed = False
            for dexpr in (cond.derivations or {}).values():
                dnums = [_tok_frac(m.group(0)) for m in
                         re.finditer(r"(?<![\d./])\d+(?:\.\d+)?(?:/\d+)?", dexpr)]
                if all(any(self._equiv(n, a) for a in base) for n in dnums):
                    try:
                        dv = Fraction(_ExprParser(_tokenize_expr(dexpr), {}).parse())
                        if dv not in allowed:
                            allowed += [dv, dv / 100]
                            progressed = True
                    except Exception:  # noqa: BLE001
                        pass
            if not progressed:
                break
        nums = [_tok_frac(m.group(0)) for m in
                re.finditer(r"(?<![\d./])\d+(?:\.\d+)?(?:/\d+)?", expr)]
        bad = [n for n in nums if not (any(self._equiv(n, a) for a in allowed)
                                        or any(n == a / bb for a in allowed
                                               for bb in allowed if bb))]
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
    def check_sufficiency(self, cond: DeclaredConditions,
                          expr: str = "") -> Tuple[bool, str]:
        # R9-A 独立检查①：材料内部数量声明必须互相闭合，矛盾材料不唯一确定任何结果
        if cond.material_formulas:
            ok, why = self._material_consistency(cond)
            if not ok:
                return False, why
        if cond.kind == "data_analysis":
            return self._sufficiency_da(cond, expr)
        if cond.kind in ("propositional", "truth_teller"):
            return self._sufficiency_logic(cond)
        if cond.kind == "fact_match":
            return self._sufficiency_fact_match(cond)
        if cond.kind == "enumeration":
            return (True, "枚举计数题：金标由生成时暴力穷举产出并复核（add 强制 brute==金标）")
        if cond.kind == "manual_semantic":
            return (True, "语义强度题：人工审查结论记录于 proof.verification=manual_only")
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
        if cond.kind == "definition":
            return self._sufficiency_definition(cond)
        if cond.kind == "monadic":
            return self._sufficiency_monadic(cond)
        # numeric：依赖层已保证操作数全部声明过；确定性算式即充分
        return True, "确定性算式，操作数均已声明"

    @staticmethod
    def _material_consistency(cond: DeclaredConditions) -> Tuple[bool, str]:
        """R9-A 检查①：材料数量声明闭合。
        formula 元组：
          ("total", name, value)                       总量基准值
          ("pct_of", part, total, value, pct, tol)     分项现期值 ≈ total×pct%
          ("growth", part, prev_name, pct, cur, tol)   现期 ≈ 基期×(1+pct%)
        tol 为绝对容差（金标声明"约为"时放宽）；矛盾即条件互斥→题目无效。"""
        vals: Dict[str, Fraction] = {}
        conflicts: list = []
        for f in cond.material_formulas:
            kind = str(f[0])
            if kind == "total":
                vals[str(f[1])] = _F(str(f[2]))
            elif kind == "pct_of":
                part, total, value, pct, tol = f[1], f[2], _F(str(f[3])), _F(str(f[4])), _F(str(f[5]))
                tv = vals.get(str(total))
                if tv is not None:
                    implied = tv * pct / 100
                    if abs(implied - value) > tol:
                        conflicts.append(f"{part}={float(value):g} 与 {total}×{float(pct):g}%"
                                         f"={float(implied):g} 矛盾（差 {float(abs(implied - value)):g}）")
            elif kind == "growth":
                part, prev, pct, cur, tol = f[1], f[2], _F(str(f[3])), _F(str(f[4])), _F(str(f[5]))
                pv = vals.get(str(prev))
                if pv is not None:
                    implied = pv * (1 + pct / 100)
                    if abs(implied - cur) > tol:
                        conflicts.append(f"{part}={float(cur):g} 与 {prev}×(1+{float(pct):g}%)"
                                         f"={float(implied):g} 矛盾")
        if conflicts:
            return False, "材料内部矛盾：" + "；".join(conflicts) + "（条件互斥，不唯一确定）"
        return True, f"材料 {len(cond.material_formulas)} 条数量声明闭合"

    def _sufficiency_fact_match(self, cond: DeclaredConditions) -> Tuple[bool, str]:
        """细节判断题的机械通道：材料事实字典 facts{key: value}，
        每个选项声明其断言的 (key, value) 清单（options_elements）。
        选项为真 = 其全部断言与材料一致；恰一为真才有效。
        另要求：金标断言 ≥2 键；每个干扰项至少与材料有一键冲突（非同义复述）。"""
        claims = {lb: list(kv) for lb, kv in cond.options_elements}
        if not cond.facts or len(claims) < 3:
            return False, "fact_match 需材料 facts 与 ≥3 选项断言"
        def true_of(lb):
            kv = claims[lb]
            return len(kv) >= 2 and all(cond.facts.get(k) == v for k, v in kv)
        hits = [lb for lb in claims if true_of(lb)]
        conflicts = [lb for lb, kv in claims.items()
                     if any(k not in cond.facts or cond.facts[k] != v for k, v in kv)]
        if cond.negative:
            if len(conflicts) != 1 or len(hits) != len(claims) - 1:
                return False, f"负设问不闭合（一致 {hits}，冲突 {conflicts}）"
            return True, f"负设问闭合：恰一选项与材料冲突（{conflicts[0]}），其余全一致"
        if len(hits) != 1:
            return False, f"与材料全一致的选项 {hits} 不唯一"
        if len(conflicts) != len(claims) - 1:
            return False, "存在既不冲突也不全同的漏网干扰项（键不在材料中）"
        return True, f"材料 {len(cond.facts)} 键，恰一选项全部断言一致（{hits[0]}），其余均有一键冲突"

    def _sufficiency_definition(self, cond: DeclaredConditions) -> Tuple[bool, str]:
        """定义判断：要件矩阵（人工逐要件审查结论的形式化记录）。
        唯一恰选项 = 恰有一个选项满足全部要件，且其余选项至少缺一要件。"""
        need = set(cond.elements)
        if not need or not cond.options_elements:
            return False, "definition 通道需 elements + options_elements"
        full = [lb for lb, got in cond.options_elements if need <= set(got)]
        if cond.negative:
            # 问"不属于"：恰一选项缺要件且其余全满足
            miss = [lb for lb, _g in cond.options_elements if lb not in full]
            if len(miss) != 1 or len(full) != len(cond.options_elements) - 1:
                return False, f"负设问不闭合（全满足 {full}，缺失 {miss}）"
            return True, f"负设问闭合：恰一选项缺要件（{miss[0]}），其余全部满足"
        if len(full) != 1:
            return False, f"满足全部要件的选项 {full} 不唯一"
        return True, f"要件矩阵恰一选项全满足（{full[0]}），其余均有缺失要件"

    def _sufficiency_monadic(self, cond: DeclaredConditions) -> Tuple[bool, str]:
        """量化三段论：单谓词一阶逻辑在有限域（size 1..3）上穷举模型。
        前提形如 all(P,Q)/some(P,Q)/no(P,Q)；选项同形，另支持特殊式
        none_above_necessary（其余选项均不必然时为真）。"""
        def parse_mm(text: str):
            t = str(text).strip()
            if t == "none_above_necessary":
                return "META"
            m = re.fullmatch(r"\s*(all|some|no)\((\w+),(\w+)\)\s*", t)
            return (m.group(1), m.group(2), m.group(3)) if m else None
        prem = [parse_mm(p) for p in cond.premises]
        opts = [(lb, parse_mm(f)) for lb, f in cond.options_logic]
        if any(p is None for p in prem) or any(p is None for _lb, p in opts):
            return False, "monadic 语句需 all/some/no(P,Q) 或 none_above_necessary 形"
        preds = sorted({p for st in prem + [x for _, x in opts if x != "META"] for p in st[1:]})
        if not prem or len(preds) > 4:
            return False, "monadic 需 ≥1 前提且谓词 ≤4"

        def sat(st, assign, d):
            kind, a, b = st
            pa = {i for i in range(d) if assign.get((a, i))}
            pb = {i for i in range(d) if assign.get((b, i))}
            if kind == "all":
                return not (pa - pb)
            if kind == "no":
                return not (pa & pb)
            return bool(pa & pb)  # some

        models = []
        for d in (1, 2, 3):
            bits = [(p, i) for p in preds for i in range(d)]
            for mask in range(1 << len(bits)):
                assign = {b: bool(mask >> k & 1) for k, b in enumerate(bits)}
                if all(sat(st, assign, d) for st in prem):
                    models.append((d, assign))
        if not models:
            return False, "前提在任何 ≤3 元域上无模型（不一致）"

        necessary = []
        for lb, pp in opts:
            if pp == "META":
                continue
            if all(sat(pp, a, d) for d, a in models):
                necessary.append(lb)
        musts = list(necessary)
        meta_labels = [lb for lb, pp in opts if pp == "META"]
        if meta_labels and not musts:
            musts = meta_labels[:1] if len(meta_labels) == 1 else []
        if len(musts) != 1:
            return False, f"必然选项 {musts} 不唯一（模型 {len(models)} 个）"
        return True, f"≤3 元模型 {len(models)} 个，恰一选项必然成立：{musts[0]}"

    @staticmethod
    def _sufficiency_da(cond: DeclaredConditions,
                        expr: str = "") -> Tuple[bool, str]:
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
        # 篡改步数防线：(1±r) 连乘次数不得超过目标年与锚年跨度
        if expr:
            steps = len(re.findall(r"\(1[+-]", expr))
            if steps > 1 and cond.ask_year and cond.ask_year.isdigit():
                span_best = None
                for y in known:
                    if y.isdigit():
                        sp = abs(int(cond.ask_year) - int(y))
                        span_best = sp if span_best is None else min(span_best, sp)
                if span_best is not None and steps > max(span_best, 1):
                    return False, (f"表达式 (1±r) 连乘 {steps} 次，超过目标年 {cond.ask_year} "
                                   f"与已知锚年跨度 {span_best}（疑似率重复使用的篡改步）")
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
                if f.strip() == "NONE_MUST":
                    out.append(("meta", None))
                elif f.startswith("rule:"):
                    out.append(("rule", parse_formula(f[5:])))
                elif f.startswith("fact:"):
                    out.append(("fact", parse_formula(f[5:])))
                else:
                    out.append(("fact", parse_formula(f)))
            return out
        try:
            def expand(text):
                s = str(text)
                for _ in range(4):
                    for k, v in (cond.defs or {}).items():
                        s = re.sub(rf"\b{k}\b", f"({v})", s)
                return s
            prem_src = [expand(p) for p in cond.premises]
            for grp in cond.exclusive_groups:
                grp = list(grp)
                dis = "|".join(grp)
                conj = "&".join(f"!(%s&%s)" % (grp[i], grp[j])
                                for i, j in itertools.combinations(range(len(grp)), 2))
                prem_src.append(f"({dis})" + (f"&{conj}" if len(grp) > 1 else ""))
            sigma = load(prem_src)
            opts = [(lb, load([expand(f)])[0]) for lb, f in cond.options_logic]
        except FormulaError as exc:
            return False, f"公式解析失败: {exc}"
        all_fx = [fx for _, fx in sigma] + [fx for _, (_k, fx) in opts if fx is not None]
        atoms = sorted({a for fx in all_fx for a in atoms_of(fx)})
        if not atoms or len(atoms) > 4:
            return False, "原子数需在 1..4 的枚举范围"
        pairs = [(x, y) for x in atoms for y in atoms if x != y]
        has_rule = (any(kind == "rule" for kind, _ in sigma)
                    or any(kind == "rule" for _lb, (kind, _fx) in opts))
        r_masks = range(1 << len(pairs)) if has_rule else (0,)
        models = []
        for rmask in r_masks:
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
                if kind != "meta" and all(holds(fx, kind, R, env) for R, env in models)]
        # 元选项 NONE_MUST：其余选项无一必然时，"无法确定"为唯一必然；否则它必假
        metas = [lb for lb, (kind, _fx) in opts if kind == "meta"]
        if metas and not must:
            must = metas[:1] if len(metas) == 1 else []
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
        semantic = (cond.kind in ("definition", "monadic", "truth_teller",
                                  "manual_semantic", "enumeration") or cond.semantic_gold)
        if cond.semantic_gold or cond.kind in ("definition", "monadic", "truth_teller",
                                               "manual_semantic", "fact_match"):
            gold = None   # 文字选项题：金标文本不做数值抽取（rb-05 口径防误伤）
        if gold is None and not semantic:
            layers["arithmetic"] = (False, "数值类题目金标非数值，无法重放")
        elif gold is None:
            layers["arithmetic"] = (True, "文字选项题：算术重放不适用（唯一性由语义通道判定）")
        else:
            layers["arithmetic"] = self.check_arithmetic(gold_expr, gold, approx_declared)
        if (cond.kind in ("series", "enumeration", "fact_match")
                or (gold is None and semantic)):
            layers["dependency"] = (True, "该题型数值依赖由充分性层通道判定")
        else:
            layers["dependency"] = self.check_dependency(gold_expr, cond)
        layers["sufficiency"] = self.check_sufficiency(cond, gold_expr)
        if cond.kind == "propositional" or semantic or gold is None:
            layers["option_unique"] = (True, "选项唯一性由模型/要件层判定")
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
